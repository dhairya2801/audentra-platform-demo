# Stateful local preview API

This is a development-only HTTP backend for exercising the student portal
without PostgreSQL or external services. It uses Node.js built-ins only and
stores fictional fixture state in `.data/state.json`.

It is not a production API, authentication provider, document store, or
payment processor. The local sign-in endpoint issues an HTTP-only demo cookie;
protected student endpoints reject requests without it. The sign-out endpoint
invalidates that demo session. Do not enter real personal, institutional,
document, or payment data into this fixture.

## Run

From this directory:

```sh
npm start
```

The service listens on `http://localhost:4000` by default. The paired web
preview exposes Aster under `/aster/*` and Harvard under `/harvard/*`; the web
runtime sends the resolved tenant slug to the preview API. Override settings
with:

```sh
DEMO_API_PORT=4100 \
DEMO_API_ORIGINS=http://localhost:3000 \
DEMO_API_DATA_FILE=/tmp/vv-demo-state.json \
npm start
```

Development mode restarts the process when source files change:

```sh
npm run dev
```

Reset the fixture while the server is stopped:

```sh
npm run reset
```

The reset is deterministic. It returns the fictional student Alex Morgan to an
unaccepted offer and clears journeys, requirements, documents, appointments,
payments, help requests, activities, and idempotency records.

## Onboarding sequence

Progress is persisted and strictly ordered:

1. `offer`
2. `about_you`
3. `housing`
4. `campus_life`
5. `emergency_contacts`
6. `family_permissions`
7. `review_and_sign`
8. `deposit`

Each step is completed with `PUT /v1/student/onboarding` using
`{ "expectedVersion": 1, "currentStep": "offer", "data": {} }`. `PATCH`
is supported as a local compatibility alias with the same body.

Offer acceptance and deposit payment create their domain records, but do not
silently change onboarding. The versioned `PUT` for `offer` verifies that the
offer was accepted. The deposit step records `pay_now`, `pay_later`, or
`waiver_or_deferral`; only `pay_now` requires a successful payment. Campus life
and deposit may be skipped for now. Offer acceptance, about-you details, the
top-level housing path, emergency contacts, and review/sign are required. Once
all eight steps have been saved, `POST /v1/student/onboarding/complete` with the latest
`expectedVersion` finalizes onboarding. Bootstrap then changes
`initialRoute` from `/onboarding` to `/dashboard`.

## Endpoints

Offer acceptance, final onboarding completion, document creation, appointment
creation, and deposit payment require an `Idempotency-Key` header containing
8-128 safe characters. Versioned onboarding and profile updates use
`expectedVersion` instead. Marking a message read is naturally idempotent and
does not require the header.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/health`, `/health/ready` | Process and fixture readiness |
| GET | `/v1/demo/fixture-state` | Safe fixture summary |
| POST | `/v1/auth/demo/sign-in`, `/v1/auth/demo/sign-out` | Demo cookie |
| GET | `/v1/auth/session` | Current demo session |
| GET | `/v1/student/bootstrap` | Canonical auth, onboarding, and initial route |
| GET/PUT | `/v1/student/onboarding` | Read or complete the current versioned step |
| POST | `/v1/student/onboarding/complete` | Finalize all completed steps |
| GET | `/v1/student/dashboard` | Student portal dashboard contract |
| POST | `/v1/admission-offers/:offerId/accept` | Accept offer and create journey |
| POST | `/v1/activity-events/batch` | Validated, deduplicated activity events |
| GET | `/v1/student/requirements` | Requirement summaries |
| GET | `/v1/student/requirements/:requirementId` | Requirement detail |
| GET | `/v1/student/messages` | Fictional enrollment messages |
| POST | `/v1/student/messages/:messageId/read` | Mark message read |
| GET | `/v1/student/documents` | Student document records and extraction state |
| POST | `/v1/student/documents/upload` | Strict single-file PDF/JPEG/PNG upload |
| POST | `/v1/student/documents/:id/retry-extraction` | Retry the stored original |
| GET/POST | `/v1/student/appointments` | Enrollment appointments |
| GET | `/v1/student/payments` | Simulated payment records |
| POST | `/v1/student/payments/deposit` | Simulated deposit; no card data |
| GET/PATCH | `/v1/student/profile` | Safe fictional profile fields |
| GET | `/v1/student/help` | Help articles and support contact |
| POST | `/v1/demo/help-requests` | Optional local-only help request fixture |

Uploads store the original before extraction begins, reject MIME/signature
mismatches, and lock competing uploads for the same requirement while parsing
is active. Processing leases expire into a retryable failure so clients do not
poll indefinitely. Payment endpoints reject payment details and record only a
dummy processor result. Profile fields are fictional local fixture data; do
not enter real contact information. Activity event properties reject email,
phone, address, government identifiers, payment details, and other sensitive
keys.

## Test

```sh
npm test
```

The tests use temporary state files and ephemeral ports. They cover atomic
persistence, concurrent mutation serialization, reset behavior, onboarding
ordering, idempotency replay/conflicts, primary enrollment flows, validation,
activity deduplication, request IDs, and demo authentication cookies.
