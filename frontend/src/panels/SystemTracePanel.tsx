import { statusOf } from "../lib/outcome";
import type { TraceEvent } from "../lib/types";

/** §17.10 made visible: which domain fired, which capabilities were called,
 *  and with what typed outcome.
 *
 *  This panel is the point of the demo. The chat shows an agent being helpful;
 *  this shows *why* it was allowed to be, and — when the billing system is
 *  down — why it declined to be. */
export function SystemTracePanel({ events }: { events: TraceEvent[] }) {
  const byTurn = new Map<number, TraceEvent[]>();
  for (const event of events) {
    const turn = event.turn ?? 0;
    if (!byTurn.has(turn)) byTurn.set(turn, []);
    byTurn.get(turn)!.push(event);
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>What happened in the system</h2>
        <span className="hint">§17.10</span>
      </div>
      <div className="panel-body">
        {events.length === 0 && (
          <p className="empty">No events yet.</p>
        )}
        {[...byTurn.entries()].map(([turn, turnEvents]) => (
          <div key={turn}>
            {turn > 0 && <div className="turn-rule">turn {turn}</div>}
            {turnEvents.map((event, index) => (
              <Row key={index} event={event} />
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}

function Row({ event }: { event: TraceEvent }) {
  return (
    <div className="event">
      <span className="glyph">{glyphFor(event.name)}</span>
      <div>
        <div className="name">{event.name}</div>
        <Detail event={event} />
      </div>
      <span className="ms">
        {event.duration_ms != null ? `${event.duration_ms} ms` : ""}
      </span>
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
          {confidence != null && <span className="chip">conf {confidence.toFixed(2)}</span>}
          {event.sentiment !== "neutral" && event.sentiment != null && (
            <span className="chip">{String(event.sentiment)}</span>
          )}
        </div>
      );
    }

    case "domain.entered":
      return (
        <div className="detail">
          {/* §17.1: the capability list is a property of the domain, so show
              what this turn *could* have done, not only what it did. */}
          {(event.capabilities as string[]).map((capability) => (
            <span className="chip" key={capability}>{capability}</span>
          ))}
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
  if (name.startsWith("capability")) return "⚙";
  if (name.startsWith("escalation")) return "↑";
  if (name.startsWith("guardrail")) return "⊘";
  if (name.startsWith("privacy")) return "✱";
  if (name.startsWith("domain")) return "▸";
  if (name.startsWith("agent")) return "✓";
  return "·";
}
