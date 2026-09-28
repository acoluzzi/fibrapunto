import type { OfferBudget } from "../lib/types";

export const euro = (value: number | string) =>
  `€${Number(value).toLocaleString("en-IE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

interface Segments {
  /** Given away for good (red). */
  used: number;
  /** Committed, awaiting a colleague's decision (gray). */
  held: number;
  /** What the next offer is measured against (green). */
  remaining: number;
}

/** The offers budget as three lengths of one bar: used, held, remaining.
 *
 *  Colour never carries it alone: each segment has a glyph and a label in the
 *  legend with its amount, and the segments are separated by a gap in the
 *  surface colour rather than a stroke. `heldLabel` names what the gray is —
 *  "held by this offer" on a case, "held, awaiting decisions" on a dashboard. */
export function BudgetBar({
  segments,
  heldLabel = "held",
  compact = false,
}: {
  segments: Segments;
  heldLabel?: string;
  compact?: boolean;
}) {
  const used = Math.max(0, segments.used);
  const held = Math.max(0, segments.held);
  const remaining = Math.max(0, segments.remaining);
  const total = used + held + remaining;
  const pct = (part: number) => (total > 0 ? (part / total) * 100 : 0);

  return (
    <div className={`budget-stack ${compact ? "compact" : ""}`}>
      <div
        className="budget-track"
        role="img"
        aria-label={`Used ${euro(used)}, ${heldLabel} ${euro(held)}, remaining ${euro(remaining)}`}
      >
        {total === 0 && <div className="budget-seg empty" style={{ width: "100%" }} />}
        {used > 0 && <div className="budget-seg used" style={{ width: `${pct(used)}%` }} title={`used ${euro(used)}`} />}
        {held > 0 && <div className="budget-seg held" style={{ width: `${pct(held)}%` }} title={`${heldLabel} ${euro(held)}`} />}
        {remaining > 0 && (
          <div className="budget-seg remaining" style={{ width: `${pct(remaining)}%` }} title={`remaining ${euro(remaining)}`} />
        )}
      </div>
      <div className="budget-legend">
        <span>
          <span className="swatch used" aria-hidden="true">■</span>
          used <strong>{euro(used)}</strong>
        </span>
        <span>
          <span className="swatch held" aria-hidden="true">◆</span>
          {heldLabel} <strong>{euro(held)}</strong>
        </span>
        <span>
          <span className="swatch remaining" aria-hidden="true">●</span>
          remaining <strong>{euro(remaining)}</strong>
        </span>
      </div>
    </div>
  );
}

/** The whole budget: confirmed offers are used, every undecided acceptance
 *  is held, the rest remains. */
export function segmentsOf(budget: OfferBudget): Segments {
  return {
    used: Number(budget.confirmed_eur),
    held: Number(budget.held_eur),
    remaining: Number(budget.remaining_eur),
  };
}

/** The budget from one case: this offer's cost is the hold; everything else
 *  committed — confirmed offers and other cases' holds — is used. */
export function segmentsAround(budget: OfferBudget, offerCost: number): Segments {
  const spent = Number(budget.spent_eur);
  return {
    used: Math.max(0, spent - offerCost),
    held: Math.min(offerCost, spent),
    remaining: Number(budget.remaining_eur),
  };
}
