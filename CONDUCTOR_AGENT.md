# Conductor Coordination Protocol (append to CLAUDE.md)

Multiple Claude agents work on this project across separate machines. To avoid conflicts and divide
the work, use the **Conductor** MCP server. Your identity comes from your token (who am I: `whoami`).
Rules:

## At the start of a session
1. `register(machine="<your-machine>")` — register yourself on the board.
2. `sync()` — what is the team doing, what are your open tasks, any unread messages? Act accordingly.

## Work loop
3. Claim the next available task with `claim_next_task()` (atomic — no other agent gets the same task).
   - If a task is returned: `heartbeat(status="working", note="<short: what you're doing>")`.
   - If `{claimed:null}`: check the board with `sync()`; add needed work with `create_task(...)`.
4. Before editing a file, **lock it**: `acquire_file_lock("file:<path>")`.
   If you can't get it, another agent is on it — switch to another task or talk via `post_message`.
5. When done: `update_task(task_id, status="done", artifacts={"commit":"...","files":[...]})`
   and `release_file_lock("file:<path>")`.
   - If blocked: `update_task(task_id, status="blocked")` + `post_message` explaining what you're
     waiting on.
   - If it goes to review: `status="review"`.

## Communication
- To tell/ask the team something, use `post_message(body, to_agent="<name or empty>")`.
- Periodically read what's addressed to you with `read_messages()`; refresh the board with `sync()`.

## Producing work (planning)
- Break big work into pieces: for each piece, `create_task(title, spec, priority,
  depends_on=[...], assign_mode="auto")`. Ordering/dependencies live in Conductor; you just define them.
- To assign work to someone specific, use `assign_mode="manual", assignee="<name>"`.

## Golden rule
Never let two agents touch the same file at once. When in doubt, **lock or message first**.
