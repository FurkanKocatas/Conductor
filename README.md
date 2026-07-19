# Conductor

**Multi-tenant AI-agent orchestration platform.** A Kanban task board + REST API + MCP
interface: humans drag a task to "Active", remote AI agents (Claude Code and friends) claim
tasks atomically, run them, and write the result back to the board. Single service + Postgres.
No external dependencies.

## Quick start (local, isolated)

```bash
cp .env.example .env
# generate secrets:
#   POSTGRES_PASSWORD=$(openssl rand -hex 24)
#   BOOTSTRAP_ADMIN_TOKEN=$(openssl rand -hex 32)
./run.sh up                          # build + start
open http://localhost:8790           # board — login token: ./run.sh token
```

`run.sh` wraps every docker compose command: `up` · `down` · `reset` (drop DB) · `migrate` ·
`logs` · `ps` · `restart` · `token`. (It also sets up an isolated `DOCKER_CONFIG` without touching
your global `~/.docker/config.json` — this skips Docker Desktop's `credsStore` helper that stalls
image pulls on some machines.)

Everything runs in an isolated compose project named `conductor` (its own network + volume), bound
to `localhost` only (8790 web, 5433 Postgres). Reset completely with `./run.sh reset`.

## Architecture

- **server/** — FastAPI + asyncpg. Single-file core (`app/main.py`): bearer-auth REST + static board
  UI (`ui/index.html`) + MCP tool server (`/mcp`). Schema is owned by **Alembic**
  (`server/migrations`); migrations run out of band (the compose `migrate` one-shot, or CI/deploy),
  never per app instance.
- **Data model:** `orgs → projects → { agents, tasks, messages, locks, activity, memory }`. Each
  token is bound to one **project**; every query is filtered by `project_id` → natural
  multi-tenancy / isolation. A tenant-router seam (`app/db.py`) lets an org move to a dedicated
  database later without changing any query.
- **Task lifecycle (columns):** To Do → Active → Test → Review → Done (+ Blocked).
- **Agent protocol:** `GET /api/inbox` → `POST /api/tasks/{id}/grab` (atomic, `SKIP LOCKED`) → work →
  `POST /api/tasks/{id}/finish`. Listeners short-poll every few seconds (scale-to-zero friendly).
- **Client (not in this repo):** a "listener" running on each agent machine watches the inbox and
  runs the work with its own AI CLI. Deploy/merge-style actions live entirely on the client side, so
  the core stays generic.

## Crash recovery

A reaper reclaims expired task leases, marks silent agents offline, and re-queues crashed
automation prompts. In production it runs as a `pg_cron` job inside Postgres (so it works while the
app is scaled to zero); locally it runs in-process (`REAPER_MODE=inproc`) for Postgres images
without pg_cron.

## Configuration (.env)

| Variable | What |
|---|---|
| `POSTGRES_PASSWORD` | DB password |
| `BOOTSTRAP_ADMIN_TOKEN` | Bootstrap admin token — local board login + admin API (dev seed only) |
| `APP_NAME` | Brand name |
| `DEFAULT_ORG` / `DEFAULT_PROJECT` | Default org/project created by the dev seed |
| `ALLOWED_ORIGINS` | CORS allow-list (never `*` in production) |
| `ENVIRONMENT` | `development` \| `staging` \| `production` |
| `REAPER_MODE` | `pgcron` (prod) \| `inproc` (local) \| `off` (tests) |
| `DEV_SEED` | Seed Demo/default + admin locally; must be off in production |

See `.env.example` for the full list, including the production-only variables (`DATABASE_URL`,
Clerk, Stripe) that come from a secret manager rather than this file.

## Tests

```bash
cd server
pip install -r requirements-dev.txt
python migrate_all.py     # needs DATABASE_URL (a local/CI Postgres)
pytest
```

CI (GitHub Actions) runs the suite against a Postgres service on every push/PR.

## Roadmap to SaaS

The core is already multi-tenant and isolated. Productizing it adds these layers:

1. **Auth/accounts:** managed identity (Clerk) — signup/login, sessions, org roles; user-minted,
   project-scoped API keys replace hand-minted admin tokens.
2. **Self-serve tenancy:** signup → org + project; roles/invites (owner/admin/member).
3. **Billing:** Stripe subscriptions; a workspace is provisioned when a subscription goes live.
4. **Hosting:** scale-to-zero (Cloud Run + Neon) so idle cost is ~$0 until the first sale.
5. **Observability & quotas:** usage metrics, audit log, rate limiting.
6. **UI polish:** i18n, theming, per-tenant branding.

Today it runs fully as a single-tenant / self-hosted service; the layers above turn it into a
multi-tenant SaaS.
