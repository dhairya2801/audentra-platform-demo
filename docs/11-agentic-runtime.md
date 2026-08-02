# Agentic runtime: Edward and document extraction

This implementation keeps every LLM behind a server-side Python adapter. The
browser never receives provider credentials, selects a model, or writes
extracted facts directly to the student record.

## Runtime boundaries

```text
Student browser
  |
  +-- asks Edward --------------------------+
  |                                        |
  +-- uploads PDF/JPEG/PNG                  |
           |                               |
           v                               v
     FastAPI adapter                bounded context builder
           |                               |
           +-- reserves metadata            |
           +-- stores immutable original    |
           +-- selects server-owned policy  |
           |       +-- staff review --------+-- no model call
           |       +-- type classification -+-- fields discarded
           |       +-- agentic extraction
           +-- Python/PyMuPDF processing adapter
           |       +-- bounded text extraction
           |       +-- bounded rendered page images
           |       |
           +-- configured AI gateway <------+
           |       +-- OpenRouter or Groq
           |       +-- bounded text + page images
           |       +-- JSON-only text extraction
           |       +-- local normalization and validation
           |       +-- token usage response
           |
           v
     extraction review
           |
           +-- student selects accepted fields
           +-- record moves to staff review
```

The development-only no-Docker preview adapter persists metadata in
`tools/demo-api/.data/state.json` and originals in
`tools/demo-api/.data/uploads`; both are ignored by Git. The production-oriented
FastAPI composition uses PostgreSQL for metadata and MinIO/managed object
storage through the S3 API for originals. A 42-operation compatibility set is
covered by parity tests. The preview still exposes additional development
routes, so closing or explicitly versioning that gap is a release gate.

## Edward

Edward receives a compact portal projection, not an unrestricted database
dump. The projection includes the preferred name, program and term, onboarding
status, enrollment completion, the next action, unread-message count, and
document statuses. It excludes contact details, document contents, payment
details, government identifiers, audit logs, and staff-only notes.

Controls:

- Edward has no shell, Python runtime, filesystem, raw SQL, secret store, or
  arbitrary-network tool. Requests to execute code, extract secrets, bypass
  authority, forge record state, or access another student are rejected before
  context collection and before a model call.
- At most six recent chat messages are forwarded.
- User and history text have hard character bounds. Browser-supplied history is
  forwarded only as quoted, untrusted user context—never as assistant or system
  instruction.
- Replies have a 420-token ceiling and a 140-word instruction.
- The model may explain or navigate, but may not approve, submit, pay, or mutate
  a record.
- Suggested actions are server-generated allowlisted portal routes.
- Provider prose is normalized again at the API boundary to remove active
  markup, unsafe URI schemes, and model-invented links.
- A deposit widget is accepted only for explicit payment intent and is rebuilt
  from the signed-in student's authoritative offer ID, deposit amount, and
  payment status. The provider cannot select or mark a payment.
- A deterministic intent router handles known navigation, document, profile,
  appointment, and deposit intents with zero LLM tokens; open questions still
  receive the bounded model context.
- The API returns `contextReceipts` only for deterministic projections it
  successfully collected for the reply. They are not model tool-call claims or
  intent-regex matches.
- The response exposes prompt, completion, and total token counts for cost
  monitoring.
- With no API key, a deterministic guided mode answers common navigation
  questions with zero LLM tokens.

## Document extraction

The upload endpoint accepts PDF, JPEG, and PNG files up to 10 MB. It:

1. validates the multipart request and optional requirement context;
2. calculates a SHA-256 digest and reserves an idempotent document row linked
   to that requirement;
3. stores the original under an opaque server-generated object key before any
   parsing work can begin;
4. derives an authoritative processing mode from the server-side requirement:
   transcript and identity documents use `agentic`, financial-aid documents
   use `classification_only`, and immunization documents use `manual_review`;
   classification-only results retain the type, summary, warnings, and provider
   metadata while discarding every extracted student/financial field;
5. for agentic and classification-only documents, atomically claims processing
   so replays do not spend LLM tokens twice;
6. for PDFs, invokes the Python/PyMuPDF processing adapter directly; bounded
   text extraction and page rendering run through `asyncio.to_thread` so CPU
   and blocking library work do not stall the ASGI event loop; transcript PDFs
   render every supported page (up to eight) as an independent 2,048px,
   quality-88 JPEG, while the images remain ephemeral and are not stored in the
   CRM;
7. first applies a conservative local heading check for unmistakable document
   mismatches (for example a FERPA release uploaded to the transcript task),
   preserving the original and keeping the requirement incomplete without an
   LLM call;
8. selects the transcript provider using `TRANSCRIPT_PARSING`; both
   `openrouter` and `groq` send one rendered page image plus that page's
   bounded text per independent multimodal request, then conservatively merge
   every page result; identity documents use the OpenRouter multimodal path;
9. treats the document text and page images as untrusted evidence, never as
   executable instructions, and requires JSON-only text output;
10. records the exact provider response or transport failure in a durable,
    correlated extraction-attempt journal before parsing it; recorder failures
    are isolated from the safety decision and surfaced operationally;
11. locally normalizes field counts and lengths, then rejects empty structured
    results;
12. for identity documents, accepts an optional `profile_photo` page region as
    normalized coordinates, validates its bounds locally, and serves a
    server-cropped JPEG preview without exposing the original object key;
13. redacts values shaped like SSNs or payment-card numbers;
14. classifies from content and refuses to advance a mismatched requirement;
15. returns a `needs_review` document rather than updating the profile;
16. sends a correctly classified financial-aid document to staff review
    without showing a student field-confirmation step; and
