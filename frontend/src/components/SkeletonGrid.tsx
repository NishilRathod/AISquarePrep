/**
 * Placeholders mirror the real card's geometry so the layout doesn't jump when
 * data lands — the reason this isn't a centred spinner.
 */
export function SkeletonGrid({ count = 6 }: { count?: number }) {
  return (
    <div
      data-testid="skeleton-grid"
      aria-hidden="true"
      className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
    >
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className="card shimmer">
          <div className="flex items-start justify-between">
            <div className="h-4 w-24 rounded bg-white/8" />
            <div className="h-4 w-8 rounded bg-white/8" />
          </div>
          <div className="mt-5 h-9 w-28 rounded bg-white/8" />
          <div className="mt-3 h-3.5 w-20 rounded bg-white/8" />
          <div className="mt-5 h-3 w-full rounded bg-white/8" />
          <div className="mt-3 h-3 w-24 rounded bg-white/8" />
        </div>
      ))}
    </div>
  );
}
