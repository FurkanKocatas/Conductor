/** Theme controller: follow the OS by default, remember an explicit choice.
 *
 * "system" stores nothing and lets the prefers-color-scheme media query in
 * tokens.css decide. Choosing light/dark stamps data-theme on <html>, which
 * overrides the media query in both directions.
 */
import { useCallback, useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";

const KEY = "conductor_theme";

export function readTheme(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function apply(choice: ThemeChoice) {
  const root = document.documentElement;
  if (choice === "system") delete root.dataset.theme;
  else root.dataset.theme = choice;
  try {
    if (choice === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, choice);
  } catch {
    /* private mode — theme just won't persist */
  }
}

/** Returns the current choice and whether it currently resolves to dark. */
export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(readTheme);
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false,
  );

  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!mq) return;
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    apply(choice);
  }, [choice]);

  const cycle = useCallback(() => {
    setChoice((c) => (c === "system" ? "light" : c === "light" ? "dark" : "system"));
  }, []);

  const isDark = choice === "dark" || (choice === "system" && systemDark);
  return { choice, setChoice, cycle, isDark };
}
