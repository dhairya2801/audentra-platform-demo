/**
 * Grounded natural-language answering.
 *
 * The original composer could only *select* pre-rendered template sentences, so
 * every reply was a concatenation of fragments. This module lets the model write
 * the reply instead, while keeping the grounding guarantee deterministic:
 *
 *   1. `buildEvidenceBundle` assembles every fact the executed reads support,
 *      independent of which request type triggered them, so a multi-domain
 *      question sees multi-domain evidence.
 *   2. `guardGroundedAnswer` re-checks the written reply against that evidence
 *      and rejects anything that invents a date, an amount, a contact, or a
 *      claim that Edward performed a write. A rejected reply falls back to the
 *      deterministic message, so grounding never depends on model compliance.
 */
import type { RequestClassification } from "./contracts";
import type { GroundedFactForComposition } from "./model";
import type { DerivedStudentState } from "./state";
import { buildGroundedFacts } from "./composition";

export interface EvidenceBundle {
  /** Facts the requested intents asked for, in intent order. */
  primaryFacts: GroundedFactForComposition[];
  /** Everything else the same reads already support, for cross-domain context. */
  supportingFacts: GroundedFactForComposition[];
  /** Sources that could not be read, so the answer can say so honestly. */
  unavailable: Array<{ source: string; reason: string }>;
}

const DEFAULT_PRIMARY_LIMIT = 18;
/**
 * Cross-domain context is there so a housing answer can cite the deposit, not
 * so every answer can recite the record. Twenty-four supporting facts alongside
 * the primary ones gave the writer forty-odd true sentences and an invitation
 * to use them: answers passed their grounding checks by emitting everything and
 * lost the question in the middle of it.
 */
const DEFAULT_SUPPORTING_LIMIT = 8;

/**
 * Assemble grounded facts for every requested intent plus the cross-domain
 * context the same tool reads already justify.
 */
export function buildEvidenceBundle(input: {
  classifications: readonly RequestClassification[];
  derived: DerivedStudentState;
  primaryLimit?: number;
  supportingLimit?: number;
}): EvidenceBundle {
  const seen = new Set<string>();
  const primaryFacts: GroundedFactForComposition[] = [];
  for (const classification of input.classifications) {
    for (const fact of buildGroundedFacts(classification, input.derived)) {
      if (seen.has(fact.id)) continue;
      seen.add(fact.id);
      primaryFacts.push(fact);
    }
  }

  // A question about what *a category of students* must do is not a question
  // about this student, and answering it with this student's own blockers
  // produced replies that mixed institutional policy with a past-due balance.
  // The primary facts still carry the policy text; only the personal context is
  // withheld.
  const impersonal = input.classifications[0]?.requestType === "policy_lookup";
  const supportingFacts: GroundedFactForComposition[] = [];
  if (!impersonal) {
    for (const fact of buildContextFacts(input.derived)) {
      if (seen.has(fact.id)) continue;
      seen.add(fact.id);
      supportingFacts.push(fact);
    }
  }

  return {
    primaryFacts: primaryFacts.slice(0, input.primaryLimit ?? DEFAULT_PRIMARY_LIMIT),
    supportingFacts: supportingFacts.slice(
      0,
      input.supportingLimit ?? DEFAULT_SUPPORTING_LIMIT,
    ),
    unavailable: input.derived.unavailableData.map((item) => ({
      source: item.source,
      reason: item.reason,
    })),
  };
}

/**
 * Cross-domain facts derived from reads that already happened. These let the
 * model connect domains ("the deposit is posted, so that is not the blocker")
 * without a bespoke request type for every combination.
 */
