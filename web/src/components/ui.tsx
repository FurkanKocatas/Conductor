import type { ButtonHTMLAttributes, ReactNode } from "react";
import "./ui.css";

type Variant = "default" | "primary" | "ghost" | "danger";

export function Button({
  variant = "default", size, className = "", children, ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant; size?: "sm" | "icon";
}) {
  const cls = [
    "btn",
    variant !== "default" ? `btn--${variant}` : "",
    size ? `btn--${size}` : "",
    className,
  ].filter(Boolean).join(" ");
  return <button className={cls} {...rest}>{children}</button>;
}

export function Chip({
  children, mono, fg, bg, title,
}: {
  children: ReactNode; mono?: boolean;
  fg?: string; bg?: string; title?: string;
}) {
  return (
    <span
      className={`chip${mono ? " chip--mono" : ""}`}
      title={title}
      style={fg || bg ? { color: fg, background: bg } : undefined}
    >
      {children}
    </span>
  );
}

/** Agent liveness as shape + colour, so state is readable without relying on hue. */
export function StatusDot({ status, live }: { status: string; live: boolean }) {
  const mod = !live ? "" : status === "working" ? " dot--working"
    : status === "blocked" ? " dot--blocked" : " dot--live";
  return <span className={`dot${mod}`} aria-hidden="true" />;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Spinner() {
  return <span className="spinner" role="status" aria-label="Loading" />;
}
