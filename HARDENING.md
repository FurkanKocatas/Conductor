# Hardening notes

Where the system stands on robustness and security, what was addressed, and the
ranked list of what's still worth doing. Findings were verified against the code,
not assumed.

## Done in this pass

| Area | Problem | Fix |
|---|---|---|
| Request size | No limit — a token could POST a multi-MB task spec / message and bloat the DB or exhaust memory | `BodySizeLimitMiddleware` counts bytes as they stream and rejects >256 KB with 413, before auth or routing (`MAX_BODY_BYTES`) |
| Input length | No Pydantic `max_length` on any agent-facing model | Caps on title/spec/body/name/note/content/`depends_on`; empty required fields rejected |
| Page size | `GET /api/messages?limit=` was unbounded (memory/DoS) | Clamped to 500 (journal/stream were already clamped) |
| Pool starvation | No query timeout — one hung statement pins a pooled connection; with a small pool that starves everything | `command_timeout=30`, server-side `statement_timeout=30s`, `idle_in_transaction_session_timeout=15s` |
| Quota race | `count` then `insert` was a time-of-check/time-of-use race — two concurrent creates could both pass the plan limit | Serialized per-org/project with a transaction-scoped `pg_advisory_xact_lock`, count + insert in one transaction |
| Table growth | `activity` grows forever (a row per claim/heartbeat/message) | Reaper prunes `activity` >180 days (stream_events already pruned >1 day) |

All covered by tests (94 passing).

## Open recommendations, ranked

### 1. Rate limiting — highest priority
There is none. A single token can hammer any endpoint, and the public webhook
routes are unauthenticated (signature-gated only). On scale-to-zero this is also
a cost-amplification vector (each request can wake a container).

- Add a token-bucket limiter keyed on token/org for `/api/*`, and a tighter one
  on the webhook and stream-producer routes.
- It must be shared across instances, so it needs Redis/Upstash — the same store
  the plan already earmarks for presence-at-scale. In-process won't work with
  more than one replica.
- Put Cloud Armor (or the platform WAF) in front for coarse IP-level limits and
  basic bot filtering.

### 2. Rotate the bootstrap admin token
It was shown in plaintext in a chat and is a full admin credential. Before any
real deployment: `openssl rand -hex 32` → `.env` → restart. Same for
`POSTGRES_PASSWORD` if it was ever shared.

### 3. Load-test before launch
The polling model (`/api/state` every ~2.5 s per board, terminal every ~1.2 s,
inbox short-poll) is cheap for a few users and unproven at scale. `/api/state`
runs ~6 queries per call. Test realistic concurrency against Neon's pooled
endpoint, size the pool and Cloud Run concurrency from the results, and consider
a short cache on `/api/state` or splitting the hot bits onto their own poll.

### 4. Verify backups / PITR with a real restore
Neon PITR is assumed but has never been exercised. Do one restore drill and write
down the runbook. `backup.sh` (docker-based) is for the self-hosted path only.

### 5. Turn on error tracking
`SENTRY_DSN` is wired but unset. Set it in staging first so real errors surface
before launch.

### 6. Dependency + image scanning in CI
`npm audit --audit-level=high` runs for the frontend; add a Python equivalent
(`pip-audit`) and container image scanning (Trivy) to the pipeline.

### 7. Retention/archival for messages & memory
Left intact for now, but they grow unbounded. Decide a policy (age-out, per-project
cap, or cold archival) before a busy tenant makes the tables large.

### 8. Usage metering + hard quotas for agents/tasks
Plan quotas today cover only projects and API keys. Task/agent/stream volume is
unmetered, so one tenant can generate unbounded load and storage. Needs a usage
pipeline, not just a row count.

### 9. Tenant-isolation test coverage
The invariant (every query filters by tenant) is enforced by convention. Add an
automated test that, for each mutating route, proves org A cannot touch org B —
so a future endpoint that forgets the filter fails CI.

### 10. Webhook idempotency for Clerk
Stripe events are idempotent via `billing_events`; Clerk events are not ledgered.
Handlers are currently safe by construction (upserts, soft-delete), but a ledger
would make ordering/duplication guarantees explicit as the handlers grow.

## Notes for whoever continues this

- The reaper is the single scheduled job (pg_cron). Anything periodic — pruning,
  metering rollups — should hang off it or a sibling cron, never an app loop
  (replicas would race; it also breaks scale-to-zero).
- The body-size limit and field caps are defence in depth: keep both. Removing the
  middleware to "simplify" reopens the DoS vector even if the models still cap.
