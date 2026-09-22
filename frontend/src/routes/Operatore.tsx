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

        <dt>Contatti</dt>
        <dd>{record.customer_contact.email} · {record.customer_contact.phone}</dd>
      </dl>

      <p className="note">
        L'assistente non può leggere queste ultime due colonne: non ha la GRANT
        su <code>customer.email</code> né su <code>internal_comments</code>. Che
        siano qui è l'identità dell'operatore che fa qualcosa che l'agente non
        può fare (§8).
      </p>

      <details className="raw" style={{ marginTop: 16 }}>
        <summary>Contesto completo</summary>
        <pre>{JSON.stringify(record.context, null, 2)}</pre>
      </details>
    </div>
  );
}
