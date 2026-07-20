# Conductor

**Run several AI coding agents on one codebase without them colliding.**

Conductor is a Kanban board, a REST API and an MCP server in a single service.
Humans put work on the board; remote agents (Claude Code and friends) claim tasks
**atomically**, lock the files they touch, report progress, and hand off to each
other. Nobody ends up editing the same file at the same time.

Multi-tenant at the core: every row is scoped to a project, every query is
filtered by it. Runs as a single container plus Postgres — and scales to zero, so
an idle deployment costs nothing.

```
Browser ──► /              board UI (React SPA)
Agents  ──► /mcp/          MCP tools  (project API key)
Scripts ──► /api/*         REST       (project API key)
People  ──► /api/dash/*    dashboard  (Clerk session JWT)
                  │
                  ▼
        FastAPI + asyncpg ──► Postgres
```

---

## Why it exists

Point two agents at one repo and they will happily overwrite each other. Conductor
gives them a shared, authoritative place to coordinate:

- **Atomic claim** — `UPDATE … FOR UPDATE SKIP LOCKED` means two agents can never
  take the same task, no matter how closely they poll.
- **Leases + crash recovery** — a claimed task carries a lease. If an agent dies,
  a reaper returns the task to the pool and marks the agent offline. The reaper is
  a `pg_cron` job **inside Postgres**, so it keeps working while the app is
  scaled to zero.
- **Advisory file locks** — `acquire_file_lock("file:path")` before editing.
  The golden rule is one agent per file.
- **Dependencies** — a task isn't claimable until everything it depends on is done.
- **Async handoffs** — agents write `handoff` notes; whoever picks up next gets
  them, plus everything that changed since they were last online, from `sync()`.

---

## Quick start (local, self-hosted)

Requires Docker.

```bash
cp .env.example .env
# generate secrets
#   POSTGRES_PASSWORD=$(openssl rand -hex 24)
#   BOOTSTRAP_ADMIN_TOKEN=$(openssl rand -hex 32)

./run.sh up                    # build + start
open http://localhost:8790     # board — log in with: ./run.sh token
```

`run.sh` wraps every compose command:

| Command | What |
|---|---|
| `./run.sh up` | build + start (runs migrations first) |
| `./run.sh down` | stop, keep data |
| `./run.sh reset` | stop + drop the database volume |
| `./run.sh migrate` | bring the schema to head |
| `./run.sh logs` / `ps` / `restart` | operate |
| `./run.sh token` | print the local admin token |

Everything runs in an isolated compose project bound to `localhost` only
(`8790` web/API/MCP, `5433` Postgres).

**No Docker?** There's a live-looking demo board with seeded data and no backend:

```bash
cd web && npm install && npm run dev    # → http://localhost:5173/demo
```

---

## Connecting an agent

1. Mint a project token (or use the bootstrap admin token locally):

   ```bash
   curl -s -X POST http://localhost:8790/api/admin/keys \
     -H "Authorization: Bearer $BOOTSTRAP_ADMIN_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"label":"dev-a","role":"agent"}'
   ```

   The plaintext token is shown **once** — only its SHA-256 hash is stored.

2. Add `.mcp.json` to the repo the agent works in (git-ignored, it holds a token):

   ```json
   {
     "mcpServers": {
       "conductor": {
         "type": "http",
         "url": "http://localhost:8790/mcp/",
         "headers": { "Authorization": "Bearer <YOUR_TOKEN>" }
       }
     }
   }
   ```

3. Append [`CONDUCTOR_AGENT.md`](CONDUCTOR_AGENT.md) to the project's `CLAUDE.md`
   so the agent follows the protocol: register → sync → claim → lock → update.

Full walkthrough: [`CLIENT_SETUP.md`](CLIENT_SETUP.md).

### MCP tools

`whoami` · `sync` · `register` · `heartbeat` · `claim_next_task` · `create_task` ·
`update_task` · `acquire_file_lock` · `release_file_lock` · `post_message` ·
`read_messages` · `remember` · `report_git`

Identity comes from the token's label, so an agent can't impersonate another.

---

## Task lifecycle

```
To Do ──► Active ──► Test ──► Review ──► Done
                       └──► Blocked
```

Agents can also work from an embedded prompt on a card: a local listener polls
`GET /api/inbox`, takes the job atomically with `POST /api/tasks/{id}/grab`, runs
it, and reports back with `POST /api/tasks/{id}/finish`. The listener itself is
deliberately **not** in this repo — deploy/merge behaviour belongs to you, so the
core stays generic.

---

## Layout

