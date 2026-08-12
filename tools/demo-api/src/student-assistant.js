import {
  createStudentAssistantGraph,
} from "@vv/student-assistant-core";
import {
  createPreviewStudentAssistantTools,
  trustedStudentAssistantIdentity,
} from "./student-assistant-tools.js";
import { normalizeEdwardResponse } from "./edward-safety.js";
import {
  emitVoiceEvent,
  observeVoiceLatency,
} from "./voice-observability.js";

const receiptSourceByTool = {
  getStudentProfile: "profile",
  getOnboardingChecklist: "onboarding",
  getDocumentStatuses: "documents",
  getEnrollmentHolds: "holds",
  getStudentDeadlines: "deadlines",
  getFinancialAidStatus: "financials",
  getFinancialAidSupportOptions: "financials",
  retrieveApprovedFinancialAidPolicy: "policies",
  retrieveApprovedPolicy: "policies",
  searchApprovedPolicies: "policies",
  getFinancialAidSummary: "financial_aid",
  getAidDisbursements: "financial_aid",
  getStudentHousingStatus: "housing",
  getStudentHousingEligibility: "housing",
  getHousingOptions: "housing",
  getRegistrationStatus: "registration",
  getStudentAccountSummary: "account",
  getAcademicCalendar: "deadlines",
  getStudentAppointments: "appointments",
};

/**
 * Runs the shared graph for a trusted, already-persisted assistant turn.
 * Unsupported general Edward questions fall through to the existing preview
 * gateway, while graph-recognized and mutation requests always use this path.
 */
