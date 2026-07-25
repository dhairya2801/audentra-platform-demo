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
     Portal API                     bounded context builder
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

The no-Docker preview adapter persists metadata in
`tools/demo-api/.data/state.json` and originals in
`tools/demo-api/.data/uploads`; both are ignored by Git. The complete Compose
stack uses PostgreSQL for metadata and MinIO through the S3 API for originals.
Both adapters implement the same browser-facing contract.

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

1. validates the multipart request and optional requirement context;
2. calculates a SHA-256 digest and reserves an idempotent document row linked
   to that requirement;
3. atomically claims parsing so replays do not spend LLM tokens twice;
4. stores the original under an opaque server-generated object key;
5. sends the file to the configured OpenRouter model, with the requirement type
   as a non-authoritative hint;
6. requires a strict structured-output schema and uses response healing for
   malformed JSON;
7. normalizes field counts and lengths;
8. redacts values shaped like SSNs or payment-card numbers;
9. classifies from content and refuses to advance a mismatched requirement;
10. returns a `needs_review` document rather than updating the profile; and
11. records only the fields explicitly accepted by the student for staff
    review.

Failures do not destroy the upload. The original remains available and the
document carries a retryable extraction status and warning.

## Production migration

The current preview and full-stack flows are deliberately synchronous so the
portal is immediately functional and returns the review result in one request.
The PostgreSQL implementation already writes audit and outbox records, uses an
atomic processing claim, and stores originals in S3-compatible object storage.
At higher volume, keep the API contract and move the heavy step behind the
transactional outbox:

```text
upload API -> object storage -> document row + outbox event -> worker
  -> malware scan -> text/OCR parse -> LLM extraction -> review projection
```

The remaining production substitutions are:

- synchronous parsing to a queue/outbox worker;
- the demo identity adapter to institutional OIDC;
- development MinIO to managed S3-compatible storage;
- console metrics to OpenTelemetry and cost dashboards.

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
OBJECT_STORAGE_ENDPOINT=http://localhost:9000
OBJECT_STORAGE_BUCKET=vv-documents
OBJECT_STORAGE_ACCESS_KEY=vv_minio
OBJECT_STORAGE_SECRET_KEY=vv_minio_password
```

The default model is only a configurable starting point. Production should pin
an approved model after accuracy, privacy, latency, and cost evaluation on a
representative redacted document set.
