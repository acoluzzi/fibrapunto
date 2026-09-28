/** Horizontal bars, one series: the form for "which is biggest".
 *
 *  HTML, not SVG: the bar is a div, so it is responsive for free and the label
 *  is real text. Bars stay thin (18px), the data end is rounded and the
 *  baseline end square, the value sits at the tip in ink — text never wears
 *  the series colour. */
export function BarChart({
  rows,
  format = String,
  color = "var(--series-1)",
  empty = "Nothing yet.",
}: {
  rows: { label: string; value: number }[];
  format?: (value: number) => string;
  color?: string;
  empty?: string;
}) {
  const max = Math.max(0, ...rows.map((r) => r.value));
  if (rows.length === 0 || max === 0) return <p className="empty">{empty}</p>;
  return (
    <div className="hbar" role="table">
      {rows.map((row) => (
        <div className="hbar-row" key={row.label} role="row" title={`${row.label}: ${format(row.value)}`}>
          <span className="hbar-label" role="cell">{row.label}</span>
          <span className="hbar-track" role="cell">
            <span className="hbar-lane">
              <span
                className="hbar-fill"
                style={{ width: `${(row.value / max) * 100}%`, background: color }}
              />
            </span>
            <span className="hbar-value">{format(row.value)}</span>
          </span>
        </div>
      ))}
    </div>
  );
}
