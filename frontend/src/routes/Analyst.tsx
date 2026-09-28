import { type FormEvent, useEffect, useState } from "react";
import { BudgetBar, euro, segmentsOf } from "../components/BudgetBar";
import { Gate } from "../components/Gate";
import { ThemeButton } from "../components/ThemeButton";
import { BarChart } from "../components/charts/BarChart";
import { LineChart, type Series } from "../components/charts/LineChart";
import { useTheme } from "../hooks/useTheme";
import { ApiError, api } from "../lib/api";
import { statusOf } from "../lib/outcome";
import type { AnalystOverview, Campaign, EvalMetric, EvalRun, OfferBudget } from "../lib/types";
import { deltaOf, formatMetric } from "../panels/MetricsPanel";

/** The analyst's view: the numbers, never a conversation.
 *
 *  Three readings of the same platform. The offers budget, which the analyst
 *  sets — the one decision this page makes; the campaigns and what each has
 *  done; the §1 metrics run over run, which is the one series here that is a
 *  real progression and so gets a line; and the operation counted over every
 *  conversation and every case. Everything is polled, so a `make eval`
 *  finishing in a terminal, or a customer accepting an offer in another tab,
 *  shows up without a reload. */
export function Analyst() {
  return (
    <Gate
      role="analyst"
      title="Analyst sign-in"
      blurb="Aggregate metrics, the offer campaigns and the offers budget. Setting the budget decides what the assistant may give away."
    >
      {({ logout, expired }) => <AnalystView onLogout={logout} onExpired={expired} />}
    </Gate>
  );
}

function AnalystView({ onLogout, onExpired }: { onLogout: () => void; onExpired: () => void }) {
  const [theme, toggleTheme] = useTheme();
  const [budget, setBudget] = useState<OfferBudget | null>(null);
  const [campaigns, setCampaigns] = useState<Campaign[] | null>(null);
  const [overview, setOverview] = useState<AnalystOverview | null>(null);
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [unreachable, setUnreachable] = useState<string | null>(null);

  useEffect(() => {
    const gone = (e: ApiError) => {
      if (e.status === 401) onExpired();
    };
    const readOffers = () => {
      api.analystBudget().then(setBudget).catch((e: ApiError) => {
        gone(e);
        setBudget(null);
      });
      api.analystCampaigns().then(setCampaigns).catch((e: ApiError) => {
        gone(e);
        setCampaigns(null);
      });
    };
    const readOverview = () =>
      api
        .analystOverview()
        .then((o) => {
          setOverview(o);
          setUnreachable(null);
        })
        .catch((e: ApiError) => {
          gone(e);
          setUnreachable(e.message);
        });
    const readRuns = () => api.evalRuns(30).then(setRuns).catch(() => undefined);
    readOffers();
    readOverview();
    readRuns();
    const timers = [
      window.setInterval(readOffers, 5000),
      window.setInterval(readOverview, 15000),
      window.setInterval(readRuns, 30000),
    ];
    return () => timers.forEach((t) => window.clearInterval(t));
  }, [onExpired]);

  return (
    <div className="app">
      <header className="strip">
        <span className="brand">
          Fibrapunto <span className="role">analyst</span>
        </span>
        <span className="spacer" />
        <nav className="views" aria-label="Other views">
          <a href="/">Conversation</a>
          <a href="/operator">Operator</a>
        </nav>
        <button onClick={onLogout}>Sign out</button>
        <ThemeButton theme={theme} onToggle={toggleTheme} />
      </header>

      <main className="analyst">
        {unreachable && <div className="banner error">{unreachable}</div>}

        <BudgetSection budget={budget} onChanged={setBudget} />
        <CampaignsSection campaigns={campaigns} />
        <EvalSection runs={runs} />
        <OperationSection overview={overview} />
      </main>
    </div>
  );
}

// --- the offers budget: the one thing the analyst sets ------------------------

