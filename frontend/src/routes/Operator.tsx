import { type FormEvent, useCallback, useEffect, useState } from "react";
import { ApiError, api } from "../lib/api";

/** §12.3 — what the receiving operator sees.
 *
 *  "The operator's first action should be to continue the conversation, not to
 *  reconstruct it." So the summary is the top of the page and everything else
 *  is below it, and the three fields §12.2 says are most often omitted get
 *  their own rows rather than being buried in a context blob. */
export function Operator() {
  /* Nothing of the cases view renders until the server says this browser is
   * signed in. The data was always behind the check; the page now is too, so
   * nobody is shown an empty queue and left to wonder whether it is empty. */
  const [auth, setAuth] = useState<"checking" | "out" | "in">("checking");
  const [unavailable, setUnavailable] = useState<string | null>(null);

  useEffect(() => {
    api
      .operatorSession()
      .then(() => setAuth("in"))
      .catch((e: ApiError) => {
        if (e.status !== 401) setUnavailable(e.message);
        setAuth("out");
      });
  }, []);

  const expired = useCallback(() => setAuth("out"), []);

  function logout() {
    api.operatorLogout().finally(() => setAuth("out"));
  }

  if (auth === "checking") return null;
  if (auth === "out") {
    return <Login unavailable={unavailable} onSignedIn={() => setAuth("in")} />;
  }
  return <Cases onLogout={logout} onExpired={expired} />;
}

function Login({
  unavailable,
  onSignedIn,
}: {
  unavailable: string | null;
  onSignedIn: () => void;
}) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    api
      .operatorLogin(password)
      .then(onSignedIn)
      .catch((e: ApiError) =>
        setError(e.status === 401 ? "Wrong password." : e.message),
      )
      .finally(() => setBusy(false));
  }

  return (
    <div className="operator login">
      <p><a href="/">← back to the conversation</a></p>
      <form onSubmit={submit}>
        <h1>Operator sign-in</h1>
        <p className="muted">
          The operator view shows customer contact details the assistant is not
          allowed to read.
        </p>
        {unavailable ? (
          <p className="login-error">{unavailable}</p>
        ) : (
          <>
            <label htmlFor="operator-password">Password</label>
            <input
              id="operator-password"
              type="password"
              autoComplete="current-password"
              autoFocus
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            {error && <p className="login-error">{error}</p>}
            <button className="primary" type="submit" disabled={busy || !password}>
              Sign in
            </button>
          </>
        )}
      </form>
    </div>
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
  }

  return (
    <div className="operator">
      <p style={{ display: "flex", justifyContent: "space-between" }}>
        <a href="/">← back to the conversation</a>
        <button onClick={onLogout}>Sign out</button>
      </p>

      <h1 style={{ fontSize: 18, marginTop: 0 }}>Cases</h1>

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
        <Detail record={selected} onChanged={() => changed(selected.summary.reference)} />
      )}
    </div>
  );
}

function Detail({ record, onChanged }: { record: any; onChanged: () => void }) {
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
 *  customer is shown it; its cost goes back to the offers budget. */
function OfferDecision({
  reference,
  offer,
  onDecided,
}: {
  reference: string;
  offer: any;
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

  return (
    <>
      {offer.title} <span className="chip">accepted by the customer — awaiting your decision</span>
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
