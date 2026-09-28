import { useCallback, useEffect, useMemo, useState } from "react";
import { AppHeader } from "../components/AppHeader";
import { BudgetBar, euro, segmentsAround, segmentsOf } from "../components/BudgetBar";
import { Gate } from "../components/Gate";
import { toast } from "../components/Toast";
import { useOfferBudget } from "../hooks/useOfferBudget";
import { ApiError, api } from "../lib/api";
import type { Campaign, OfferBudget } from "../lib/types";

/** §12.3 — what the receiving operator sees.
 *
 *  "The operator's first action should be to continue the conversation, not to
 *  reconstruct it." So the page is a queue on the left and one case on the
 *  right, and inside the case the summary and the actions are pinned at the
 *  top: everything a colleague needs to pick up the phone, before anything
 *  they might need afterwards. The three fields §12.2 says are most often
 *  omitted get their own cards rather than being buried in a context blob. */
export function Operator() {
  return (
    <Gate
      role="operator"
      title="Operator sign-in"
      blurb="The operator view shows customer contact details the assistant is not allowed to read."
    >
      {({ logout, expired }) => <Cases onLogout={logout} onExpired={expired} />}
    </Gate>
  );
}

type Status = "open" | "in_progress" | "closed";

function Cases({ onLogout, onExpired }: { onLogout: () => void; onExpired: () => void }) {
  const [records, setRecords] = useState<any[] | null>(null);
  const [selected, setSelected] = useState<any | null>(null);
  const [statusFilter, setStatusFilter] = useState<"todo" | Status | "all">("todo");
  const [queueFilter, setQueueFilter] = useState<string>("all");
  /* The offers budget is the team's, across every case: shown here so a
   * colleague deciding an offer knows what confirming it leaves. */
  const offers = useOfferBudget();

  const reload = useCallback(() => {
    api
      .operatorRecords()
      .then(setRecords)
      .catch((e: ApiError) => (e.status === 401 ? onExpired() : setRecords([])));
  }, [onExpired]);

  useEffect(() => {
    reload();
    const timer = window.setInterval(reload, 10000);
    return () => window.clearInterval(timer);
  }, [reload]);

  const open = useCallback(
    (reference: string) => {
      api
        .operatorRecord(reference)
        .then(setSelected)
        .catch((e: ApiError) => e.status === 401 && onExpired());
    },
    [onExpired],
  );

  /* A status change alters the row's badge and its place in the list, so
   * both the detail and the table are refreshed. */
  function changed(reference: string) {
    reload();
    open(reference);
    offers.refresh();
  }

  const queues = useMemo(
    () => [...new Set((records ?? []).map((r) => r.queue as string))].sort(),
    [records],
  );
  const visible = (records ?? []).filter(
    (r) =>
      (queueFilter === "all" || r.queue === queueFilter) &&
      (statusFilter === "all" ||
        (statusFilter === "todo" ? r.status !== "closed" : r.status === statusFilter)),
  );
  const todo = (records ?? []).filter((r) => r.status !== "closed").length;

  return (
    <div className="app">
      <AppHeader view="operator" role="operator" onLogout={onLogout} />
      <div className="op-layout">
        <aside className="queue">
          <div className="budget-summary">
            <div className="section-title">
              Offers budget
              {offers.budget && (
                <span className="muted" style={{ marginLeft: 6, textTransform: "none", letterSpacing: 0, fontWeight: 400 }}>
                  {euro(offers.budget.total_eur)} in total
                </span>
              )}
            </div>
            {offers.budget ? (
              <BudgetBar segments={segmentsOf(offers.budget)} heldLabel="held" compact />
            ) : (
              <p className="empty">Recommendation system not reachable.</p>
            )}
          </div>
          <div className="queue-head">
            <h1>
              Cases{" "}
              <span className="muted" style={{ fontSize: 12, fontFamily: "var(--sans)", fontWeight: 400 }}>
                {todo} to do
              </span>
            </h1>
            <div className="queue-filters">
              {(
                [
                  ["todo", "To do"],
                  ["open", "Open"],
                  ["in_progress", "In progress"],
                  ["closed", "Closed"],
                  ["all", "All"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  className={`chipbtn ${statusFilter === key ? "on" : ""}`}
                  onClick={() => setStatusFilter(key)}
                >
                  {label}
                </button>
              ))}
              {queues.length > 1 && (
                <select
                  className="small"
                  value={queueFilter}
                  onChange={(event) => setQueueFilter(event.target.value)}
                  style={{ fontSize: 11, padding: "2px 6px" }}
                  aria-label="Queue"
                >
                  <option value="all">every queue</option>
                  {queues.map((q) => (
                    <option key={q} value={q}>
                      {q.replace(/_/g, " ")}
                    </option>
                  ))}
                </select>
              )}
            </div>
          </div>
          <div className="queue-list">
            {records === null && (
              <div style={{ padding: 10, display: "grid", gap: 10 }}>
                <div className="skeleton" style={{ width: "70%" }} />
                <div className="skeleton" />
                <div className="skeleton" style={{ width: "50%" }} />
              </div>
            )}
            {records !== null && visible.length === 0 && (
              <p className="empty" style={{ padding: 12 }}>
                {records.length === 0
                  ? "No cases yet. They appear here as the assistant opens them."
                  : "Nothing in this filter."}
              </p>
            )}
            {visible.map((record) => (
              <button
                key={record.reference}
                className={`case-row ${selected?.summary.reference === record.reference ? "selected" : ""}`}
                onClick={() => open(record.reference)}
              >
                <span className="top">
                  <span>{record.reference}</span>
                  <span className={`prio ${record.priority}`}>{record.priority}</span>
                  <StatusBadge status={record.status} />
                </span>
                <span className="subject">{record.subject}</span>
                <span className="who">
                  {record.customer_name ?? "customer not linked"} · {String(record.queue).replace(/_/g, " ")}
                </span>
              </button>
            ))}
          </div>
        </aside>

        <main className="case">
          {selected ? (
            <Detail
              record={selected}
              budget={offers.budget}
              campaigns={offers.campaigns}
              onChanged={() => changed(selected.summary.reference)}
            />
          ) : (
            <div className="case-empty">
              <div>
                <div className="big">Pick a case</div>
                The summary is at the top of every case: one sentence, then act.
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function Detail({
  record,
  budget,
  campaigns,
  onChanged,
}: {
  record: any;
  budget: OfferBudget | null;
  campaigns: Campaign[] | null;
  onChanged: () => void;
}) {
  const s = record.summary;
  const contact = record.customer_contact;
  return (
    <>
      <div className="case-summary">
        <h1>{s.subject}</h1>
        <div className="meta">
          <span>
            Case <strong style={{ fontFamily: "var(--mono)" }}>{s.reference}</strong>
          </span>
          <span>·</span>
          <span>{s.customer ?? "customer not linked"}</span>
          <span>·</span>
          <span style={{ fontFamily: "var(--mono)" }}>{s.contract}</span>
          {!s.contract_linked && <span className="chip">contract not linked</span>}
          {s.handled_by_agent && <span className="chip">opened by the assistant</span>}
          <span className={`prio ${s.priority}`}>{s.priority} priority</span>
          <span>·</span>
          <span>
            {s.queue_label}, {s.promised}
          </span>
        </div>

        <StatusActions
          reference={s.reference}
          status={s.status}
          offerAwaitingDecision={record.retention_offer?.status === "accepted"}
          onChanged={onChanged}
        />

        {record.retention_offer && (
          <OfferDecision
            reference={s.reference}
            offer={record.retention_offer}
            budget={budget}
            campaigns={campaigns}
            onDecided={onChanged}
          />
        )}
      </div>

      <div className="case-grid">
        <div>
          <section className="card">
            <div className="card-head">
              <h2>Why it was opened</h2>
              <span className="hint" style={{ fontFamily: "var(--mono)" }}>{record.why_it_escalated}</span>
            </div>
            <dl className="fields">
              <dt>In their words</dt>
              <dd>{record.stated_reason ?? <span className="muted">not recorded</span>}</dd>
              <dt>Lookups</dt>
              <dd>
                {record.lookups.length === 0
                  ? <span className="muted">none</span>
                  : record.lookups.map((lookup: any, index: number) => (
                      <span className="chip" key={index}>{lookup.capability}: {lookup.outcome}</span>
                    ))}
              </dd>
            </dl>
          </section>

          {/* §12.2: "the field most often left out and the one that most often
              causes a second complaint." Shown as what it is: the assistant's
              side of the conversation, so the colleague does not contradict it. */}
          <section className="card">
            <div className="card-head">
              <h2>Already told to the customer</h2>
              <span className="hint">do not contradict it</span>
            </div>
            {record.already_told_the_customer.length === 0 ? (
              <p className="empty">Nothing yet.</p>
            ) : (
              <div className="told">
                {record.already_told_the_customer.map((line: string, index: number) => (
                  <div className="bubble" key={index}>{line}</div>
                ))}
              </div>
            )}
          </section>

          {record.outbox?.length > 0 && (
            <section className="card">
              <div className="card-head">
                <h2>Emails to the customer</h2>
                <span className="hint">queued, never sent by the demo</span>
              </div>
              {record.outbox.map((mail: any, index: number) => (
                <div key={index} style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 13 }}>
                    <span className="chip">{mail.sent ? "sent" : "queued"}</span>
                    to {mail.recipient} — <em>{mail.subject}</em>
                  </div>
                  <div className="muted" style={{ fontSize: 12, fontStyle: "normal" }}>{mail.body}</div>
                </div>
              ))}
            </section>
          )}

          {/* The engineer's view: the raw column, to make the point that a
              handover record is structured data with named keys rather than a
              paragraph of prose (§13). Collapsed: an operator picking up the
              phone should never need to open it. */}
          <details className="raw" style={{ marginTop: 12 }}>
            <summary>collected_context — the raw column</summary>
            <pre>{JSON.stringify(record.context, null, 2)}</pre>
          </details>
        </div>

        <div>
          <section className="card contact">
            <div className="card-head">
              <h2>How to reach them</h2>
              <span className="chip">read as operator</span>
            </div>
            {contact.email || contact.phone ? (
              <>
                {contact.email && (
                  <div className="line"><span className="k">email</span><a href={`mailto:${contact.email}`}>{contact.email}</a></div>
                )}
                {contact.phone && (
                  <div className="line"><span className="k">phone</span><span>{contact.phone}</span></div>
                )}
              </>
            ) : (
              /* Empty because the record carries no customer: the contract could
                 not be linked, so there is nobody to join to. Saying why beats a
                 blank field that reads as a missing permission. */
              <span className="muted">not available — this case is not linked to a contract</span>
            )}
            <p className="note">
              The assistant holds no GRANT on <code>customer.email</code> or{" "}
              <code>customer.phone</code>: that data never entered its context. The same
              query, run here as <code>platform_admin</code>, returns it. Not a different
              permission in the interface — a different database identity (§8).
            </p>
          </section>

          <section className="card">
            <div className="card-head">
              <h2>Internal notes</h2>
              <span className="chip">never readable by the assistant</span>
            </div>
            {record.internal_comments ? (
              <pre className="notes">{record.internal_comments}</pre>
            ) : (
              <p className="empty">No note yet. Add one with the next status change.</p>
            )}
          </section>
        </div>
      </div>
    </>
  );
}

/** An offer the customer accepted: a colleague confirms it or declines it.
 *
 *  Either way the customer is told — in their conversation (a templated
 *  message, not the model) and by email. A decline needs a reason, because the
 *  customer is shown it; its hold goes back to the offers budget. Confirming
 *  spends the hold for good — the bar shows exactly that: what is already
 *  used, what this offer holds, what would remain either way. */
function OfferDecision({
  reference,
  offer,
  budget,
  campaigns,
  onDecided,
}: {
  reference: string;
  offer: any;
  budget: OfferBudget | null;
  campaigns: Campaign[] | null;
  onDecided: () => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function decide(decision: "confirm" | "decline") {
    setBusy(true);
    setError(null);
    api
      .operatorDecideOffer(reference, decision, reason)
      .then(() => {
        toast(decision === "confirm" ? "Offer confirmed; the customer was told." : "Offer declined; the customer was told.");
        onDecided();
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setBusy(false));
  }

  if (offer.status !== "accepted") {
    return (
      <div className="decision-card" style={{ background: "var(--surface-2)", borderColor: "var(--border)" }}>
        <div className="title">Retention offer: {offer.title}</div>
        <span className="chip">
          {offer.status === "confirmed"
            ? "confirmed by the team"
            : offer.status === "declined_by_team"
              ? "declined by the team"
              : offer.status}
        </span>
        {offer.decision_reason && (
          <span className="muted" style={{ fontSize: 12, marginLeft: 6 }}>
            Reason given to the customer: {offer.decision_reason}
          </span>
        )}
        {offer.decided_at && (
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
            {offer.customer_notified_in_chat
              ? "The customer was told in their conversation."
              : "The conversation no longer exists; the customer was told by email only."}
          </div>
        )}
      </div>
    );
  }

  // The record carries the cost since it started travelling with the case;
  // an older record is priced from the catalogue by offer id.
  const cost =
    offer.cost_eur != null
      ? Number(offer.cost_eur)
      : Number(campaigns?.find((c) => c.offer_id === offer.offer_id)?.cost_eur ?? NaN);

  return (
    <div className="decision-card">
      <div className="title">
        The customer accepted an offer: {offer.title}
        <span className="chip" style={{ marginLeft: 8 }}>awaiting your decision</span>
      </div>
      {budget && !Number.isNaN(cost) ? (
        <div className="offer-budget">
          <BudgetBar segments={segmentsAround(budget, cost)} heldLabel="held by this offer" compact />
          <div className="muted" style={{ fontSize: 12, fontStyle: "normal" }}>
            {cost > 0
              ? `Confirming spends ${euro(cost)} for good; declining returns it to the budget.`
              : "This offer costs nothing: the budget is unaffected either way."}
          </div>
        </div>
      ) : (
        <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
          The offers budget cannot be read right now; the decision still stands.
        </div>
      )}
      <div className="actions">
        <button className="primary" disabled={busy} onClick={() => decide("confirm")}>
          Confirm and apply
        </button>
        <input
          placeholder="Reason, shown to the customer"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
        <button className="danger" disabled={busy || !reason.trim()} onClick={() => decide("decline")}>
          Decline
        </button>
      </div>
      {error && <p className="login-error">{error}</p>}
    </div>
  );
}

/** Whether a person has dealt with the case. The glyph and the word carry
 *  it; open is the one that asks for attention, so it is the one with colour. */
function StatusBadge({ status }: { status: string }) {
  const look =
    status === "open"
      ? { role: "warning", glyph: "◆", label: "open" }
      : status === "in_progress"
        ? { role: "neutral", glyph: "◐", label: "in progress" }
        : { role: "good", glyph: "●", label: "closed" };
  return (
    <span className={`badge ${look.role}`}>
      <span aria-hidden="true">{look.glyph}</span>
      {look.label}
    </span>
  );
}

/** Take, release, close, reopen — with an optional note kept in
 *  internal_comments. The rules (handover/lifecycle.py) are the server's; the
 *  buttons only show the moves it will accept, and a 409 is shown as text so
 *  the reason reaches the operator rather than a console. */
function StatusActions({
  reference,
  status,
  offerAwaitingDecision,
  onChanged,
}: {
  reference: string;
  status: string;
  offerAwaitingDecision: boolean;
  onChanged: () => void;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function move(target: Status) {
    setBusy(true);
    setError(null);
    api
      .operatorChangeStatus(reference, target, note.trim())
      .then((result) => {
        setNote("");
        if (result.changed) {
          toast(
            result.customer_notified_in_chat
              ? "Done. The customer was told in their conversation."
              : target === "open"
                ? "Back in the queue; the customer was not told."
                : "Done. The conversation no longer exists; the customer was not told.",
          );
        } else if (note.trim()) {
          toast("Note added.");
        }
        onChanged();
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setBusy(false));
  }

  const moves: { label: string; target: Status; primary?: boolean }[] =
    status === "open"
      ? [{ label: "Take", target: "in_progress", primary: true }, { label: "Close", target: "closed" }]
      : status === "in_progress"
        ? [{ label: "Close", target: "closed", primary: true }, { label: "Release", target: "open" }]
        : [{ label: "Reopen", target: "in_progress" }];

  return (
    <>
      <div className="actions">
        <StatusBadge status={status} />
        {status !== "closed" && (
          <span className="muted" style={{ fontSize: 12 }}>
            {status === "open" ? "nobody has acted on this case yet" : "a colleague is on it"}
          </span>
        )}
        <span style={{ flex: 1 }} />
        {moves.map((m) => (
          <button
            key={m.target}
            className={m.primary ? "primary" : undefined}
            disabled={busy || (m.target === "closed" && offerAwaitingDecision)}
            title={m.target === "closed" && offerAwaitingDecision ? "Decide the accepted offer first" : undefined}
            onClick={() => move(m.target)}
          >
            {m.label}
          </button>
        ))}
        <input
          placeholder="Note for colleagues (optional)"
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </div>
      {offerAwaitingDecision && status !== "closed" && (
        <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
          The customer accepted an offer: confirm or decline it below before closing.
        </div>
      )}
      {error && <p className="login-error">{error}</p>}
    </>
  );
}
