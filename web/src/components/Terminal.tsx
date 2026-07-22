import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { tailStream, type StreamEvent } from "../lib/api";
import "./Terminal.css";

const POLL_MS = 1200;

/** Live wire-report of a teammate's Claude. Tails /api/stream, coalescing
 *  consecutive text chunks into one growing paragraph (that's how token streams
 *  arrive), and rendering tool/result/sys as their own printed blocks.
 *
 *  In demo mode a scripted stream plays so the feature is visible with no backend.
 */
export function Terminal({
  agent, demo = false, onClose,
}: {
  agent: string; demo?: boolean; onClose: () => void;
}) {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [status, setStatus] = useState("connecting");
  const since = useRef(0);
  const bodyRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    since.current = 0;
    setEvents([]);

    if (demo) {
      setStatus("running · live");
      const script = demoScript(agent);
      let i = 0;
      const id = setInterval(() => {
        if (i >= script.length) { setStatus("task complete"); clearInterval(id); return; }
        const ev = { ...script[i], id: i + 1 };
        setEvents((prev) => [...prev, ev]);
        i++;
      }, 550);
      return () => clearInterval(id);
    }

    let alive = true;
    const tick = async () => {
      try {
        const rows = await tailStream(agent, since.current);
        if (!alive) return;
        if (rows.length) {
          since.current = Math.max(since.current, ...rows.map((r) => r.id));
          setEvents((prev) => [...prev, ...rows]);
          const last = rows[rows.length - 1].content;
          setStatus(last.includes("done") ? "task complete" : "running · live");
        } else if (since.current === 0) {
          setStatus("idle");
        }
      } catch {
        if (alive) setStatus("offline");
      }
    };
    void tick();
    const id = setInterval(() => void tick(), POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, [agent, demo]);

  // Keep pinned to the bottom only if the viewer hasn't scrolled up.
  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [events]);

  const running = status.startsWith("running");
  const merged = coalesce(events);

  return (
    <div className="term-backdrop" onClick={onClose}>
      <div className="term" onClick={(e) => e.stopPropagation()}>
        <div className="term__head">
          <div>
            <div className="term__fig">Live wire · fig. ∞</div>
            <div className="term__who">{agent}</div>
          </div>
          <span className="term__sub">
            <span className={running ? "dot dot--working" : "dot"} aria-hidden="true" />
            {status}
          </span>
          <button className="term__x" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div
          className="term__body"
          ref={bodyRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
          }}
        >
          {merged.length === 0 ? (
            <div className="term__empty">
              No live stream right now.<br />
              When a task for <b>{agent}</b> goes Active, everything its Claude
              does — reasoning, commands, results — prints here as it happens.
            </div>
          ) : (
            <>
              {merged.map((ev, i) => (
                <div key={i} className={cls(ev.kind)}>{ev.content}</div>
              ))}
              {running && <span className="live-cursor" aria-hidden="true" />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function cls(kind: StreamEvent["kind"]) {
  return kind === "tool" ? "s-tool" : kind === "result" ? "s-result"
    : kind === "sys" ? "s-sys" : "s-text";
}

/** Merge runs of `text` events into a single block; keep others discrete. */
function coalesce(events: StreamEvent[]): { kind: StreamEvent["kind"]; content: string }[] {
  const out: { kind: StreamEvent["kind"]; content: string }[] = [];
  for (const e of events) {
    const last = out[out.length - 1];
    if (e.kind === "text" && last?.kind === "text") last.content += e.content;
    else out.push({ kind: e.kind, content: e.content });
  }
  return out;
}

function demoScript(agent: string): Omit<StreamEvent, "id">[] {
  const t = () => new Date().toISOString();
  return [
    { kind: "sys", content: `Claude session started for ${agent}`, task_id: null, created_at: t() },
    { kind: "text", content: "Reading the failing test to understand the retry path… ", task_id: null, created_at: t() },
    { kind: "text", content: "the backoff never caps, so the 5th attempt waits 16s. ", task_id: null, created_at: t() },
    { kind: "tool", content: "grep -n 'sleep' payments/retry.py", task_id: null, created_at: t() },
    { kind: "result", content: "payments/retry.py:42:  time.sleep(2 ** attempt)", task_id: null, created_at: t() },
    { kind: "text", content: "Adding a ceiling with min(2**attempt, 8) and a jitter. ", task_id: null, created_at: t() },
    { kind: "tool", content: "pytest tests/test_retry.py -q", task_id: null, created_at: t() },
    { kind: "result", content: "4 passed in 0.31s", task_id: null, created_at: t() },
    { kind: "sys", content: "Task moved to Test. done", task_id: null, created_at: t() },
  ];
}
