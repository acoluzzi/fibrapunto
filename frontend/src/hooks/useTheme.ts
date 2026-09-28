import { useState } from "react";

/** Light or dark, chosen by the viewer, never by the OS.
 *
 *  This must agree with the stylesheet's default, or the first press appears
 *  to do nothing: it would compute the theme that is already on screen.
 *  styles.css declares light on :root and applies dark only under
 *  [data-theme="dark"] — the OS setting is deliberately not consulted, so a
 *  demo looks the same on any laptop. Hence "light" when the attribute is
 *  absent. Changing one of these two without the other reintroduces the
 *  two-press bug. */
export function useTheme(): ["light" | "dark", () => void] {
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    document.documentElement.dataset.theme === "dark" ? "dark" : "light",
  );
  function toggle() {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    setTheme(next);
  }
  return [theme, toggle];
}
