# syntax=docker/dockerfile:1

FROM node:22-alpine AS frontend
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FINWATCH_DB=/data/finwatch.db \
    FINWATCH_WEB_DIST=/app/web/dist \
    PORT=8765

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
COPY --from=frontend /build/web/dist ./web/dist/

RUN pip install --no-cache-dir ".[web]" && mkdir -p /data

VOLUME ["/data"]
EXPOSE 8765

CMD ["sh", "-c", "finwatch serve --host 0.0.0.0 --allow-remote --port \"${PORT:-8765}\""]
