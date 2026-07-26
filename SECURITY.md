# Security policy

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or exposed student
record. Use the repository's **Security → Report a vulnerability** workflow so
the maintainers can investigate through a private GitHub security advisory.

Include:

- the affected route, component, or commit;
- reproduction steps with synthetic data only;
- expected and observed behavior;
- the likely impact;
- any temporary mitigation already applied.

Do not upload real identity documents, transcripts, credentials, access tokens,
or provider responses to an issue or pull request.

## Supported version

This repository is pre-release software. Only the current `main` branch and the
currently deployed preview receive security fixes.

## Security boundaries

- The public preview is not approved for real student or payment data.
- Secrets belong in ignored environment files or a managed secret store, never
  in Git.
- Official enrollment decisions remain deterministic server-side actions.
- Model output is untrusted evidence and cannot directly authorize, pay,
  enroll, waive, or modify official records.
- Deployment access uses GitHub OIDC, Google Workload Identity Federation,
  IAP-only SSH, and short-lived credentials. Long-lived Google service-account
  keys are prohibited.