function buildContextFacts(
  derived: DerivedStudentState,
): GroundedFactForComposition[] {
  const facts: GroundedFactForComposition[] = [];

  const checklistReceipts = [
    ...new Set(
      [...derived.completedSteps, ...derived.remainingSteps].flatMap(
        (step) => step.contextReceiptIds,
      ),
    ),
  ];
  if (checklistReceipts.length > 0) {
    facts.push({
      id: "context:checklist-counts",
      text: `Checklist totals: ${derived.completedSteps.length} complete, ${derived.remainingSteps.length} remaining, ${derived.blockedSteps.length} blocked.`,
      contextReceiptIds: checklistReceipts,
    });
  }
  for (const step of derived.completedSteps) {
    facts.push({
      id: `context:complete:${step.code}`,
      text: `${step.title} is already complete.`,
      contextReceiptIds: step.contextReceiptIds,
    });
  }
  for (const step of derived.remainingSteps) {
    facts.push({
      id: `context:remaining:${step.code}`,
      text: `${step.title} is not complete yet.`,
      contextReceiptIds: step.contextReceiptIds,
    });
  }
  for (const blocker of derived.derivedBlockers) {
    if (!blocker.studentSafeReason) continue;
    facts.push({
      id: `context:blocker:${blocker.type}:${blocker.id}`,
      text: blocker.studentSafeReason,
      contextReceiptIds: blocker.contextReceiptIds,
    });
  }
  if (derived.holdReceiptId && derived.officialHolds.length === 0) {
    facts.push({
      id: "context:no-official-holds",
      text: "There are no official enrollment holds on this record.",
      contextReceiptIds: [derived.holdReceiptId],
    });
  }
  if (derived.prioritizedAction) {
    facts.push({
      id: `context:priority:${derived.prioritizedAction.id}`,
      text: `The highest-priority outstanding item is ${derived.prioritizedAction.label}.`,
      contextReceiptIds: derived.prioritizedAction.contextReceiptIds,
    });
  }
  for (const deadline of derived.deadlines.slice(0, 8)) {
    if (!deadline.dueAt) continue;
    facts.push({
      id: `context:deadline:${deadline.id}`,
      text: `${deadline.title} is dated ${deadline.dueAt.slice(0, 10)} (${deadline.urgency.replaceAll("_", " ")}).`,
      contextReceiptIds: deadline.contextReceiptIds,
    });
  }
  const aid = derived.financialAid;
  if (aid && aid.contextReceiptIds.length > 0) {
    facts.push({
      id: "context:aid-summary",
      text: `Financial aid is ${aid.status}: ${aid.completedRequirements.length} requirement(s) complete, ${aid.remainingRequirements.length} remaining, verification ${aid.verificationStatus.replaceAll("_", " ")}.`,
      contextReceiptIds: aid.contextReceiptIds,
    });
  }
  const housing = derived.housing;
  if (housing && housing.contextReceiptIds.length > 0) {
    facts.push({
      id: "context:housing-summary",
      text: `Housing requirement state is ${housing.planRequirementState.replaceAll("_", " ")} and the housing plan is ${housing.planStatus.replaceAll("_", " ")}.`,
      contextReceiptIds: housing.contextReceiptIds,
    });
  }
  for (const document of derived.missingDocuments) {
    facts.push({
      id: `context:document:${document.requirementCode}`,
      text: `${document.title} is still outstanding as a document requirement.`,
      contextReceiptIds: document.contextReceiptIds,
    });
  }

  // Any broader capability that was read becomes cross-domain context, so a
  // housing question can cite the deposit payment and a registration question
  // can cite the account balance without a bespoke request type per pairing.
  for (const requestType of crossDomainRequestTypes) {
    for (const fact of buildGroundedFacts(
      { ...neutralClassification, requestType },
      derived,
    )) {
      facts.push(fact);
    }
  }
  return facts;
}

/**
 * Capability domains whose facts are useful as background for *any* question.
 * They are read straight from the graph's already-executed reads, so including
 * them costs no extra tool call.
 */
const crossDomainRequestTypes = [
  "housing_eligibility",
  "registration_status",
  "student_account",
  "appointments",
  "policy_lookup",
] as const satisfies readonly RequestClassification["requestType"][];

const neutralClassification: RequestClassification = {
  requestType: "onboarding_status",
  confidence: 1,
  source: "deterministic",
  requirementReference: null,
  deadlineWindow: null,
  requestedEntity: null,
  financialAidEntity: null,
  housingEntity: null,
  deadlineScope: null,
  blockerScope: null,
  blockerTarget: null,
  priorityExplanationRequested: false,
  registrationQuestion: false,
};

export interface GroundedAnswerGuardResult {
  accepted: boolean;
  /** Machine-readable reason the answer was refused, for telemetry and tests. */
  reasonCode:
    | null
    | "empty"
    | "too_long"
    | "ungrounded_date"
    | "ungrounded_number"
    | "ungrounded_contact"
    | "claimed_write"
    | "leaked_identifier"
    | "invented_causation"
    | "contradicted_document_state";
  answer: string;
}

const MAX_ANSWER_CHARACTERS = 1_200;

/** First-person claims that Edward changed a record. Edward is read-only. */
const WRITE_CLAIM_PATTERN =
  /\bi(?:'ve|'ll| have| will| can| am going to| just)?\s+(?:go ahead and\s+)?(?:submitted?|paid?|pay|updated?|update|changed?|change|removed?|remove|cancell?ed?|cancel|registered?|register|applied|apply|uploaded?|upload|approved?|approve|waived?|waive|scheduled?|schedule|booked?|book|enrolled?|enroll|posted?|post|cleared?|clear|fixed?|fix)\b/i;

