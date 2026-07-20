import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { useTheme } from "../lib/theme";
import "./Shell.css";

function EditionToggle() {
  const { choice, cycle, isDark } = useTheme();
  const next = choice === "system" ? "day" : choice === "light" ? "night" : "auto";
  const label = choice === "system" ? "Auto" : isDark ? "Night" : "Day";
  return (
    <button
      className="btn btn--sm"
      onClick={cycle}
      title={`Edition: ${label} — switch to ${next}`}
      aria-label={`Edition: ${label}. Switch to ${next}.`}
    >
      {label} ed.
    </button>
  );
}

export function Shell({
  project, connection, actions, children,
}: {
  project?: string;
  connection?: "live" | "connecting" | "offline";
  actions?: ReactNode;
  children: ReactNode;
}) {
  const conn = connection ?? "connecting";
  const dotClass = conn === "live" ? "dot dot--live"
    : conn === "offline" ? "dot dot--blocked" : "dot";

  return (
    <div className="shell grain">
      <div className="sheet">
        <span className="crop tl" /><span className="crop tr" />
        <span className="crop bl" /><span className="crop br" />

        <header className="masthead">
          <span className="brand">Conductor<span className="dot">.</span></span>
          {project && <span className="edition truncate" title={project}>{project}</span>}

          <nav className="nav">
            <NavLink to="/board">Board</NavLink>
            <NavLink to="/memory">Memory</NavLink>
            <NavLink to="/analytics">Analytics</NavLink>
          </nav>

          <span className="masthead__end">
            <span className="live" title={`Connection: ${conn}`}>
              <span className={dotClass} aria-hidden="true" />
              {conn}
            </span>
            {actions}
            <EditionToggle />
          </span>
        </header>

        <main className="shell__body">{children}</main>

        <footer className="folio">
          <span className="no">Agent orchestration</span>
          <span className="grow">Two agents never touch the same file</span>
          <span className="barcode" aria-hidden="true">
            {Array.from({ length: 14 }, (_, i) => <i key={i} />)}
          </span>
        </footer>
      </div>
    </div>
  );
}
