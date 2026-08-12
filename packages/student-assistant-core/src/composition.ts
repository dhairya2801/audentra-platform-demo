import type { RequirementStatus } from "@vv/contracts";
import type {
  EligibilityGate,
  OpenWindow,
  RequestClassification,
  StudentAssistantContextReceipt,
  StudentAssistantRequestType,
} from "./contracts";
import type {
  GroundedFactForComposition,
  ModelGroundedCompositionOutput,
  StudentAssistantModel,
} from "./model";
import type {
  DerivedStudentState,
  NormalizedStudentRequest,
} from "./state";
import {
  isActionRequiredSubmissionState,
  submissionStateSentence,
} from "./document-lifecycle";
import { financialAidRequirementMatchesEntity } from "./financial-aid";
import {
  buildCausalGuards,
  buildEvidenceBundle,
  guardGroundedAnswer,
} from "./answer";

export interface GroundedCompositionDraft {
  facts: GroundedFactForComposition[];
  selectedFactIds: string[] | null;
  fallbackMessage: string;
  modelAttempted: boolean;
  modelFailed: boolean;
  invalidModelResult: boolean;
  /** Guarded natural-language answer, when the model wrote one that passed. */
  proseAnswer: string | null;
  /** Why a written answer was refused, for telemetry. */
  proseRejectedReason: string | null;
}

export interface ValidatedGroundedMessage {
  message: string;
  usedDeterministicFallback: boolean;
  removedUnsupportedClaims: number;
}

export async function composeGroundedAnswerNode(input: {
  classification: RequestClassification;
  additionalClassifications?: RequestClassification[];
  normalized: NormalizedStudentRequest;
  derived: DerivedStudentState;
  model?: StudentAssistantModel;
}): Promise<GroundedCompositionDraft> {
  // "Can first-year students live off campus?" asks what the rule is. The
  // planner sometimes attaches this student's own intents to it, and the reply
  // then opened with their outstanding verification worksheet and never reached
  // the policy. A policy question is answered from policy alone.
  const classifications =
    input.classification.requestType === "policy_lookup"
      ? [input.classification]
      : [input.classification, ...(input.additionalClassifications ?? [])];
  const facts = deduplicateFacts(
    classifications.flatMap((classification) =>
      buildGroundedFacts(classification, input.derived),
    ),
  );
  const fallbackMessage = buildFallbackMessage(
    classifications,
    input.derived,
    facts,
  );

  // Preferred path: the model writes the reply and a deterministic guard
  // re-checks it. Safety-gated turns never reach the writer.
  if (input.model?.writeGroundedAnswer && canWriteProse(input)) {
    const written = await writeProseAnswer({
      classifications,
      normalized: input.normalized,
      derived: input.derived,
      model: input.model,
      fallbackFacts: facts,
    });
    if (written) {
      return {
        facts,
        selectedFactIds: null,
        fallbackMessage,
        modelAttempted: true,
        modelFailed: written.modelFailed,
        invalidModelResult: false,
        proseAnswer: written.answer,
        proseRejectedReason: written.rejectedReason,
      };
    }
  }

  if (
    (input.classification.requestType !== "explain_requirement" &&
      input.classification.requestType !== "aid_requirement_explanation" &&
      classifications.length === 1) ||
    !input.model ||
    // Fact-ID selection concatenates whole template sentences. A model that can
    // write has already had its chance above; falling through to selection here
    // would produce exactly the dump the written path exists to replace.
    input.model.writeGroundedAnswer ||
    facts.length === 0
  ) {
    return {
      facts,
      selectedFactIds: null,
      fallbackMessage,
      modelAttempted: false,
      modelFailed: false,
      invalidModelResult: false,
      proseAnswer: null,
      proseRejectedReason: null,
    };
  }

  try {
    const output: unknown = await input.model.composeGroundedResponse({
      requestType: input.classification.requestType,
      requestTypes: classifications.map(
        (classification) => classification.requestType,
      ),
      normalizedMessage: input.normalized.text,
      facts,
    });
    if (!isCompositionOutput(output)) {
      return {
        facts,
        selectedFactIds: null,
        fallbackMessage,
        modelAttempted: true,
        modelFailed: false,
        invalidModelResult: true,
        proseAnswer: null,
        proseRejectedReason: null,
      };
    }
    return {
      facts,
      selectedFactIds: output.factIds.slice(0, 12),
      fallbackMessage,
      modelAttempted: true,
      modelFailed: false,
      invalidModelResult: false,
      proseAnswer: null,
      proseRejectedReason: null,
    };
  } catch {
    return {
      facts,
      selectedFactIds: null,
      fallbackMessage,
      modelAttempted: true,
      modelFailed: true,
      invalidModelResult: false,
      proseAnswer: null,
      proseRejectedReason: null,
    };
  }
}

/**
 * Render the deterministic message once per *distinct* message rather than once
 * per classification. Rendering per classification repeated the entire merged
 * fact list behind each request type's lead-in, which is why multi-intent
 * questions produced the same deadline list two and three times over.
 */
function buildFallbackMessage(
  classifications: readonly RequestClassification[],
  derived: DerivedStudentState,
  facts: readonly GroundedFactForComposition[],
): string {
  if (classifications.length === 0) return "";
  // A request for clarification is the entire answer. Concatenating other
  // intents behind it produced replies that asked a question and then answered
  // a different one.
  const ambiguous = classifications.find(
    (classification) => classification.blockerScope === "registration_ambiguous",
  );
  if (ambiguous) return deterministicMessage(ambiguous, derived, []);
  if (classifications.length === 1) {
    return deterministicMessage(classifications[0]!, derived, facts);
  }
  // Each intent renders only the facts it actually asked for. Rendering every
  // intent against the merged fact list repeated the same deadline list behind
  // each intent's lead-in.
  const messages = classifications.map((classification) =>
    deterministicMessage(
      classification,
      derived,
      buildGroundedFacts(classification, derived),
    ),
  );
  return deduplicateSentences([...new Set(messages)].join(" "));
}

/**
 * Internal status words are for the record, not for a student. Each of these
 * translates one enum into a sentence a person can act on; leaving the raw
 * value in the text was how "its current authoritative state is none" reached
 * a student-facing answer.
 */
function aidDocumentSentence(label: string, documentState: string): string {
  switch (documentState) {
    case "none":
      return `${label}: not received yet, so it is still needed.`;
    case "uploaded":
    case "processing":
      return `${label}: uploaded and queued for review.`;
    case "under_review":
      return `${label}: received and currently under review by Financial Aid.`;
    case "rejected":
      return `${label}: reviewed and returned, so a corrected copy is needed.`;
    case "accepted":
      return `${label}: accepted.`;
    default:
      return `${label}: still needed.`;
  }
}

function aidRequirementPhrase(status: string): string {
  return (
    {
      not_started: "has not been started",
      action_required: "still needs action from you",
      submitted: "has been submitted and is waiting on review",
      under_review: "is under review",
      satisfied: "is complete",
      rejected: "was returned and needs to be resubmitted",
    }[status] ?? "is not complete yet"
  );
}

function aidVerificationSentence(status: string): string {
  return (
    {
      not_required: "Verification is not required for your aid this year.",
      action_required:
        "Financial Aid still needs your verification documents before your aid can be finalized.",
      submitted:
        "Your verification documents have been submitted and are waiting on review.",
      under_review: "Your verification documents are under review.",
      verified: "Your verification is complete.",
      needs_correction:
        "Your verification documents were returned and need a correction.",
    }[status] ?? "I can't confirm your verification status from your record."
  );
}

function formatUsd(amount: number): string {
  return `$${amount.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function formatDay(value: string): string {
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  return new Date(parsed).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}

/**
 * FAFSA state in words a student recognises. Kept as a table rather than left
 * to a model: "received" and "selected for verification" mean very different
 * things to someone waiting on money.
 */
function fafsaSentence(
  status: string,
  receivedAt: string | null,
  aidYear: string | null,
): string {
  const year = aidYear ? ` for ${aidYear}` : "";
  switch (status) {
    case "not_received":
      return `No FAFSA has been received${year}. Filing one is what starts your aid package.`;
    case "received":
      return `Your FAFSA${year} has been received${receivedAt ? ` (${formatDay(receivedAt)})` : ""} and is being processed.`;
    case "selected_for_verification":
      return `Your FAFSA${year} has been received${receivedAt ? ` (${formatDay(receivedAt)})` : ""} and was selected for verification, so Financial Aid needs supporting documents before your aid can be finalized.`;
    case "verification_complete":
      return `Your FAFSA${year} has been received and verification is complete.`;
    case "rejected":
      return `Your FAFSA${year} was rejected and needs to be corrected before it can be used.`;
    case "not_required":
      return `A FAFSA is not required${year} for the aid on your record.`;
    default:
      return `I can't confirm the status of your FAFSA${year} from your record.`;
  }
}

