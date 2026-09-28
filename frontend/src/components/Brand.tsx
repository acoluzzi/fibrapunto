/** The mark: three arcs of signal over a point, the fibre reaching a home.
 *  Drawn in currentColor so it takes the accent wherever it sits. */
export function Mark({ className = "mark" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M4 10.5a11 11 0 0 1 16 0" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
      <path d="M7.2 14a6.6 6.6 0 0 1 9.6 0" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
      <circle cx="12" cy="18.2" r="2.2" fill="currentColor" />
    </svg>
  );
}