function BudgetSection({
  budget,
  onChanged,
}: {
  budget: OfferBudget | null;
  onChanged: (budget: OfferBudget) => void;
}) {
  const [draft, setDraft] = useState<string>("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    api
      .setOfferBudget(draft.trim())
      .then((next) => {
        onChanged(next);
        setEditing(false);
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setBusy(false));
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>Offers budget</h2>
        <span className="hint">what the assistant may give away in discounts, across every conversation</span>
      </div>
      {!budget ? (
        <p className="empty">Recommendation system not reachable.</p>
      ) : (
        <>
          <div className="hero-row">
            <div className="hero">
              <div className="label">Remaining</div>
              <div className="value">{euro(budget.remaining_eur)}</div>
              <div className="sub">
                of {euro(budget.total_eur)}
                {budget.discounts_available ? (
                  <span className="badge good" style={{ marginLeft: 8 }}>
                    <span aria-hidden="true">●</span>discounts available
                  </span>
                ) : (
                  <span className="badge critical" style={{ marginLeft: 8 }}>
                    <span aria-hidden="true">■</span>budget spent
                  </span>
                )}
              </div>
            </div>
            <form className="budget-form" onSubmit={submit}>
              {editing ? (
                <>
                  <label htmlFor="budget-total">New total, €</label>
                  <input
                    id="budget-total"
                    inputMode="decimal"
                    autoFocus
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    style={{ width: 120 }}
                  />
                  <button className="primary" type="submit" disabled={busy || !/^\d+([.,]\d{1,2})?$/.test(draft.trim())}>
                    Set budget
                  </button>
                  <button type="button" disabled={busy} onClick={() => setEditing(false)}>
                    Cancel
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setDraft(Number(budget.total_eur).toFixed(2));
                    setEditing(true);
                  }}
                >
                  Edit budget
                </button>
              )}
              {error && <p className="login-error">{error}</p>}
            </form>
          </div>
          <BudgetBar segments={segmentsOf(budget)} heldLabel="held, awaiting decisions" />
          <div className="tiles four">
            <Tile label="Confirmed offers" value={String(budget.confirmed)} />
            <Tile label="Awaiting a decision" value={String(budget.accepted)} />
            <Tile label="Given away" value={euro(budget.confirmed_eur)} />
            <Tile label="Committed" value={euro(budget.spent_eur)} hint="confirmed + held" />
          </div>
          <p className="note">
            Applies to the next offer shown. What is already held or confirmed stays
            counted, so a budget set below it leaves nothing to offer until a colleague
            declines one. Reset restores the configured value.
          </p>
        </>
      )}
    </section>
  );
}

// --- the campaigns -----------------------------------------------------------