/**
 * The reply a student gets when the written answer is refused.
 *
 * The claim guard will always occasionally refuse a draft -- that is the point
 * of it -- so what it falls back to has to be readable. Concatenating every
 * gate fact produced 300 words naming four gates, each with its full reason and
 * owning office, and never answered the question. This states the outcome, names
 * the open gates, and gives one place to start.
 */
function gateFallbackMessage(
  requestType: StudentAssistantRequestType,
  derived: DerivedStudentState,
): string | null {
  const source =
    requestType === "registration_status"
      ? {
          gates: derived.registration?.gates ?? null,
          lead: `You can't register for ${derived.registration?.termName ?? "this term"} yet`,
        }
      : requestType === "housing_eligibility"
        ? {
            gates: derived.housingEligibility?.gates ?? null,
            lead: "You can't apply for housing yet",
          }
        : null;
  if (!source?.gates) return null;
  const open = source.gates.filter((gate) => !gate.satisfied);
  if (open.length === 0) return null;

  const first = open[0]!;
  return [
    `${source.lead}. ${open.length === 1 ? "One thing is" : `${open.length} things are`} still open: ${listSentence(open.map((gate) => gate.label.toLowerCase()))}.`,
    `These are the only things holding registration back${open.length > 1 ? "" : ""} — nothing else on your checklist affects it.`.replace(
      "registration",
      requestType === "registration_status" ? "registration" : "your housing application",
    ),
    `Start with ${first.label.toLowerCase()}${first.resolutionOwner ? `, which ${first.resolutionOwner} handles` : ""}.`,
  ].join(" ");
}

/** "a, b, and c" — used where a bare list would read as a data dump. */
function listSentence(items: readonly string[]): string {
  if (items.length === 0) return "";
  if (items.length === 1) return items[0]!;
  return `${items.slice(0, -1).join(", ")}, and ${items[items.length - 1]}`;
}

