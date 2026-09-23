# Come si farebbe su Salesforce

Materiale per due slide. Non è il contenuto del panel — è la prova che la scelta
di costruire fuori dalla piattaforma è stata **una decisione e non un ripiego**.

La tesi in una frase: *la specifica è indipendente dalla piattaforma, e questa
implementazione lo dimostra costruendola altrove — ma ogni pezzo ha un
corrispettivo Agentforce, e in metà dei casi quello nativo è migliore.*

---

## Slide 1 — La mappatura

| Questa implementazione | Su Salesforce |
|---|---|
| `DOMAINS` con scope statement + istruzioni proprie | **Topics** in Agent Builder — classification description e instructions per topic |
| La tupla di capability per dominio | **Actions** assegnate al topic (Apex invocable, Flow, prompt template) |
| `crm.handover_record` | **Case**, con record type, priority e queue |
| `queue` derivata dal dominio | **Assignment rules** + **Omni-Channel** routing |
| Ruolo Postgres `fibrapunto_agent` | **Agent User** con il proprio permission set |
| `REVOKE SELECT (email, phone)` | **Field-Level Security** sul profilo dell'agente |
| `privacy/detectors.py` + i quattro punti di applicazione | **Einstein Trust Layer** — data masking |
| `MODEL_AGENT` come stringa di configurazione | **Trust Layer** / BYOLLM |
| `platform.trace_event` | Audit trail del Trust Layer |
| `evals/` | **Testing Center** per gli agenti |

Il punto da dire ad alta voce mentre è a schermo:

> «§4 della specifica descrive i Topics senza chiamarli così. Non è un caso:
> l'ho scritta cercando di dire *cosa* serve, non *quale prodotto*. Il fatto che
> mappi così bene è la verifica che la specifica sia davvero indipendente dalla
> piattaforma.»

---

## Slide 2 — Dove la piattaforma vince, e dove no

Questa è la slide che conta. La prima dimostra conoscenza; questa dimostra
giudizio.

### Gratis su Salesforce, costato giorni qui

- **Field-Level Security.** Dichiarativa, auditabile, con una UI che un
  amministratore usa senza chiamare un ingegnere. Io ho `GRANT` generati da uno
  YAML e un renderer da mantenere. La piattaforma vince nettamente.
- **Case, Queue, Omni-Channel.** Routing, escalation, SLA, riassegnazione: qui è
  una tabella e una vista. Lì è un prodotto maturo.
- **Masking PII.** Il Trust Layer lo fa fuori dagli schemi. I miei detector con
  checksum sono ~200 righe da mantenere e da estendere per ogni nuovo formato.
- **Zero-retention con il provider.** Contrattualizzato. Io lo ottengo con un
  flag su OpenRouter e lo dichiaro come sub-processore nel README.

### Da costruire comunque, su qualunque piattaforma

- **Gli esiti tipizzati (§5.1).** Un'Apex invocable action dichiara un tipo di
  ritorno, ma un'eccezione non gestita arriva al planner come errore generico.
  La garanzia «nessuna eccezione non tipizzata raggiunge il livello di
  ragionamento» è una disciplina che si scrive, non un default che si eredita.
  Vale anche qui: il `ToolNode` di LangGraph fa la cosa sbagliata in entrambi i
  rami, ed è per questo che il nodo è nostro.
- **Il guardrail di grounding.** Che ogni importo in risposta provenga da una
  verifica di *questa* conversazione non lo garantisce nessuno: serve
  conoscere il dominio.
- **La metà deterministica delle valutazioni.** Un Testing Center dà la
  struttura; che «routing accuracy» sia calcolata da codice sui fatti riportati
  e non dall'opinione di un giudice è una scelta di disegno.
- **Il budget di turni, l'isteresi del router, il blocco di conferma.** Tutte
  regole di prodotto.

### La riga da chiudere

> «Se dovessi rifarlo per un cliente su Agentforce, riscriverei circa metà di
> questo codice e cancellerei l'altra metà — perché la piattaforma la copre già
> meglio. Quello che non cambierebbe è il documento di design: i confini, gli
> esiti tipizzati, cosa si misura. È quella la parte che ho voluto mostrare, ed
> è il motivo per cui l'ho costruita in un posto dove ogni affermazione si può
> ispezionare invece che mostrare.»

---

## Nota per chi presenta

Due rischi su questa sezione:

1. **Non esagerare.** Un architetto in sala conosce Agentforce meglio di
   chiunque abbia letto la documentazione per una settimana. Le affermazioni
   sopra sono al livello dei concetti — Topics, Actions, FLS, Trust Layer,
   Omni-Channel — e lì reggono. Se qualcuno chiede un dettaglio di
   configurazione, la risposta è «non lo so, non ho costruito su org per
   questo»: è vera, ed è meglio di un'approssimazione che verrà corretta.
2. **Non farne il centro.** Sono due slide su quarantacinque minuti. Il
   contenuto è l'agente e il ragionamento dietro; questa parte esiste solo per
   togliere dal tavolo la domanda «ma sa lavorare sulla piattaforma?».
