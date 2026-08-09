/**
 * Placeholders mirror the real card's geometry so the layout doesn't jump when
 * data lands — the reason this isn't a centred spinner.
 */
export function SkeletonGrid({ count = 9 }: { count?: number }) {
  return (
    <div
      data-testid="skeleton-grid"
      aria-hidden="true"
      className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
    >
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className="card shimmer">
          <div className="h-[3px] w-full" style={{ backgroundColor: "#e7ddd0" }} />
          <div className="p-4">
            <div className="flex items-start justify-between">
              <div className="h-4 w-24 rounded bg-black/8" />
              <div className="h-6 w-7 rounded bg-black/8" />
            </div>
            <div className="mt-3 h-8 w-24 rounded bg-black/8" />
            <div className="mt-3 h-3.5 w-20 rounded bg-black/8" />
            <div className="mt-2 h-3 w-28 rounded bg-black/8" />
            <div className="mt-4 h-3 w-full rounded bg-black/8" />
          </div>
        </div>
      ))}
    </div>
  );
}
