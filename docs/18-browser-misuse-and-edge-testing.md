# Browser misuse and edge testing

The acceptance boundary is the rendered student portal plus its real HTTP API,
durable preview store, background document processing, and enrollment
projections. The Playwright suite does not replace API or domain tests; it
proves that those layers work together from a student's browser.

## Run it

Install the pinned browser once:

```bash
npx playwright install chromium
```

Run the deterministic local acceptance suite:

```bash
npm run test:e2e
```

The harness starts an isolated portal and demo API on dedicated ports, creates
a temporary state/object-storage directory, and deletes that directory when it
stops. `E2E_WEB_PORT` and `E2E_API_PORT` can override the defaults if another
process is using them.

To exercise an already deployed synthetic preview:

```bash
E2E_BASE_URL=https://preview.example.edu npm run test:e2e
```

External runs do not enable the deterministic provider and may create synthetic
accounts or mutate the demo student's enrollment state. Never point them at a
real student environment.

## Covered browser journeys

| Journey | Misuse | Required visible result | Required domain result |
|---|---|---|---|
| Create account | malformed email | persistent field error and focus | no request/account |
| Create account | malformed phone | international-format guidance | no request/account |
| Create account | weak/mismatched password | separate actionable errors | no request/account |
| Create account | normalized duplicate email | generic duplicate warning | second account rejected |
| Sign in | unknown email or wrong password | one generic credential error | no account-existence disclosure |
| Financial-aid upload | restaurant menu PDF | warning, identified as Other | original retained; requirement stays ready |
| Financial-aid upload | FAFSA-like PDF | identified as Financial Aid | under review; model fields discarded |
| Document picker | text or empty file | per-file validation | upload request remains disabled |
| Document upload | fake PDF MIME/name with non-PDF bytes | retryable file error | request rejected before storage/parsing |
| Edward | request Python/shell execution or secret extraction | bounded refusal with no action | model is not called; no state write |
| Edward | prompt injection or hidden-prompt request | bounded refusal with no action | model is not called; no context read |
| Edward | forge a paid deposit or approved requirement | authorized-workflow guidance only | payment and enrollment state unchanged |
| Edward | request another student's records | permission-scoped refusal | no cross-student context is collected |
| Edward | user-supplied HTML/script | text is shown literally | no DOM element or script execution |
| Edward provider | active markup, unsafe URLs, or external actions | no clickable unsafe action | markup/URIs stripped at the API boundary |
| Edward provider | forged deposit ID, amount, or completed status | canonical `$500` ready widget, only for explicit payment intent | fields rebuilt from the authoritative offer; no payment until click |
| Edward composer | more than 2,000 characters | input is bounded to 2,000 | API payload remains within its contract |

Tests live under `tools/browser-e2e/specs`. Failure artifacts include a
screenshot, video, and Playwright trace under `test-results/browser-e2e`.

## Deterministic classifier versus live LLM

CI sets `VV_E2E_DOCUMENT_AI=deterministic` only inside the temporary development
server. The server refuses that flag in production. This double exercises the
real upload, storage-first guarantee, queue, extraction completion, mismatch
gate, polling, CRM projection, and rendered UI without rate limits or token
cost. It does not prove semantic model quality.

Provider contract tests separately prove that an LLM response of
`documentType: "other"` is retained as an actionable mismatch. A controlled
external preview run is the final check for the configured OpenRouter model and
actual PDF preprocessing.

## Edward adversarial boundary

Edward is not a general-purpose tool-running agent. It has no shell, Python
runtime, filesystem, secret store, raw SQL, or arbitrary HTTP client. The
browser sends a question; the application collects a fixed, permission-scoped
student projection; the model may return prose; and the server independently
creates allowlisted navigation or reconstructs typed widgets from the
student's explicit intent.

High-risk capability-escalation requests are rejected deterministically before
record collection or an LLM call. Model prose is treated as untrusted and
normalized again at the HTTP boundary. A provider cannot authorize a payment:
the boundary requires explicit payment intent and reconstructs the widget from
the signed-in student's offer ID, deposit amount, and current payment status.
The payment POST still performs its own offer validation and idempotent domain
transition.

The text guard is a cost and user-experience control, not an authorization
mechanism; an obfuscated request may still reach the model. Safety does not
depend on recognizing every hostile phrase: Edward has no general executor,
and every usable action is allowlisted, rebound to authoritative data, and
re-authorized by its own API endpoint.

The browser suite uses a deliberately hostile provider double for this
boundary. It returns script markup, `javascript:` and external links, a
one-cent payment widget for a foreign offer, and a false completed status. The
test passes only when those outputs are removed or replaced with authoritative
values and the payment list remains byte-for-byte unchanged.

These application checks do not replace deployment controls. Production still
needs distributed rate limiting at the ingress/API gateway, WAF/bot rules,
security-event alerting, egress allowlisting, and provider-budget limits. Those
controls must use a shared store such as Redis or the cloud gateway rather than
an in-process counter that resets on every pod.

## Exploratory subagent loop

The reusable bounded prompt is in
`tools/browser-e2e/AGENT_HARNESS.md`. A smaller agent may explore one journey
through the browser and report surprising behavior. Confirmed cases must be
converted into deterministic Playwright scenarios so CI can reproduce them.

The exploration order is:

1. missing and malformed values;
2. duplicate and repeated actions;
3. wrong order or blocked prerequisites;
4. refresh during an in-flight operation;
5. unsupported, empty, oversized, and mismatched files;
6. retry after provider/storage/network failure;
7. verify both visible feedback and the resulting enrollment status.
