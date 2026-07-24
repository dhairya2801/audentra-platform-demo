import { isSpanContextValid, trace } from "@opentelemetry/api";
import { findStateEffectByEvent } from "@vv/state-effects";

const effectIdByAuditAction: Readonly<Record<string, string>> = {
  "admission_offer.accepted": "admissions.acceptOffer",
  "student_onboarding.step_completed": "onboarding.saveStep",
  "student_onboarding.completed": "onboarding.complete",
  "document.upload_reserved": "documents.reserveUpload",
  "document.extraction_completed": "documents.completeExtraction",
  "document.extraction_confirmed": "academics.confirmTranscriptExtraction",
  "payment.deposit_succeeded": "financials.recordDeposit",
  "student_financial.payment_plan_selected": "financials.selectPaymentPlan",
  "student_profile.updated": "studentProfile.update",
};

export interface RuntimeLineage {
  correlationId: string;
  effectRegistryVersion: 1;
  effectId?: string;
  traceId?: string;
  spanId?: string;
}

export function getRuntimeLineage(input: {
  correlationId: string;
  eventName?: string;
  auditAction?: string;
}): RuntimeLineage {
  const spanContext = trace.getActiveSpan()?.spanContext();
  const eventEffect = input.eventName
    ? findStateEffectByEvent(input.eventName)
    : undefined;
  const effectId =
    eventEffect?.id ??
    (input.auditAction
      ? effectIdByAuditAction[input.auditAction]
      : undefined);
  return {
    correlationId: input.correlationId,
    effectRegistryVersion: 1,
    ...(effectId ? { effectId } : {}),
    ...(spanContext && isSpanContextValid(spanContext)
      ? {
          traceId: spanContext.traceId,
          spanId: spanContext.spanId,
        }
      : {}),
  };
}
