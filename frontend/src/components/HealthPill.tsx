import { ApiError } from "../api/client";
import { useHealth } from "../hooks/queries";

export function HealthPill() {
  const { data, error, isPending } = useHealth();

  let color = "var(--color-ink-faint)";
  let label = "checking…";

  if (!isPending) {
    if (data?.status === "ok") {
      color = "var(--color-live)";
      label = "API + Redis";
    } else if (error instanceof ApiError) {
      color = "var(--color-danger)";
      // A 503 here means the API answered but Redis is down — a different
      // problem from the API being unreachable, so don't merge the two.
      label = error.isNetworkError ? "API unreachable" : "Redis unreachable";
    } else {
      color = "var(--color-danger)";
      label = "degraded";
    }
  }

  return (
    <span
      className="tabular inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[11px]"
      style={{
        borderColor: "var(--color-edge)",
        backgroundColor: "var(--color-panel)",
        color: "var(--color-ink-muted)",
      }}
    >
      <span aria-hidden="true" className="size-2 rounded-full" style={{ backgroundColor: color }} />
      {label}
    </span>
  );
}
