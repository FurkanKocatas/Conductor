import type { Status } from "./api";

/** Kanban lanes. `key` matches the backend status value and must never change;
 *  `label` is display-only. Each lane carries its own token pair so status is
 *  encoded in colour AND position, not colour alone. */
export const LANES: {
  key: Status; label: string; fg: string; bg: string; hint: string;
}[] = [
  { key: "todo",        label: "To Do",   fg: "var(--ink-peri)",      bg: "var(--ink-peri-soft)",      hint: "Waiting to be claimed" },
  { key: "claimed",     label: "Active",  fg: "var(--ink-tangerine)", bg: "var(--ink-tangerine-soft)", hint: "An agent is on it" },
  { key: "in_progress", label: "Test",    fg: "var(--ink-blue)",      bg: "var(--ink-blue-soft)",      hint: "Built, being verified" },
  { key: "blocked",     label: "Blocked", fg: "var(--ink-pink)",      bg: "var(--ink-pink-soft)",      hint: "Needs a decision" },
  { key: "review",      label: "Review",  fg: "var(--ink-violet)",    bg: "var(--ink-violet-soft)",    hint: "Awaiting sign-off" },
  { key: "done",        label: "Done",    fg: "var(--ink-teal)",      bg: "var(--ink-teal-soft)",      hint: "Finished" },
];

/** Each agent gets a stable ink, so a person recognises "the violet one" at a
 *  glance. Deterministic hash → no flicker between renders. */
const AGENT_INKS = ["peri", "blue", "teal", "violet", "pink", "amber", "tangerine"] as const;

export function agentInk(name: string): { fg: string; bg: string } {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  const ink = AGENT_INKS[h % AGENT_INKS.length];
  return { fg: `var(--ink-${ink})`, bg: `var(--ink-${ink}-soft)` };
}

export function initials(name: string): string {
  const parts = name.split(/[-_\s.]+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

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
