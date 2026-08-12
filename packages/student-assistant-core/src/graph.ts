import {
  studentAssistantRequestTypes,
  type RequestClassification,
  type StudentAssistantGraphNodeName,
  type StudentAssistantGraphTraceEntry,
  type StudentAssistantGraphExecution,
  type StudentAssistantInput,
  type StudentAssistantResponse,
  type StudentAssistantToolName,
  type StudentAssistantTools,
  type StudentAssistantUnavailableData,
  type ApprovedPolicySearchRead,
  type StudentToolContext,
} from "./contracts";
import { buildResponseBlocks } from "./blocks";
import {
  composeGroundedAnswerNode,
  validateGroundingNode,
} from "./composition";
import {
  studentAssistantToolCatalog,
  validateModelClassification,
  validateModelToolPlan,
  type StudentAssistantModel,
  type ValidatedModelToolPlan,
} from "./model";
import {
  deriveStudentStateNode,
  deterministicClassificationNode,
  deterministicConversationalClassificationNode,
  deterministicSafetyClassificationNode,
  executeToolReadsNode,
  normalizeRequestNode,
  retrievePolicyNode,
  selectToolReadsNode,
} from "./nodes";
import { createInitialGraphState, type ExecutedToolRead } from "./state";

export interface StudentAssistantGraphOptions {
  tools: StudentAssistantTools;
  model?: StudentAssistantModel;
  toolTimeoutMs?: number;
  historyLimit?: number;
  clock?: () => number;
  now?: () => Date;
  institutionalTimeZone?: string | null;
}

export class StudentAssistantGraph {
  private readonly tools: StudentAssistantTools;
  private readonly model: StudentAssistantModel | undefined;
  private readonly toolTimeoutMs: number;
  private readonly historyLimit: number;
  private readonly clock: () => number;
  private readonly now: () => Date;
  private readonly institutionalTimeZone: string | null;

  constructor(options: StudentAssistantGraphOptions) {
    this.tools = options.tools;
    this.model = options.model;
    this.toolTimeoutMs = options.toolTimeoutMs ?? 2_500;
    this.historyLimit = options.historyLimit ?? 6;
    this.clock = options.clock ?? Date.now;
    this.now = options.now ?? (() => new Date());
    this.institutionalTimeZone = options.institutionalTimeZone ?? "UTC";
  }

