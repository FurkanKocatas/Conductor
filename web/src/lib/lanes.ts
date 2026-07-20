import type { Status } from "./api";

/** Kanban lanes. `key` matches the backend status value and must never change;
 *  `label` is display-only. Each lane carries its own token pair so status is
 *  encoded in colour AND position, not colour alone. */
export const LANES: {
  key: Status; label: string; fg: string; bg: string; hint: string;
}[] = [
  { key: "todo",        label: "To Do",   fg: "var(--st-todo)",    bg: "var(--st-todo-bg)",    hint: "Waiting to be claimed" },
  { key: "claimed",     label: "Active",  fg: "var(--st-active)",  bg: "var(--st-active-bg)",  hint: "An agent is on it" },
  { key: "in_progress", label: "Test",    fg: "var(--st-test)",    bg: "var(--st-test-bg)",    hint: "Built, being verified" },
  { key: "blocked",     label: "Blocked", fg: "var(--st-blocked)", bg: "var(--st-blocked-bg)", hint: "Needs a decision" },
  { key: "review",      label: "Review",  fg: "var(--st-review)",  bg: "var(--st-review-bg)",  hint: "Awaiting sign-off" },
  { key: "done",        label: "Done",    fg: "var(--st-done)",    bg: "var(--st-done-bg)",    hint: "Finished" },
];

export const LANE_BY_KEY = Object.fromEntries(LANES.map((l) => [l.key, l]));

export function laneLabel(status: string) {
  return LANE_BY_KEY[status]?.label ?? status;
}

/** Compact relative time: 12s, 4m, 3h, 2d. */
export function ago(iso: string | null): string {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.max(0, Math.round(s))}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

/** An agent counts as live only if it has beaten recently. */
export function agentLive(lastHeartbeat: string | null): boolean {
  if (!lastHeartbeat) return false;
  return Date.now() - new Date(lastHeartbeat).getTime() < 120_000;
}
