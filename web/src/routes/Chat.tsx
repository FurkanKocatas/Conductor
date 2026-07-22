import {
  Fragment, useCallback, useEffect, useMemo, useRef, useState,
} from "react";
import {
  ApiError, getState, pollMessages, postMessage,
  type Agent, type Message,
} from "../lib/api";
import { agentInk, ago } from "../lib/lanes";
import { Shell } from "../components/Shell";
import { Button, Empty } from "../components/ui";
import { demoState } from "../lib/demoData";
import "./Chat.css";

const POLL_MS = 2500;

interface Candidate { name: string; insert: string; sub: string; bot: boolean; }

export default function Chat({ demo = false }: { demo?: boolean }) {
  const [messages, setMessages] = useState<Message[]>(demo ? demoMessages() : []);
  const [agents, setAgents] = useState<Agent[]>(demo ? demoState().agents : []);
  const [conn, setConn] = useState<"live" | "connecting" | "offline">(demo ? "live" : "connecting");
  const since = useRef(0);
  const logRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [msgs, state] = await Promise.all([pollMessages(since.current), getState()]);
      if (msgs.length) {
        since.current = Math.max(since.current, ...msgs.map((m) => m.id));
        setMessages((prev) => [...prev, ...msgs]);
      }
      setAgents(state.agents);
      setConn("live");
    } catch (e) {
      setConn(e instanceof ApiError && e.status === 401 ? "offline" : "offline");
    }
  }, []);

  useEffect(() => {
    if (demo) return;
    void refresh();
    const id = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(id);
  }, [refresh, demo]);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const candidates = useMemo<Candidate[]>(() => {
    const list: Candidate[] = [];
    for (const a of agents) {
      list.push({ name: a.name, insert: a.name, sub: "notify", bot: false });
      list.push({
        name: `${a.name}-claude`, insert: `${a.name}-claude`,
        sub: `ask ${a.name}'s Claude`, bot: true,
      });
    }
    return list;
  }, [agents]);

  async function send(text: string) {
    const body = text.trim();
    if (!body) return;
    if (demo) {
      setMessages((prev) => [...prev, {
        id: Date.now(), from_agent: "you", to_agent: null, body, task_id: null,
        created_at: new Date().toISOString(),
      }]);
      return;
    }
    // Optimistic; the poll reconciles with the server copy.
    setMessages((prev) => [...prev, {
      id: -Date.now(), from_agent: "you", to_agent: null, body, task_id: null,
      created_at: new Date().toISOString(),
    }]);
    try { await postMessage(body); } catch { /* poll will retry */ }
  }

  return (
    <Shell project={demo ? "acme/checkout-service" : undefined} connection={conn}>
      <div className="chat">
        <div className="chat__log" ref={logRef}>
          {messages.length === 0 ? (
            <div className="chat__empty">
              No messages yet — this is the team room. 👋<br />
              Type <b>@</b> to notify a teammate, or <b>@name-claude</b> to ask
              their Claude a question.
            </div>
          ) : (
            messages.map((m) => <MessageRow key={m.id} m={m} />)
          )}
          {conn === "offline" && !demo && messages.length === 0 && (
            <Empty>Not connected</Empty>
          )}
        </div>
        <Composer candidates={candidates} onSend={send} />
      </div>
    </Shell>
  );
}

function MessageRow({ m }: { m: Message }) {
  const who = m.from_agent ?? "system";
  const bot = who.endsWith("-claude");
  return (
    <div className={`msg${bot ? " is-bot" : ""}`}>
      <span className="msg__mark" aria-hidden="true" style={{ color: agentInk(who) }}>
        {bot ? "◆" : "▪"}
      </span>
      <div className="msg__body">
        <div className="msg__head">
          <span className="msg__who">{who}</span>
          {m.to_agent && <span className="msg__to">→ {m.to_agent}</span>}
          <span className="msg__ts">{ago(m.created_at)}</span>
        </div>
        <div className="msg__text">{highlightMentions(m.body)}</div>
      </div>
    </div>
  );
}