  async execute(input: StudentAssistantInput): Promise<StudentAssistantResponse> {
    const graphStartedAt = this.clock();
    const state = createInitialGraphState(input);

    let startedAt = this.clock();
    state.normalized = normalizeRequestNode(
      input.message,
      input.context,
      { historyLimit: this.historyLimit },
    );
    addTrace(
      state.trace,
      "normalize_request",
      "completed",
      elapsed(this.clock, startedAt),
      `normalized request with ${state.normalized.history.length} bounded history messages`,
    );

    startedAt = this.clock();
    const safetyClassification =
      deterministicSafetyClassificationNode(state.normalized);
    let plannedTools: StudentAssistantToolName[] | null = null;
    let additionalClassifications: RequestClassification[] = [];
    let toolSelectionSource: StudentAssistantGraphExecution["toolSelectionSource"] =
      "deterministic_fallback";
    state.classification =
      safetyClassification ??
      deterministicConversationalClassificationNode(state.normalized);
    if (state.classification) {
      // Both the safety gates and the conversational openers are settled before
      // the planner is asked anything. A greeting that reaches the planner comes
      // back as a checklist question -- observed as "hi" answered with a full
      // onboarding dump -- and no prompt wording reliably prevents that, because
      // the planner's whole job is to find a record to read.
      toolSelectionSource = "safety_gate";
    } else {
      const modelPlan = await this.planWithModel(state.normalized);
      if (modelPlan) {
        state.classification = modelPlan.classification;
        additionalClassifications = modelPlan.additionalClassifications;
        plannedTools = modelPlan.toolNames;
        toolSelectionSource = "model_plan";
      }
    }
    if (!state.classification) {
      state.classification = deterministicClassificationNode(state.normalized);
    }
    if (!state.classification) {
      state.classification = await this.classifyWithModel(state.normalized);
      if (!state.classification) {
        state.classification = safeFallbackClassification();
        state.classificationFallback = true;
        state.failureCodes.push("classification_model_failure");
        state.toolExecution.unavailableData.push({
          source: "classification",
          reason: "model_error",
          retryable: true,
        });
      }
    }
    addTrace(
      state.trace,
      "classify_request",
      state.classification.source === "safe_fallback" ? "fallback" : "completed",
      elapsed(this.clock, startedAt),
      `${state.classification.requestType} via ${toolSelectionSource === "model_plan" ? "model tool plan" : state.classification.source}`,
    );

    // Clarification is worth asking for only when the question genuinely could
    // mean either course registration or orientation registration. Two ways it
    // was asked for when it should not have been: on questions that never used
    // the word at all, and -- more damagingly -- on questions that used it with
    // plenty of context, like "can I register even though my financial aid
    // isn't complete?". That one is not ambiguous, and marking it so suppressed
    // the writer entirely and left the student with a template dump of every
    // gate instead of an answer.
    if (state.classification.blockerScope === "registration_ambiguous") {
      const text = state.normalized.comparableText;
      const raisedRegistration = /\bregist(?:er|ering|ration)\b/i.test(text);
      const alreadySpecific =
        /\bclass(?:es)?\b|\bcourses?\b|\bcredits?\b|\bsections?\b|\bsemester\b|\bterm\b|\borientation\b|financial[ -]?aid|\baid\b|\bdeposit\b|\bbalance\b|\bhold\b|\bdocuments?\b|\btranscripts?\b|\bimmuni[sz]/i.test(
          text,
        );
      if (!raisedRegistration || alreadySpecific) {
        state.classification = {
          ...state.classification,
          blockerScope: "enrollment",
        };
      }
    }

    startedAt = this.clock();
    state.selectedTools =
      plannedTools ?? selectToolReadsNode(state.classification.requestType);
    addTrace(
      state.trace,
      "select_tool_reads",
      "completed",
      elapsed(this.clock, startedAt),
      `${state.selectedTools.length} approved read-only tools selected`,
    );

    startedAt = this.clock();
    const classificationUnavailable = [...state.toolExecution.unavailableData];
    state.toolExecution = await executeToolReadsNode(
      state.selectedTools,
      this.tools,
      trustedToolContext(input),
      { timeoutMs: this.toolTimeoutMs },
    );
    state.toolExecution.unavailableData.unshift(...classificationUnavailable);
    addTrace(
      state.trace,
      "execute_tool_reads",
      state.toolExecution.unavailableData.some((item) =>
        state.toolExecution.executedTools.includes(
          item.source as (typeof state.toolExecution.executedTools)[number],
        ),
      )
        ? "fallback"
        : "completed",
      elapsed(this.clock, startedAt),
      `${state.toolExecution.executedTools.length} read-only tools executed`,
    );

    startedAt = this.clock();
    state.derived = deriveStudentStateNode(
      state.classification,
      state.toolExecution,
      {
        now: this.now(),
        institutionalTimeZone: this.institutionalTimeZone,
        priorPriority: input.context.priorPriority ?? null,
      },
    );
    addTrace(
      state.trace,
      "derive_student_state",
      state.derived.unavailableData.length > 0 ? "fallback" : "completed",
      elapsed(this.clock, startedAt),
      `${state.derived.completedSteps.length} completed, ${state.derived.remainingSteps.length} remaining, ${state.derived.officialHolds.length} official holds, ${state.derived.derivedBlockers.length} derived blockers`,
    );

    startedAt = this.clock();
    const policyTool =
      state.classification.requestType === "aid_requirement_explanation"
        ? "retrieveApprovedFinancialAidPolicy"
        : "retrieveApprovedPolicy";
    const policyPlanned = state.selectedTools.includes(policyTool);
    const policyResult = policyPlanned
      ? await retrievePolicyNode({
          classification: state.classification,
          normalized: state.normalized,
          derived: state.derived,
          tools: this.tools,
          trustedContext: trustedToolContext(input),
          receiptSequence: state.toolExecution.receipts.length + 1,
          timeoutMs: this.toolTimeoutMs,
        })
      : {
          derived: state.derived,
          read: null,
          aidRead: null,
          unavailableData: [],
          executed: false,
        };
    state.derived = {
      ...policyResult.derived,
      unavailableData: deduplicateUnavailable([
        ...policyResult.derived.unavailableData,
        ...policyResult.unavailableData,
      ]),
    };
    if (policyResult.read) {
      state.toolExecution.reads.retrieveApprovedPolicy = policyResult.read;
      state.toolExecution.receipts.push(policyResult.read.receipt);
    }
    if (policyResult.aidRead) {
      state.toolExecution.reads.retrieveApprovedFinancialAidPolicy =
        policyResult.aidRead;
      state.toolExecution.receipts.push(policyResult.aidRead.receipt);
    }
    if (policyResult.executed) {
      state.toolExecution.executedTools.push(
        state.classification.requestType === "aid_requirement_explanation"
          ? "retrieveApprovedFinancialAidPolicy"
          : "retrieveApprovedPolicy",
      );
    }
    state.toolExecution.unavailableData.push(...policyResult.unavailableData);
    const policyStatus =
      ![
        "explain_requirement",
        "aid_requirement_explanation",
      ].includes(state.classification.requestType)
        ? "skipped"
        : policyResult.derived.policy ||
            policyResult.derived.financialAid?.policyExplanation
          ? "completed"
          : "fallback";
    addTrace(
      state.trace,
      "retrieve_policy",
      policyStatus,
      elapsed(this.clock, startedAt),
      policyStatus === "skipped"
        ? "policy not required for request type"
        : !policyPlanned
          ? "approved policy not included in the validated plan"
        : policyResult.executed
          ? "approved policy read attempted"
          : "no checklist-backed policy selector available",
    );

    // Policy search is topic-shaped rather than identity-shaped, so it runs
    // outside the plain read dispatcher. The topic is the student's own words,
    // bounded; the tool decides what approved policy matches.
    if (state.selectedTools.includes("searchApprovedPolicies")) {
      const read = await runPolicySearch(
        this.tools,
        trustedToolContext(input),
        state.normalized.text,
        state.toolExecution.receipts.length + 1,
        this.toolTimeoutMs,
      );
      state.toolExecution.reads.searchApprovedPolicies = read;
      state.toolExecution.receipts.push(read.receipt);
      state.toolExecution.executedTools.push("searchApprovedPolicies");
      if (read.result.status === "available") {
        state.derived.policyMatches = read.result.data;
        state.derived.capabilityReceiptIds.searchApprovedPolicies = [
          read.receipt.id,
        ];
      } else {
        state.derived.unavailableData.push({
          source: "searchApprovedPolicies",
          reason: read.result.reason,
          retryable: read.result.retryable,
        });
      }
    }

    startedAt = this.clock();
    const draft = await composeGroundedAnswerNode({
      classification: state.classification,
      additionalClassifications,
      normalized: state.normalized,
      derived: state.derived,
      ...(this.model ? { model: this.model } : {}),
    });
    state.draftMessage = draft.fallbackMessage;
    if (draft.proseRejectedReason) {
      // The writer produced something the claim guard would not stand behind.
      // The deterministic message is used instead and the reason is recorded.
      state.compositionFallback = true;
      state.failureCodes.push(
        `written_answer_rejected:${draft.proseRejectedReason}`,
      );
    }
    if (draft.modelFailed) {
      state.compositionFallback = true;
      state.failureCodes.push("composition_model_failure");
      state.derived.unavailableData.push({
        source: "composition",
        reason: "model_error",
        retryable: true,
      });
    } else if (draft.invalidModelResult) {
      state.compositionFallback = true;
      state.failureCodes.push("composition_invalid_model_result");
      state.derived.unavailableData.push({
        source: "composition",
        reason: "invalid_model_result",
        retryable: false,
      });
    }
    addTrace(
      state.trace,
      "compose_grounded_answer",
      draft.modelFailed || draft.invalidModelResult ? "fallback" : "completed",
      elapsed(this.clock, startedAt),
      draft.modelAttempted
        ? "bounded grounded fact composition attempted"
        : "deterministic composition selected",
    );

    startedAt = this.clock();
    const validated = validateGroundingNode({
      draft,
      receipts: state.toolExecution.receipts,
    });
    state.validatedMessage = validated.message;
    if (validated.removedUnsupportedClaims > 0) {
      state.failureCodes.push("unsupported_model_claim_removed");
    }
    if (validated.usedDeterministicFallback && draft.modelAttempted) {
      state.compositionFallback = true;
    }
    addTrace(
      state.trace,
      "validate_grounding",
      validated.removedUnsupportedClaims > 0 ? "fallback" : "completed",
      elapsed(this.clock, startedAt),
      `${validated.removedUnsupportedClaims} unsupported model fact selections removed`,
    );

    startedAt = this.clock();
    const unavailableData = deduplicateUnavailable(
      state.derived.unavailableData,
    );
    const supportRecommended =
      unavailableData.length > 0 ||
      state.classification.requestType === "unsupported_or_out_of_scope" ||
      state.derived.officialHolds.length > 0 ||
      state.derived.derivedBlockers.length > 0;
    const suggestedActions = [...state.derived.suggestedActions];
    if (
      supportRecommended &&
      (state.classification.requestType.startsWith("aid_") ||
        state.classification.requirementReference ===
          "sensitive_financial_data" ||
        state.classification.requirementReference ===
          "financial_aid_write_unavailable") &&
      !suggestedActions.some((action) => action.href === "/appointments")
    ) {
      suggestedActions.push({
        label: "Schedule financial-aid help",
        href: "/appointments",
        readOnly: true,
      });
    } else if (
      supportRecommended &&
      !suggestedActions.some((action) => action.href === "/help")
    ) {
      suggestedActions.push({
        label: "View support options",
        href: "/help",
        readOnly: true,
      });
    }
    const failureCodes = [
      ...new Set([
        ...state.failureCodes,
        ...unavailableData.map(
          (item) => `unavailable:${item.source}:${item.reason}`,
        ),
        ...(state.classification.requestType === "unsupported_or_out_of_scope"
          ? ["unsupported_or_out_of_scope"]
          : []),
      ]),
    ];
    addTrace(
      state.trace,
      "finalize_response",
      failureCodes.length > 0 ? "fallback" : "completed",
      elapsed(this.clock, startedAt),
      `${failureCodes.length} bounded failure codes`,
    );

    return {
      message: state.validatedMessage,
      blocks: buildResponseBlocks({
        classification: state.classification,
        derived: state.derived,
        message: state.validatedMessage,
        question: state.normalized.text,
        authored: draft.proseAnswer !== null && !validated.usedDeterministicFallback,
      }),
      requestType: state.classification.requestType,
      requestTypes: [
        state.classification.requestType,
        ...additionalClassifications.map((item) => item.requestType),
      ],
      requestedEntity: state.classification.requestedEntity,
      financialAidEntity: state.classification.financialAidEntity,
      housingEntity: state.classification.housingEntity,
      deadlineScope: state.classification.deadlineScope,
      matchedDeadlines: state.derived.deadlines,
      blockerTarget: state.classification.blockerTarget,
      completedSteps: state.derived.completedSteps,
      remainingSteps: state.derived.remainingSteps,
      awaitingReviewSteps: state.derived.awaitingReviewSteps,
      documentStates: state.derived.documentStates,
      blockedSteps: state.derived.blockedSteps,
      officialHolds: state.derived.officialHolds,
      derivedBlockers: state.derived.derivedBlockers,
      incompleteNonBlockingRequirements:
        state.derived.incompleteNonBlockingRequirements,
      nonBlockingActions: state.derived.nonBlockingActions,
      missingDocuments: state.derived.missingDocuments,
      deadlines: state.derived.deadlines,
      prioritizedAction: state.derived.prioritizedAction,
      priorityReasonCode:
        state.derived.prioritizedAction?.reasonCode ?? null,
      priorityEvidence: state.derived.priorityEvidence,
      registrationEligibility: state.derived.registrationEligibility,
      capabilitySummary: state.derived.capabilitySummary,
      supportOptions: state.derived.supportOptions,
      financialAid: state.derived.financialAid,
      housing: state.derived.housing,
      contextReceipts: state.toolExecution.receipts,
      suggestedActions,
      unavailableData,
      safeFailure:
        failureCodes.length > 0
          ? {
              partial:
                unavailableData.length > 0 &&
                state.toolExecution.receipts.some(
                  (receipt) => receipt.status === "available",
                ),
              classificationFallback: state.classificationFallback,
              compositionFallback: state.compositionFallback,
              supportRecommended,
              codes: failureCodes,
            }
          : null,
      graphExecution: {
        graphVersion: "student-onboarding-v1",
        durationMs: elapsed(this.clock, graphStartedAt),
        toolSelectionSource,
        selectedTools: [...state.selectedTools],
        executedTools: [...state.toolExecution.executedTools],
        trace: state.trace,
      },
    };
  }

