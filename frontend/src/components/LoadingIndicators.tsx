/**
 * Reusable loading indicators following UX best practices:
 * - < 0.1s: instant, no indicator needed
 * - 0.1–2s: skeleton placeholders (perceived as layout settling)
 * - > 2s:   spinner / looped animation (indeterminate)
 * - > 10s:  progress bar (determinate)
 */

/* ── Spinner (indeterminate) ─────────────────────────────── */
export function LoadingSpinner({
  size = 'md',
  label,
}: {
  size?: 'sm' | 'md' | 'lg';
  label?: string;
}) {
  const cls =
    size === 'sm'
      ? 'loading-spinner loading-spinner-sm'
      : size === 'lg'
        ? 'loading-spinner loading-spinner-lg'
        : 'loading-spinner';

  return (
    <span className="inline-flex items-center gap-2">
      <span className={cls} aria-hidden="true" />
      {label && (
        <span className="text-[10px] font-mono text-text-muted tracking-[0.1em] uppercase loading-dots">
          {label}
        </span>
      )}
    </span>
  );
}

/* ── Section skeleton (for Suspense fallbacks) ───────────── */
export function SectionSkeleton({
  rows = 4,
  barWidths,
}: {
  rows?: number;
  barWidths?: string[];
}) {
  const widths = barWidths ?? ['85%', '65%', '45%', '70%', '55%', '80%'];
  return (
    <div className="section-skeleton" aria-label="Chargement de la section" role="status">
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          className="section-skeleton-bar"
          style={{ width: widths[i % widths.length] }}
        />
      ))}
    </div>
  );
}

/* ── Table skeleton (for table Suspense fallbacks) ───────── */
export function TableSkeleton({
  columns = 6,
  rows = 4,
}: {
  columns?: number;
  rows?: number;
}) {
  const widths = ['45%', '85%', '65%', '85%', '45%', '65%'];
  return (
    <div className="overflow-x-auto" role="status" aria-label="Chargement du tableau">
      <table>
        <thead>
          <tr>
            {Array.from({ length: columns }, (_, i) => (
              <th key={i}>
                <span
                  className="skeleton-shimmer inline-block h-2.5"
                  style={{ width: widths[i % widths.length] }}
                />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, rowIdx) => (
            <tr key={rowIdx}>
              {Array.from({ length: columns }, (_, colIdx) => (
                <td key={colIdx}>
                  <span
                    className="table-skeleton-bar"
                    style={{ width: widths[colIdx % widths.length] }}
                  />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── Chart skeleton (for chart Suspense fallbacks) ───────── */
export function ChartSkeleton({ height = 520 }: { height?: number }) {
  return (
    <div
      className="flex flex-col items-center justify-center border border-border rounded-lg bg-bg gap-3"
      style={{ height: `${height}px` }}
      role="status"
      aria-label="Chargement du graphique"
    >
      <LoadingSpinner size="md" />
      <span className="text-text-muted text-[10px] font-mono tracking-[0.1em] uppercase loading-dots">
        Chargement graphique
      </span>
    </div>
  );
}

/* ── Full-page route loader ──────────────────────────────── */
export function RouteLoader() {
  return (
    <div className="loading-screen">
      <LoadingSpinner size="lg" />
      <span className="loading-dots">Chargement</span>
    </div>
  );
}

/* ── Determinate progress bar ────────────────────────────── */
export function ProgressBar({
  percent,
  label,
  striped = false,
}: {
  percent: number;
  label?: string;
  striped?: boolean;
}) {
  const clamped = Math.min(100, Math.max(0, percent));
  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <div className="flex items-center justify-between">
          <span className="text-[9px] font-mono text-text-muted tracking-[0.1em] uppercase">
            {label}
          </span>
          <span className="text-[9px] font-mono text-text-muted tabular-nums">
            {Math.round(clamped)}%
          </span>
        </div>
      )}
      <div className="progress-track">
        <div
          className={striped ? 'progress-fill-striped' : 'progress-fill'}
          style={{ width: `${clamped}%` }}
          role="progressbar"
          aria-valuenow={clamped}
          aria-valuemin={0}
          aria-valuemax={100}
        />
      </div>
    </div>
  );
}

/* ── Inline button spinner ───────────────────────────────── */
export function ButtonSpinner() {
  return <span className="loading-spinner loading-spinner-sm" aria-hidden="true" />;
}
