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

  useEffect(() => {
    api
      .operatorRecords()
      .then(setRecords)
      .catch((e: ApiError) => (e.status === 401 ? onExpired() : setRecords([])));
  }, [onExpired]);

  function open(reference: string) {
    api
      .operatorRecord(reference)
      .then(setSelected)
      .catch((e: ApiError) => e.status === 401 && onExpired());
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
              <th>Case</th><th>Subject</th><th>Queue</th><th>Priority</th><th>Complete</th>
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
                <td>
                  <span className={`badge ${record.is_complete ? "good" : "critical"}`}>
                    <span aria-hidden="true">{record.is_complete ? "●" : "■"}</span>
                    {record.is_complete ? "yes" : "no"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {selected && <Detail record={selected} />}
    </div>
  );
}

function Detail({ record }: { record: any }) {
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
        This row is §8 seen from the other side. The assistant holds no GRANT
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
        <summary>collected_context — the raw column (§13)</summary>
        <pre>{JSON.stringify(record.context, null, 2)}</pre>
      </details>
    </div>
  );
}
