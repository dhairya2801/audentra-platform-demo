# Edward GPT-6 Luna upgrade

Edward's university/demo runtime now uses `gpt-6-luna`. The gateway recognizes
GPT-6 generation parameters and the model's strict JSON schema support. Existing
reasoning settings are preserved (`none` by default). Evaluation hosts and cost
estimates use the new model; historical results and older model compatibility
remain intact.

Official model documentation: https://developers.openai.com/api/docs/models/gpt-6-luna
Standard short-context rates checked September 22, 2026: $0.10 input, $0.01 cached
input, and $0.50 output per million tokens. The evaluation estimates conservatively
charge cached input at the uncached rate.

## Validation

- Lint and typecheck passed.
- Nine focused model/request compatibility and launcher tests passed.
- Full Python suite: 1,454 passed, 166 skipped. The aggregate coverage gate still
  fails at 62.25% against 67%. A clean checkout of the previous release reproduced
  exactly 62.25% coverage (1,452 passed, 166 skipped); no threshold was changed.
- All 425 Node/workspace tests passed.
- Four HTTP Edward questions on an isolated test database passed: enrollment
  next steps, outstanding documents, staff board summary, and urgent board tasks.
  All 13 provider calls used GPT-6 Luna, with no provider errors or fallback
  responses. Key answers were checked against retrieved checklist and board data.
- Two questions through the deployed HTTPS API passed for student and staff
  sessions. Both responses identified `openai` / `gpt-6-luna`; the staff board
  answer matched the live API's 64 tasks and 10 students.

This is a small smoke check, not a comprehensive model quality evaluation.
Raw responses and traces remain in ignored local artifacts, outside Git.

## Deployment

- GCP project: `audentra`; VM: `audentra-api-vm`; zone: `us-east4-a`.
- Backend release: `b5b9b4c1c00ac3c60f23927c52e0a1c8e8a38829`.
- Previous release retained: `3d2b9cf6b5198effe1c0e984e8b3f7a2f7ae064f`.
- Built the existing API Dockerfile from the release archive on the VM.
- Compared rendered Compose configurations: only the API image and
  `OPENAI_MODEL` changed. Recreated only the API with `--no-deps --no-build`.
  No migration, seed, reset, worker startup, or database/storage change ran.
- Docker health and the public `/health/ready` endpoint passed before updating
  `/opt/audentra-platform/current` to the new release.

The frozen-demo profile uses the release's `demo-deployment.env` and both
`infra/preview-vm/compose.yaml` and `infra/preview-vm/demo-reset.override.yaml`,
with `/opt/audentra-platform/shared/.env`. For rollback, use those files from
the retained previous release to recreate **only** `api`, verify health, and
restore the `current` link. Do not use the generic seeded-preview deployment
script for this frozen-demo profile.