export async function runPreviewStudentAssistant({
  store,
  ai,
  clock,
  requestId,
  conversationId,
  message,
  history,
  priorPriority,
  inputMode,
  pageContext,
  observability,
  logger,
}) {
  const identity = trustedStudentAssistantIdentity(store);
  if (!identity) {
    return { handled: false, response: null, graphResponse: null };
  }
  if (belongsToLegacyEdward(message, pageContext?.path ?? "")) {
    return { handled: false, response: null, graphResponse: null };
  }
  const boundModel = bindPreviewStudentAssistantModel({
    ai,
    identity,
    requestId,
  });
  const context = {
    ...identity,
    conversationId,
    inputMode,
    history,
    pageContext,
    priorPriority: priorPriority ?? null,
  };
  const graphVersion = "student-onboarding-v1";
  emitVoiceEvent(logger, clock, "graph_started", {
    requestId,
    conversationId,
    voiceSessionId: observability?.voiceSessionId,
    clientMessageId: observability?.clientMessageId,
    userMessageId: observability?.userMessageId,
    streamId: observability?.streamId,
    graphVersion,
    inputMode,
  });
  const graphResponse = await createStudentAssistantGraph({
    tools: createPreviewStudentAssistantTools({ store, clock }),
    model: boundModel.model,
    now: clock,
    institutionalTimeZone: "UTC",
  }).execute({ message, context });
  emitVoiceEvent(logger, clock, "graph_completed", {
    requestId,
    conversationId,
    voiceSessionId: observability?.voiceSessionId,
    clientMessageId: observability?.clientMessageId,
    userMessageId: observability?.userMessageId,
    streamId: observability?.streamId,
    graphVersion: graphResponse.graphExecution.graphVersion,
    inputMode,
    durationMs: graphResponse.graphExecution.durationMs,
    selectedTools: graphResponse.graphExecution.selectedTools,
    executedTools: graphResponse.graphExecution.executedTools,
    nodeDurations: Object.fromEntries(
      graphResponse.graphExecution.trace.map((entry) => [
        entry.node,
        entry.durationMs,
      ]),
    ),
    safeFallback: graphResponse.safeFailure !== null,
    deadlineBucketCounts:
      graphResponse.capabilitySummary.deadlineBucketCounts,
    officialHoldCount: graphResponse.capabilitySummary.officialHoldCount,
    derivedBlockerCount: graphResponse.capabilitySummary.derivedBlockerCount,
    priorityDerivationResultCode:
      graphResponse.capabilitySummary.priorityDerivationResultCode,
    unavailableDeadlineOrHoldSourceCount:
      graphResponse.capabilitySummary.unavailableDeadlineOrHoldSourceCount,
    financialAidRequestType: graphResponse.requestType.startsWith("aid_")
      ? graphResponse.requestType
      : null,
    financialAidCompletedCount:
      graphResponse.capabilitySummary.financialAidCompletedCount,
    financialAidRemainingCount:
      graphResponse.capabilitySummary.financialAidRemainingCount,
    financialAidMissingDocumentCount:
      graphResponse.capabilitySummary.financialAidMissingDocumentCount,
    financialAidVerificationState:
      graphResponse.capabilitySummary.financialAidVerificationState,
    financialAidUnavailableSourceCount:
      graphResponse.capabilitySummary.financialAidUnavailableSourceCount,
    financialAidNextActionReasonCode:
      graphResponse.capabilitySummary.financialAidNextActionReasonCode,
    housingRequestType: graphResponse.requestType.startsWith("housing_")
      ? graphResponse.requestType
      : null,
    housingStateResultCode:
      graphResponse.capabilitySummary.housingStateResultCode,
    housingRemainingStepCount:
      graphResponse.capabilitySummary.housingRemainingStepCount,
    housingDeadlineCount:
      graphResponse.capabilitySummary.housingDeadlineCount,
    housingUnavailableSourceCount:
      graphResponse.capabilitySummary.housingUnavailableSourceCount,
    housingNextActionReasonCode:
      graphResponse.capabilitySummary.housingNextActionReasonCode,
  });
  observeVoiceLatency(
    logger,
    clock,
    "graph_latency_ms",
    graphResponse.graphExecution.durationMs,
    {
      requestId,
      conversationId,
      voiceSessionId: observability?.voiceSessionId,
      graphVersion: graphResponse.graphExecution.graphVersion,
      inputMode,
    },
  );
  if (graphResponse.safeFailure) {
    emitVoiceEvent(logger, clock, "recoverable_voice_error", {
      requestId,
      conversationId,
      voiceSessionId: observability?.voiceSessionId,
      clientMessageId: observability?.clientMessageId,
      userMessageId: observability?.userMessageId,
      graphVersion: graphResponse.graphExecution.graphVersion,
      inputMode,
      errorCategory: "graph_safe_fallback",
      failureCodes: graphResponse.safeFailure.codes,
    });
  }
  // The graph keeps the turn unless its own planner found nothing in scope.
  // A safety gate decision is always kept: those turns carry the exact refusal
  // wording for a write request or for shared sensitive data, and must never be
  // handed to the ungrounded legacy assistant to rephrase.
  const handled =
    graphResponse.requestType !== "unsupported_or_out_of_scope" ||
    graphResponse.graphExecution.toolSelectionSource === "safety_gate";
  return {
    handled,
    graphResponse,
    response: handled
      ? mapGraphResponseToEdward(
          graphResponse,
          boundModel.telemetry(),
          inputMode,
        )
      : null,
  };
}


/**
 * Topics the grounded graph genuinely has no read for: the course catalogue,
 * a student's academic plan, credit and exemption detail, and campus life.
 * These still belong to the legacy assistant.
 */
const legacyOnlyTopics =
  /\bcatalog(?:ue)?\b|\bmy plan\b|\bclassrooms?\b|\bclubs?\b|\bevents?\b|\bactivit(?:y|ies)\b|\borganizations?\b|social life|campus life|\bmajors?\b|\bminors?\b|\bprerequisite/i;

/**
 * Topics the graph owns outright. Checked first, because a question like
 * "why can't I register for classes" mentions classes but is a registration
 * question the graph answers from authoritative reads. A keyword list that
 * ignored this sent every such question to the ungrounded legacy assistant.
 */
const graphOwnedTopics =
  /\bregist(?:er|ration)\b|\benroll?(?:ment)?\b|\bhold\b|\bdeposit\b|\bbalance\b|\bbill(?:ing)?\b|\btuition\b|\bpay(?:ment)?\b|\bowe\b|\bhousing\b|\bresidence\b|\bdorm\b|\broommate\b|\bmove[ -]?in\b|financial[ -]?aid|\baid\b|\bfafsa\b|verification|\bdeadlines?\b|\borientation\b|\bchecklist\b|\bdocuments?\b|\btranscripts?\b|immuni[sz]ation|\bappointments?\b|\badvis(?:or|ing|er)\b|\bpolic(?:y|ies)\b|\bnext\b|\bblock(?:ed|ing)?\b|\bmissing\b/i;

