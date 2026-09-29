import { useEffect, useRef, useState } from "react";
import { PERSONAS, PERSONA_ORDER, type Persona, type View } from "../lib/personas";

/** One persona, as a card: who, when, the first job, what is kept from them,
 *  and the decision that follows. `compact` drops the decision, for the
 *  sign-in form, where the point is who is about to sign in. */
export function PersonaCard({
  persona,
  compact = false,
  current = false,
}: {
  persona: Persona;
  compact?: boolean;
  current?: boolean;
}) {
  return (
    <section className={`persona${current ? " current" : ""}`} aria-label={`${persona.name}, ${persona.role}`}>
      <header>
        <span className="persona-name">{persona.name}</span>
        <span className="persona-role">{persona.role}</span>
        {current && <span className="persona-here">this view</span>}
      </header>
      <p className="persona-situation">{persona.situation}</p>
      <dl>
        <dt>First job</dt>
        <dd>{persona.firstJob}</dd>
        <dt>Never shown</dt>
        <dd>{persona.never}</dd>
        {!compact && (
          <>
            <dt>So</dt>
            <dd>
              {persona.decision} <span className="persona-refs">{persona.refs.join(" · ")}</span>
            </dd>
          </>
        )}
      </dl>
    </section>
  );
}

/** The header button on the gated views: "Who it's for" opens the four
 *  personas, the current view's first. It is the one place where the whole
 *  cast is visible at once, from any page. */
export function PersonasButton({ view }: { view: View }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  const order = [view, ...PERSONA_ORDER.filter((v) => v !== view)];

  return (
    <div className="personas" ref={ref}>
      <button
        className="ghost small"
        aria-expanded={open}
        aria-controls="personas-panel"
        onClick={() => setOpen((o) => !o)}
        title="Who each view is built for"
      >
        Who it's for
      </button>
      {open && (
        <div id="personas-panel" className="personas-panel" role="dialog" aria-label="Who each view is built for">
          <p className="personas-intro">
            Four views, four people. Each card ends with the design decision the person explains, and where it is
            written down in the spec.
          </p>
          {order.map((v) => (
            <PersonaCard key={v} persona={PERSONAS[v]} current={v === view} />
          ))}
        </div>
      )}
    </div>
  );
}
