# syntax=docker/dockerfile:1
# Multi-stage image (ADR-0005 §1). Build: docker compose --profile local build

# ── web: build the React app (only frontend/dist reaches the runtime image) ──
FROM node:22-bookworm-slim AS web
WORKDIR /src
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
# Cache mounts keep downloaded packages across rebuilds and retries.
RUN --mount=type=cache,id=vi-pnpm-store,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile --network-concurrency 8
COPY frontend ./frontend
# openapi.json and schema.d.ts are committed, so the type export step that
# `pnpm build` runs first (which needs Python) is skipped here.
RUN pnpm exec vite build --config frontend/vite.config.ts

# ── app: Python runtime from uv.lock, non-root ──────────────────────────────
FROM python:3.12-slim-trixie AS app
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_HTTP_TIMEOUT=120 \
    UV_CONCURRENT_DOWNLOADS=4 \
    PATH=/opt/venv/bin:$PATH
WORKDIR /app

# Dependencies first, so source edits reuse this layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,id=vi-uv-cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY backend ./backend
COPY config ./config
COPY knowledge ./knowledge
COPY alembic.ini ./
COPY --from=web /src/frontend/dist ./frontend/dist
RUN --mount=type=cache,id=vi-uv-cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

RUN useradd --system --uid 10001 --home-dir /app vi \
    && mkdir -p /app/data \
    && chown -R vi:vi /app/data /app/knowledge
USER vi

# The container binds all interfaces; compose publishes only 127.0.0.1.
ENV API_HOST=0.0.0.0 \
    API_PORT=8000
EXPOSE 8000
CMD ["uvicorn", "venture_intelligence.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
