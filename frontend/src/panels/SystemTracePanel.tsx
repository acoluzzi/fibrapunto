import { useState } from "react";
import { statusOf } from "../lib/outcome";
import type { TraceEvent } from "../lib/types";
import { formatCost } from "./MetricsPanel";

/** A cost chip, or nothing when the provider did not report one (§16). */
function CostChip({ usd }: { usd: unknown }) {
  if (typeof usd !== "number") return null;
  const { value, unit } = formatCost(usd);
  return <span className="chip">{unit}{value}</span>;
}

/** §17.10 made visible: which domain fired, which capabilities were called,
 *  and with what typed outcome.
 *
 *  This panel is the point of the demo. The chat shows an agent being helpful;
 *  this shows *why* it was allowed to be, and — when the billing system is
 *  down — why it declined to be.
 *
 *  Laid out as a waterfall per turn: each event is a row, and the bar beside
 *  it is when it ran within the turn and for how long. The model calls and
 *  the capability calls sit indented under the turn, since they are what the
 *  turn was made of; the router's decision, the domain and the reply are the
 *  turn's own milestones. A row opens to its raw JSON. */

type Kind = "routing" | "llm" | "capability" | "guardrail" | "escalation" | "other";

function kindOf(name: string): Kind {
  if (name.startsWith("router") || name.startsWith("domain")) return "routing";
  if (name.startsWith("llm")) return "llm";
  if (name.startsWith("capability") || name.startsWith("offer")) return "capability";
  if (name.startsWith("guardrail") || name.startsWith("privacy")) return "guardrail";
  if (name.startsWith("escalation")) return "escalation";
  return "other";
}

const KINDS: { key: Kind; label: string }[] = [
  { key: "routing", label: "routing" },
  { key: "llm", label: "model" },
  { key: "capability", label: "capabilities" },
  { key: "guardrail", label: "guardrails" },
  { key: "escalation", label: "escalation" },
  { key: "other", label: "other" },
];

const NESTED = new Set<Kind>(["llm", "capability"]);

export function SystemTracePanel({ events }: { events: TraceEvent[] }) {
  const [hidden, setHidden] = useState<Set<Kind>>(new Set());
  const [query, setQuery] = useState("");
  const [openRow, setOpenRow] = useState<number | null>(null);

  const byTurn = new Map<number, { index: number; event: TraceEvent }[]>();
  events.forEach((event, index) => {
    const turn = event.turn ?? 0;
    if (!byTurn.has(turn)) byTurn.set(turn, []);
    byTurn.get(turn)!.push({ index, event });
  });
  const q = query.trim().toLowerCase();
  const shown = (event: TraceEvent) =>
    !hidden.has(kindOf(event.name)) &&
    (q === "" || event.name.includes(q) || JSON.stringify(event).toLowerCase().includes(q));

  function toggle(kind: Kind) {
    setHidden((current) => {
      const next = new Set(current);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>What happened in the system</h2>
        <span className="hint">routing, capabilities, outcomes</span>
        <span className="spacer" />
        <span className="hint">{events.length} events</span>
      </div>
      <div className="trace-tools">
        {KINDS.map((k) => (
          <button
            key={k.key}
            className={`chipbtn ${hidden.has(k.key) ? "" : "on"}`}
            onClick={() => toggle(k.key)}
            aria-pressed={!hidden.has(k.key)}
          >
            {k.label}
          </button>
        ))}
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search events"
          aria-label="Search events"
        />
      </div>
      <div className="panel-body">
        {events.length === 0 && <p className="empty">No events yet.</p>}
        {[...byTurn.entries()].map(([turn, rows]) => {
          const visible = rows.filter((r) => shown(r.event));
          if (visible.length === 0) return null;
          // The turn's span: from its earliest start to its latest end. An
          // event's `at` is when it was emitted — the end of anything timed.
          const ends = rows.map((r) => Date.parse(r.event.at));
          const starts = rows.map((r, i) => ends[i] - (r.event.duration_ms ?? 0));
          const t0 = Math.min(...starts);
          const span = Math.max(1, Math.max(...ends) - t0);
          return (
            <details className="turn" key={turn} open>
              <summary>
                <span className="caret" aria-hidden="true">▸</span>
                {turn > 0 ? `turn ${turn}` : "session"}
                <span className="span">{turn > 0 ? `${(span / 1000).toFixed(1)} s` : ""}</span>
              </summary>
              {visible.map(({ index, event }) => (
                <Row
                  key={index}
                  event={event}
                  t0={t0}
                  span={span}
                  open={openRow === index}
                  onToggle={() => setOpenRow(openRow === index ? null : index)}
                />
              ))}
            </details>
          );
        })}
      </div>
    </section>
  );
}

function Row({
  event,
  t0,
  span,
  open,
  onToggle,
}: {
  event: TraceEvent;
  t0: number;
  span: number;
  open: boolean;
  onToggle: () => void;
}) {
  const kind = kindOf(event.name);
  const end = Date.parse(event.at) - t0;
  const duration = event.duration_ms ?? 0;
  const left = Math.max(0, ((end - duration) / span) * 100);
  const width = Math.max(0, (duration / span) * 100);
  const bad =
    (event.name === "capability.result" && ["serviceUnavailable", "failed"].includes(String(event.outcome))) ||
    (event.name === "guardrail.triggered" && event.blocking === true) ||
    event.cut_off === true;
  return (
    <div
      className={`event ${NESTED.has(kind) ? "nested" : ""} ${open ? "open" : ""}`}
      onClick={onToggle}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onToggle())}
    >
      <span className="glyph">{glyphFor(event.name)}</span>
      <div style={{ minWidth: 0 }}>
        <div className="name">{event.name}</div>
        <Detail event={event} />
      </div>
      <div className="bar" aria-hidden="true">
        {duration > 0 ? (
          <i className={`${kind} ${bad ? "bad" : ""}`} style={{ left: `${left}%`, width: `${width}%` }} />
        ) : (
          <i className="tick" style={{ left: `${Math.min(99, (end / span) * 100)}%` }} />
        )}
      </div>
      <span className="ms">{event.duration_ms != null ? `${event.duration_ms} ms` : ""}</span>
      {open && (
        <pre className="event-json" onClick={(e) => e.stopPropagation()}>
          {JSON.stringify(event, null, 2)}
        </pre>
      )}
    </div>
  );
}

