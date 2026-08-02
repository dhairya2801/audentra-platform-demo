# Repository split

The original product monorepo was split into two deployment repositories:

- `Audentra-platform`: FastAPI application API, Python outbox worker, database,
  backend packages, infrastructure, development preview tooling, and canonical
  system documentation;
- `Audentra-portals`: Next.js/React portals, browser-owned tests, public web
  assets, and portal operations documentation.

Architecture documents intentionally describe the complete system and may
mention paths in both repositories. Frontend paths belong to
`Audentra-portals`. In this repository, the API and worker are two entry points
of the Python application under `apps/api`: `audentra-api` and
`audentra-worker`. Their source lives under
`apps/api/src/audentra/interfaces/http` and
`apps/api/src/audentra/interfaces/worker` respectively. The retired combined
VM preview is preserved only in Git history; new frontend and backend
deployment automation must remain independent.

The migration, development seed, API, and worker roles are built once from
`infra/docker/api.Dockerfile` and selected with different commands. This keeps
the release artifact consistent without coupling API and worker scaling. The
seed entry point fails closed in production, and the current production API
composition is intentionally blocked until an institutional identity adapter
replaces `AUTH_MODE=demo`.

## Temporary shared contract

This repository owns the canonical `packages/contracts` source. The portals
repository contains a snapshot solely to keep the initial split independently
installable. Publish a compiled, versioned API client from this repository and
remove the snapshot before independent feature development begins.
