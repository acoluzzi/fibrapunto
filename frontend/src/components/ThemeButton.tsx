export function ThemeButton({ theme, onToggle }: { theme: "light" | "dark"; onToggle: () => void }) {
  return (
    <button onClick={onToggle} title="Light / dark theme" aria-label="Light / dark theme">
      {theme === "dark" ? "☾" : "☀"}
    </button>
  );
}
