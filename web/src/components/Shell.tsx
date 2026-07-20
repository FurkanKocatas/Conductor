import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { useTheme } from "../lib/theme";
import { Button } from "./ui";
import "./Shell.css";

function ThemeToggle() {
  const { choice, cycle, isDark } = useTheme();
  const icon = choice === "system" ? "◐" : isDark ? "☾" : "☀";
  const next = choice === "system" ? "light" : choice === "light" ? "dark" : "system";
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={cycle}
      title={`Theme: ${choice} — switch to ${next}`}
      aria-label={`Theme: ${choice}. Switch to ${next}.`}
    >
      <span aria-hidden="true" style={{ fontSize: 14, lineHeight: 1 }}>{icon}</span>
    </Button>
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
    <div className="shell">
      <header className="topbar">
        <span className="brand">
          <span className="brand__mark" aria-hidden="true"><i /><i /><i /></span>
          Conductor
        </span>

        {project && <span className="project truncate" title={project}>{project}</span>}

        <nav className="nav">
          <NavLink to="/board">Board</NavLink>
          <NavLink to="/memory">Memory</NavLink>
          <NavLink to="/analytics">Analytics</NavLink>
        </nav>

        <div className="topbar__end">
          <span className="live" title={`Connection: ${conn}`}>
            <span className={dotClass} aria-hidden="true" />
            {conn}
          </span>
          {actions}
          <ThemeToggle />
        </div>
      </header>

      <main className="shell__body">{children}</main>
    </div>
  );
}
