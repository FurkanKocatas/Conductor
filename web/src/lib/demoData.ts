/** Seeded demo workspace.
 *
 * Powers the public demo (/demo): a realistic, read-only board a prospect can
 * look at without signing up or running anything. Also what the UI is developed
 * against, so design work never depends on a live backend.
 *
 * Timestamps are generated relative to load so the board always looks current.
 */
import type { Agent, BoardState, Task } from "./api";

const now = () => Date.now();
const agoIso = (seconds: number) => new Date(now() - seconds * 1000).toISOString();

let seq = 0;
const task = (t: Partial<Task> & Pick<Task, "title" | "status">): Task => ({
  id: `demo-task-${++seq}`,
  spec: null,
  assignee: null,
  assign_mode: "auto",
  priority: 0,
  board_order: seq,
  depends_on: [],
  artifacts: {},
  prompt: null,
  prompt_state: "idle",
  created_at: agoIso(3600),
  updated_at: agoIso(120),
  ...t,
});

const agent = (a: Partial<Agent> & Pick<Agent, "name" | "status">): Agent => ({
  id: `demo-agent-${a.name}`,
  machine: null,
  note: null,
  branch: null,
  git: {},
  current_task_id: null,
  last_heartbeat: agoIso(8),
  ...a,
});

export function demoState(): BoardState {
  seq = 0;
  return {
    project: "acme/checkout-service",
    brief: "Demo workspace — resets automatically.",
    agents: [
      agent({ name: "dev-a", status: "working", note: "Refactoring payment retries",
              branch: "feat/retry-backoff" }),
      agent({ name: "dev-b", status: "working", note: "Writing integration tests",
              branch: "test/checkout-e2e" }),
      agent({ name: "reviewer", status: "idle", note: "Waiting for review queue",
              branch: "main" }),
      agent({ name: "ci", status: "offline", last_heartbeat: agoIso(900) }),
    ],
    tasks: [
      task({ title: "Add exponential backoff to payment retries", status: "claimed",
             assignee: "dev-a", priority: 2, prompt_state: "running" }),
      task({ title: "End-to-end test for guest checkout", status: "claimed",
             assignee: "dev-b", priority: 1 }),
      task({ title: "Extract webhook signature verification into a module",
             status: "in_progress", assignee: "dev-a" }),
      task({ title: "Idempotency keys on order creation", status: "review",
             assignee: "dev-b", priority: 3 }),
      task({ title: "Rate-limit the public quotes endpoint", status: "todo", priority: 2 }),
      task({ title: "Replace deprecated currency helper", status: "todo" }),
      task({ title: "Backfill order_events for June", status: "todo" }),
      task({ title: "Split settlement job into batches", status: "blocked",
             assignee: "dev-a", depends_on: ["demo-task-1"] }),
      task({ title: "Upgrade Postgres driver to 3.2", status: "done", assignee: "dev-b",
             prompt_state: "done" }),
      task({ title: "Cache exchange rates for 60s", status: "done", assignee: "dev-a",
             prompt_state: "done" }),
    ],
    counts: { todo: 3, claimed: 2, in_progress: 1, blocked: 1, review: 1, done: 2 },
    activity: [
      { id: 9, actor: "dev-a", kind: "task.grabbed", detail: { title: "Add exponential backoff to payment retries" }, created_at: agoIso(15) },
      { id: 8, actor: "dev-b", kind: "task.updated", detail: { title: "Idempotency keys on order creation", status: "review" }, created_at: agoIso(95) },
      { id: 7, actor: "dev-a", kind: "git.reported", detail: { branch: "feat/retry-backoff" }, created_at: agoIso(160) },
      { id: 6, actor: "reviewer", kind: "message.posted", detail: {}, created_at: agoIso(240) },
      { id: 5, actor: "dev-b", kind: "task.claimed", detail: { title: "End-to-end test for guest checkout" }, created_at: agoIso(420) },
      { id: 4, actor: "reaper", kind: "task.reclaimed", detail: { title: "Backfill order_events for June" }, created_at: agoIso(680) },
      { id: 3, actor: "dev-a", kind: "task.finished", detail: { title: "Cache exchange rates for 60s" }, created_at: agoIso(900) },
      { id: 2, actor: "dev-b", kind: "memory.added", detail: { kind: "handoff" }, created_at: agoIso(1500) },
      { id: 1, actor: "dev-a", kind: "agent.registered", detail: {}, created_at: agoIso(3400) },
    ],
    messages: [],
    users: [
      { label: "you", role: "admin", online: true },
      { label: "teammate", role: "ui", online: false },
    ],
    server_time: new Date().toISOString(),
  };
}