  private async classifyWithModel(
    normalized: NonNullable<ReturnType<typeof normalizeRequestNode>>,
  ): Promise<RequestClassification | null> {
    if (!this.model || !normalized.text) return null;
    try {
      const result: unknown = await this.model.classifyRequest({
        normalizedMessage: normalized.resolvedText,
        conversationContext: normalized.history.map((message) => ({
          ...message,
          trust: "untrusted_conversation_text" as const,
        })),
        pageContext: {
          path: normalized.pagePath,
          label: normalized.pageLabel,
        },
        allowedRequestTypes: studentAssistantRequestTypes,
      });
      return validateModelClassification(result);
    } catch {
      return null;
    }
  }

  private async planWithModel(
    normalized: NonNullable<ReturnType<typeof normalizeRequestNode>>,
  ): Promise<ValidatedModelToolPlan | null> {
    if (!this.model?.planToolReads || !normalized.text) return null;
    try {
      const result: unknown = await this.model.planToolReads({
        normalizedMessage: normalized.resolvedText,
        conversationContext: normalized.history.map((message) => ({
          ...message,
          trust: "untrusted_conversation_text" as const,
        })),
        pageContext: {
          path: normalized.pagePath,
          label: normalized.pageLabel,
        },
        allowedRequestTypes: studentAssistantRequestTypes,
        availableTools: studentAssistantToolCatalog.map((tool) => ({ ...tool })),
      });
      return validateModelToolPlan(result);
    } catch {
      return null;
    }
  }
}

