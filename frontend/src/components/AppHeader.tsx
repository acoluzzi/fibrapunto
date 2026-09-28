import type { ReactNode } from "react";
import { Mark } from "./Brand";
import { ThemeButton } from "./ThemeButton";
import { useTheme } from "../hooks/useTheme";

export type View = "customer" | "console" | "operator" | "analyst";

const VIEWS: { key: View; href: string; label: string; external?: boolean }[] = [
  { key: "customer", href: "/", label: "Conversation" },
  { key: "console", href: "/console", label: "Console", external: true },
  { key: "operator", href: "/operator", label: "Operator" },
  { key: "analyst", href: "/analyst", label: "Analyst" },
];

/** One header for the four views: the brand, the role, what sits in the
 *  middle (a conversation selector, filters), the view switcher and the
 *  theme. The console opens in its own tab, so on stage the conversation and
 *  its trace can sit side by side; each gated view asks for its own password. */
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
  return (
    <header className="topbar">
      <a className="brand" href="/">
        <Mark />
        <span className="name">Fibrapunto</span>
        {role && <span className="role">{role}</span>}
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
              target={v.external && v.key !== view ? "_blank" : undefined}
              rel={v.external ? "noopener" : undefined}
            >
              {v.label}
              {v.external && v.key !== view && <span className="ext">↗</span>}
            </a>
          ))}
        </nav>
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