17. records only the fields explicitly accepted by the student for full
    agentic categories.

Model names and bounds are runtime configuration. The checked-in development
defaults are `openai/gpt-4o-mini` for OpenRouter and `qwen/qwen3.6-27b` for
Groq. Only `TRANSCRIPT_PARSING=groq` selects Groq for transcripts; any other
value selects OpenRouter. Both transcript paths receive bounded text and
rendered page evidence, and identity documents use the OpenRouter multimodal
path. The current Groq defaults bound output to 1,400 tokens, input text to
40,000 characters, and reasoning effort to `none`. The request does not rely
on a provider-specific PDF-file capability or tool-call endpoint. Production
must pin evaluated providers/models and retain the same safe retry and human
review boundary.

### Failure and retry semantics

Failures do not destroy the upload. The original remains available and a
failed extraction preserves only a safe `failureCode` (`provider_unavailable`,
`unsupported_capability`, `invalid_response`, `timeout`, or `unknown`) plus a
`retryable` flag. The exact provider payload is never copied into the
student-facing document record or shown to the browser. It is retained
separately in the development `aiProviderResponses` attempt journal or the
PostgreSQL `ai_provider_response_attempt` table with document, request,
attempt, model,
provider (`openrouter` or `groq`), HTTP, finish-reason, usage, duration, and
transport correlation.

A completed response with no useful structured result is treated as a
needs-attention failure rather than a successful parse. An explicit
classification that disagrees with the server-owned requirement type is itself
useful: it is stored as a warning result and the requirement remains
incomplete. The API makes at most one immediate automatic retry for clearly
transient provider outcomes (for example a timeout, rate limit, 5xx response,
or an empty completion). It does not retry unsupported capabilities or
malformed requests blindly.

The student can retry a retryable failure without re-uploading through
`POST /v1/student/documents/:id/retry-extraction` with an
`Idempotency-Key`. `documents.retryExtraction` atomically queues the already
stored original and publishes `document.extraction_requested.v1`; the worker
then invokes `documents.processQueuedExtraction`. A replay after a terminal
result returns the same document without another model call. The retry-start
audit/outbox fact, extraction-request fact, completion fact, OpenTelemetry
span, and response correlation ID provide the runtime lineage for an
individual attempt.

An earlier `document.upload_reserved.v1` event is also consumed by the worker
as a reconciler. If an API pod stops after object storage succeeds but before
the queue transaction commits, the worker verifies the immutable original and
queues it. If storage has not completed, the event retries; it never starts a
parser request against a missing original.

## Production migration

The preview and full-stack flows use the same durability rule: they return as
soon as the document record and immutable original are safe. The PostgreSQL
implementation then writes an atomic processing claim and
`document.extraction_requested.v1` outbox event; the worker performs the heavy
step after that transaction commits:

```text
upload API -> document reservation -> object storage -> processing row + outbox event -> worker
  -> bounded text extraction + page render -> LLM extraction
  -> review projection
```

The remaining production substitutions are:

- the local PyMuPDF dependency to an approved, sandboxed renderer after legal
  review of its AGPL/commercial licensing terms;
- the demo identity adapter to institutional OIDC;
- development MinIO to managed S3-compatible storage;
- console metrics to OpenTelemetry and cost dashboards.

The backend Kubernetes deployment scales FastAPI and extraction-worker pods
independently from the separately deployed portals. Worker concurrency,
per-student rate limits, model allowlists, document size limits, timeouts, and
retry budgets remain server configuration rather than frontend logic.

## Relevant environment variables

```text
AUDENTRA_ENV=development             # production fails closed with demo auth
AUTH_MODE=demo
DATABASE_URL=postgresql://user:password@localhost:5432/audentra

OBJECT_STORAGE_ENDPOINT=http://localhost:9000
OBJECT_STORAGE_REGION=us-east-1
OBJECT_STORAGE_BUCKET=audentra-documents
OBJECT_STORAGE_ACCESS_KEY=replace-me
OBJECT_STORAGE_SECRET_KEY=replace-me
OBJECT_STORAGE_FORCE_PATH_STYLE=true
MAX_DOCUMENT_BYTES=10485760

OPENROUTER_API_KEY=                  # optional, server only
OPENROUTER_MODEL=openai/gpt-4o-mini
OPENROUTER_APP_URL=http://localhost:3000
OPENROUTER_APP_NAME=Audentra Student Portal
OPENROUTER_DOCUMENT_TIMEOUT_MS=120000
OPENROUTER_DOCUMENT_MAX_TOKENS=6000
OPENROUTER_DOCUMENT_REASONING_TOKENS=256
TRANSCRIPT_PARSING=openrouter         # exact "groq" selects Groq
GROQ_API_KEY=                         # optional, server only
GROQ_MODEL=qwen/qwen3.6-27b
GROQ_TRANSCRIPT_TIMEOUT_MS=60000
GROQ_TRANSCRIPT_MAX_TOKENS=1400
GROQ_TRANSCRIPT_MAX_TEXT_CHARACTERS=40000
GROQ_TRANSCRIPT_REASONING_EFFORT=none

API_INTERNAL_URL=http://localhost:4000
DOCUMENT_WORKER_TOKEN=replace-with-a-long-shared-secret
WORKER_POLL_INTERVAL_SECONDS=1
WORKER_BATCH_SIZE=20
WORKER_LEASE_SECONDS=60
WORKER_MAX_ATTEMPTS=10
```

These model defaults are configurable starting points. Production should pin
approved models after accuracy, privacy, latency, and cost evaluation on a
representative redacted document set. Production also requires a real identity
adapter; the application rejects `AUDENTRA_ENV=production` while
`AUTH_MODE=demo` is selected.