function highlightMentions(text: string) {
  const parts = text.split(/(@[a-zA-Z0-9._-]+)/g);
  return parts.map((p, i) =>
    p.startsWith("@")
      ? <span className="mention" key={i}>{p}</span>
      : <Fragment key={i}>{p}</Fragment>);
}

function Composer({
  candidates, onSend,
}: {
  candidates: Candidate[]; onSend: (text: string) => void;
}) {
  const [value, setValue] = useState("");
  const [open, setOpen] = useState(false);
  const [idx, setIdx] = useState(0);
  const ref = useRef<HTMLTextAreaElement>(null);

  // Active @query = the token right before the caret, if it starts with @.
  const query = useMemo(() => {
    const el = ref.current;
    if (!el) return null;
    const upto = value.slice(0, el.selectionStart ?? value.length);
    const m = upto.match(/@([a-zA-Z0-9._-]*)$/);
    return m ? m[1].toLowerCase() : null;
  }, [value]);

  const matches = useMemo(() => {
    if (query === null) return [];
    return candidates
      .filter((c) => c.insert.toLowerCase().includes(query))
      .slice(0, 8);
  }, [candidates, query]);

  useEffect(() => {
    setOpen(matches.length > 0 && query !== null);
    setIdx(0);
  }, [matches.length, query]);

  function choose(c: Candidate) {
    const el = ref.current;
    const caret = el?.selectionStart ?? value.length;
    const before = value.slice(0, caret).replace(/@([a-zA-Z0-9._-]*)$/, `@${c.insert} `);
    const next = before + value.slice(caret);
    setValue(next);
    setOpen(false);
    requestAnimationFrame(() => el?.focus());
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (open && matches.length) {
      if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => (i + 1) % matches.length); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => (i - 1 + matches.length) % matches.length); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); choose(matches[idx]); return; }
      if (e.key === "Escape") { setOpen(false); return; }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend(value);
      setValue("");
    }
  }

  return (
    <div className="composer">
      {open && (
        <div className="mentions" role="listbox">
          <div className="mentions__hdr">Address</div>
          {matches.map((c, i) => (
            <div
              key={c.insert}
              className={`mentions__item${i === idx ? " on" : ""}`}
              role="option"
              aria-selected={i === idx}
              onMouseDown={(e) => { e.preventDefault(); choose(c); }}
            >
              <span className="mentions__name" style={{ color: c.bot ? "var(--red)" : undefined }}>
                {c.bot ? "◆" : "▪"} {c.name}
              </span>
              <span className="mentions__insert">@{c.insert}</span>
              <span className="mentions__sub">{c.sub}</span>
            </div>
          ))}
        </div>
      )}
      <textarea
        ref={ref}
        className="field"
        placeholder="Message the team…  @ to address, Enter to send"
        value={value}
        rows={1}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
      />
      <Button variant="primary" onClick={() => { onSend(value); setValue(""); }} disabled={!value.trim()}>
        Send
      </Button>
    </div>
  );
}

function demoMessages(): Message[] {
  const t = (s: number) => new Date(Date.now() - s * 1000).toISOString();
  return [
    { id: 1, from_agent: "dev-a", to_agent: null, body: "Picking up the retry backoff task.", task_id: null, created_at: t(600) },
    { id: 2, from_agent: "reviewer", to_agent: "dev-a", body: "@dev-a make sure the cap is tested", task_id: null, created_at: t(520) },
    { id: 3, from_agent: "dev-b", to_agent: null, body: "@dev-a-claude what's the current max backoff on attempt 5?", task_id: null, created_at: t(400) },
    { id: 4, from_agent: "dev-a-claude", to_agent: "dev-b", body: "On attempt 5 it's 16s (2^4). I'm capping it at min(2^attempt, 8) with jitter.", task_id: null, created_at: t(360) },
    { id: 5, from_agent: "dev-b", to_agent: null, body: "nice, that fixes the timeout we saw in staging", task_id: null, created_at: t(120) },
  ];
}