const MONTHS = [
  "january",
  "february",
  "march",
  "april",
  "may",
  "june",
  "july",
  "august",
  "september",
  "october",
  "november",
  "december",
];

/**
 * Re-check a written answer against the evidence it was supposed to use.
 *
 * Dates, multi-digit numbers, and contact details must all be traceable to the
 * evidence text. Bare one- and two-digit integers are allowed because they are
 * almost always counts the model derived from the evidence list itself (for
 * example "3 steps remain"), and a count is not the kind of claim that misleads
 * a student about an obligation.
 */
export function guardGroundedAnswer(input: {
  answer: string;
  evidenceTexts: readonly string[];
  causalGuards?: readonly CausalGuard[];
  documentStates?: ReadonlyArray<{ title: string; submissionState: string }>;
}): GroundedAnswerGuardResult {
  const answer = input.answer.replace(/\s+/g, " ").trim();
  const reject = (
    reasonCode: NonNullable<GroundedAnswerGuardResult["reasonCode"]>,
  ): GroundedAnswerGuardResult => ({ accepted: false, reasonCode, answer });

  if (answer.length === 0) return reject("empty");
  if (answer.length > MAX_ANSWER_CHARACTERS) return reject("too_long");
  if (WRITE_CLAIM_PATTERN.test(answer)) return reject("claimed_write");
  if (IDENTIFIER_PATTERN.test(answer)) return reject("leaked_identifier");

  const corpus = input.evidenceTexts.join("\n").toLowerCase();
  const allowedDates = collectDates(corpus);
  const allowedNumbers = collectNumbers(maskDatesAndContacts(corpus));
  const allowedContacts = collectContacts(corpus);
  const lowered = answer.toLowerCase();

  // These token classes overlap: a phone number is also a run of digits, and a
  // prose date carries a four-digit year. Each class is therefore checked
  // most-specific first, and validated classes are masked out of the text
  // before the next class is extracted.
  for (const contact of collectContacts(lowered)) {
    if (!allowedContacts.has(contact)) return reject("ungrounded_contact");
  }
  for (const date of collectDates(lowered)) {
    if (!allowedDates.has(date)) return reject("ungrounded_date");
  }
  for (const value of collectNumbers(maskDatesAndContacts(lowered))) {
    if (!allowedNumbers.has(value)) return reject("ungrounded_number");
  }
  if (detectInventedCausation(lowered, input.causalGuards ?? [])) {
    return reject("invented_causation");
  }
  if (detectContradictedDocumentState(lowered, input.documentStates ?? [])) {
    return reject("contradicted_document_state");
  }

  return { accepted: true, reasonCode: null, answer };
}

const IDENTIFIER_PATTERN =
  /\breceipt-\d+|\bcontext:|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-/i;

const contactPattern = () =>
  /[\w.+-]+@[\w-]+\.[\w.]+|https?:\/\/\S+|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b/g;

const isoDatePattern = () => /(\d{4})-(\d{2})-(\d{2})/g;

const prosePattern = () =>
  new RegExp(
    `\\b(${MONTHS.join("|")}|${MONTHS.map((month) => month.slice(0, 3)).join("|")})\\.?\\s+(\\d{1,2})(?:\\s*,?\\s*(\\d{4}))?`,
    "g",
  );

/** Blank out spans already validated as dates or contact details. */
function maskDatesAndContacts(text: string): string {
  return text
    .replaceAll(contactPattern(), " ")
    .replaceAll(isoDatePattern(), " ")
    .replaceAll(prosePattern(), " ");
}

/** Canonical `month-day[-year]` keys for both ISO and prose date forms. */
function collectDates(text: string): Set<string> {
  const dates = new Set<string>();
  for (const match of text.matchAll(isoDatePattern())) {
    const [, year, month, day] = match;
    dates.add(`${Number(month)}-${Number(day)}`);
    dates.add(`${Number(month)}-${Number(day)}-${year}`);
  }
  for (const match of text.matchAll(prosePattern())) {
    const monthToken = match[1]?.replace(".", "");
    if (!monthToken) continue;
    const monthIndex = MONTHS.findIndex((month) =>
      month.startsWith(monthToken),
    );
    if (monthIndex < 0) continue;
    const day = Number(match[2]);
    dates.add(`${monthIndex + 1}-${day}`);
    if (match[3]) dates.add(`${monthIndex + 1}-${day}-${match[3]}`);
  }
  return dates;
}

