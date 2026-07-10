# syntax=docker/dockerfile:1

FROM node:22-alpine AS frontend
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.10.11 AS uv

FROM python:3.12-slim AS backend
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/
# --frozen makes uv.lock, including hashes, the dependency authority for the image.
RUN uv sync --frozen --no-dev --extra web --no-editable

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FINWATCH_DB=/data/finwatch.db \
    FINWATCH_WEB_DIST=/app/web/dist \
    FINWATCH_REMOTE=1 \
    PORT=8765 \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app
COPY --from=backend /app/.venv ./.venv
COPY --from=frontend /build/web/dist ./web/dist/

RUN groupadd --gid 10001 finwatch \
    && useradd --uid 10001 --gid finwatch --create-home --shell /usr/sbin/nologin finwatch \
    && mkdir -p /data \
    && chown finwatch:finwatch /data

USER finwatch

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import json,os,urllib.request; host=os.environ['FINWATCH_ALLOWED_HOSTS'].split(',')[0].strip(); request=urllib.request.Request(f\"http://127.0.0.1:{os.environ.get('PORT', '8765')}/healthz\", headers={'Host': host}); payload=json.load(urllib.request.urlopen(request, timeout=3)); raise SystemExit(0 if payload == {'status': 'ok'} else 1)"]

CMD ["sh", "-c", "exec finwatch serve --host 0.0.0.0 --allow-remote --port \"${PORT:-8765}\""]