function Detail({ event }: { event: TraceEvent }) {
  switch (event.name) {
    case "router.decision": {
      const confidence = event.confidence as number | null;
      return (
        <div className="detail">
          → <strong>{String(event.chosen)}</strong>{" "}
          <span className="chip">{String(event.decided_by)}</span>
          {/* The router runs on its own pin (MODEL_ROUTER), not the agent's.
              Absent when the confirmation lock skipped the call. */}
          {event.model_served != null && (
            <span className="chip">{String(event.model_served)}</span>
          )}
          {confidence != null && <span className="chip">conf {confidence.toFixed(2)}</span>}
          {event.sentiment !== "neutral" && event.sentiment != null && (
            <span className="chip">{String(event.sentiment)}</span>
          )}
          <CostChip usd={event.cost_usd} />
        </div>
      );
    }

    case "agent.reply":
      return (
        <div className="detail">
          {event.model_served != null && (
            <span className="chip">{String(event.model_served)}</span>
          )}
          {/* §16: what the turn cost, router included. The model-call count
              is the honest companion: three calls at a cent each is a
              different story from one call at three cents. */}
          {typeof event.model_calls === "number" && event.model_calls > 0 && (
            <span className="chip">
              {event.model_calls} {event.model_calls === 1 ? "call" : "calls"}
            </span>
          )}
          <CostChip usd={event.turn_cost_usd} />
        </div>
      );

    case "domain.entered":
      return (
        <div className="detail">
          {/* The domain first: shown alone, the one-tool list of the fallback
              domain read as if "transfer_to_human" were the domain. */}
          <strong>{String(event.domain)}</strong>
          {/* §17.1: the capability list is a property of the domain, so show
              what this turn *could* have done, not only what it did. */}
          <span className="muted" style={{ margin: "0 4px 0 8px" }}>can use</span>
          {(event.capabilities as string[]).map((capability) => (
            <span className="chip" key={capability}>{capability}</span>
          ))}
        </div>
      );

    case "capability.invoked":
      // Announced before the call, so the customer's screen can say what is
      // being looked up. The outcome is on the capability.result that follows.
      return (
        <div className="detail">
          <span className="chip">{String(event.capability)}</span>
          <span className="muted">called</span>
        </div>
      );

    case "llm.request":
      return (
        <div className="detail">
          <span className="chip">{String(event.role)}</span>
          {typeof event.round === "number" && (
            <span className="chip">round {event.round}</span>
          )}
          {event.tools_available === false && (
            <span className="muted">answer only</span>
          )}
        </div>
      );

    case "llm.response": {
      // The shape of what came back, never the text. `cut_off` is the case
      // that once reached a customer as a blank bubble: the output ceiling
      // hit mid tool call.
      const calls = (event.tool_calls as string[] | undefined) ?? [];
      return (
        <div className="detail">
          <span className="chip">{String(event.role)}</span>
          {typeof event.round === "number" && (
            <span className="chip">round {event.round}</span>
          )}
          {calls.map((name) => (
            <span className="chip" key={name}>→ {name}</span>
          ))}
          {calls.length === 0 && typeof event.text_chars === "number" && (
            <span className="chip">
              {event.text_chars === 0 ? "no text" : `${event.text_chars} chars`}
            </span>
          )}
          {event.cut_off === true && (
            <span className="badge critical">
              <span aria-hidden="true">■</span>
              cut off ({String(event.finish_reason ?? "invalid tool call")})
            </span>
          )}
        </div>
      );
    }

    case "offer.lookup":
      return (
        <div className="detail">
          <span className="chip">{String(event.context)}</span>
          <span className="muted">recommendation system asked</span>
        </div>
      );

    case "capability.result": {
      const { role, glyph } = statusOf(String(event.outcome));
      return (
        <div className="detail">
          <span className="chip">{String(event.capability)}</span>
          <span className={`badge ${role}`}>
            <span aria-hidden="true">{glyph}</span>
            {String(event.outcome)}
          </span>
        </div>
      );
    }

    case "domain.switched":
      return (
        <div className="detail">
          {String(event.from)} → <strong>{String(event.to)}</strong>
          {/* §15: the switch must not cost what was already collected. */}
          {event.contract_number_retained != null && (
            <span className="chip">
              retained {String(event.contract_number_retained)}
            </span>
          )}
        </div>
      );

    case "escalation.triggered":
      return (
        <div className="detail">
          <span className="badge serious">
            <span aria-hidden="true">▲</span>
            {String(event.kind)}
          </span>
          <div style={{ marginTop: 3 }}>{String(event.reason)}</div>
          <span className="chip">decided by: {String(event.decided_by)}</span>
        </div>
      );

    case "guardrail.triggered":
      return (
        <div className="detail">
          <span className={`badge ${event.blocking ? "critical" : "warning"}`}>
            <span aria-hidden="true">{event.blocking ? "■" : "◆"}</span>
            {String(event.guard)}
          </span>
          <div style={{ marginTop: 3 }}>{(event.problems as string[]).join(" · ")}</div>
          {event.substituted === true && (
            <span className="chip">reply substituted</span>
          )}
        </div>
      );

    case "privacy.redaction_applied":
      return (
        <div className="detail">
          {/* §9: the KIND, never the value. */}
          <span className="badge warning">
            <span aria-hidden="true">◆</span>
            {(event.kinds as string[]).join(", ")}
          </span>
          {/* The toggle is off: the model did receive the value. The trace says
              so rather than implying a redaction that did not happen. */}
          {event.redacted === false && (
            <span className="chip">not removed — sent to the model</span>
          )}
        </div>
      );

    case "privacy.outbound_filter_applied":
      return (
        <div className="detail">
          {/* §9's fourth enforcement point. Reaching this means the reasoning
              layer produced a value it should never have held — which is only
              possible with inbound redaction switched off. */}
          <span className="badge critical">
            <span aria-hidden="true">■</span>
            removed from the reply: {(event.kinds as string[]).join(", ")}
          </span>
        </div>
      );

    case "session.started":
      return (
        <div className="detail">
          <span className="chip">{String(event.agent_model)}</span>
          {event.test_mode != null && (
            <span className="chip">stub: {String(event.test_mode)}</span>
          )}
        </div>
      );

    default:
      return null;
  }
}

function glyphFor(name: string): string {
  if (name.startsWith("router")) return "⑃";
  if (name.startsWith("llm")) return "◌";
  if (name.startsWith("capability")) return "⚙";
  if (name.startsWith("offer")) return "⚙";
  if (name.startsWith("escalation")) return "↑";
  if (name.startsWith("guardrail")) return "⊘";
  if (name.startsWith("privacy")) return "✱";
  if (name.startsWith("domain")) return "▸";
  if (name.startsWith("agent")) return "✓";
  return "·";
}
