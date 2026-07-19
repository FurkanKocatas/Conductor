# Connecting Claude Code to Conductor (per developer)

Conductor exposes an MCP endpoint alongside the board. Point any Claude Code
instance at it and your agents share one task pool with live isolation per token.

- **Board (browser):** `http://localhost:8790`
- **MCP endpoint:** `http://localhost:8790/mcp/`

> Replace `localhost:8790` with your deployment's host/port if you run Conductor
> on a shared server (e.g. `http://conductor.internal:8790`).

## 1) Get a token

Each developer (and the UI) authenticates with its own bearer token. Mint one
per agent with the admin token:

```bash
curl -s -X POST http://localhost:8790/api/admin/keys \
  -H "Authorization: Bearer $BOOTSTRAP_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"label":"dev-a","role":"agent"}'
```

The response contains the plain token **once** — store it safely. The server
only keeps a SHA-256 hash; a lost token is re-minted, never recovered.

## 2) Add `.mcp.json` to your project root

Each developer creates this file at the root of the repo they're working in:

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

> Do **not** commit `.mcp.json` (it holds a token) — add it to `.gitignore`.
> To share a template, put the token in a `${CONDUCTOR_TOKEN}` env var instead.

## 3) Start Claude Code and approve the server

When `claude` starts, approve the `conductor` MCP server. Verify:

```
/mcp        → conductor: connected (tools listed)
```

## 4) Working loop (give Claude `CONDUCTOR_AGENT.md`)

Append `CONDUCTOR_AGENT.md` to each developer's project `CLAUDE.md` so Claude
uses the task pool correctly (register → sync → claim → update).

## Watching the board

For oversight: open **`http://localhost:8790`** in a browser (admin token) —
live Kanban + agent status + message feed.

## Troubleshooting

- `/mcp` not connected → wrong token or wrong host. Try `curl http://localhost:8790/health`.
- `406`/`400` from curl is normal (MCP needs a handshake); a real client (Claude) connects fine.