/**
 * Money amounts and any integer of three or more digits (years, amounts, ids),
 * canonicalised by numeric value. Evidence renders currency as "$500.00" while
 * a natural reply says "$500"; comparing the raw strings rejected correct
 * answers and forced them back to the deterministic fallback.
 */
function collectNumbers(text: string): Set<string> {
  const numbers = new Set<string>();
  for (const match of text.matchAll(
    /\$\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]{2,}(?:\.\d+)?\b|\b\d+(?:\.\d+)?%/g,
  )) {
    const token = match[0].replace(/[\s,$]/g, "");
    if (token.endsWith("%")) {
      numbers.add(`${canonicalNumber(token.slice(0, -1))}%`);
      continue;
    }
    numbers.add(canonicalNumber(token));
  }
  return numbers;
}

function canonicalNumber(token: string): string {
  const value = Number(token);
  return Number.isFinite(value) ? String(value) : token;
}

function collectContacts(text: string): Set<string> {
  const contacts = new Set<string>();
  for (const match of text.matchAll(contactPattern())) {
    contacts.add(match[0].replace(/[.,;)]+$/, ""));
  }
  return contacts;
}

/* ---------------------------------------------------------------------------
 * Invented causation.
 *
 * The most serious failure this assistant can produce is a confident, specific,
 * wrong reason: "you cannot register because your financial aid is incomplete",
 * when the gate list says nothing of the kind. It is worse than a vague answer
 * because a student will act on it, and the model judge does not catch it — in
 * calibration it scored three such answers "good".
 *
 * The previous pass mitigated this in data, by declaring gate lists exhaustive,
 * and asserted two known falsehoods by hand. It still recurred. This is the
 * generalisation: the guard is *generated from the gate data itself*, so any
 * claim of the form "X is blocked because Y" is checked against whether Y is
 * actually an open gate of X.
 * ------------------------------------------------------------------------- */

