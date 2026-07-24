# Agentic runtime: Edward and document extraction

This implementation keeps the LLM behind a server-side adapter. The browser
never receives the OpenRouter key, selects a model, or writes extracted facts
directly to the student record.

## Runtime boundaries

```text
Student browser
  |
  +-- asks Edward --------------------------+
  |                                        |
  +-- uploads PDF/JPEG/PNG                  |
           |                               |
           v                               v
     Preview API                    bounded context builder
           |                               |
           +-- stores original             |
           +-- calculates SHA-256           |
           +-- OpenRouter gateway <---------+
           |       |
           |       +-- file-parser for PDF
           |       +-- strict JSON schema
           |       +-- token usage response
           |
           v
     extraction review
           |
           +-- student selects accepted fields
           +-- record moves to staff review
```

The current local adapter persists metadata in
`tools/demo-api/.data/state.json` and originals in
`tools/demo-api/.data/uploads`. Both are ignored by Git. The domain and HTTP
contracts keep those details out of the browser response.

## Edward

Edward receives a compact portal projection, not an unrestricted database
dump. The projection includes the preferred name, program and term, onboarding
status, enrollment completion, the next action, unread-message count, and
document statuses. It excludes contact details, document contents, payment
details, government identifiers, audit logs, and staff-only notes.

Controls:

- At most six recent chat messages are forwarded.
- User and history text have hard character bounds.
- Replies have a 420-token ceiling and a 140-word instruction.
- The model may explain or navigate, but may not approve, submit, pay, or mutate
  a record.
- Suggested actions are server-generated allowlisted portal routes.
- The response exposes prompt, completion, and total token counts for cost
  monitoring.
- With no API key, a deterministic guided mode answers common navigation
  questions with zero LLM tokens.

## Document extraction

The upload endpoint accepts PDF, JPEG, and PNG files up to 10 MB. It:

1. validates the multipart request and file category;
2. stores the original under an opaque server-generated key;
3. calculates a SHA-256 digest;
4. sends the file to the configured OpenRouter model;
5. requires a strict structured-output schema;
6. normalizes field counts and lengths;
7. redacts values shaped like SSNs or payment-card numbers;
8. returns a `needs_review` document rather than updating the profile; and
9. records only the fields explicitly accepted by the student for staff review.

Failures do not destroy the upload. The original remains available and the
document carries a retryable extraction status and warning.

## Production migration

The local flow is deliberately synchronous so the current portal is immediately
functional. The production adapter should keep the API contracts but move the
heavy step behind the transactional outbox:

```text
upload API -> object storage -> document row + outbox event -> worker
  -> malware scan -> text/OCR parse -> LLM extraction -> review projection
```

Replace:

- JSON state with PostgreSQL repositories;
- the local upload directory with S3-compatible object storage;
- synchronous parsing with a queue/outbox worker;
- the demo cookie with institutional OIDC;
- console metrics with OpenTelemetry and cost dashboards.

The Kubernetes deployment then scales web/API pods separately from extraction
workers. Worker concurrency, per-student rate limits, model allowlists, document
size limits, timeouts, and retry budgets become configuration rather than
frontend logic.

## Required environment variables

```text
OPENROUTER_API_KEY=                 # server only
OPENROUTER_MODEL=openai/gpt-4o-mini
OPENROUTER_APP_URL=http://localhost:3000
OPENROUTER_APP_NAME=Aster Student Portal
DOCUMENT_UPLOAD_DIR=./tools/demo-api/.data/uploads
```

The default model is only a configurable starting point. Production should pin
an approved model after accuracy, privacy, latency, and cost evaluation on a
representative redacted document set.