export function createStudentAssistantGraph(
  options: StudentAssistantGraphOptions,
): StudentAssistantGraph {
  return new StudentAssistantGraph(options);
}

async function runPolicySearch(
  tools: StudentAssistantTools,
  context: StudentToolContext,
  topic: string,
  receiptSequence: number,
  timeoutMs: number,
): Promise<ExecutedToolRead<ApprovedPolicySearchRead>> {
  const receiptId = `receipt-${receiptSequence}-searchApprovedPolicies`;
  const unavailable = (
    reason: "not_configured" | "timeout" | "upstream_error",
  ): ExecutedToolRead<ApprovedPolicySearchRead> => ({
    result: { status: "unavailable", reason, retryable: reason !== "not_configured" },
    receipt: {
      id: receiptId,
      source: "searchApprovedPolicies",
      status: "unavailable",
      observedAt: null,
      sourceVersion: null,
      recordCount: 0,
    },
  });
  if (!tools.searchApprovedPolicies) return unavailable("not_configured");
  try {
    const result = await Promise.race([
      tools.searchApprovedPolicies(context, { topic: topic.slice(0, 400) }),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), timeoutMs)),
    ]);
    if (!result) return unavailable("timeout");
    return {
      result,
      receipt: {
        id: receiptId,
        source: "searchApprovedPolicies",
        status: result.status === "available" ? "available" : "unavailable",
        observedAt: result.status === "available" ? result.observedAt : null,
        sourceVersion:
          result.status === "available" ? (result.sourceVersion ?? null) : null,
        recordCount:
          result.status === "available" ? result.data.matches.length : 0,
      },
    };
  } catch {
    return unavailable("upstream_error");
  }
}

function trustedToolContext(input: StudentAssistantInput): StudentToolContext {
  return {
    tenantId: input.context.tenantId,
    studentId: input.context.studentId,
    conversationId: input.context.conversationId,
    inputMode: input.context.inputMode,
  };
}

function safeFallbackClassification(): RequestClassification {
  return {
    requestType: "unsupported_or_out_of_scope",
    confidence: 0,
    source: "safe_fallback",
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
}

function elapsed(clock: () => number, startedAt: number): number {
  return Math.max(0, Math.round(clock() - startedAt));
}

function addTrace(
  trace: StudentAssistantGraphTraceEntry[],
  node: StudentAssistantGraphNodeName,
  status: StudentAssistantGraphTraceEntry["status"],
  durationMs: number,
  summary: string,
): void {
  trace.push({ node, status, durationMs, summary });
}

function deduplicateUnavailable(
  values: readonly StudentAssistantUnavailableData[],
): StudentAssistantUnavailableData[] {
  const seen = new Set<string>();
  return values.filter((value) => {
    const key = `${value.source}:${value.reason}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