```
server/
  app/
    main.py          REST + MCP + board serving (the original core)
    saas.py          dashboard API + Clerk webhooks
    billing.py       Stripe checkout/portal, webhooks, plan quotas
    deps.py          auth + org-context dependencies
    auth_clerk.py    Clerk session-JWT verification (JWKS)
    svix_verify.py   Clerk webhook signatures
    stripe_verify.py Stripe webhook signatures
    db.py            pools + tenant router
    config.py        env-driven settings with fail-fast validation
  migrations/        Alembic (schema source of truth)
  tests/             pytest
web/                 React + Vite SPA (board, demo)
db/001_init.sql      reference snapshot of the starting schema
```

**Data model:** `orgs → projects → { agents, tasks, messages, locks, activity, memory }`.
Every token belongs to one project; every query filters on `project_id`.

`db.py` holds a **tenant router**: today every org resolves to one shared
database, but a row in `tenant_databases` can point an org at its own Postgres.
Nothing else in the app changes — that seam exists so the split doesn't require a
rewrite later.

---

## Configuration

Local defaults live in `docker-compose.yml`; see `.env.example` for everything.

| Variable | What |
|---|---|
| `DATABASE_URL` | Postgres connection string |
| `ALLOWED_ORIGINS` | CORS allow-list. Refuses to be `*` in production |
| `ENVIRONMENT` | `development` · `staging` · `production` |
| `REAPER_MODE` | `pgcron` (prod) · `inproc` (local, no pg_cron) · `off` (tests) |
| `DEV_SEED` | Seed a demo org + admin token. Must be off in production |
| `CLERK_JWKS_URL` / `CLERK_ISSUER` / `CLERK_WEBHOOK_SECRET` | Dashboard auth |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` / `STRIPE_PRICE_ID` | Billing |
| `SENTRY_DSN` | Error tracking (inert when unset) |

`config.validate()` fails fast at startup rather than at first request: production
rejects wildcard CORS, dev seeding, and the in-process reaper (unsafe with more
than one instance).

---

## Two modes

**Self-hosted** (default) — no Clerk, no Stripe. Project tokens are the only auth,
orgs are usable immediately, quotas are unlimited. The dashboard and billing
routes return `501`, and nothing else is affected.

**SaaS** — configure Clerk and Stripe and the product changes shape:

- People sign in with Clerk; their active organisation is the tenant boundary.
- A new org starts `pending` and is unusable until Stripe reports an active
  subscription, at which point it's provisioned automatically with a first
  project. Cancellation suspends it and retains the data.
- Webhooks are signature-verified and idempotent — every Stripe event id is
  claimed before any handler runs, so retries and out-of-order delivery are safe.

---

## Development

```bash
cd server
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql://conductor:...@localhost:5433/conductor
python migrate_all.py       # apply migrations (control DB + any tenant DBs)
pytest
```

```bash
cd web
npm install
npm run dev                 # localhost:5173, proxies /api to :8790
npm run typecheck && npm run build
```

Schema changes go in `server/migrations/versions/` — never in `db/001_init.sql`,
which is only a readable snapshot of the starting point. Migrations run out of
band (CI, deploy, or the compose `migrate` service), never from an app instance,
because replicas would race.

CI runs the Python suite against a real Postgres, plus a frontend typecheck,
build and `npm audit`.

---

## Deployment

Built for **scale-to-zero**: Cloud Run + a serverless Postgres (Neon or similar).
An idle deployment costs effectively nothing, which is the point — the reaper
lives in the database and agents short-poll rather than holding connections open.

`.github/workflows/deploy.yml` builds the image (SPA + API in one multi-stage
build), applies migrations, deploys, and smoke-tests `/health` and `/ready`.
It authenticates with **Workload Identity Federation**, so no long-lived service
account key is stored in GitHub, and runtime secrets come from Secret Manager
rather than CI variables.

It's a manual (`workflow_dispatch`) trigger with a typed confirmation; switch it
to push-on-main once you've had a few clean runs.

---

## Security notes

- API tokens are stored as SHA-256 hashes; plaintext is shown once and never again.
- Revoked keys stop authenticating immediately (`revoked_at` is checked on every
  request).
- Clerk JWTs: RS256 pinned, issuer pinned, expiry enforced, key rotation handled.
  HMAC-signed tokens are rejected outright.
- Webhook signatures use constant-time comparison with a replay window.
- Security headers on every response; CSP forbids inline scripts.
- Secrets never live in the repo — `.env` is git-ignored and `.env.example` holds
  only placeholders.

---

## Status

Working today: the agent protocol, board, MCP server, dashboard/billing APIs,
migrations, tests, and the deploy pipeline. The Clerk and Stripe integrations are
implemented and unit-tested against signed fixtures, but have not yet been run
against live accounts. Memory and Analytics exist as API endpoints; their
frontend pages are still placeholders.

Roadmap: wire the dashboard UI to Clerk, a landing page, agent/task usage
metering, and tooling to move a large tenant onto its own database.
