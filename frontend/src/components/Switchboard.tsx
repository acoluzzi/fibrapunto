import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { BillingState, Faults } from "../lib/types";

/** Every module the agent depends on, with a live switch (§7, §15).
 *
 *  "Failures are provoked on purpose and repeated on command." Each switch
 *  applies at once, to every conversation, and stays until moved back. The
 *  customer keeps talking; the switch decides whether the part of the system
 *  their next message needs is there. What they then see is the real failure
 *  path — a typed outcome, a stated limitation, an honest closing — not a
 *  simulation of one.
 *
 *  The state is read back every few seconds rather than trusted from the last
 *  click, so the board always shows what the system is actually doing. */

type Option = { value: string; label: string };

const BILLING: Option[] = [
  { value: "normal", label: "up" },
  { value: "slow", label: "slow" },
  { value: "timeout", label: "not responding" },
  { value: "unauthorized", label: "rejects our credential" },
  { value: "malformed", label: "returns invalid data" },
];
const UP_DOWN: Option[] = [
  { value: "up", label: "up" },
  { value: "down", label: "down" },
];
const COLLEAGUES: Option[] = [
  { value: "schedule", label: "opening hours" },
  { value: "available", label: "available" },
  { value: "unavailable", label: "none available" },
];

const ROWS: { key: keyof Faults; label: string; options: Option[]; hint: string }[] = [
  { key: "crm", label: "CRM", options: UP_DOWN, hint: "Case records and contract linking" },
  {
    key: "colleagues",
    label: "Colleagues",
    options: COLLEAGUES,
    hint: "Whether anyone can take a transfer",
  },
  { key: "model", label: "Model provider", options: UP_DOWN, hint: "The router and the agent" },
  {
    key: "credential_store",
    label: "Credential store",
    options: UP_DOWN,
    hint: "The billing token (§17.7)",
  },
  {
    key: "trace_store",
    label: "Trace store",
    options: UP_DOWN,
    hint: "Observability database — the conversation must carry on without it",
  },
];

const HEALTHY = new Set(["normal", "up", "schedule", "available"]);

export function Switchboard({ onError }: { onError: (message: string | null) => void }) {
  const [billing, setBilling] = useState<BillingState>("normal");
  const [faults, setFaults] = useState<Faults | null>(null);

  const read = useCallback(() => {
    api.billingState().then(({ mode }) => setBilling(mode)).catch(() => undefined);
    // 404 when FAULT_INJECTION is off: those switches are simply not shown.
    api.faults().then(setFaults).catch(() => setFaults(null));
  }, []);

  useEffect(() => {
    read();
    const timer = window.setInterval(read, 3000);
    return () => window.clearInterval(timer);
  }, [read]);

  function report(e: Error) {
    onError(`Switch not applied: ${e.message}`);
  }

  function flipBilling(mode: BillingState) {
    api
      .setBillingState(mode)
      .then(() => {
        setBilling(mode);
        onError(null);
      })
      .catch(report);
  }

  function flip(key: keyof Faults, value: string) {
    api
      .setFault({ [key]: value } as Partial<Faults>)
      .then((next) => {
        setFaults(next);
        onError(null);
      })
      .catch(report);
  }

  return (
    <div className="switchboard" aria-label="Failure switches">
      <span className="switchboard-title">Live switches</span>
      <Switch
        id="sw-billing"
        label="Billing system"
        hint="The external billing system (the stub)"
        value={billing}
        options={BILLING}
        onChange={(v) => flipBilling(v as BillingState)}
      />
      {faults &&
        ROWS.map((row) => (
          <Switch
            key={row.key}
            id={`sw-${row.key}`}
            label={row.label}
            hint={row.hint}
            value={faults[row.key]}
            options={row.options}
            onChange={(v) => flip(row.key, v)}
          />
        ))}
    </div>
  );
}

function Switch({
  id,
  label,
  hint,
  value,
  options,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  value: string;
  options: Option[];
  onChange: (value: string) => void;
}) {
  return (
    <div className={`field switch ${HEALTHY.has(value) ? "" : "failing"}`} title={hint}>
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}
