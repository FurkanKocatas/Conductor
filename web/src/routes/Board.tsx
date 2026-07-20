import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError, getLocalToken, getState, patchTask, setLocalToken,
  type Agent, type BoardState, type Status, type Task,
} from "../lib/api";
import { LANES, agentInk, agentLive, ago, initials, laneLabel } from "../lib/lanes";
import { demoState } from "../lib/demoData";
import { Shell } from "../components/Shell";
import { Button, Chip, Empty } from "../components/ui";
import "./Board.css";

const POLL_MS = 2500;

export default function Board({ demo = false }: { demo?: boolean }) {
  const [state, setState] = useState<BoardState | null>(demo ? demoState() : null);
  const [conn, setConn] = useState<"live" | "connecting" | "offline">(
    demo ? "live" : "connecting");
  const [needsToken, setNeedsToken] = useState(() => !demo && !getLocalToken());
  const [dragId, setDragId] = useState<string | null>(null);
  const [overLane, setOverLane] = useState<Status | null>(null);
  // Skip a poll while a drag is in flight so the board can't snap back mid-drop.
  const busy = useRef(false);

  const refresh = useCallback(async () => {
    if (busy.current) return;
    try {
      const s = await getState();
      setState(s);
      setConn("live");
      setNeedsToken(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setNeedsToken(true);
        setConn("offline");
      } else {
        setConn("offline");
      }
    }
  }, []);

  useEffect(() => {
    if (demo || needsToken) return;   // demo is static, and never asks for a token
    void refresh();
    const id = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(id);
  }, [refresh, needsToken, demo]);

  const byLane = useMemo(() => {
    const map = new Map<Status, Task[]>(LANES.map((l) => [l.key, []]));
    for (const t of state?.tasks ?? []) map.get(t.status)?.push(t);
    for (const list of map.values()) {
      list.sort((a, b) =>
        a.board_order - b.board_order ||
        b.priority - a.priority ||
        a.created_at.localeCompare(b.created_at));
    }
    return map;
  }, [state]);

  async function moveTask(id: string, status: Status) {
    const task = state?.tasks.find((t) => t.id === id);
    if (!task || task.status === status) return;
    if (demo) {   // demo moves are local only — nothing is persisted
      setState((s) => s && {
        ...s, tasks: s.tasks.map((t) => (t.id === id ? { ...t, status } : t)),
      });
      return;
    }
    busy.current = true;
    // Optimistic: move it now, reconcile on the next poll.
    setState((s) => s && {
      ...s,
      tasks: s.tasks.map((t) => (t.id === id ? { ...t, status } : t)),
    });
    try {
      await patchTask(id, { status });
    } catch {
      await refresh();
    } finally {
      busy.current = false;
      void refresh();
    }
  }

  if (needsToken) return <TokenGate onSaved={() => { setNeedsToken(false); void refresh(); }} />;

  return (
    <Shell project={state?.project} connection={conn}>
      <div className="board-layout">
        <section className="col col--side">
          <div className="col__head">
            <h2>Performers</h2>
            <span className="col__count tabular">{state?.agents.length ?? 0}</span>
          </div>
          <div className="scroll">
            {state?.agents.length
              ? state.agents.map((a) => <AgentRow key={a.id} agent={a} />)
              : <Empty>No performers</Empty>}
          </div>
        </section>

        <section className="col">
          <div className="lanes">
            {LANES.map((lane) => {
              const items = byLane.get(lane.key) ?? [];
              return (
                <div
                  className={`lane${overLane === lane.key ? " is-over" : ""}`}
                  key={lane.key}
                  style={{ ["--lane-plate" as string]: lane.plate }}
                  onDragOver={(e) => { e.preventDefault(); setOverLane(lane.key); }}
                  onDragLeave={() => setOverLane((l) => (l === lane.key ? null : l))}
                  onDrop={(e) => {
                    e.preventDefault();
                    setOverLane(null);
                    if (dragId) void moveTask(dragId, lane.key);
                    setDragId(null);
                  }}
                >
                  <div className="lane__head">
                    <span className="lane__title">{lane.label}</span>
                    <span className="lane__n tabular">{items.length}</span>
                  </div>
                  <div className="lane__body">
                    {items.map((t) => (
                      <TaskCard
                        key={t.id}
                        task={t}
                        dragging={dragId === t.id}
                        onDragStart={() => setDragId(t.id)}
                        onDragEnd={() => { setDragId(null); setOverLane(null); }}
                      />
                    ))}
                    {!items.length && <div className="lane__hint">{lane.hint}</div>}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="col col--feed">
          <div className="col__head"><h2>Log</h2></div>
          <div className="scroll">
            {state?.activity.length
              ? state.activity.slice(0, 50).map((a) => (
                  <div className="feed-item" key={`a${a.id}`}>
                    <span className="feed-item__ts">{ago(a.created_at)}</span>
                    <span className="grow">
                      <span className="feed-item__who">{a.actor ?? "system"}</span>{" "}
                      <span className="feed-item__body">{describe(a.kind, a.detail)}</span>
                    </span>
                  </div>
                ))
              : <Empty>No entries</Empty>}
          </div>
        </section>
      </div>
    </Shell>
  );
}

function TaskCard({
  task, dragging, onDragStart, onDragEnd,
}: {
  task: Task; dragging: boolean;
  onDragStart: () => void; onDragEnd: () => void;
}) {
  const promptChip = {
    pending: { text: "queued", color: "var(--mustard)" },
    running: { text: "running", color: "var(--red)" },
    done: { text: "filed", color: "var(--green)" },
    idle: null,
  }[task.prompt_state];

  const unmetDeps = task.depends_on?.length ?? 0;

  return (
    <article
      className={`task${dragging ? " is-dragging" : ""}`}
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
    >
      <div className="task__title">{task.title}</div>
      <div className="task__meta">
        {task.priority !== 0 && <span className="task__pri">P{task.priority}</span>}
        {task.assignee
          ? <Mark name={task.assignee} />
          : <Chip mono title="Any agent can claim this">auto</Chip>}
        {promptChip && (
          <span className="stamp task__stamp" style={{ color: promptChip.color }}>
            {promptChip.text}
          </span>
        )}
        {unmetDeps > 0 && (
          <Chip mono title={`${unmetDeps} dependency task(s)`}>⛔ {unmetDeps}</Chip>
        )}
      </div>
    </article>
  );
}

function AgentRow({ agent }: { agent: Agent }) {
  const live = agentLive(agent.last_heartbeat);
  const status = live ? agent.status : "offline";
  return (
    <div className={`agent${live ? "" : " is-off"}`}>
      <span className="agent__mark" aria-hidden="true"
            style={{ color: agentInk(agent.name) }}>
        {status === "working" ? "▸" : status === "blocked" ? "✖" : "•"}
      </span>
      <div className="grow">
        <div className="agent__name truncate">{agent.name}</div>
        <div className="agent__note truncate">
          {status === "offline" ? "offline" : agent.note || status}
        </div>
        {agent.branch && <div className="agent__branch">⎇ {agent.branch}</div>}
      </div>
      <span className="agent__hb">{ago(agent.last_heartbeat)}</span>
    </div>
  );
}

function describe(kind: string, detail: Record<string, unknown>): string {
  const title = typeof detail?.title === "string" ? detail.title : "";
  const map: Record<string, string> = {
    "task.created": "created", "task.claimed": "claimed",
    "task.updated": "updated", "task.deleted": "deleted",
    "task.grabbed": "picked up", "task.finished": "finished",
    "task.reclaimed": "reclaimed (lease expired)",
    "message.posted": "posted a message", "memory.added": "wrote a note",
    "agent.registered": "registered", "git.reported": "reported git status",
    "project.brief_updated": "updated the brief",
  };
  const verb = map[kind] ?? kind;
  const status = typeof detail?.status === "string" ? ` → ${laneLabel(detail.status)}` : "";
  return `${verb}${title ? ` “${title}”` : ""}${status}`;
}

function TokenGate({ onSaved }: { onSaved: () => void }) {
  const [value, setValue] = useState("");
  return (
    <div className="gate">
      <div className="card gate__card">
        <div className="kick">Access</div>
        <h2>Present your token</h2>
        <p>Paste a project API token to open the board. Run <code>./run.sh token</code> for the local admin token.</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const v = value.trim();
            if (!v) return;
            setLocalToken(v);
            onSaved();
          }}
        >
          <input
            className="field"
            type="password"
            autoFocus
            placeholder="Token"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          <div style={{ marginTop: 12, display: "flex", justifyContent: "flex-end" }}>
            <Button variant="primary" type="submit" disabled={!value.trim()}>Connect</Button>
          </div>
        </form>
      </div>
    </div>
  );
}


/** Colour-coded initials. Each agent keeps the same ink everywhere, so people
 *  are recognisable at a glance without reading a name. */
function Mark({ name }: { name: string }) {
  return (
    <span className="chip" style={{ color: agentInk(name), borderColor: "currentColor" }} title={name}>
      {initials(name)}
    </span>
  );
}