/** Final safety net against the same sentence surfacing from two intents. */
function deduplicateSentences(message: string): string {
  const seen = new Set<string>();
  return message
    .split(/(?<=\.)\s+/)
    .map((sentence) => sentence.trim())
    .filter((sentence) => {
      if (!sentence) return false;
      const key = sentence.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .join(" ");
}

/**
 * Turns whose wording is a safety or scope decision keep their exact
 * deterministic text. The model is never given the chance to soften a refusal,
 * invent a policy, or restate a privacy boundary in its own words.
 */
function canWriteProse(input: {
  classification: RequestClassification;
  normalized: NormalizedStudentRequest;
}): boolean {
  const { classification, normalized } = input;
  // A greeting and a capability overview have one right answer each, already
  // written. Sending them to a model buys variance, latency, and cost for a
  // sentence that should never vary.
  if (conversationalRequestTypes.has(classification.requestType)) return false;
  if (normalized.isMutationRequest) return false;
  if (normalized.containsSensitiveFinancialData) return false;
  if (classification.source === "safe_fallback") return false;
  if (classification.requestType === "unsupported_or_out_of_scope") return false;
  if (classification.blockerScope === "registration_ambiguous") return false;
  return !gatedRequirementReferences.has(
    classification.requirementReference ?? "",
  );
}

const conversationalRequestTypes = new Set<StudentAssistantRequestType>([
  "greeting",
  "capability_overview",
]);

/**
 * What Edward can actually help with, in the order a new student meets it.
 * Data rather than prose so the overview cannot drift away from the domains the
 * graph really reads, and so a tenant can shorten it without touching code.
 */
const capabilityAreas: readonly string[] = [
  "your enrollment checklist and next steps",
  "documents and their review status",
  "financial aid, awards, and what is still outstanding",
  "your student account balance and payments",
  "housing and move-in",
  "course registration and holds",
  "deadlines, orientation, and appointments",
];

const gatedRequirementReferences = new Set([
  "other_person_contact_details",
  "eligibility_unavailable",
  "award_amount_unavailable",
  "sensitive_financial_data",
  "financial_aid_write_unavailable",
  "housing_write_unavailable",
]);

async function writeProseAnswer(input: {
  classifications: readonly RequestClassification[];
  normalized: NormalizedStudentRequest;
  derived: DerivedStudentState;
  model: StudentAssistantModel;
  fallbackFacts: readonly GroundedFactForComposition[];
}): Promise<{
  answer: string | null;
  rejectedReason: string | null;
  modelFailed: boolean;
} | null> {
  const bundle = buildEvidenceBundle({
    classifications: input.classifications,
    derived: input.derived,
  });
  const allFacts = [...bundle.primaryFacts, ...bundle.supportingFacts];
  if (allFacts.length === 0) return null;

  const causalGuards = buildCausalGuards({
    registrationGates: input.derived.registration?.gates ?? null,
    housingGates: input.derived.housingEligibility?.gates ?? null,
    disbursementGates: input.derived.aidDisbursements?.gates ?? null,
  });
  const facts = [
    ...bundle.primaryFacts.map((fact) => ({
      ...fact,
      relevance: "primary" as const,
    })),
    ...bundle.supportingFacts.map((fact) => ({
      ...fact,
      relevance: "supporting" as const,
    })),
  ];

  const attempt = async (correction: string | null) => {
    let output: { answer?: unknown } | null = null;
    try {
      output = (await input.model.writeGroundedAnswer?.({
        normalizedMessage: correction
          ? `${input.normalized.resolvedText}\n\n${correction}`
          : input.normalized.resolvedText,
        requestTypes: input.classifications.map((item) => item.requestType),
        conversationContext: input.normalized.history.map((message) => ({
          ...message,
          trust: "untrusted_conversation_text" as const,
        })),
        facts,
        unavailable: bundle.unavailable,
      })) as { answer?: unknown } | null;
    } catch {
      return { failed: true as const };
    }
    if (!output || typeof output.answer !== "string") {
      return { invalid: true as const };
    }
    return {
      guard: guardGroundedAnswer({
        answer: output.answer,
        evidenceTexts: allFacts.map((fact) => fact.text),
        causalGuards,
        documentStates: input.derived.documentStates,
      }),
    };
  };

  const first = await attempt(null);
  if ("failed" in first) {
    return { answer: null, rejectedReason: "model_error", modelFailed: true };
  }
  if ("invalid" in first) {
    return { answer: null, rejectedReason: "invalid_model_result", modelFailed: false };
  }
  if (first.guard.accepted) {
    return { answer: first.guard.answer, rejectedReason: null, modelFailed: false };
  }

  // An invented cause is the one rejection worth a second attempt. The answer
  // was otherwise grounded, the model simply asserted a link the gate data does
  // not support, and it can be told exactly which link that was. Falling
  // straight through to the deterministic message instead traded one wrong
  // sentence for a paragraph of raw state -- the guard was doing its job and the
  // student got a worse reply for it.
  // Both of these are the model asserting something the record contradicts
  // rather than fabricating a value, and both are correctable by saying which
  // claim was wrong.
  const correctable =
    first.guard.reasonCode === "invented_causation" ||
    first.guard.reasonCode === "contradicted_document_state";
  if (!correctable) {
    return {
      answer: null,
      rejectedReason: first.guard.reasonCode,
      modelFailed: false,
    };
  }
  const second = await attempt(
    first.guard.reasonCode === "invented_causation"
      ? "Correction: your previous reply named a cause the record does not support. Only a fact that explicitly says something is blocking may be given as the reason for it. State what is true without claiming one thing causes another unless a fact says so."
      : "Correction: your previous reply said a document had not been submitted when the facts say it is on file. Read each document fact again and describe the state it actually gives -- uploaded, under review, accepted, rejected, or needing resubmission -- and never tell the student they have not sent something the record shows they sent.",
  );
  if ("failed" in second || "invalid" in second) {
    return {
      answer: null,
      rejectedReason: first.guard.reasonCode,
      modelFailed: "failed" in second,
    };
  }
  return second.guard.accepted
    ? { answer: second.guard.answer, rejectedReason: null, modelFailed: false }
    : {
        answer: null,
        rejectedReason: `${first.guard.reasonCode}_retried:${second.guard.reasonCode}`,
        modelFailed: false,
      };
}

function deduplicateFacts(
  facts: GroundedFactForComposition[],
): GroundedFactForComposition[] {
  const seen = new Set<string>();
  return facts.filter((fact) => {
    if (seen.has(fact.id)) return false;
    seen.add(fact.id);
    return true;
  });
}

export function validateGroundingNode(input: {
  draft: GroundedCompositionDraft;
  receipts: readonly StudentAssistantContextReceipt[];
}): ValidatedGroundedMessage {
  const receiptIds = new Set(
    input.receipts
      .filter((receipt) => receipt.status === "available")
      .map((receipt) => receipt.id),
  );
  const supportedFacts = input.draft.facts.filter(
    (fact) =>
      fact.contextReceiptIds.length > 0 &&
      fact.contextReceiptIds.every((receiptId) => receiptIds.has(receiptId)),
  );
  const factsById = new Map(supportedFacts.map((fact) => [fact.id, fact]));

  // A written answer has already passed the deterministic claim guard against
  // the same evidence, but it is only usable if at least one read succeeded.
  if (input.draft.proseAnswer && receiptIds.size > 0) {
    return {
      message: input.draft.proseAnswer,
      usedDeterministicFallback: false,
      removedUnsupportedClaims: 0,
    };
  }

  if (input.draft.selectedFactIds) {
    const selected = input.draft.selectedFactIds
      .map((id) => factsById.get(id))
      .filter((fact): fact is GroundedFactForComposition => fact !== undefined);
    const removedUnsupportedClaims =
      input.draft.selectedFactIds.length - selected.length;
    if (selected.length > 0) {
      return {
        message: selected.map((fact) => fact.text).join(" "),
        usedDeterministicFallback: false,
        removedUnsupportedClaims,
      };
    }
    return {
      message: input.draft.fallbackMessage,
      usedDeterministicFallback: true,
      removedUnsupportedClaims,
    };
  }

  return {
    message: input.draft.fallbackMessage,
    usedDeterministicFallback:
      input.draft.modelAttempted ||
      input.draft.modelFailed ||
      input.draft.invalidModelResult,
    removedUnsupportedClaims: 0,
  };
}

export function buildGroundedFacts(
  classification: RequestClassification,
  derived: DerivedStudentState,
): GroundedFactForComposition[] {
  const requestType = classification.requestType;
  switch (requestType) {
    case "remaining_steps":
      return [
        ...derived.remainingSteps.map((step) => ({
          id: `remaining:${step.code}`,
          text: `${step.title} ${requirementPhrase(step.status)}.`,
          contextReceiptIds: step.contextReceiptIds,
        })),
        // Reported, but explicitly not as something to do. Leaving these out
        // entirely made a submitted document look forgotten; listing them as
        // remaining made it look outstanding. Both are wrong.
        ...derived.awaitingReviewSteps.map((step) => ({
          id: `awaiting:${step.code}`,
          text: `${step.title} is submitted and waiting on review -- no action needed from you.`,
          contextReceiptIds: step.contextReceiptIds,
        })),
      ];
    case "completed_steps":
      return derived.completedSteps.map((step) => ({
        id: `completed:${step.code}`,
        text: `${step.title} is complete.`,
        contextReceiptIds: step.contextReceiptIds,
      }));
    case "greeting":
    case "capability_overview":
      // Answered in full by deterministicMessage; no record is cited, so there
      // is nothing to ground.
      return [];
    case "general_help": {
      const action = derived.prioritizedAction;
      const outstanding = derived.remainingSteps.slice(0, 4);
      return [
        ...(action
          ? [
              {
                id: `help-next:${action.id}`,
                text: prioritizedActionText(
                  action,
                  derived.priorityEvidence,
                  false,
                ),
                contextReceiptIds: action.contextReceiptIds,
              },
            ]
          : []),
        ...outstanding.map((step) => ({
          id: `help-outstanding:${step.code}`,
          text: `${step.title} ${requirementPhrase(step.status)}.`,
          contextReceiptIds: step.contextReceiptIds,
        })),
      ];
    }
    case "aid_summary": {
      const summary = derived.aidSummary;
      if (!summary) return [];
      const receipts = derived.capabilityReceiptIds.getFinancialAidSummary ?? [];
      const accepted = summary.awards.filter((award) => award.status === "accepted");
      return [
        ...(summary.awards.length > 0
          ? [
              {
                id: "aid-summary:total",
                text: `You have ${summary.awards.length} financial aid award${summary.awards.length === 1 ? "" : "s"} for ${summary.aidYear ?? "this aid year"}, ${formatUsd(summary.totals.acceptedUsd)} of it accepted out of ${formatUsd(summary.totals.offeredUsd)} offered.`,
                contextReceiptIds: receipts,
              },
            ]
          : [
              {
                id: "aid-summary:none",
                text: "No financial aid awards are on your record for this aid year.",
                contextReceiptIds: receipts,
              },
            ]),
        ...accepted.map((award) => ({
          id: `aid-award:${award.id}`,
          text: `${award.name}: ${formatUsd(award.acceptedUsd)} accepted.`,
          contextReceiptIds: receipts,
        })),
        ...summary.awards
          .filter((award) => award.requiresAction)
          .map((award) => ({
            id: `aid-award-action:${award.id}`,
            text: `${award.name} is ${award.status} and still needs you to accept or decline it.`,
            contextReceiptIds: receipts,
          })),
        ...(summary.packageState === "estimated"
          ? [
              {
                id: "aid-summary:estimated",
                text: "These amounts are estimated and can change until your aid is finalized.",
                contextReceiptIds: receipts,
              },
            ]
          : []),
      ];
    }
    case "aid_application_status": {
      const summary = derived.aidSummary;
      if (!summary) return [];
      const receipts = derived.capabilityReceiptIds.getFinancialAidSummary ?? [];
      return [
        {
          id: "aid-application:fafsa",
          text: fafsaSentence(summary.fafsaStatus, summary.fafsaReceivedAt, summary.aidYear),
          contextReceiptIds: receipts,
        },
        ...summary.gates
          .filter((gate) => !gate.satisfied)
          .map((gate) => ({
            id: `aid-application-gate:${gate.code}`,
            text: gate.reason,
            contextReceiptIds: receipts,
          })),
      ];
    }
    case "aid_disbursement": {
      const disbursements = derived.aidDisbursements;
      if (!disbursements) return [];
      const receipts = derived.capabilityReceiptIds.getAidDisbursements ?? [];
      const held = disbursements.items.filter((item) => item.state === "held");
      const blocking = disbursements.gates.filter((gate) => !gate.satisfied);
      return [
        {
          id: "aid-disbursement:summary",
          text:
            disbursements.totalDisbursedUsd > 0
              ? `${formatUsd(disbursements.totalDisbursedUsd)} of your aid has disbursed to your student account.`
              : disbursements.items.length === 0
                ? "No aid disbursements are scheduled on your record yet."
                : `None of your aid has disbursed yet; ${formatUsd(disbursements.totalScheduledUsd)} is scheduled.`,
          contextReceiptIds: receipts,
        },
        ...(disbursements.nextScheduledFor
          ? [
              {
                id: "aid-disbursement:next",
                text: `The next scheduled disbursement is ${formatDay(disbursements.nextScheduledFor)}.`,
                contextReceiptIds: receipts,
              },
            ]
          : []),
        // The exhaustive framing matters: without it a model fills the gap with
        // a plausible cause of its own.
        ...(blocking.length > 0
          ? [
              {
                id: "aid-disbursement:gates",
                text: `Disbursement is held for ${blocking.length === 1 ? "one reason" : `${blocking.length} reasons`}, and these are all of them: ${blocking.map((gate) => gate.reason).join(" ")}`,
                contextReceiptIds: receipts,
              },
            ]
          : []),
        // Grouped by award. An annual award splits across terms, and listing
        // each instalment separately repeated every award name twice.
        ...[...new Set(held.map((item) => item.awardName))].map((awardName) => {
          const total = held
            .filter((item) => item.awardName === awardName)
            .reduce((sum, item) => sum + item.amountUsd, 0);
          return {
            id: `aid-disbursement-held:${awardName}`,
            text: `${awardName} (${formatUsd(total)}) is on hold.`,
            contextReceiptIds: receipts,
          };
        }),
      ];
    }
    case "aid_coverage": {
      const summary = derived.aidSummary;
      if (!summary) return [];
      const receipts = derived.capabilityReceiptIds.getFinancialAidSummary ?? [];
      const coverage = summary.coverage;
      return [
        {
          id: "aid-coverage:total",
          text: `Your aid applies ${formatUsd(coverage.aidAppliedUsd)} against a cost of attendance of ${formatUsd(coverage.costOfAttendanceUsd)}.`,
          contextReceiptIds: receipts,
        },
        {
          id: "aid-coverage:balance",
          text:
            coverage.estimatedRefundUsd > 0
              ? `Your aid exceeds your charges, so an estimated refund of ${formatUsd(coverage.estimatedRefundUsd)} would be returned to you.`
              : coverage.remainingBalanceUsd > 0
                ? `That leaves ${formatUsd(coverage.remainingBalanceUsd)} for you to cover.`
                : "That covers your charges in full, so nothing is left for you to pay.",
          contextReceiptIds: receipts,
        },
        ...(coverage.includesEstimatedAid
          ? [
              {
                id: "aid-coverage:estimated",
                text: "Some of this aid is still estimated, so the amount can change until it is finalized.",
                contextReceiptIds: receipts,
              },
            ]
          : []),
      ];
    }
    case "next_action":
      return derived.prioritizedAction
        ? [
            {
              id: `next:${derived.prioritizedAction.id}`,
              text: prioritizedActionText(
                derived.prioritizedAction,
                derived.priorityEvidence,
                classification.priorityExplanationRequested,
              ),
              contextReceiptIds: derived.prioritizedAction.contextReceiptIds,
            },
          ]
        : [];
    case "missing_documents":
      // Only the documents the student still has to act on. A document sitting
      // with a reviewer is reported by "document_status", not as missing --
      // conflating the two is what told students to re-upload files that had
      // already arrived.
      return derived.documentStates
        .filter((document) => isActionRequiredSubmissionState(document.submissionState))
        .map((document) => ({
          id: `document:${document.requirementCode}`,
          text: submissionStateSentence(document.submissionState, document.title),
          contextReceiptIds: document.contextReceiptIds,
        }));
    case "document_status": {
      const requested = classification.requestedEntity;
      const matching = derived.documentStates.filter(
        (document) =>
          !requested || document.requirementCode === requested,
      );
      const scoped = matching.length > 0 ? matching : derived.documentStates;
      return scoped.map((document) => ({
        id: `document-state:${document.requirementCode}`,
        text: submissionStateSentence(document.submissionState, document.title),
        contextReceiptIds: document.contextReceiptIds,
      }));
    }
    case "onboarding_status": {
      const steps = [
        ...derived.completedSteps,
        ...derived.remainingSteps,
      ];
      const receiptIds = [...new Set(steps.flatMap((step) => step.contextReceiptIds))];
      return receiptIds.length > 0
        ? [
            {
              id: "status:summary",
              text: `${derived.completedSteps.length} onboarding step${derived.completedSteps.length === 1 ? " is" : "s are"} complete and ${derived.remainingSteps.length} remain.`,
              contextReceiptIds: receiptIds,
            },
          ]
        : [];
    }
    case "holds_and_blockers":
      if (classification.blockerScope === "registration_ambiguous") return [];
      if (
        classification.blockerScope === "official_holds" &&
        derived.officialHolds.length === 0 &&
        derived.holdReceiptId
      ) {
        return [
          {
            id: "official-holds:none",
            text: "You do not currently have any official enrollment holds.",
            contextReceiptIds: [derived.holdReceiptId],
          },
        ];
      }
      return [
        ...(derived.registrationEligibility
          ? [
              {
                id: "registration-eligibility:unknown",
                text: derived.registrationEligibility.studentSafeReason,
                contextReceiptIds:
                  derived.registrationEligibility.contextReceiptIds,
              },
            ]
          : []),
        ...derived.officialHolds.map((hold) => ({
          id: `official-hold:${hold.id}`,
          text:
            "An official enrollment hold is currently reported, but the authoritative source does not provide its reason or removal steps.",
          contextReceiptIds: hold.contextReceiptIds,
        })),
        ...derived.derivedBlockers.map((blocker) => ({
          id: `derived-blocker:${blocker.type}:${blocker.id}`,
          text:
            blocker.studentSafeReason ??
            `${blocker.label ?? "A requirement"} is currently blocked, but the current source does not provide the reason.`,
          contextReceiptIds: blocker.contextReceiptIds,
        })),
        ...derived.nonBlockingActions.map((action) => ({
          id: `non-blocking-action:${action.type}:${action.id}`,
          text:
            action.studentSafeReason ??
            `${action.label ?? "An item"} needs attention but is not reported as an official hold.`,
          contextReceiptIds: action.contextReceiptIds,
        })),
      ];
    case "deadlines":
      return derived.deadlines.map((deadline) => ({
        id: `deadline:${deadline.kind}:${deadline.id}`,
        text: deadlineFactText(deadline),
        contextReceiptIds: deadline.contextReceiptIds,
      }));
    case "explain_requirement": {
      const requirement = resolveExplainedRequirement(derived);
      const facts: GroundedFactForComposition[] = requirement
        ? [
            {
              id: `requirement:${requirement.code}`,
              text: `${requirement.title}: ${requirement.description}`,
              contextReceiptIds: requirement.contextReceiptIds,
            },
          ]
        : [];
      if (derived.policy && derived.policyReceiptId) {
        facts.push({
          id: `policy:${derived.policy.requirementCode}`,
          text: boundedPolicyText(derived.policy.text),
          contextReceiptIds: [derived.policyReceiptId],
        });
      }
      return facts;
    }
    case "request_support":
      return derived.supportOptions.flatMap((option, index) => {
        if (option.kind === "article") {
          return [
            {
              id: `support:article:${index}`,
              text: `${option.article.question}: ${option.article.answer}`,
              contextReceiptIds: option.contextReceiptIds,
            },
          ];
        }
        return [
          {
            id: `support:${option.kind}`,
            text: `${option.label}: ${option.value}.`,
            contextReceiptIds: option.contextReceiptIds,
          },
        ];
      });
    case "aid_status":
    case "aid_incomplete_reason": {
      const aid = derived.financialAid;
      if (!aid) return [];
      if (aid.contextReceiptIds.length === 0) return [];
      return [
        {
          id: "aid:status",
          text: `Your current financial-aid status is ${aid.status}; ${aid.completedRequirements.length} requirement${aid.completedRequirements.length === 1 ? " is" : "s are"} complete and ${aid.remainingRequirements.length} remain.`,
          contextReceiptIds: aid.contextReceiptIds,
        },
        // Named individually, and declared exhaustive, so the reason for
        // incompleteness cannot be filled in from an unrelated domain.
        ...aid.remainingRequirements.slice(0, 6).map((item) => ({
          id: `aid:outstanding:${item.code}`,
          text: `${item.label} is outstanding on your financial-aid record.`,
          contextReceiptIds: aid.contextReceiptIds,
        })),
        {
          id: "aid:reason-exhaustive",
          text: `Those ${aid.remainingRequirements.length} outstanding financial-aid requirement(s) are the complete reason the aid record is ${aid.status}. Nothing outside the financial-aid record, including the student account balance, makes financial aid incomplete.`,
          contextReceiptIds: aid.contextReceiptIds,
        },
      ];
    }
    case "aid_remaining_steps": {
      const aid = derived.financialAid;
      if (!aid) return [];
      return aid.remainingRequirements
        .filter((item) =>
          financialAidRequirementMatchesEntity(
            item,
            classification.financialAidEntity,
          ),
        )
        .map((item) => ({
          id: `aid:remaining:${item.code}`,
          text: `${item.label} ${aidRequirementPhrase(item.status)}.`,
          contextReceiptIds: aid.contextReceiptIds,
        }));
    }
    case "aid_missing_documents":
      return (derived.financialAid?.missingDocuments ?? []).map((item) => ({
        id: `aid:document:${item.requirementCode}`,
        text: aidDocumentSentence(item.label, item.authoritativeDocumentState),
        contextReceiptIds: item.contextReceiptIds,
      }));
    case "aid_verification_status": {
      const aid = derived.financialAid;
      return aid
        ? [
            {
              id: "aid:verification-status",
              text: aidVerificationSentence(aid.verificationStatus),
              contextReceiptIds: aid.contextReceiptIds,
            },
          ]
        : [];
    }
    case "aid_deadlines":
      return (derived.financialAid?.deadlines ?? []).map((deadline) => ({
        id: `aid:deadline:${deadline.id}`,
        text: deadlineFactText(deadline),
        contextReceiptIds: deadline.contextReceiptIds,
      }));
    case "aid_award_acceptance_status":
      return (derived.financialAid?.awardAcceptanceStatuses ?? []).map(
        (award) => ({
          id: `aid:award:${award.awardId}`,
          text: `${award.awardLabel} is ${award.status}.`,
          contextReceiptIds: derived.financialAid?.contextReceiptIds ?? [],
        }),
      );
    case "aid_requirement_explanation": {
      const aid = derived.financialAid;
      if (!aid) return [];
      const requirement = aid.remainingRequirements
        .concat(aid.completedRequirements)
        .find((item) =>
          financialAidRequirementMatchesEntity(
            item,
            classification.financialAidEntity,
          ),
        );
      const facts: GroundedFactForComposition[] = requirement
        ? [
            {
              id: `aid:requirement:${requirement.code}`,
              text: `Student status: ${requirement.label} is ${requirement.status.replaceAll("_", " ")}.`,
              contextReceiptIds: aid.contextReceiptIds,
            },
          ]
        : [];
      if (aid.policyExplanation) {
        facts.push({
          id: `aid:policy:${aid.policyExplanation.requirementCode}`,
          text: `Approved policy explanation: ${boundedPolicyText(aid.policyExplanation.studentVisibleText)}`,
          contextReceiptIds: aid.policyContextReceiptId
            ? [aid.policyContextReceiptId]
            : [],
        });
      }
      if (aid.nextAction) {
        facts.push({
          id: `aid:next:${aid.nextAction.id}`,
          text: `Recommended next action: ${aid.nextAction.label}.`,
          contextReceiptIds: aid.nextAction.contextReceiptIds,
        });
      }
      return facts;
    }
    case "aid_next_action": {
      const action = derived.financialAid?.nextAction;
      return action
        ? [
            {
              id: `aid:next:${action.id}`,
              text: `Your next financial-aid action is ${action.label}.`,
              contextReceiptIds: action.contextReceiptIds,
            },
          ]
        : [];
    }
    case "aid_support":
      return (derived.financialAid?.supportOptions ?? []).map((option, index) => ({
        id: `aid:support:${option.kind}:${index}`,
        text:
          option.kind === "route"
            ? `${option.label}: ${option.href}.`
            : `${option.label}: ${option.value}.`,
        contextReceiptIds: derived.financialAid?.contextReceiptIds ?? [],
      }));
    case "housing_status": {
      const housing = derived.housing;
      if (!housing) return [];
      const receiptIds = housing.contextReceiptIds;
      if (housing.planStatus === "unknown" && receiptIds.length === 0) return [];
      const unavailableByEntity: Partial<Record<NonNullable<RequestClassification["housingEntity"]>, string>> = {
        housing_application: "A formal housing application status is not available in the current records.",
        housing_assignment: "A housing or room assignment cannot be verified from the current records.",
        housing_waitlist: "Housing waitlist membership or position cannot be verified from the current records.",
        housing_agreement: "A housing agreement or signature status cannot be verified from the current records.",
        housing_deposit: "A housing-deposit status cannot be verified; the enrollment deposit is a different record.",
        meal_plan: "Actual meal-plan selection or enrollment cannot be verified from the current records.",
        housing_accommodation: "Accommodation status is not available to Edward. Use the approved housing requirement route or general support without sharing health details here.",
      };
      const unavailable = classification.housingEntity
        ? unavailableByEntity[classification.housingEntity]
        : null;
      if (unavailable) {
        return [{ id: `housing:unavailable:${classification.housingEntity}`, text: unavailable, contextReceiptIds: receiptIds }];
      }
      if (classification.housingEntity === "roommate_preferences") {
        return [{
          id: "housing:roommate-preference-state",
          text: `${roommatePreferencePhrase(housing.roommatePreferenceState)} No roommate names, contact details, or personal preferences are available to Edward.`,
          contextReceiptIds: receiptIds,
        }];
      }
      if (classification.housingEntity === "housing_residence_preference") {
        return [{
          id: "housing:residence-preference",
          text: housing.residencePreference
            ? `Your selected residence preference is ${housing.residencePreference.replaceAll("_", " ")}; this is not a housing assignment.`
            : "No residence preference is selected; this does not establish assignment status.",
          contextReceiptIds: receiptIds,
        }];
      }
      return [
        {
          id: "housing:plan-status",
          text: housing.housingOptionType
            ? `Your housing plan preference is ${housing.housingOptionType.replaceAll("_", " ")}, and ${housingRequirementPhrase(housing.planRequirementState)}`
            : `No housing plan preference is selected, and ${housingRequirementPhrase(housing.planRequirementState)}`,
          contextReceiptIds: receiptIds,
        },
        ...(housing.residencePreference
          ? [{
              id: "housing:residence-preference",
              text: `Your residence preference is ${housing.residencePreference.replaceAll("_", " ")}; it is not an assignment.`,
              contextReceiptIds: receiptIds,
            }]
          : []),
      ];
    }
    case "housing_options":
      return (derived.housing?.options ?? []).map((option) => ({
        id: `housing:option:${option.code}`,
        text: `${option.name} is a ${option.synthetic ? "synthetic demo " : "tenant "}listed residence preference option: ${option.description} This listing does not confirm vacancy, eligibility, price, or assignment.`,
        contextReceiptIds: derived.housing?.contextReceiptIds ?? [],
      }));
    case "housing_remaining_steps":
      return (derived.housing?.remainingSteps ?? []).map((step) => ({
        id: `housing:remaining:${step.code}`,
        text: `${step.label}${step.blocked ? " is currently blocked." : "."}`,
        contextReceiptIds: step.contextReceiptIds,
      }));
    case "housing_deadlines":
      return (derived.housing?.deadlines ?? []).map((deadline) => ({
        id: `housing:deadline:${deadline.id}`,
        text: deadlineFactText(deadline),
        contextReceiptIds: deadline.contextReceiptIds,
      }));
    case "housing_next_action": {
      const action = derived.housing?.nextAction;
      return action
        ? [{
            id: `housing:next:${action.reasonCode}`,
            text: `Your next housing action is: ${action.label}.`,
            contextReceiptIds: action.contextReceiptIds,
          }]
        : [];
    }
    case "housing_support":
      return (derived.housing?.supportOptions ?? []).map((option, index) => ({
        id: `housing:support:${option.kind}:${index}`,
        text: option.kind === "route"
          ? `${option.label}: ${option.href}.`
          : `${option.label}: ${option.value}.`,
        contextReceiptIds: derived.housing?.contextReceiptIds ?? [],
      }));
    case "housing_eligibility":
      return housingEligibilityFacts(derived);
    case "registration_status":
      return registrationFacts(derived);
    case "student_account":
      return accountFacts(derived);
    case "academic_calendar":
      return calendarFacts(derived);
    case "appointments":
      return appointmentFacts(derived);
    case "policy_lookup":
      return policyFacts(derived);
    case "unsupported_or_out_of_scope":
      return [];
  }
}

/* -------------------------------------------------------------------------
 * Facts for the broader university capabilities.
 *
 * Each builder states the answer *and* the reason, because "you cannot
 * register" without the gate that is closed is not an answer a student can act
 * on. Gate reasons come from the read, never from prompt text.
 * ----------------------------------------------------------------------- */

function receiptsFor(
  derived: DerivedStudentState,
  tool: string,
): string[] {
  return derived.capabilityReceiptIds[tool] ?? [];
}

function gateFacts(
  gates: readonly EligibilityGate[],
  receiptIds: string[],
  prefix: string,
): GroundedFactForComposition[] {
  return gates.map((gate) => ({
    id: `${prefix}:gate:${gate.code}`,
    text: gate.satisfied
      ? `${gate.label} is satisfied, so it is not blocking: ${gate.reason}`
      : `${gate.label} is not satisfied and is blocking: ${gate.reason}${gate.resolutionOwner ? ` This is handled by ${gate.resolutionOwner}.` : ""}`,
    contextReceiptIds: receiptIds,
  }));
}

function windowText(label: string, window: OpenWindow): string | null {
  if (window.open === true) return `${label} is open now.`;
  if (window.opensAt && window.open === false) {
    return `${label} is not open yet; it opens ${window.opensAt.slice(0, 10)}.`;
  }
  if (window.closesAt && window.open === false) {
    return `${label} closed on ${window.closesAt.slice(0, 10)}.`;
  }
  return null;
}

function housingEligibilityFacts(
  derived: DerivedStudentState,
): GroundedFactForComposition[] {
  const eligibility = derived.housingEligibility;
  if (!eligibility) return [];
  const receiptIds = receiptsFor(derived, "getStudentHousingEligibility");
  if (receiptIds.length === 0) return [];
  const facts: GroundedFactForComposition[] = [
    {
      id: "housing-eligibility:state",
      text:
        eligibility.state === "eligible"
          ? "You are currently eligible to apply for housing."
          : eligibility.state === "not_yet_open"
            ? "Housing applications are not open to you yet."
            : eligibility.state === "closed"
              ? "The housing application period has closed."
              : eligibility.state === "blocked"
                ? "You cannot apply for housing yet because at least one requirement is not satisfied."
                : "Housing eligibility could not be determined from the current records.",
      contextReceiptIds: receiptIds,
    },
  ];
  const window = windowText("The housing application window", eligibility.applicationWindow);
  if (window) {
    facts.push({
      id: "housing-eligibility:window",
      text: window,
      contextReceiptIds: receiptIds,
    });
  }
  facts.push(...gateFacts(eligibility.gates, receiptIds, "housing-eligibility"));
  facts.push({
    id: "housing-eligibility:gates-exhaustive",
    text: `The ${eligibility.gates.length} item(s) listed above are the complete set of conditions on applying for housing. Nothing else blocks the housing application.`,
    contextReceiptIds: receiptIds,
  });
  facts.push({
    id: "housing-eligibility:assignment",
    text:
      eligibility.assignment.state === "assigned"
        ? `You have a room assignment: ${eligibility.assignment.residenceName ?? "a residence"}${eligibility.assignment.roomLabel ? `, room ${eligibility.assignment.roomLabel}` : ""}${eligibility.assignment.moveInAt ? `, with move-in from ${eligibility.assignment.moveInAt.slice(0, 10)}` : ""}.`
        : eligibility.assignment.state === "waitlisted"
          ? "You are on the housing waitlist and do not have a room assignment."
          : eligibility.assignment.state === "not_assigned"
            ? "You do not have a room assignment yet."
            : "A room assignment could not be verified from the current records.",
    contextReceiptIds: receiptIds,
  });
  return facts;
}

function registrationFacts(
  derived: DerivedStudentState,
): GroundedFactForComposition[] {
  const registration = derived.registration;
  if (!registration) return [];
  const receiptIds = receiptsFor(derived, "getRegistrationStatus");
  if (receiptIds.length === 0) return [];
  const facts: GroundedFactForComposition[] = [
    {
      id: "registration:state",
      text:
        registration.state === "eligible"
          ? `You are eligible to register for ${registration.termName}.`
          : registration.state === "not_yet_open"
            ? `Registration for ${registration.termName} has not opened for you yet.`
            : registration.state === "closed"
              ? `Registration for ${registration.termName} has closed.`
              : registration.state === "blocked"
                ? `You cannot register for ${registration.termName} yet because at least one requirement is not satisfied.`
                : `Registration eligibility for ${registration.termName} could not be determined.`,
      contextReceiptIds: receiptIds,
    },
  ];
  const window = windowText("Your registration window", registration.registrationWindow);
  if (window) {
    facts.push({
      id: "registration:window",
      text: window,
      contextReceiptIds: receiptIds,
    });
  }
  facts.push(...gateFacts(registration.gates, receiptIds, "registration"));
  facts.push({
    id: "registration:gates-exhaustive",
    text: `The ${registration.gates.length} item(s) listed above are the complete set of gates on registration for ${registration.termName}. Nothing else blocks registration, including anything outstanding elsewhere on the checklist or in financial aid.`,
    contextReceiptIds: receiptIds,
  });
  facts.push({
    id: "registration:enrolled",
    text: `You are registered for ${registration.registeredCourseCount} course(s) totalling ${registration.registeredCreditCount} credits for ${registration.termName}.`,
    contextReceiptIds: receiptIds,
  });
  if (registration.advisingRequired) {
    facts.push({
      id: "registration:advising",
      text:
        registration.advisingHoldCleared === true
          ? "Advising is required before registration and your advising requirement has been cleared."
          : "Advising is required before you can register, and it has not been cleared yet.",
      contextReceiptIds: receiptIds,
    });
  }
  return facts;
}

function accountFacts(
  derived: DerivedStudentState,
): GroundedFactForComposition[] {
  const account = derived.account;
  if (!account) return [];
  const receiptIds = receiptsFor(derived, "getStudentAccountSummary");
  if (receiptIds.length === 0) return [];
  const facts: GroundedFactForComposition[] = [
    {
      id: "account:balance",
      text: `Your student account balance is $${account.balanceUsd.toFixed(2)}${account.pastDueUsd > 0 ? `, of which $${account.pastDueUsd.toFixed(2)} is past due` : ", with nothing past due"}.`,
      contextReceiptIds: receiptIds,
    },
  ];
  for (const charge of account.charges.slice(0, 8)) {
    facts.push({
      id: `account:charge:${charge.code}`,
      text: `${charge.label} is $${charge.amountUsd.toFixed(2)} and is ${charge.state === "outstanding" ? "still outstanding" : charge.state}${charge.dueAt ? `, due ${charge.dueAt.slice(0, 10)}` : ""}.`,
      contextReceiptIds: receiptIds,
    });
  }
  for (const payment of account.payments.slice(0, 8)) {
    facts.push({
      id: `account:payment:${payment.id}`,
      // "posted" versus "pending" is the distinction that decides whether other
      // systems can rely on a payment, so it is stated explicitly.
      text:
        payment.state === "posted"
          ? `A payment of $${payment.amountUsd.toFixed(2)} for ${payment.label} has posted to your account${payment.postedAt ? ` on ${payment.postedAt.slice(0, 10)}` : ""}.`
          : payment.state === "pending"
            ? `A payment of $${payment.amountUsd.toFixed(2)} for ${payment.label} is still pending and has not posted yet, so systems that check for a posted payment will not see it.`
            : `A payment of $${payment.amountUsd.toFixed(2)} for ${payment.label} failed.`,
      contextReceiptIds: receiptIds,
    });
  }
  facts.push({
    id: "account:blocks-registration",
    text: account.blocksRegistration
      ? "Your outstanding account balance is currently blocking course registration."
      : "Your account balance is not blocking course registration.",
    contextReceiptIds: receiptIds,
  });
  if (account.nextPaymentDueAt) {
    facts.push({
      id: "account:next-due",
      text: `Your next payment is due ${account.nextPaymentDueAt.slice(0, 10)}.`,
      contextReceiptIds: receiptIds,
    });
  }
  return facts;
}

function calendarFacts(
  derived: DerivedStudentState,
): GroundedFactForComposition[] {
  const calendar = derived.calendar;
  if (!calendar) return [];
  const receiptIds = receiptsFor(derived, "getAcademicCalendar");
  if (receiptIds.length === 0) return [];
  return calendar.events.slice(0, 14).map((event) => ({
    id: `calendar:${event.code}`,
    text: `${event.label} ${event.endsAt ? `runs ${event.startsAt.slice(0, 10)} to ${event.endsAt.slice(0, 10)}` : `is on ${event.startsAt.slice(0, 10)}`} (${calendar.termName}). ${event.description}`,
    contextReceiptIds: receiptIds,
  }));
}

function appointmentFacts(
  derived: DerivedStudentState,
): GroundedFactForComposition[] {
  const appointments = derived.appointments;
  if (!appointments) return [];
  const receiptIds = receiptsFor(derived, "getStudentAppointments");
  if (receiptIds.length === 0) return [];
  const facts: GroundedFactForComposition[] = [];
  const upcoming = appointments.scheduled.filter(
    (item) => item.state === "scheduled",
  );
  if (upcoming.length === 0) {
    facts.push({
      id: "appointments:none",
      text: "You have no scheduled appointments on record.",
      contextReceiptIds: receiptIds,
    });
  }
  for (const appointment of upcoming.slice(0, 6)) {
    facts.push({
      id: `appointments:${appointment.id}`,
      text: `${appointment.label} is scheduled for ${appointment.startsAt.slice(0, 10)}${appointment.withWhom ? ` with ${appointment.withWhom}` : ""}${appointment.location ? ` at ${appointment.location}` : ""}.`,
      contextReceiptIds: receiptIds,
    });
  }
  for (const route of appointments.bookingRoutes.slice(0, 6)) {
    facts.push({
      id: `appointments:booking:${route.kind}`,
      text: `${route.label} can be booked by the student at ${route.href}.`,
      contextReceiptIds: receiptIds,
    });
  }
  return facts;
}

function policyFacts(
  derived: DerivedStudentState,
): GroundedFactForComposition[] {
  const matches = derived.policyMatches;
  if (!matches) return [];
  const receiptIds = receiptsFor(derived, "searchApprovedPolicies");
  if (receiptIds.length === 0) return [];
  if (matches.matches.length === 0) {
    return [
      {
        id: "policy:none",
        text: "No approved policy covering that topic is available to Edward.",
        contextReceiptIds: receiptIds,
      },
    ];
  }
  return matches.matches.slice(0, 4).map((policy) => ({
    id: `policy:${policy.code}`,
    text: `${policy.title} (applies to ${policy.appliesTo}, owned by ${policy.sourceOwner}): ${boundedPolicyText(policy.studentVisibleText)}`,
    contextReceiptIds: receiptIds,
  }));
}

function deterministicMessage(
  classification: RequestClassification,
  derived: DerivedStudentState,
  facts: readonly GroundedFactForComposition[],
): string {
  const requestType = classification.requestType;
  const gateSummary = gateFallbackMessage(requestType, derived);
  if (gateSummary) return gateSummary;
  if (requestType === "greeting") {
    const name = derived.profile?.preferredName || derived.profile?.firstName;
    return `Hi${name ? ` ${name}` : ""}! I'm Edward. I can help with your enrollment, documents, financial aid, housing, registration, deadlines, and other university questions. What can I help you with?`;
  }
  if (requestType === "capability_overview") {
    return `I'm Edward, your university's enrollment assistant. I answer questions from your own student record, so I can tell you where things actually stand rather than what usually happens. I can help with ${listSentence(capabilityAreas)}. I can look things up and explain them, but I can't change your record for you. What would you like to know?`;
  }
  if (classification.blockerScope === "registration_ambiguous") {
    return "Do you mean course registration or orientation registration?";
  }
  if (classification.requirementReference === "eligibility_unavailable") {
    return "I can report your current financial-aid records, but I can’t determine eligibility. Use the Financial Aid appointment route for an authoritative answer.";
  }
  if (classification.requirementReference === "award_amount_unavailable") {
    return "I can report whether an award is offered or accepted, but I can’t calculate or promise an award amount. Review Financials or contact Financial Aid.";
  }
  if (classification.requirementReference === "other_person_contact_details") {
    return "I can't share another student's phone number, email, or address — I only have access to your own record. If you need to reach a roommate, Housing & Residence Life can pass on a message or tell you what they are able to share.";
  }
  if (classification.requirementReference === "sensitive_financial_data") {
    return "Please don't share Social Security, tax-return, bank-account, card, or FAFSA contents here. I can't review those details in chat. Use the secure Documents area or contact Financial Aid through the appointment route.";
  }
  if (
    classification.requirementReference === "financial_aid_write_unavailable"
  ) {
    return "I can read your current financial-aid status, but I can't approve documents, accept or decline awards, upload files, or change financial-aid records. Use Financials, secure Documents, or the Financial Aid appointment route.";
  }
  if (classification.requirementReference === "housing_write_unavailable") {
    return "I can read your current housing preference record, but I can't change a housing plan, select a residence, accept an agreement, pay a housing deposit, change roommate preferences, or alter a waitlist. Use the housing requirement route or contact general enrollment support.";
  }
  if (
    classification.requestType === "aid_requirement_explanation" &&
    !derived.financialAid?.policyExplanation
  ) {
    const stateAndAction = facts.map((fact) => fact.text).join(" ");
    return `${stateAndAction ? `${stateAndAction} ` : ""}I can verify your current status, but I can't confirm why this requirement applies from an approved policy source. Contact Financial Aid through the appointment route.`;
  }
  if (facts.length > 0) {
    const lead: Partial<Record<StudentAssistantRequestType, string>> = {
      greeting: "",
      capability_overview: "",
      general_help:
        "Happy to help. Here is where things stand and what to do first:",
      remaining_steps: "Your current remaining onboarding steps are:",
      completed_steps: "Your current completed onboarding steps are:",
      next_action: "Based on the current checklist:",
      missing_documents: "The current records show:",
      document_status: "Your document records show:",
      onboarding_status: "Your current onboarding status:",
      holds_and_blockers: "The current enrollment records show:",
      deadlines: "Your current deadlines are:",
      explain_requirement: "Here is the approved information available:",
      request_support: "Current support options:",
      aid_status: "Your current financial-aid record shows:",
      aid_remaining_steps: "Your remaining financial-aid steps are:",
      aid_incomplete_reason: "Your financial aid is incomplete because:",
      aid_missing_documents: "Your missing financial-aid documents are:",
      aid_verification_status: "Current verification status:",
      aid_deadlines: "Your current financial-aid deadlines are:",
      aid_award_acceptance_status: "Current award acceptance status:",
      aid_requirement_explanation: "Authoritative financial-aid information:",
      aid_next_action: "Based on the current financial-aid record:",
      aid_support: "Financial-aid support options:",
      aid_summary: "Your financial aid:",
      aid_application_status: "Your financial-aid application:",
      aid_disbursement: "Your aid disbursement:",
      aid_coverage: "Your aid against your bill:",
      housing_status: "Your current housing preference record shows:",
      housing_options: "The current tenant-listed residence preference options are:",
      housing_remaining_steps: "Your remaining housing steps are:",
      housing_deadlines: "Your current housing deadline is:",
      housing_next_action: "Based on the current housing record:",
      housing_support: "Housing support information:",
      housing_eligibility: "Your current housing eligibility:",
      registration_status: "Your current registration eligibility:",
      student_account: "Your current student account:",
      academic_calendar: "Key dates for your term:",
      appointments: "Your appointments:",
      policy_lookup: "Approved institutional policy:",
    };
    return `${lead[requestType] ?? "Current records show:"} ${facts
      .map((fact) => fact.text)
      .join(" ")}`;
  }

  if (derived.unavailableData.length > 0 && requestType.startsWith("housing_")) {
    const unavailableMessages: Partial<
      Record<StudentAssistantRequestType, string>
    > = {
      housing_status:
        "I couldn't verify your current housing preference record. Assignment, waitlist, agreement, and housing-deposit data are not available from the current sources.",
      housing_options:
        "I couldn't verify the current tenant-listed residence preference options. This does not establish whether rooms are available.",
      housing_remaining_steps:
        "I couldn't verify the current housing requirement, so I can't safely identify remaining housing steps.",
      housing_deadlines:
        "I couldn't verify a current deadline for the housing requirement.",
      housing_next_action:
        "I couldn't verify enough current housing data to identify a safe next action. Use the housing requirement route or general enrollment support.",
      housing_support:
        "I couldn't verify housing support information. Use the housing requirement route or general enrollment support.",
    };
    return unavailableMessages[requestType] ??
      "I couldn't verify the current housing data needed to answer safely.";
  }
  if (derived.unavailableData.length > 0) {
    return "I couldn't verify all of the current onboarding data needed to answer safely. Please try again or use the support page.";
  }
  const emptyMessages: Record<StudentAssistantRequestType, string> = {
    greeting:
      "Hi! I'm Edward. I can help with your enrollment, documents, financial aid, housing, registration, deadlines, and other university questions. What can I help you with?",
    capability_overview:
      "I'm Edward, your university's enrollment assistant. I answer questions from your own student record. What would you like to know?",
    general_help:
      "You're all caught up as far as your record shows -- nothing is outstanding right now. Ask me about deadlines, documents, financial aid, housing, or registration whenever you need to.",
    remaining_steps:
      "The current checklist does not show any remaining onboarding steps.",
    completed_steps:
      "The current checklist does not show any completed onboarding steps yet.",
    next_action:
      "The current checklist does not show another onboarding action to take.",
    missing_documents:
      "The current checklist and document records do not show any missing documents.",
    document_status:
      "The current checklist does not list any document requirements for you.",
    onboarding_status:
      "No onboarding requirements are currently listed for this student.",
    holds_and_blockers:
      "The current authoritative records do not show an official hold or a derived blocker. Ordinary incomplete steps may still remain.",
    deadlines: "The current enrollment records do not list a deadline in that time window.",
    explain_requirement:
      "I couldn't identify a current requirement with approved policy information to explain.",
    request_support:
      "Support contact information is not currently available. Please use the portal support page.",
    aid_status:
      "I couldn't verify a current financial-aid status. Please try again or use the Financial Aid appointment route.",
    aid_remaining_steps:
      "The current financial-aid record does not show any remaining requirements.",
    aid_incomplete_reason:
      "I couldn't verify why financial aid is incomplete from the current authoritative records.",
    aid_missing_documents:
      "The current financial-aid record does not show any missing documents.",
    aid_verification_status:
      "I couldn't verify a current financial-aid verification status.",
    aid_deadlines:
      "The current financial-aid record does not list a matching deadline.",
    aid_award_acceptance_status:
      "I couldn't verify an authoritative award acceptance status.",
    aid_requirement_explanation:
      "I can verify current status, but I can't confirm why this requirement applies from an approved policy source. Contact Financial Aid through the appointment route.",
    aid_next_action:
      "The current financial-aid record does not show another action to take.",
    aid_support:
      "A financial-aid-specific contact is not configured. You can use the Financial Aid appointment route or generic enrollment support.",
    aid_summary:
      "I couldn't read a financial-aid summary for you. Check Financials, or use the Financial Aid appointment route.",
    aid_application_status:
      "I couldn't verify the status of your financial-aid application. Check Financials, or use the Financial Aid appointment route.",
    aid_disbursement:
      "I couldn't read a disbursement schedule for your aid. Financial Aid can confirm when your aid is expected to pay out.",
    aid_coverage:
      "I couldn't compare your aid against your charges. Check Financials for your current balance.",
    housing_status:
      "I couldn't verify your current housing preference record.",
    housing_options:
      "No tenant-listed residence preference options are currently available. This does not mean rooms are unavailable.",
    housing_remaining_steps:
      "The current housing record does not show any remaining housing checklist steps.",
    housing_deadlines:
      "The current housing requirement does not list a verifiable deadline.",
    housing_next_action:
      "I couldn't verify a next housing action from the current record.",
    housing_support:
      "Housing-specific contact details are not verified. Use the housing requirement route or general enrollment support.",
    housing_eligibility:
      "I couldn't verify your current housing eligibility from the available records.",
    registration_status:
      "I couldn't verify your current registration eligibility from the available records.",
    student_account:
      "I couldn't verify your current student-account balance or charges from the available records.",
    academic_calendar:
      "I couldn't verify the current academic calendar for your term.",
    appointments:
      "I couldn't verify your scheduled appointments from the available records.",
    policy_lookup:
      "I couldn't find an approved policy covering that topic. Institutional policy I haven't been given isn't something I can infer.",
    unsupported_or_out_of_scope:
      "I can help with accepted-student onboarding steps, documents, deadlines, holds, and support, but I can't perform record changes or handle that request.",
  };
  return emptyMessages[requestType];
}

function resolveExplainedRequirement(derived: DerivedStudentState) {
  if (derived.policy) {
    return [...derived.remainingSteps, ...derived.completedSteps].find(
      (step) => step.code === derived.policy?.requirementCode,
    );
  }
  return derived.nextStep;
}

function isCompositionOutput(
  value: unknown,
): value is ModelGroundedCompositionOutput {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return (
    Array.isArray(candidate.factIds) &&
    candidate.factIds.length <= 12 &&
    candidate.factIds.every(
      (id) => typeof id === "string" && id.length > 0 && id.length <= 160,
    ) &&
    ["concise", "supportive", "direct"].includes(String(candidate.tone))
  );
}

/**
 * "Submit your official transcript is still ready" is a column value wearing a
 * sentence. Each status becomes a phrase a student can act on; the raw value
 * stays in the response payload for the portal, which has its own labels.
 */
/** Housing state, in words rather than in the record's vocabulary. */
function housingRequirementPhrase(state: string): string {
  return (
    {
      complete: "the housing requirement is complete.",
      incomplete: "the housing requirement is not complete yet.",
      waived: "the housing requirement has been waived for you.",
      not_applicable: "the housing requirement does not apply to you.",
      conflicting:
        "your housing records disagree with each other, so Housing & Residence Life needs to resolve them before this can be settled.",
    }[state] ?? "I can't confirm the state of the housing requirement."
  );
}

function roommatePreferencePhrase(state: string): string {
  return (
    {
      not_applicable:
        "Roommate preferences do not apply to your housing plan.",
      not_provided: "You have not given any roommate preferences yet.",
      partially_provided:
        "Your roommate preferences are only partly filled in.",
      provided: "Your roommate preferences are recorded.",
    }[state] ?? "I can't confirm your roommate preference state."
  );
}

function requirementPhrase(status: RequirementStatus): string {
  return (
    {
      ready: "has not been started yet",
      in_progress: "is in progress",
      blocked: "is waiting on an earlier step",
      submitted: "has been submitted and is waiting on review",
      under_review: "is under review",
      rejected: "was returned and needs another submission",
      expired: "has passed its deadline",
      completed: "is complete",
      waived: "has been waived",
      not_applicable: "does not apply to you",
      help_requested: "is waiting on a reply from the university",
    }[status] ?? "is not complete yet"
  );
}

function formatDate(value: string): string {
  return value.slice(0, 10);
}

function deadlineFactText(
  deadline: DerivedStudentState["deadlines"][number],
): string {
  if (!deadline.dueAt || deadline.urgency === "unknown_date") {
    return `A due date for ${deadline.title} could not be verified from the current source.`;
  }
  const date = formatDate(deadline.dueAt);
  const verb = deadline.kind === "appointment" ? "is scheduled for" : "is due";
  if (deadline.urgency === "overdue") {
    return `${deadline.title} was due ${date} and is overdue.`;
  }
  if (deadline.urgency === "due_today") {
    return `${deadline.title} ${verb} today (${date}).`;
  }
  return `${deadline.title} ${verb} ${date}.`;
}

function prioritizedActionText(
  action: NonNullable<DerivedStudentState["prioritizedAction"]>,
  evidence: DerivedStudentState["priorityEvidence"],
  explanationRequested: boolean,
): string {
  if (action.reasonCode === "official_hold_support") {
    return "Handle the official enrollment hold first by contacting enrollment support; the current source does not provide a self-resolution path.";
  }
  if (action.reasonCode === "dependency_prerequisite") {
    return `Handle ${action.label} first because it is an unsatisfied prerequisite for a blocked requirement.`;
  }
  if (action.reasonCode === "urgent_deadline") {
    if (evidence?.deadlineAt && evidence.institutionalTimeZone) {
      const date = formatDeadlineDate(
        evidence.deadlineAt,
        evidence.deadlinePrecision,
        evidence.institutionalTimeZone,
      );
      const remaining =
        evidence.daysRemaining !== null && evidence.daysRemaining > 0
          ? `, in ${evidence.daysRemaining} day${evidence.daysRemaining === 1 ? "" : "s"},`
          : ",";
      const blocked =
        evidence.blocksRequirementLabels.length > 0
          ? ` It also unlocks ${evidence.blocksRequirementLabels.join(" and ")}.`
          : "";
      if (!evidence.isCurrentTopPriority) {
        return `${action.label} was the prior selected action and is due ${date}${remaining} but current records no longer rank it first.${blocked}`;
      }
      return `${action.label} should be handled first because it is due ${date}${remaining} and is the earliest unresolved deadline in your checklist.${blocked}`;
    }
    return explanationRequested
      ? `The current records still rank ${action.label} first, but its exact deadline could not be verified.`
      : `Handle ${action.label} first based on the current deadline urgency.`;
  }
  return `Your next onboarding action is ${action.label}.`;
}

function formatDeadlineDate(
  value: string,
  precision: "date" | "instant" | null,
  timeZone: string,
): string {
  const date =
    precision === "date"
      ? new Date(`${value.slice(0, 10)}T12:00:00.000Z`)
      : new Date(value);
  return new Intl.DateTimeFormat("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: precision === "date" ? "UTC" : timeZone,
  }).format(date);
}

function boundedPolicyText(value: string): string {
  const normalized = value.replace(/\s+/g, " ").trim().slice(0, 1_200);
  return normalized.endsWith(".") ? normalized : `${normalized}.`;
}