/** The vocabulary a causal clause can name. */
const causalTopics: ReadonlyArray<{ key: string; pattern: RegExp }> = [
  { key: "balance", pattern: /\b(?:balance|past[- ]due|owe|owing|unpaid charges?)\b/ },
  { key: "deposit", pattern: /\bdeposit\b/ },
  { key: "immunisation", pattern: /\bimmunis\w*|\bimmuniz\w*|\bhealth (?:record|clearance)\b/ },
  { key: "advising", pattern: /\badvis(?:ing|er|or)\b/ },
  { key: "transcript", pattern: /\btranscript\b/ },
  { key: "financial_aid", pattern: /\bfinancial[- ]aid\b|\baid (?:package|award|is|isn'?t|being)\b/ },
  { key: "verification", pattern: /\bverification\b|\bworksheet\b/ },
  { key: "fafsa", pattern: /\bfafsa\b/ },
  { key: "housing", pattern: /\bhousing (?:plan|preference|application)\b/ },
  { key: "identity", pattern: /\bidentity document\w*\b/ },
  { key: "orientation", pattern: /\borientation\b/ },
  { key: "hold", pattern: /\bhold\b/ },
];

/** Which topic each gate code is about. */
const topicByGateCode: Readonly<Record<string, string>> = {
  account_balance: "balance",
  enrollment_deposit_posted: "deposit",
  enrollment_confirmed: "deposit",
  immunization_cleared: "immunisation",
  advising_complete: "advising",
  final_transcript: "transcript",
  housing_preference_selected: "housing",
  fafsa_received: "fafsa",
  verification_complete: "verification",
  award_decisions: "financial_aid",
};

export interface CausalGuard {
  /** How the answer would refer to the blocked outcome. */
  outcome: RegExp;
  /** Topics that really are open gates of it. */
  openTopics: ReadonlySet<string>;
}

const causalConnective =
  /\b(?:because|since|due to|owing to|as a result of|caused by|is blocking|are blocking|blocks|prevents?|preventing)\b/;

/**
 * Build the guards from whatever gated capabilities the reads returned. A
 * capability that was not read produces no guard, so the check never fires on
 * evidence Edward did not have.
 */
export function buildCausalGuards(input: {
  registrationGates?: ReadonlyArray<{ code: string; satisfied: boolean }> | null;
  housingGates?: ReadonlyArray<{ code: string; satisfied: boolean }> | null;
  disbursementGates?: ReadonlyArray<{ code: string; satisfied: boolean }> | null;
}): CausalGuard[] {
  const openTopics = (
    gates: ReadonlyArray<{ code: string; satisfied: boolean }>,
  ): Set<string> =>
    new Set(
      gates
        .filter((gate) => !gate.satisfied)
        .map((gate) => topicByGateCode[gate.code])
        .filter((topic): topic is string => Boolean(topic)),
    );

  const guards: CausalGuard[] = [];
  if (input.registrationGates) {
    guards.push({
      outcome:
        /(?:can(?:no|')?t|cannot|can not|unable to|not able to)\s+(?:currently\s+)?register\b|\bregistration (?:is|remains|stays) (?:currently )?(?:blocked|closed|unavailable|not (?:open|available|possible))\b|\byou (?:are|'re) (?:currently )?(?:blocked|prevented) from registering\b/,
      openTopics: openTopics(input.registrationGates),
    });
  }
  if (input.housingGates) {
    guards.push({
      outcome:
        /(?:can(?:no|')?t|cannot|can not|unable to|not able to)\s+(?:currently\s+)?apply for housing\b|\bhousing (?:application )?(?:is|remains) (?:currently )?(?:blocked|closed|unavailable|not (?:open|available))\b/,
      openTopics: openTopics(input.housingGates),
    });
  }
  if (input.disbursementGates) {
    guards.push({
      outcome:
        /\b(?:aid|funds|money) (?:has|have)(?:n't| not) (?:been )?disbursed\b|\bdisbursement (?:is|remains) (?:currently )?(?:held|on hold|blocked)\b/,
      openTopics: openTopics(input.disbursementGates),
    });
  }
  return guards;
}

/**
 * Returns the offending topic when a sentence blames a blocked outcome on
 * something the gate data does not list as an open gate of it.
 *
 * Deliberately conservative: it only fires when the sentence both names the
 * outcome and carries an explicit causal connective. A sentence that says
 * "you cannot register. Your aid is also incomplete." is two true statements
 * and is left alone.
 */
export function detectInventedCausation(
  answer: string,
  guards: readonly CausalGuard[],
): string | null {
  if (guards.length === 0) return null;
  const sentences = answer.split(/(?<=[.!?])\s+/);
  for (const sentence of sentences) {
    const lowered = sentence.toLowerCase();
    for (const guard of guards) {
      if (!guard.outcome.test(lowered)) continue;
      if (!causalConnective.test(lowered)) continue;
      for (const topic of causalTopics) {
        if (!topic.pattern.test(lowered)) continue;
        if (guard.openTopics.has(topic.key)) continue;
        // A topic that is not an open gate of this outcome, named as its cause.
        return topic.key;
      }
    }
  }
  return null;
}

/* ---------------------------------------------------------------------------
 * Contradicting the document record.
 *
 * The bug this whole run is about, reappearing one level up. The state was
 * correct and the evidence said so -- "official transcript: needs to be
 * resubmitted before it can be accepted" -- and the written answer still opened
 * "your transcript has not been uploaded yet", contradicting its own next
 * sentence. Telling a student they never sent something they sent is the single
 * most damaging thing this assistant can say, because they act on it and upload
 * it again.
 *
 * Numbers and dates were already guarded. This guards the one prose claim that
 * matters as much as they do.
 * ------------------------------------------------------------------------- */

const notSubmittedClaim =
  /\b(?:has not|hasn't|have not|haven't|not|never)\s+(?:yet\s+)?(?:been\s+)?(?:be(?:en)?\s+)?(?:uploaded|submitted|received|sent|provided|arrived)\b|\bis (?:still )?missing\b|\bno .{0,24}(?:on file|on record|received)\b/;

/**
 * Returns the title of a document the answer wrongly describes as never sent.
 *
 * Scoped per sentence and per document: an answer may quite correctly say that
 * *other* documents have not been submitted, and usually needs to.
 */
export function detectContradictedDocumentState(
  answer: string,
  documentStates: ReadonlyArray<{ title: string; submissionState: string }>,
): string | null {
  const onFile = documentStates.filter(
    (state) => state.submissionState !== "NOT_SUBMITTED",
  );
  if (onFile.length === 0) return null;
  for (const sentence of answer.split(/(?<=[.!?])\s+/)) {
    const lowered = sentence.toLowerCase();
    if (!notSubmittedClaim.test(lowered)) continue;
    for (const state of onFile) {
      // Match on the distinctive noun rather than the whole requirement title,
      // which is phrased as an instruction ("Submit your official transcript").
      const noun = documentNoun(state.title);
      if (noun && lowered.includes(noun)) return state.title;
    }
  }
  return null;
}

function documentNoun(title: string): string | null {
  const lowered = title.toLowerCase();
  for (const noun of [
    "transcript",
    "immunisation",
    "immunization",
    "identity",
    "verification",
    "worksheet",
  ]) {
    if (lowered.includes(noun)) return noun;
  }
  return null;
}