function belongsToLegacyEdward(message, pagePath) {
  const text = `${message} ${pagePath}`;
  if (graphOwnedTopics.test(text)) return false;
  return legacyOnlyTopics.test(text);
}

export function bindPreviewStudentAssistantModel({
  ai,
  identity,
  requestId,
}) {
  let attempt = 0;
  let model = null;
  let provider = "guided";
  let usage = null;

  const capture = (result) => {
    model = result.model ?? model;
    provider = result.provider ?? provider;
    if (result.usage) {
      usage = {
        promptTokens:
          (usage?.promptTokens ?? 0) + result.usage.promptTokens,
        completionTokens:
          (usage?.completionTokens ?? 0) + result.usage.completionTokens,
        totalTokens: (usage?.totalTokens ?? 0) + result.usage.totalTokens,
      };
    }
    return result.output;
  };

  return {
    model: {
      async planToolReads(modelInput) {
        if (typeof ai?.planStudentAssistantToolReads !== "function") {
          throw new Error(
            "The configured preview gateway does not support tool planning",
          );
        }
        attempt += 1;
        return capture(
          await ai.planStudentAssistantToolReads({
            modelInput,
            tenantId: identity.tenantId,
            studentId: identity.studentId,
            requestId,
            attempt,
          }),
        );
      },
      // Only advertised when the gateway can actually write, so a gateway
      // without the capability keeps the deterministic composition path
      // instead of paying for a call that will always fail.
      ...(typeof ai?.writeStudentAssistantAnswer === "function" &&
      ai?.configured !== false
        ? {
            async writeGroundedAnswer(modelInput) {
              attempt += 1;
              return capture(
                await ai.writeStudentAssistantAnswer({
                  modelInput,
                  tenantId: identity.tenantId,
                  studentId: identity.studentId,
                  requestId,
                  attempt,
                }),
              );
            },
          }
        : {}),
      async classifyRequest(modelInput) {
        if (typeof ai?.classifyStudentAssistantRequest !== "function") {
          throw new Error(
            "The configured preview gateway does not support shared-core classification",
          );
        }
        attempt += 1;
        return capture(
          await ai.classifyStudentAssistantRequest({
            modelInput,
            tenantId: identity.tenantId,
            studentId: identity.studentId,
            requestId,
            attempt,
          }),
        );
      },
      async composeGroundedResponse(modelInput) {
        if (typeof ai?.composeStudentAssistantResponse !== "function") {
          throw new Error(
            "The configured preview gateway does not support shared-core composition",
          );
        }
        attempt += 1;
        return capture(
          await ai.composeStudentAssistantResponse({
            modelInput,
            tenantId: identity.tenantId,
            studentId: identity.studentId,
            requestId,
            attempt,
          }),
        );
      },
    },
    telemetry: () => ({
      provider: model ? provider : "guided",
      model,
      usage,
    }),
  };
}

function mapGraphResponseToEdward(graphResponse, telemetry, inputMode) {
  const contextReceipts = [];
  const seenReceiptSources = new Set();
  for (const receipt of graphResponse.contextReceipts) {
    if (receipt.status !== "available") continue;
    const source = receiptSourceByTool[receipt.source];
    if (!source || seenReceiptSources.has(source)) continue;
    seenReceiptSources.add(source);
    contextReceipts.push({ source });
  }
  return {
    ...normalizeEdwardResponse({
    message: graphResponse.message,
    blocks: graphResponse.blocks,
    provider: telemetry.provider,
    model: telemetry.model,
    usage: telemetry.usage,
    suggestedActions:
      inputMode === "voice"
        ? []
        : graphResponse.suggestedActions.map(({ label, href }) => ({
            label,
            href,
          })),
    contextReceipts,
      widgets: [],
    }),
    studentAssistant: structuredClone(graphResponse),
  };
}
