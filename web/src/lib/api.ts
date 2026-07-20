/** API client.
 *
 * Two auth modes, matching the backend:
 *   • local / self-hosted — a project bearer token kept in localStorage (the
 *     legacy board flow). Works with no Clerk account.
 *   • SaaS — a Clerk session JWT supplied by the host app.
 *
 * `setTokenProvider` lets the Clerk layer take over without this module
 * depending on Clerk, so the app still builds and runs standalone.
 */

export type Status =
  | "todo" | "claimed" | "in_progress" | "blocked" | "review" | "done";

export interface Task {
  id: string;
  title: string;
  spec: string | null;
  status: Status;
  assignee: string | null;
  assign_mode: "auto" | "manual";
  priority: number;
  board_order: number;
  depends_on: string[];
  artifacts: Record<string, unknown>;
  prompt: string | null;
  prompt_state: "idle" | "pending" | "running" | "done";
  created_at: string;
  updated_at: string;
}

export interface Agent {
  id: string;
  name: string;
  machine: string | null;
  status: "offline" | "idle" | "working" | "blocked";
  note: string | null;
  branch: string | null;
  git: Record<string, unknown>;
  current_task_id: string | null;
  last_heartbeat: string | null;
}

export interface Activity {
  id: number; actor: string | null; kind: string;
  detail: Record<string, unknown>; created_at: string;
}

export interface Message {
  id: number; from_agent: string | null; to_agent: string | null;
  body: string; task_id: string | null; created_at: string;
}

export interface BoardState {
  project: string;
  brief: string | null;
  agents: Agent[];
  tasks: Task[];
  counts: Partial<Record<Status, number>>;
  activity: Activity[];
  messages: Message[];
  users: { label: string; role: string; online: boolean }[];
  server_time: string;
}

const TOKEN_KEY = "conductor_token";

let tokenProvider: (() => Promise<string | null>) | null = null;

/** Let an auth layer (e.g. Clerk) supply the bearer token instead of localStorage. */
export function setTokenProvider(fn: (() => Promise<string | null>) | null) {
  tokenProvider = fn;
}

export function getLocalToken(): string {
  try { return localStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; }
}

export function setLocalToken(token: string) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* ignore */ }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function authHeader(): Promise<string | null> {
  if (tokenProvider) {
    const t = await tokenProvider();
    if (t) return `Bearer ${t}`;
  }
  const local = getLocalToken();
  return local ? `Bearer ${local}` : null;
}

export async function api<T>(
  method: string, path: string, body?: unknown,
): Promise<T> {
  const auth = await authHeader();
  const headers: Record<string, string> = {};
  if (auth) headers.Authorization = auth;
  if (body !== undefined) headers["content-type"] = "application/json";

  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (res.status === 204) return undefined as T;
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data?.detail === "string" ? data.detail
             : JSON.stringify(data?.detail ?? data);
    } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const getState = () => api<BoardState>("GET", "/api/state");

export const patchTask = (id: string, patch: Partial<Task>) =>
  api<Task>("PATCH", `/api/tasks/${id}`, patch);

export const createTask = (body: {
  title: string; spec?: string; priority?: number;
  assign_mode?: string; assignee?: string | null;
}) => api<Task>("POST", "/api/tasks", body);

export const deleteTask = (id: string) =>
  api<{ deleted: string }>("DELETE", `/api/tasks/${id}`);

export const postMessage = (body: string, to_agent?: string) =>
  api<Message>("POST", "/api/messages", { body, to_agent: to_agent || null });
