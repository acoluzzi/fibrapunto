import type { ReactNode } from "react";
import { Mark } from "./Brand";
import { PersonasButton } from "./PersonaCard";
import { ThemeButton } from "./ThemeButton";
import { usePageTitle } from "../hooks/usePageTitle";
import { useTheme } from "../hooks/useTheme";
import { PERSONAS, VIEW_LABEL, type View } from "../lib/personas";

export type { View };

const VIEWS: { key: View; href: string; label: string; external?: boolean }[] = [
  { key: "customer", href: "/", label: VIEW_LABEL.customer },
  { key: "console", href: "/console", label: VIEW_LABEL.console, external: true },
  { key: "operator", href: "/operator", label: VIEW_LABEL.operator },
  { key: "analyst", href: "/analyst", label: VIEW_LABEL.analyst },
];

/** One header for the four views: the brand, who the view is for, what sits
 *  in the middle (a conversation selector, filters), the view switcher and
 *  the theme. The console opens in its own tab, so on stage the conversation
 *  and its trace can sit side by side; each gated view asks for its own
 *  password.
 *
 *  On the gated views the badge names the persona rather than the permission
 *  ("Marta · retention & billing operations", not "operator"), and "Who it's
 *  for" opens all four. The customer's page stays plain: no badge, no panel. */
export function AppHeader({
  view,
  role,
  children,
  onLogout,
}: {
  view: View;
  role?: string;
  children?: ReactNode;
  onLogout?: () => void;
}) {
  const [theme, toggleTheme] = useTheme();
  const persona = PERSONAS[view];
  const gated = view !== "customer";
  usePageTitle(view, "in");
  return (
    <header className="topbar">
      <a className="brand" href="/">
        <Mark />
        <span className="name">Fibrapunto</span>
        {gated && (
          <span className="role" title={role ?? persona.role}>
            {persona.name} · {persona.role}
          </span>
        )}
      </a>
      <div className="center">{children}</div>
      <div className="right">
        <nav className="viewnav" aria-label="Views">
          {VIEWS.map((v) => (
            <a
              key={v.key}
              href={v.href}
              className={v.key === view ? "current" : undefined}
              aria-current={v.key === view ? "page" : undefined}
              title={`${PERSONAS[v.key].name}, ${PERSONAS[v.key].role}`}
              target={v.external && v.key !== view ? "_blank" : undefined}
              rel={v.external ? "noopener" : undefined}
            >
              {v.label}
              {v.external && v.key !== view && <span className="ext">↗</span>}
            </a>
          ))}
        </nav>
        {gated && <PersonasButton view={view} />}
        {onLogout && (
          <button className="ghost small" onClick={onLogout}>
            Sign out
          </button>
        )}
        <ThemeButton theme={theme} onToggle={toggleTheme} />
      </div>
    </header>
  );
}
