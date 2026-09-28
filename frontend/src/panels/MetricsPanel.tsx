import { useEffect, useState } from "react";
import { BudgetBar, segmentsOf } from "../components/BudgetBar";
import { useOfferBudget } from "../hooks/useOfferBudget";
import { api } from "../lib/api";
import { statusOf } from "../lib/outcome";
import type { AgentReply, Domain, EvalMetric, EvalRun, TraceEvent } from "../lib/types";

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
 *  information. The run-over-run view is §1's table at the bottom: the
 *  latest eval run against the one before it, which is a progression. */
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

        <div className="section-title">Perimeter</div>
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

        <div className="section-title">Active domains</div>
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
          Cancellation has none that can execute one — which is why the cancellation
          metric is zero by construction.
        </p>

        <EvalMetricsSection />
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

/** Dollars with four decimals, as the provider reported them: a single turn
 *  costs a fraction of a cent, and two decimals would read "0.00" for most
 *  of a conversation. */
export function formatCost(usd: number | null): { value: string; unit?: string } {
  if (usd == null) return { value: "—" };
  return { value: usd.toFixed(4), unit: "$" };
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

/** The recommendation system's discount budget (recommendation-system/).
 *
 *  An accepted offer holds its cost; a colleague's confirmation spends it,
 *  a decline releases it. Once what remains cannot cover a discount, the
 *  system stops offering discounted deals and only no-cost offers remain.
 *  Read from the system itself every few seconds, across every conversation
 *  — it is the operator's budget, not this session's. */
function OfferBudgetSection() {
  const { budget } = useOfferBudget();

  if (!budget) {
    return (
      <>
        <div className="section-title">Offers budget</div>
        <p className="empty">Recommendation system not reachable.</p>
      </>
    );
  }
  return (
    <>
      <div className="section-title">Offers budget</div>
      <BudgetBar segments={segmentsOf(budget)} heldLabel="held" compact />
      <table className="metrics">
        <tbody>
          <Row label="Total" value={`€${budget.total_eur}`} />
          <Row label="Awaiting a decision" value={String(budget.accepted)} />
          <Row label="Confirmed" value={String(budget.confirmed)} />
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

/** §1's table, from the last eval run the harness recorded (§14).
 *
 *  Not this session's numbers: the six metrics are computed over the test
 *  set's conversations, and the harness hands them to the backend when a run
 *  ends. Each row is the metric against its target, with the movement since
 *  the previous run beside it — a number that went from 100% to 100% and one
 *  that fell from 100% to 90% are different news, and the table must say so.
 *  Polled, so a `make eval` finishing in a terminal shows up here without a
 *  reload. */
function EvalMetricsSection() {
  const [runs, setRuns] = useState<EvalRun[] | null>(null);
  const [unreachable, setUnreachable] = useState(false);

  useEffect(() => {
    const read = () =>
      api
        .evalRuns(10)
        .then((items) => {
          setRuns(items);
          setUnreachable(false);
        })
        .catch(() => setUnreachable(true));
    read();
    const timer = window.setInterval(read, 30000);
    return () => window.clearInterval(timer);
  }, []);

  const latest = runs?.[0];
  const previous = runs?.[1];

  if (!latest) {
    return (
      <>
        <div className="section-title">Eval metrics</div>
        <p className="empty">
          {unreachable
            ? "The backend did not answer; the recorded runs live there."
            : "No eval run recorded yet."}
        </p>
        <p className="note">
          <code>make eval</code> runs the test set and records its metrics table here;
          <code> make eval-publish RUN=all</code> records the runs already saved
          under <code>evals/runs/</code>.
        </p>
      </>
    );
  }

  const before = new Map((previous?.metrics ?? []).map((m) => [m.name, m.value]));
  const passed = latest.metrics.filter((m) => m.passed === true).length;
  const measured = latest.metrics.filter((m) => m.passed !== null).length;

  return (
    <>
      <div className="section-title">Eval metrics</div>
      <div className="tiles">
        <Tile label="Targets met" value={`${passed}/${measured}`} />
        <Tile label="Conversations" value={latest.case_count == null ? "—" : String(latest.case_count)} />
        <Tile label="Runs" value={String(runs?.length ?? 0)} />
      </div>
      <table className="metrics">
        <thead>
          <tr>
            <th>Metric</th>
            <th className="num">Value</th>
            <th className="num">Target</th>
            <th className="num">vs previous</th>
          </tr>
        </thead>
        <tbody>
          {latest.metrics.map((metric) => {
            const delta = deltaOf(metric, before.get(metric.name));
            return (
              <tr key={metric.name}>
                <td>{metric.name}</td>
                <td className="num">
                  <MetricBadge metric={metric} />
                </td>
                <td className="num" style={{ color: "var(--text-secondary)" }}>{metric.target}</td>
                <td className="num" style={{ color: delta.color, fontFamily: "var(--mono)", fontSize: 11 }}>
                  {delta.text}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <table className="metrics" style={{ marginTop: 8 }}>
        <tbody>
          <Row label="Run" value={latest.run_id} />
          <Row label="Agent" value={latest.agent_model} />
          <Row label="Judge" value={latest.judge_model ?? "none (deterministic only)"} />
          {previous && <Row label="Previous" value={previous.run_id} />}
        </tbody>
      </table>
      <p className="note">
        Computed by the eval harness over the test set, not over this
        conversation. Escalation precision is a manual review of a sample,
        so it is never a number here.
      </p>
    </>
  );
}

function MetricBadge({ metric }: { metric: EvalMetric }) {
  const text = formatMetric(metric);
  if (metric.passed === null) {
    return <span className="badge neutral">{text}</span>;
  }
  const role = metric.passed ? "good" : "critical";
  return (
    <span className={`badge ${role}`}>
      <span aria-hidden="true">{metric.passed ? "●" : "■"}</span>
      {text}
    </span>
  );
}

/** Whether the metric is a ratio or a count. Read from the target, not the
 *  value: JSON has no float 1.0, so a resolution rate of 100% arrives as the
 *  integer 1, indistinguishable from one invented reply. */
const isRatio = (metric: EvalMetric) => metric.target.includes("%");

/** A ratio reads as a percentage, a count as itself: "0" vs "0%" is the
 *  difference between "no invented data" and "0% of something". */
export function formatMetric(metric: EvalMetric): string {
  if (metric.value == null) return "—";
  return isRatio(metric) ? `${Math.round(metric.value * 100)}%` : String(metric.value);
}

/** The movement since the previous run, in the metric's own unit. Green is
 *  "improved", which for a count that must be zero means it went down. */
export function deltaOf(
  metric: EvalMetric,
  previous: number | null | undefined,
): { text: string; color: string } {
  const muted = "var(--text-muted)";
  if (metric.value == null || previous == null) return { text: "—", color: muted };
  const diff = metric.value - previous;
  if (diff === 0) return { text: "=", color: muted };
  // A count with a "= 0" target improves by going down; a ratio by going up.
  const improved = isRatio(metric) ? diff > 0 : diff < 0;
  const sign = diff > 0 ? "+" : "−";
  const magnitude = isRatio(metric) ? `${Math.round(Math.abs(diff) * 100)}pt` : String(Math.abs(diff));
  return { text: `${sign}${magnitude}`, color: improved ? "var(--good)" : "var(--critical)" };
}
