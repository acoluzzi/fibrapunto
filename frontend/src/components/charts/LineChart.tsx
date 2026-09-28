import { useEffect, useRef, useState } from "react";

export interface Series {
  name: string;
  /** One value per label; null where the run did not measure it. */
  values: (number | null)[];
}

interface Props {
  /** Short, for the axis: at most a handful of characters. */
  labels: string[];
  /** Full, for the tooltip; defaults to the axis labels. */
  titles?: string[];
  series: Series[];
  domain?: [number, number];
  format?: (value: number) => string;
  height?: number;
}

const M = { top: 12, right: 16, bottom: 26, left: 44 };

/** Change over time, several series on one axis (never two axes).
 *
 *  Colours are the fixed categorical slots in order — the order is what keeps
 *  adjacent series apart under colour-vision deficiency — and every series is
 *  in the legend, so identity never rests on hue alone. A crosshair follows
 *  the pointer and the tooltip lists every series at that point; nothing is
 *  labelled on the plot itself, because eval metrics converge at 100% and
 *  end-labels there would sit on top of each other. */
export function LineChart({ labels, titles, series, domain, format = String, height = 200 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const all = series.flatMap((s) => s.values.filter((v): v is number => v != null));
  const [lo, hi] = domain ?? [Math.min(0, ...all), Math.max(1, ...all)];
  const innerW = Math.max(0, width - M.left - M.right);
  const innerH = height - M.top - M.bottom;
  const n = labels.length;
  const x = (i: number) => M.left + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);
  const y = (v: number) => M.top + innerH - ((v - lo) / (hi - lo || 1)) * innerH;
  const ticks = 4;
  const tickValues = Array.from({ length: ticks + 1 }, (_, i) => lo + ((hi - lo) * i) / ticks);
  // Axis labels at most every ~70px, so they never sit on top of each other;
  // the tooltip names the point exactly.
  const every = Math.max(1, Math.ceil((n * 70) / Math.max(innerW, 1)));

  if (n === 0 || series.length === 0) return <p className="empty">No runs recorded yet.</p>;

  function move(event: React.MouseEvent<SVGSVGElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const px = event.clientX - box.left;
    let best = 0;
    for (let i = 1; i < n; i += 1) if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i;
    setHover(best);
  }

  const tooltipLeft = hover == null ? 0 : Math.min(x(hover) + 10, Math.max(0, width - 200));

  return (
    <div className="linechart" ref={ref}>
      <div className="chart-legend">
        {series.map((s, i) => (
          <span key={s.name}>
            <span className="key" style={{ background: `var(--series-${(i % 8) + 1})` }} aria-hidden="true" />
            {s.name}
          </span>
        ))}
      </div>
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={`${series.map((s) => s.name).join(", ")} over ${n} runs`}
        onMouseMove={move}
        onMouseLeave={() => setHover(null)}
      >
        {tickValues.map((v) => (
          <g key={v}>
            <line x1={M.left} x2={M.left + innerW} y1={y(v)} y2={y(v)} className="gridline" />
            <text x={M.left - 6} y={y(v)} dy="0.35em" textAnchor="end" className="tick">
              {format(v)}
            </text>
          </g>
        ))}
        <line x1={M.left} x2={M.left + innerW} y1={y(lo)} y2={y(lo)} className="baseline" />
        {labels.map((label, i) =>
          i % every === 0 || i === n - 1 ? (
            <text key={label + i} x={x(i)} y={height - 8} textAnchor="middle" className="tick">
              {label}
            </text>
          ) : null,
        )}
        {hover != null && (
          <line x1={x(hover)} x2={x(hover)} y1={M.top} y2={M.top + innerH} className="crosshair" />
        )}
        {series.map((s, si) => {
          const color = `var(--series-${(si % 8) + 1})`;
          const path = s.values
            .map((v, i) => (v == null ? null : `${x(i)},${y(v)}`))
            .reduce<string[]>((acc, point, i) => {
              if (point == null) return acc;
              const prev = i > 0 ? s.values[i - 1] : null;
              acc.push(`${acc.length === 0 || prev == null ? "M" : "L"}${point}`);
              return acc;
            }, [])
            .join(" ");
          return (
            <g key={s.name}>
              <path d={path} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              {s.values.map((v, i) =>
                v == null ? null : (
                  <circle
                    key={i}
                    cx={x(i)}
                    cy={y(v)}
                    r={hover === i ? 5 : 4}
                    fill={color}
                    className="marker"
                  />
                ),
              )}
            </g>
          );
        })}
      </svg>
      {hover != null && (
        <div className="chart-tooltip" style={{ left: tooltipLeft, top: M.top }}>
          <div className="when">{(titles ?? labels)[hover]}</div>
          {series.map((s, i) => (
            <div key={s.name}>
              <span className="key" style={{ background: `var(--series-${(i % 8) + 1})` }} aria-hidden="true" />
              {s.name}
              <strong>{s.values[hover] == null ? "—" : format(s.values[hover] as number)}</strong>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
