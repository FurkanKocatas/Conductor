# CLAUDE.md — working notes for AI assistants

Orientation for Claude Code (or any agent) picking this repo up cold.
Read this before changing anything.

> Not to be confused with [`CONDUCTOR_AGENT.md`](CONDUCTOR_AGENT.md), which is the
> protocol agents follow when *using* Conductor to coordinate. This file is about
> *developing* Conductor itself.

---

## 1. What this is

An agent-orchestration platform: Kanban board + REST API + MCP server in one
FastAPI service, backed by Postgres, with a React SPA frontend. It exists so
several AI coding agents can work one codebase without colliding.

It runs in two shapes:

- **Self-hosted** — no Clerk, no Stripe. Project bearer tokens are the only auth.
  Dashboard and billing routes return `501`; everything else works.
- **SaaS** — Clerk supplies human identity (the user's active org is the tenant),
  Stripe activates a workspace. Designed to scale to zero so idle costs nothing.

Both must keep working. Don't make Clerk or Stripe mandatory.

---

## 2. Set up on a new machine

```bash
git clone https://github.com/FurkanKocatas/Conductor.git
cd Conductor
cp .env.example .env
#   POSTGRES_PASSWORD=$(openssl rand -hex 24)
#   BOOTSTRAP_ADMIN_TOKEN=$(openssl rand -hex 32)
./run.sh up                       # Docker: db + migrations + server
open http://localhost:8790        # token: ./run.sh token
```

Backend tests (needs a reachable Postgres):

```bash
cd server
python -m venv .venv && ./.venv/bin/pip install -r requirements-dev.txt
export DATABASE_URL=postgresql://conductor:<pw>@localhost:5433/conductor
python migrate_all.py
pytest                            # 81 passing, 1 skipped without a DB
```

Frontend:

```bash
cd web
npm install
npm run dev                       # :5173, proxies /api → :8790
npm run typecheck && npm run build
```

`npm run dev` + `/demo` renders a seeded board with **no backend at all** — use it
for any UI work.

---

## 3. Commands

| Task | Command |
|---|---|
| Start / stop / reset | `./run.sh up` · `down` · `reset` |
| Apply migrations | `./run.sh migrate` or `cd server && python migrate_all.py` |
| Local admin token | `./run.sh token` |
| Backend tests | `cd server && pytest` |
| Frontend typecheck | `cd web && npm run typecheck` |
| Frontend build | `cd web && npm run build` → `server/static/` |

---

## 4. Layout

```
server/app/
  main.py           REST + MCP tools + board serving. The original core.
  saas.py           dashboard API (/api/dash) + Clerk webhooks
  billing.py        Stripe checkout/portal + webhooks + plan quotas
  deps.py           auth / org-context dependencies shared by saas + billing
  auth_clerk.py     Clerk session-JWT verification via JWKS
  svix_verify.py    Clerk (Svix) webhook signatures
  stripe_verify.py  Stripe webhook signatures — DIFFERENT scheme, see §5
  db.py             connection pools + the tenant router
  config.py         env settings + fail-fast validation
  security.py       response security headers
  observability.py  optional Sentry
server/migrations/  Alembic — the schema source of truth
web/src/            React SPA (board + demo)
db/001_init.sql     reference snapshot only, NOT applied
```

---

## 5. Hard-won gotchas

These cost real time. Don't rediscover them.

**`log` is taken in `main.py`.** There's an `async def log(c, pid, actor, kind, …)`
activity-log helper. The module logger is deliberately named `applog`. Naming a
logger `log` shadows the helper at runtime and everything breaks.

**FastAPI ≥0.116 doesn't flatten included routers.** `app.routes` shows an
`_IncludedRouter` marker, not the individual routes. Asserting
`"/api/dash/me" in {r.path for r in app.routes}` fails even though routing works.
Assert against `app.openapi()["paths"]` instead.

**asyncpg won't coerce `str` → `uuid`.** Stripe metadata gives you an org id as a
string; a DB lookup gives a `UUID`. Every statement casts explicitly (`$1::uuid`)
and passes `str(...)`. A malformed id is rejected before it reaches the driver —
otherwise the webhook 500s and Stripe retries it forever.

**Svix and Stripe signatures are not the same.** Svix: base64 digest, secret is
base64-decoded after the `whsec_` prefix, signs `id.timestamp.body`. Stripe: hex
digest, secret used raw *including* the prefix, signs `timestamp.body`. Conflating
them silently verifies nothing. There are tests pinning both.

**Billing routes can't require an active workspace.** Otherwise nobody could ever
subscribe. `billing_context` reaches `pending`/`suspended` orgs; `org_context`
requires `active` and returns `402` otherwise.

**Migrations never run from an app instance.** Replicas would race. They run in
CI, the deploy step, or the compose `migrate` one-shot. `migrate_all.py` loops
over the control DB plus any registered tenant DBs.

**The reaper lives in Postgres.** It's a `pg_cron` job (migration 0002), so it
works while the app is scaled to zero and can't race across replicas.
`REAPER_MODE=inproc` is a **local-only** fallback because `postgres:16-alpine`
has no pg_cron; `config.validate()` rejects it in production.

**Don't reintroduce long-polling.** `/api/inbox` used to hold a request ~55s. That
breaks scale-to-zero and pins a DB connection. Listeners short-poll now.

**Presence is a table, not a dict.** An in-process dict is wrong across replicas.

**Docker build context is the repo root**, not `server/`. The image builds the SPA
from `web/` and the API from `server/` in one multi-stage build
(`docker build -f server/Dockerfile .`).

**Cloud Run injects `$PORT`** and the container must listen on it. The `CMD` uses
shell form for that reason.

**Browser screenshots letterbox.** In this environment the painted viewport is
~62% of the reported height, so a screenshot makes full-height columns look cut
off. Verify layout by hit-testing the DOM (`elementFromPoint`, `getBoundingClientRect`)
before believing a layout bug exists. I chased a phantom one.

**Local Python is 3.14, CI is 3.12.** Some wheels differ. CI is the source of truth.

---

## 6. Invariants — don't break these

- **Tenant isolation.** Every data-plane query filters by `project_id` (and the
  dashboard by the caller's org). Adding an endpoint that forgets this leaks
  across customers. There are tests; keep adding them.
- **Revoked keys must not authenticate.** `_resolve_token` checks
  `revoked_at IS NULL`. Without it revocation is cosmetic.
- **Tokens are stored hashed** (SHA-256), shown in plaintext exactly once.
- **`config.validate()` guardrails** — production refuses wildcard CORS, `DEV_SEED`,
  and the in-process reaper. Don't soften these to make something work.
- **Webhook handlers stay idempotent.** Stripe event ids are claimed in
  `billing_events` before any handler runs.
- **Schema changes go in `server/migrations/versions/`.** Never edit
  `db/001_init.sql` (a readable snapshot) and never re-add an in-code `migrate()`.
- **Secrets never enter the repo.** `.env` is git-ignored; the repo is public.
  Check `git ls-files` before committing anything secret-adjacent.
- **CSP forbids inline scripts.** `theme-init.js` is a real file for exactly this
  reason. Don't inline it back into `index.html`.
- **The project is English-only** — code, comments, docs, UI. It was converted
  from Turkish deliberately.

---

## 7. The tenant router (the growth seam)

`db.py` exposes `tenant_pool(org_id)`. Today every org resolves to the one shared
pool. A row in `tenant_databases` can point an org at its own Postgres, and the
router hands back that pool instead — no query changes anywhere.

`main.py` still uses the shared pool directly via the module-level `pool` alias.
When moving an org to a dedicated database becomes real work, data-plane handlers
should be migrated to `tenant_pool(org_id)`. Control-plane tables (orgs, projects,
api_keys, billing) always stay on `control_pool()`.

---

## 8. Design language — read before touching the UI

The visual direction is **letterpress / editorial print**, matching the owner's
portfolio. It is not generic SaaS, and two earlier attempts were rejected for
being exactly that (grey enterprise dashboard; then rounded pills + soft shadows
+ gradient washes). Don't drift back.

Rules:

- **Paper and ink.** Warm paper stock, ink text. Colour arrives as flat pastel
  **plates** (one per lane), never gradients or colour washes.
- **Elevation is a solid offset shadow** (`4px 4px 0`), never a blur. Hover
  translates the element up-left.
- **Hard rules, square corners.** 1.5px borders, `--r: 2px`. No pill shapes.
- **Type:** Fraunces (serif) for names and entries, Courier Prime (mono) for every
  label. Micro-labels are uppercase, tracked `.16em`.
- **Print furniture is part of the system:** SVG grain overlay, crop marks,
  rotated rubber stamps for status, a barcode in the folio strip.
- Vocabulary follows the metaphor: masthead, folio, plates, Performers, Log.
- Fonts are bundled locally via `@fontsource`. **No font CDNs** — that would
  require loosening the CSP.
- **Both themes come from one token set** in `web/src/styles/tokens.css`, resolved
  `:root` → `prefers-color-scheme` → `[data-theme]`. Never style a component
  inside a media query; add a token.
- Check contrast empirically. The first pass shipped lanes at 1.055 contrast
  against the page — invisible. Measure, don't eyeball.

---

## 9. Where things stand

Done: agent protocol, board, MCP server, Alembic migrations, tenant-router seam,
Clerk identity + dashboard API, Stripe billing with quotas, security headers,
Sentry hook, CI, Cloud Run deploy workflow, and the SPA (board + demo).

Not done / next:

1. **Wire the dashboard UI to Clerk** — the API exists (`/api/dash/*`), the
   frontend doesn't use it yet. `setTokenProvider` in `web/src/lib/api.ts` is the
   hook for supplying a Clerk JWT.
2. **Memory and Analytics pages** — endpoints (`/api/journal`, `/api/analytics`)
   work; the routes are placeholders in `web/src/App.tsx`.
3. **Landing / pricing page** for the SaaS.
4. **Exercise Clerk and Stripe against real accounts.** Both are unit-tested with
   signed fixtures but have never talked to the live services.
5. **Usage metering** for agents and tasks (quotas today only cover projects and
   API keys).
6. **Tooling to migrate a tenant to a dedicated database** (the seam exists).
7. **No LICENSE file yet** — public with no license means all-rights-reserved.

---

## 10. Before committing

- `cd server && pytest` and `cd web && npm run typecheck && npm run build`
- `git ls-files` — no `.env`, no `node_modules`, no `server/static/`, no venv
- Migrations added for any schema change, and they apply cleanly from scratch
- New endpoints filter by tenant and have a test proving cross-org access fails
- Commit messages explain **why**, not just what
