# Multi-stage: build the SPA with Node, serve it from the Python app.
# Build context is the REPO ROOT (see docker-compose.yml / deploy.yml), because
# the frontend lives in web/ and the backend in server/.

# ── 1. Frontend ──────────────────────────────────────────────
FROM node:22-alpine AS web
WORKDIR /app/web
# Copy manifests first so npm ci is cached until dependencies actually change.
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# vite.config.ts writes to ../server/static → /app/server/static
RUN npm run build

# ── 2. Runtime ───────────────────────────────────────────────
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/app ./app
COPY server/migrations ./migrations
COPY server/alembic.ini server/migrate_all.py ./
COPY server/ui ./ui
COPY --from=web /app/server/static ./static

EXPOSE 8000
# Cloud Run injects $PORT and the container MUST listen on it; locally the
# compose default (8000) applies. Shell form so the variable expands.
# Migrations run out of band (compose `migrate` service / CI / deploy step), not
# here — so multiple Cloud Run instances never race to migrate on startup.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
