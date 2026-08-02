# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.7.12 AS uv

FROM python:3.12.11-slim-bookworm AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/audentra-venv

COPY --from=uv /uv /uvx /bin/
WORKDIR /workspace

# Resolve the immutable dependency graph before copying application sources so
# source-only edits keep the expensive dependency layer cached.
COPY apps/api/pyproject.toml apps/api/uv.lock ./apps/api/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --directory apps/api --locked --no-dev --no-install-project

COPY apps/api/src ./apps/api/src
COPY apps/api/assets ./apps/api/assets
COPY apps/api/migrations ./apps/api/migrations
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --directory apps/api --locked --no-dev --no-editable

FROM python:3.12.11-slim-bookworm AS runtime

ENV PATH=/opt/audentra-venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 audentra \
    && useradd --system --uid 10001 --gid audentra \
      --home-dir /nonexistent --shell /usr/sbin/nologin audentra

WORKDIR /workspace/apps/api
COPY --from=build /opt/audentra-venv /opt/audentra-venv
COPY --from=build --chown=audentra:audentra /workspace/apps/api/assets ./assets
COPY --from=build --chown=audentra:audentra /workspace/apps/api/migrations ./migrations

USER audentra
EXPOSE 4000

# The same image also exposes audentra-worker, audentra-migrate, and
# audentra-seed. Compose and deployment manifests select a role by overriding
# this command.
CMD ["audentra-api"]
