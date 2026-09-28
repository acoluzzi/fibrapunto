import { useCallback, useEffect, useState } from "react";
import { BudgetBar, euro, segmentsAround, segmentsOf } from "../components/BudgetBar";
import { Gate } from "../components/Gate";
import { useOfferBudget } from "../hooks/useOfferBudget";
import { ApiError, api } from "../lib/api";
import type { Campaign, OfferBudget } from "../lib/types";

/** §12.3 — what the receiving operator sees.
 *
 *  "The operator's first action should be to continue the conversation, not to
 *  reconstruct it." So the summary is the top of the page and everything else
 *  is below it, and the three fields §12.2 says are most often omitted get
 *  their own rows rather than being buried in a context blob. */
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

function Cases({
  onLogout,
  onExpired,
}: {
  onLogout: () => void;
  onExpired: () => void;
}) {
  const [records, setRecords] = useState<any[]>([]);
  const [selected, setSelected] = useState<any | null>(null);
  /* The offers budget is the team's, across every case: shown here so a
   * colleague deciding an offer knows what confirming it leaves. */
  const offers = useOfferBudget();

  const reload = useCallback(() => {
    api
      .operatorRecords()
      .then(setRecords)
      .catch((e: ApiError) => (e.status === 401 ? onExpired() : setRecords([])));
  }, [onExpired]);

  useEffect(reload, [reload]);

  function open(reference: string) {
    api
      .operatorRecord(reference)
      .then(setSelected)
      .catch((e: ApiError) => e.status === 401 && onExpired());
  }

  /* A status change alters the row's badge and its place in the list, so
   * both the detail and the table are refreshed. */
  function changed(reference: string) {
    reload();
    open(reference);
    offers.refresh();
  }

  return (
    <div className="operator">
      <p style={{ display: "flex", justifyContent: "space-between" }}>
        <a href="/">← back to the conversation</a>
        <button onClick={onLogout}>Sign out</button>
      </p>

      <h1 style={{ fontSize: 18, marginTop: 0 }}>Cases</h1>

      <BudgetSummary budget={offers.budget} />

      {records.length === 0 && (
        <p className="empty">No cases yet. Open one from the conversation.</p>
      )}

      {records.length > 0 && (
        <table className="metrics">
          <thead>
            <tr>
              <th>Case</th><th>Subject</th><th>Queue</th><th>Priority</th>
              {/* Whether a person has dealt with it — every case here exists
                  because one is needed (§12.1). Not to be confused with §13's
                  "record complete", which is a data-quality metric the
                  dashboard reports, not a lifecycle: a refund request is
                  complete the moment the assistant opens it, and not done
                  until a colleague closes it. */}
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {records.map((record) => (
              <tr
                key={record.reference}
                onClick={() => open(record.reference)}
                style={{ cursor: "pointer" }}
              >
                <td style={{ fontFamily: "var(--mono)" }}>{record.reference}</td>
                <td>{record.subject}</td>
                <td>{record.queue}</td>
                <td>{record.priority}</td>
                <td><StatusBadge status={record.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {selected && (
        <Detail
          record={selected}
          budget={offers.budget}
          campaigns={offers.campaigns}
          onChanged={() => changed(selected.summary.reference)}
        />
      )}
    </div>
  );
}

/** The team's offers budget, as it stands: what confirmed offers have used,
 *  what accepted offers awaiting a decision hold, what remains. */
function BudgetSummary({ budget }: { budget: OfferBudget | null }) {
  return (
    <section className="budget-summary">
      <div className="section-title">
        Offers budget
        {budget && (
          <span className="muted" style={{ marginLeft: 8, textTransform: "none", letterSpacing: 0, fontWeight: 400 }}>
            {euro(budget.total_eur)} in total · {budget.accepted} awaiting a decision
          </span>
        )}
      </div>
      {budget ? (
        <BudgetBar segments={segmentsOf(budget)} heldLabel="held, awaiting decisions" />
      ) : (
        <p className="empty">Recommendation system not reachable.</p>
      )}
    </section>
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
  return (
    <div style={{ marginTop: 28 }}>
      <div className="record-head">
        <h1>{s.subject}</h1>
        <div className="meta">
          Case <strong>{s.reference}</strong> · {s.customer} · {s.contract}
          {!s.contract_linked && (
            <span className="chip" style={{ marginLeft: 8 }}>contract not linked</span>
          )}
          {s.handled_by_agent && <span className="chip">opened by the assistant</span>}
        </div>
      </div>

      <dl className="fields">
        <dt>Picked up by</dt>
        <dd>{s.queue_label}, {s.promised}</dd>

        <dt>Status</dt>
        <dd>
          <StatusActions
            reference={s.reference}
            status={s.status}
            offerAwaitingDecision={record.retention_offer?.status === "accepted"}
            onChanged={onChanged}
          />
        </dd>

        <dt>Reason (customer's words)</dt>
        <dd>{record.stated_reason ?? "—"}</dd>

        <dt>Why it was opened</dt>
        <dd style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{record.why_it_escalated}</dd>

        {/* §12.2: "the field most often left out and the one that most often
            causes a second complaint." */}
        <dt>Already told to the customer</dt>
        <dd>
          {record.already_told_the_customer.length === 0 ? "—" : (
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {record.already_told_the_customer.map((line: string, index: number) => (
                <li key={index}>{line}</li>
              ))}
            </ul>
          )}
        </dd>

        <dt>Lookups performed</dt>
        <dd>
          {record.lookups.length === 0 ? "none" : record.lookups.map((lookup: any, index: number) => (
            <span className="chip" key={index}>{lookup.capability}: {lookup.outcome}</span>
          ))}
        </dd>

        {record.retention_offer && (
          <>
            <dt>Retention offer</dt>
            <dd>
              <OfferDecision
                reference={s.reference}
                offer={record.retention_offer}
                budget={budget}
                campaigns={campaigns}
                onDecided={onChanged}
              />
            </dd>
          </>
        )}

        {record.outbox?.length > 0 && (
          <>
            <dt>Emails to the customer</dt>
            <dd>
              {record.outbox.map((mail: any, index: number) => (
                <div key={index} style={{ marginBottom: 6 }}>
                  <span className="chip">{mail.sent ? "sent" : "queued, not sent (demo)"}</span>{" "}
                  to {mail.recipient} — <em>{mail.subject}</em>
                  <div className="muted" style={{ fontSize: 12 }}>{mail.body}</div>
                </div>
              ))}
            </dd>
          </>
        )}

        {record.internal_comments && (
          <>
            <dt>Internal notes</dt>
            <dd>
              <pre className="notes">{record.internal_comments}</pre>
              <span className="chip">never readable by the assistant</span>
            </dd>
          </>
        )}

        <dt>How to reach them</dt>
        <dd>
          {record.customer_contact.email || record.customer_contact.phone ? (
            <>
              {[record.customer_contact.email, record.customer_contact.phone]
                .filter(Boolean)
                .join(" · ")}
              <span className="chip">read as operator</span>
            </>
          ) : (
            /* Empty because the record carries no customer: the contract could
               not be linked, so there is nobody to join to. Saying why beats a
               blank field that reads as a missing permission. */
            <span className="muted">
              not available — this case is not linked to a contract
            </span>
          )}
        </dd>
      </dl>

      <p className="note">
        This row is the database perimeter seen from the other side. The assistant holds no GRANT
        on <code>customer.email</code> or <code>customer.phone</code>: that data
        never entered its context, so it could not repeat it or send it to a
        model. The same query, run here as <code>platform_admin</code>, returns
        it. This is not a different permission in the interface — it is a
        different database identity.
      </p>

      {/* The named rows above are the operator's view. This is the engineer's:
          the raw column, shown to make the point that a handover record is
          structured data with named keys rather than a paragraph of prose (§13)
          — which is what makes "complete" measurable instead of degrading into
          "long enough". Collapsed, because an operator picking up the phone
          should never need to open it. */}
      <details className="raw" style={{ marginTop: 16 }}>
        <summary>collected_context — the raw column</summary>
        <pre>{JSON.stringify(record.context, null, 2)}</pre>
      </details>
    </div>
  );
}

/** An offer the customer accepted: a colleague confirms it or declines it.
 *
 *  Either way the customer is told — in their conversation (a templated
 *  message, not the model) and by email. A decline needs a reason, because the
 *  customer is shown it; its hold goes back to the offers budget. Confirming
 *  spends the hold for good — the bar above the buttons shows exactly that:
 *  what is already used, what this offer holds, what would remain either way. */
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
      .then(onDecided)
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setBusy(false));
  }

  if (offer.status !== "accepted") {
    return (
      <>
        {offer.title}{" "}
        <span className="chip">
          {offer.status === "confirmed"
            ? "confirmed by the team"
            : offer.status === "declined_by_team"
              ? "declined by the team"
              : offer.status}
        </span>
        {offer.decision_reason && (
          <div className="muted" style={{ fontSize: 12 }}>
            Reason given to the customer: {offer.decision_reason}
          </div>
        )}
        {offer.decided_at && (
          <div className="muted" style={{ fontSize: 12 }}>
            {offer.customer_notified_in_chat
              ? "The customer was told in their conversation."
              : "The conversation no longer exists; the customer was told by email only."}
          </div>
        )}
      </>
    );
  }

  // The record carries the cost since it started travelling with the case;
  // an older record is priced from the catalogue by offer id.
  const cost =
    offer.cost_eur != null
      ? Number(offer.cost_eur)
      : Number(campaigns?.find((c) => c.offer_id === offer.offer_id)?.cost_eur ?? NaN);

  return (
    <>
      {offer.title} <span className="chip">accepted by the customer — awaiting your decision</span>
      {budget && !Number.isNaN(cost) ? (
        <div className="offer-budget">
          <BudgetBar segments={segmentsAround(budget, cost)} heldLabel="held by this offer" compact />
          <div className="muted" style={{ fontSize: 12 }}>
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
      <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
        <button className="primary" disabled={busy} onClick={() => decide("confirm")}>
          Confirm and apply
        </button>
        <input
          placeholder="Reason, shown to the customer"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          style={{ minWidth: 260 }}
        />
        <button disabled={busy || !reason.trim()} onClick={() => decide("decline")}>
          Decline
        </button>
      </div>
      {error && <p className="login-error">{error}</p>}
    </>
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
  const [told, setTold] = useState<string | null>(null);

  function move(target: "open" | "in_progress" | "closed") {
    setBusy(true);
    setError(null);
    setTold(null);
    api
      .operatorChangeStatus(reference, target, note.trim())
      .then((result) => {
        setNote("");
        if (result.changed) {
          setTold(
            result.customer_notified_in_chat
              ? "The customer was told in their conversation."
              : target === "open"
                ? "Back in the queue; the customer was not told."
                : "The conversation no longer exists; the customer was not told.",
          );
        }
        onChanged();
      })
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setBusy(false));
  }

  const moves: { label: string; target: "open" | "in_progress" | "closed"; primary?: boolean }[] =
    status === "open"
      ? [{ label: "Take", target: "in_progress", primary: true }, { label: "Close", target: "closed" }]
      : status === "in_progress"
        ? [{ label: "Close", target: "closed", primary: true }, { label: "Release", target: "open" }]
        : [{ label: "Reopen", target: "in_progress" }];

  return (
    <>
      <StatusBadge status={status} />
      {status !== "closed" && (
        <span className="muted" style={{ marginLeft: 8, fontSize: 12 }}>
          {status === "open"
            ? "nobody has acted on this case yet"
            : "a colleague is on it"}
        </span>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
        {moves.map((m) => (
          <button
            key={m.target}
            className={m.primary ? "primary" : undefined}
            disabled={busy || (m.target === "closed" && offerAwaitingDecision)}
            title={
              m.target === "closed" && offerAwaitingDecision
                ? "Decide the accepted offer first"
                : undefined
            }
            onClick={() => move(m.target)}
          >
            {m.label}
          </button>
        ))}
        <input
          placeholder="Note for colleagues (optional)"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          style={{ minWidth: 260 }}
        />
      </div>
      {offerAwaitingDecision && status !== "closed" && (
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          The customer accepted an offer: confirm or decline it below before closing.
        </div>
      )}
      {told && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{told}</div>}
      {error && <p className="login-error">{error}</p>}
    </>
  );
}
