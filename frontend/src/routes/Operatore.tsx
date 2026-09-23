import { useEffect, useState } from "react";
import { api } from "../lib/api";

/** §12.3 — what the receiving operator sees.
 *
 *  "The operator's first action should be to continue the conversation, not to
 *  reconstruct it." So the summary is the top of the page and everything else
 *  is below it, and the three fields §12.2 says are most often omitted get
 *  their own rows rather than being buried in a context blob. */
export function Operatore() {
  const [records, setRecords] = useState<any[]>([]);
  const [selected, setSelected] = useState<any | null>(null);

  useEffect(() => {
    api.operatorRecords().then(setRecords).catch(() => setRecords([]));
  }, []);

  function open(reference: string) {
    api.operatorRecord(reference).then(setSelected).catch(() => undefined);
  }

  return (
    <div className="operator">
      <p><a href="/">← torna alla conversazione</a></p>

      <h1 style={{ fontSize: 18, marginTop: 0 }}>Pratiche</h1>

      {records.length === 0 && (
        <p className="empty">Nessuna pratica. Ne apra una dalla conversazione.</p>
      )}

      {records.length > 0 && (
        <table className="metrics">
          <thead>
            <tr>
              <th>Pratica</th><th>Oggetto</th><th>Coda</th><th>Priorità</th><th>Completa</th>
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
                    {record.is_complete ? "sì" : "no"}
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
          Pratica <strong>{s.reference}</strong> · {s.customer} · {s.contract}
          {!s.contract_linked && (
            <span className="chip" style={{ marginLeft: 8 }}>contratto non collegato</span>
          )}
          {s.handled_by_agent && <span className="chip">aperta dall'assistente</span>}
        </div>
      </div>

      <dl className="fields">
        <dt>Presa in carico da</dt>
        <dd>{s.queue_label}, {s.promised}</dd>

        <dt>Motivo (parole del cliente)</dt>
        <dd>{record.stated_reason ?? "—"}</dd>

        <dt>Perché è stata aperta</dt>
        <dd style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{record.why_it_escalated}</dd>

        {/* §12.2: "the field most often left out and the one that most often
            causes a second complaint." */}
        <dt>Già detto al cliente</dt>
        <dd>
          {record.already_told_the_customer.length === 0 ? "—" : (
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {record.already_told_the_customer.map((line: string, index: number) => (
                <li key={index}>{line}</li>
              ))}
            </ul>
          )}
        </dd>

        <dt>Verifiche eseguite</dt>
        <dd>
          {record.lookups.length === 0 ? "nessuna" : record.lookups.map((lookup: any, index: number) => (
            <span className="chip" key={index}>{lookup.capability}: {lookup.outcome}</span>
          ))}
        </dd>

        <dt>Come ricontattarlo</dt>
        <dd>
          {record.customer_contact.email || record.customer_contact.phone ? (
            <>
              {[record.customer_contact.email, record.customer_contact.phone]
                .filter(Boolean)
                .join(" · ")}
              <span className="chip">letto come operatore</span>
            </>
          ) : (
            /* Empty because the record carries no customer: the contract could
               not be linked, so there is nobody to join to. Saying why beats a
               blank field that reads as a missing permission. */
            <span className="muted">
              non disponibile — la pratica non è collegata a un contratto
            </span>
          )}
        </dd>
      </dl>

      <p className="note">
        Questa riga è il §8 visto dall'altro lato. L'assistente non ha la GRANT
        su <code>customer.email</code> e <code>customer.phone</code>: quei dati
        non sono mai entrati nel suo contesto, quindi non poteva ripeterli né
        inviarli a un modello. La stessa query, eseguita qui come
        <code> platform_admin</code>, li restituisce. Non è un permesso diverso
        nell'interfaccia — è un'identità di database diversa.
      </p>

      {/* The named rows above are the operator's view. This is the engineer's:
          the raw column, shown to make the point that a handover record is
          structured data with named keys rather than a paragraph of prose (§13)
          — which is what makes "complete" measurable instead of degrading into
          "long enough". Collapsed, because an operator picking up the phone
          should never need to open it. */}
      <details className="raw" style={{ marginTop: 16 }}>
        <summary>collected_context — la colonna grezza (§13)</summary>
        <pre>{JSON.stringify(record.context, null, 2)}</pre>
      </details>
    </div>
  );
}