function CampaignsSection({ campaigns }: { campaigns: Campaign[] | null }) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>Campaigns</h2>
        <span className="hint">the offers the assistant may make, and what each has done</span>
      </div>
      {!campaigns ? (
        <p className="empty">Recommendation system not reachable.</p>
      ) : (
        <div className="two-col">
          <table className="metrics">
            <thead>
              <tr>
                <th>Campaign</th>
                <th>Plans</th>
                <th className="num">Cost</th>
                <th className="num">Awaiting</th>
                <th className="num">Confirmed</th>
                <th className="num">Declined</th>
                <th className="num">Committed</th>
              </tr>
            </thead>
            <tbody>
              {campaigns.map((c) => (
                <tr key={c.offer_id}>
                  <td>
                    {c.title}
                    <div className="muted" style={{ fontSize: 11 }}>
                      {Number(c.cost_eur) > 0
                        ? `€${c.monthly_discount_eur}/month × ${c.duration_months}`
                        : "costs nothing"}
                    </div>
                  </td>
                  <td>{c.plans ? c.plans.join(", ") : "any"}</td>
                  <td className="num">{euro(c.cost_eur)}</td>
                  <td className="num">{c.accepted}</td>
                  <td className="num">{c.confirmed}</td>
                  <td className="num">{c.declined}</td>
                  <td className="num">{euro(Number(c.held_eur) + Number(c.spent_eur))}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div>
            <div className="section-title">Committed per campaign</div>
            <BarChart
              rows={campaigns.map((c) => ({
                label: c.title,
                value: Number(c.held_eur) + Number(c.spent_eur),
              }))}
              format={euro}
              empty="No offer accepted yet."
            />
          </div>
        </div>
      )}
    </section>
  );
}

// --- §1's table, run over run -------------------------------------------------

/** Whether the metric is a ratio or a count, read from the target: JSON has
 *  no float 1.0, so a 100% rate arrives as the integer 1. */
const isRatio = (metric: EvalMetric) => metric.target.includes("%");

function EvalSection({ runs }: { runs: EvalRun[] }) {
  const latest = runs[0];
  const previous = runs[1];
  if (!latest) {
    return (
      <section className="card">
        <div className="card-head">
          <h2>Eval metrics</h2>
          <span className="hint">§1's table, as the eval harness recorded it</span>
        </div>
        <p className="empty">No eval run recorded yet.</p>
        <p className="note">
          <code>make eval</code> runs the test set and records its metrics here;
          <code> make eval-publish RUN=all</code> records the runs already saved.
        </p>
      </section>
    );
  }

  // Oldest first on the x axis; metric order fixed by the latest run, so a
  // metric keeps its colour from one poll to the next.
  const ordered = [...runs].reverse();
  // Run ids share most of their characters; the axis counts runs and the
  // tooltip names them.
  const labels = ordered.map((_, i) => `#${i + 1}`);
  const titles = ordered.map((r) => `run ${r.run_id}`);
  const names = latest.metrics.map((m) => m.name);
  const seriesFor = (pick: (m: EvalMetric) => boolean): Series[] =>
    names
      .filter((name) => pick(latest.metrics.find((m) => m.name === name)!))
      .map((name) => ({
        name,
        values: ordered.map((run) => run.metrics.find((m) => m.name === name)?.value ?? null),
      }))
      // A metric no run measured (escalation precision is a manual review)
      // would be a legend entry with no line under it.
      .filter((s) => s.values.some((v) => v != null));
  const rates = seriesFor(isRatio);
  const counts = seriesFor((m) => !isRatio(m));
  const before = new Map((previous?.metrics ?? []).map((m) => [m.name, m.value]));
  const passed = latest.metrics.filter((m) => m.passed === true).length;
  const measured = latest.metrics.filter((m) => m.passed !== null).length;

  return (
    <section className="card">
      <div className="card-head">
        <h2>Eval metrics</h2>
        <span className="hint">
          §1's table over {runs.length} recorded {runs.length === 1 ? "run" : "runs"} — computed over the test set, never over live conversations
        </span>
      </div>
      <div className="tiles four">
        <Tile label="Targets met, latest" value={`${passed}/${measured}`} />
        <Tile label="Conversations per run" value={latest.case_count == null ? "—" : String(latest.case_count)} />
        <Tile label="Agent model" value={latest.agent_model.split("/").pop() ?? latest.agent_model} small />
        <Tile label="Judge" value={latest.judge_model ? latest.judge_model.split("/").pop()! : "none"} small />
      </div>
      <div className="two-col">
        <div>
          <div className="section-title">Rates, run over run</div>
          <LineChart
            labels={labels}
            titles={titles}
            series={rates}
            domain={[0, 1]}
            format={(v) => `${Math.round(v * 100)}%`}
          />
          {counts.length > 0 && (
            <>
              <div className="section-title">Counts, run over run</div>
              <LineChart labels={labels} titles={titles} series={counts} format={String} height={140} />
            </>
          )}
        </div>
        <div>
          <div className="section-title">Latest run against its target</div>
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
          <p className="note">
            Escalation precision is a manual review of a sample, so it is never a
            number here. A count with a "= 0" target improves by going down.
          </p>
        </div>
      </div>
    </section>
  );
}

function MetricBadge({ metric }: { metric: EvalMetric }) {
  const text = formatMetric(metric);
  if (metric.passed === null) return <span className="badge neutral">{text}</span>;
  return (
    <span className={`badge ${metric.passed ? "good" : "critical"}`}>
      <span aria-hidden="true">{metric.passed ? "●" : "■"}</span>
      {text}
    </span>
  );
}

// --- the operation, counted ---------------------------------------------------

function OperationSection({ overview }: { overview: AnalystOverview | null }) {
  if (!overview) {
    return (
      <section className="card">
        <div className="card-head">
          <h2>Operation</h2>
        </div>
        <p className="empty">Loading…</p>
      </section>
    );
  }
  const { conversations, turns, events, cases } = overview;
  const seconds = (ms: number | null) => (ms == null ? "—" : (ms / 1000).toFixed(1));
  const byQueue = new Map<string, { open: number; in_progress: number; closed: number; complete: number; total: number }>();
  for (const row of cases) {
    const q = byQueue.get(row.queue) ?? { open: 0, in_progress: 0, closed: 0, complete: 0, total: 0 };
    q[row.status as "open" | "in_progress" | "closed"] += row.n;
    q.complete += row.complete;
    q.total += row.n;
    byQueue.set(row.queue, q);
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>Operation</h2>
        <span className="hint">every conversation and every case still within the retention period (§9)</span>
      </div>
      <div className="tiles four">
        <Tile label="Conversations" value={String(conversations.total)} hint={conversations.from_eval_runs > 0 ? `${conversations.from_eval_runs} from eval runs` : undefined} />
        <Tile label="Replies" value={String(turns.agent)} />
        <Tile label="Latency p50" value={seconds(turns.latency_p50_ms)} unit="s" />
        <Tile label="Latency p95" value={seconds(turns.latency_p95_ms)} unit="s" />
        <Tile label="Cost" value={turns.cost_usd == null ? "—" : Number(turns.cost_usd).toFixed(2)} unit={turns.cost_usd == null ? undefined : "$"} hint={turns.cost_usd == null ? "provider did not report" : `${turns.costed_turns} costed replies`} />
        <Tile label="Guardrail hits" value={String(events["guardrail.triggered"] ?? 0)} />
        <Tile label="Escalations" value={String(events["escalation.triggered"] ?? 0)} />
        <Tile label="Domain switches" value={String(events["domain.switched"] ?? 0)} />
      </div>

      <div className="two-col">
        <div>
          <div className="section-title">Conversations by domain</div>
          <BarChart rows={conversations.by_domain.map((r) => ({ label: r.domain, value: r.n }))} empty="No conversation yet." />
          <div className="section-title">Conversations by outcome</div>
          <BarChart rows={conversations.by_status.map((r) => ({ label: r.status, value: r.n }))} color="var(--series-2)" empty="No conversation yet." />
          <div className="section-title">Why cases were opened</div>
          <BarChart rows={overview.escalations.map((r) => ({ label: r.kind ?? "unknown", value: r.n }))} color="var(--series-3)" empty="No escalation yet." />
        </div>
        <div>
          <div className="section-title">Capability outcomes</div>
          {overview.capability_outcomes.length === 0 ? (
            <p className="empty">No lookup yet.</p>
          ) : (
            <table className="metrics">
              <tbody>
                {overview.capability_outcomes.map((row) => {
                  const { role, glyph } = statusOf(row.outcome ?? "");
                  return (
                    <tr key={row.outcome ?? "none"}>
                      <td>
                        <span className={`badge ${role}`}>
                          <span aria-hidden="true">{glyph}</span>
                          {row.outcome ?? "—"}
                        </span>
                      </td>
                      <td className="num">{row.n}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          <div className="section-title">Cases by queue</div>
          {byQueue.size === 0 ? (
            <p className="empty">No case yet.</p>
          ) : (
            <table className="metrics">
              <thead>
                <tr>
                  <th>Queue</th>
                  <th className="num">Open</th>
                  <th className="num">In progress</th>
                  <th className="num">Closed</th>
                  <th className="num">Complete</th>
                </tr>
              </thead>
              <tbody>
                {[...byQueue.entries()].map(([queue, q]) => (
                  <tr key={queue}>
                    <td>{queue}</td>
                    <td className="num">{q.open}</td>
                    <td className="num">{q.in_progress}</td>
                    <td className="num">{q.closed}</td>
                    <td className="num">
                      {q.total === 0 ? "—" : `${Math.round((q.complete / q.total) * 100)}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="note">
            "Complete" is §13's data-quality measure: whether the record carried
            every field a colleague needs. Not the same as closed.
          </p>
          {overview.offers_on_cases.length > 0 && (
            <>
              <div className="section-title">Offers on cases</div>
              <table className="metrics">
                <tbody>
                  {overview.offers_on_cases.map((row) => (
                    <tr key={row.status}>
                      <td>{row.status.replace(/_/g, " ")}</td>
                      <td className="num">{row.n}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function Tile({
  label,
  value,
  unit,
  hint,
  small = false,
}: {
  label: string;
  value: string;
  unit?: string;
  hint?: string;
  small?: boolean;
}) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value" style={small ? { fontSize: 14, wordBreak: "break-all" } : undefined}>
        {value}
        {unit && <small> {unit}</small>}
      </div>
      {hint && <div className="tile-hint">{hint}</div>}
    </div>
  );
}
