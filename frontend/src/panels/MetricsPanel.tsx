import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { statusOf } from "../lib/outcome";
import type { AgentReply, Domain, OfferBudget, TraceEvent } from "../lib/types";

interface Props {
  replies: AgentReply[];
  events: TraceEvent[];
  domains: Domain[];
  perimeter: Record<string, any> | null;
}

/** Session metrics.
 *
 *  Stat tiles, not charts — deliberately. A single session has no series to
 *  plot: four numbers and a tally of outcomes is the whole of what is known,
 *  and a sparkline over two data points would be decoration pretending to be
 *  information. The run-over-run view in §1's table arrives with the eval
 *  harness, when there is actually a progression to show. */
export function MetricsPanel({ replies, events, domains, perimeter }: Props) {
  const turns = replies.length;
  const latencies = events
    .filter((event) => event.name === "agent.reply" && event.duration_ms != null)
    .map((event) => event.duration_ms as number);
  const p50 = median(latencies);

  const outcomes = new Map<string, number>();
  for (const reply of replies) {
    for (const call of reply.capability_calls) {
      outcomes.set(call.outcome, (outcomes.get(call.outcome) ?? 0) + 1);
    }
  }

  const guardrailHits = events.filter((e) => e.name === "guardrail.triggered").length;
  const last = replies.at(-1);

  /* §16: cost per conversation. Each agent.reply carries what its whole turn
   * cost — router included — as the provider reported it, so the session's
   * cost is the sum over turns. Turns with no report are left out of the sum
   * and counted, so the tile can say "3 of 5 turns" rather than a total that
   * silently omits them. */
  const replyEvents = events.filter((e) => e.name === "agent.reply");
  const costed = replyEvents.filter((e) => typeof e.turn_cost_usd === "number");
  const costUsd = costed.reduce((acc, e) => acc + (e.turn_cost_usd as number), 0);
  const cost = formatCost(costed.length > 0 ? costUsd : null);
  const modelCalls = replyEvents.reduce(
    (acc, e) => acc + (typeof e.model_calls === "number" ? e.model_calls : 0),
    0,
  );

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Metrics</h2>
        <span className="hint">this session</span>
      </div>
      <div className="panel-body">
        <div className="tiles">
          <Tile label="Turns" value={String(turns)} />
          <Tile
            label="Latency p50"
            value={p50 == null ? "—" : (p50 / 1000).toFixed(1)}
            unit={p50 == null ? undefined : "s"}
          />
          <Tile label="Lookups" value={String(sum(outcomes))} />
          <Tile label="Guardrails" value={String(guardrailHits)} />
          <Tile label="Model calls" value={String(modelCalls)} />
          <Tile label="Cost" value={cost.value} unit={cost.unit} />
        </div>
        {costed.length > 0 && costed.length < replyEvents.length && (
          <p className="note">
            Cost covers {costed.length} of {replyEvents.length} turns: the provider
            did not report the others.
          </p>
        )}

        <div className="section-title">Capability outcomes</div>
        {outcomes.size === 0 ? (
          <p className="empty">No lookups yet.</p>
        ) : (
          <table className="metrics">
            <tbody>
              {[...outcomes.entries()].map(([outcome, count]) => {
                const { role, glyph } = statusOf(outcome);
                return (
                  <tr key={outcome}>
                    <td>
                      <span className={`badge ${role}`}>
                        <span aria-hidden="true">{glyph}</span>
                        {outcome}
                      </span>
                    </td>
                    <td className="num">{count}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        <div className="section-title">State</div>
        <table className="metrics">
          <tbody>
            <Row label="Domain" value={last?.domain ?? "—"} />
            <Row label="Contract" value={last?.contract_number ?? "—"} />
            <Row label="Case" value={last?.handover_reference ?? "—"} />
            <Row label="Outcome" value={last?.status ?? "—"} />
          </tbody>
        </table>

        <OfferBudgetSection />

        <div className="section-title">Perimeter (§8)</div>
        {perimeter ? (
          <table className="metrics">
            <tbody>
              <tr>
                <td>svc_agent</td>
                <td className="num">
                  <span className={`badge ${perimeter.svc_agent?.allowed ? "good" : "critical"}`}>
                    <span aria-hidden="true">{perimeter.svc_agent?.allowed ? "●" : "■"}</span>
                    {perimeter.svc_agent?.allowed ? "allowed" : "denied"}
                  </span>
                </td>
              </tr>
              <tr>
                <td>platform_admin</td>
                <td className="num">
                  <span className={`badge ${perimeter.platform_admin?.allowed ? "good" : "critical"}`}>
                    <span aria-hidden="true">{perimeter.platform_admin?.allowed ? "●" : "■"}</span>
                    {perimeter.platform_admin?.allowed ? "allowed" : "denied"}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        ) : (
          <p className="empty">—</p>
        )}
        <p className="note">
          Same check, two identities: <code>customer.email</code>, read
          against the perimeter policy file. The database enforces the same
          policy as column grants — shown without the app in beat 5.
        </p>

        <div className="section-title">Active domains (§17.1)</div>
        <table className="metrics">
          <tbody>
            {domains.map((domain) => (
              <tr key={domain.key}>
                <td>
                  {domain.key}
                  {!domain.enabled && <span className="chip" style={{ marginLeft: 6 }}>not enabled</span>}
                </td>
                <td className="num">{domain.capabilities.length}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="note">
          The number is how many capabilities that domain may call.
          Cancellation has none that can execute one — which is why the §1
          metric is zero by construction.
        </p>

        <div className="section-title">§1 metrics</div>
        <p className="empty">
          Available after <code>make eval</code>: they are computed over the
          trace of 30 conversations, not this one.
        </p>
      </div>
    </section>
  );
}

function Tile({ label, value, unit }: { label: string; value: string; unit?: string }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">
        {value}
        {unit && <small> {unit}</small>}
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <tr>
      <td>{label}</td>
      <td className="num" style={{ fontFamily: "var(--mono)", fontSize: 11 }}>{value}</td>
    </tr>
  );
}

const sum = (map: Map<string, number>) => [...map.values()].reduce((a, b) => a + b, 0);

/** Cents while a conversation costs less than a dollar — which is the whole
 *  point of the tile — and dollars once it does not. */
export function formatCost(usd: number | null): { value: string; unit?: string } {
  if (usd == null) return { value: "—" };
  if (usd < 1) return { value: (usd * 100).toFixed(2), unit: "¢" };
  return { value: usd.toFixed(2), unit: "$" };
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

/** The recommendation system's discount budget (recommendation-system/).
 *
 *  Accepted offers spend it; once what remains cannot cover a discount, the
 *  system stops offering discounted deals and only no-cost offers remain. Read
 *  from the system itself every few seconds, across every conversation — it
 *  is the operator's budget, not this session's. */
function OfferBudgetSection() {
  const [budget, setBudget] = useState<OfferBudget | null>(null);

  useEffect(() => {
    const read = () => api.offerBudget().then(setBudget).catch(() => setBudget(null));
    read();
    const timer = window.setInterval(read, 5000);
    return () => window.clearInterval(timer);
  }, []);

  if (!budget) {
    return (
      <>
        <div className="section-title">Offers budget</div>
        <p className="empty">Recommendation system not reachable.</p>
      </>
    );
  }
  const total = Number(budget.total_eur);
  const spent = Number(budget.spent_eur);
  const used = total > 0 ? Math.min(100, (spent / total) * 100) : 100;
  return (
    <>
      <div className="section-title">Offers budget</div>
      <div className="budget-bar" aria-hidden="true">
        <div
          className={`budget-used ${budget.discounts_available ? "" : "exhausted"}`}
          style={{ width: `${used}%` }}
        />
      </div>
      <table className="metrics">
        <tbody>
          <Row label="Spent" value={`€${budget.spent_eur} of €${budget.total_eur}`} />
          <Row label="Remaining" value={`€${budget.remaining_eur}`} />
          <Row label="Offers accepted" value={String(budget.accepted)} />
          <tr>
            <td>Discounted offers</td>
            <td className="num">
              <span className={`badge ${budget.discounts_available ? "good" : "critical"}`}>
                <span aria-hidden="true">{budget.discounts_available ? "●" : "■"}</span>
                {budget.discounts_available ? "available" : "budget spent"}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </>
  );
}
