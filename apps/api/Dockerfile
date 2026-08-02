# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.7.12 AS uv

FROM python:3.12.11-slim-bookworm AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./

# Install the locked runtime graph first so source-only changes retain this layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src

# Install a regular wheel, not an editable checkout, into the relocatable venv.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

COPY assets ./assets
COPY migrations ./migrations


FROM python:3.12.11-slim-bookworm AS runtime-base

ENV PATH="/app/.venv/bin:$PATH" \
    HOME=/home/audentra \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    API_HOST=0.0.0.0 \
    API_PORT=4000 \
    AUDENTRA_MIGRATIONS_DIR=/app/migrations \
    ONBOARDING_DOCUMENT_TEMPLATE_DIR=/app/assets/onboarding \
    PORTAL_MEDIA_DIR=/app/assets/portal-media

RUN groupadd --gid 10001 audentra \
    && useradd --uid 10001 --gid audentra --create-home \
       --home-dir /home/audentra --shell /usr/sbin/nologin audentra

WORKDIR /app
COPY --from=build --chown=audentra:audentra /app/.venv /app/.venv
COPY --from=build --chown=audentra:audentra /app/assets /app/assets
COPY --from=build --chown=audentra:audentra /app/migrations /app/migrations

USER 10001:10001
STOPSIGNAL SIGTERM


FROM runtime-base AS worker
CMD ["python", "-m", "audentra.interfaces.worker.main"]


FROM runtime-base AS seed
CMD ["audentra-seed", "--all"]


FROM runtime-base AS api
EXPOSE 4000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('API_PORT','4000')+'/health/ready',timeout=3).read()"]

CMD ["audentra-api"]
