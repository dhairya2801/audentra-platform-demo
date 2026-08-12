-- Preserve distinct AI diagnostics for different operations while keeping
-- exact provider-delivery replays idempotent.

DROP INDEX ai_provider_response_attempt_delivery_uidx;

CREATE UNIQUE INDEX ai_provider_response_attempt_delivery_uidx
  ON ai_provider_response_attempt(
    tenant_id,
    document_id,
    request_id,
    operation,
    attempt_number
  )
  WHERE document_id IS NOT NULL;
