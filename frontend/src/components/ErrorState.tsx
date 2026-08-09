import { ApiError } from "../api/client";
import { Button } from "./Button";

interface ErrorStateProps {
  error: unknown;
  onRetry: () => void;
}

/**
 * Shows what actually went wrong — the real status code and the backend's own
 * `detail` string — instead of a generic apology the user can't act on.
 */
export function ErrorState({ error, onRetry }: ErrorStateProps) {
  const isApiError = error instanceof ApiError;
  const status = isApiError && !error.isNetworkError ? error.status : null;
  const message =
    error instanceof Error ? error.message : "An unexpected error occurred while loading weather.";

  return (
    <div
      role="alert"
      className="card border-dashed"
      style={{ borderColor: "var(--color-danger)" }}
    >
      <div className="flex flex-wrap items-center gap-2">
        {status !== null && (
          <span
            className="tabular rounded-md px-2 py-0.5 text-xs font-semibold"
            style={{ backgroundColor: "#fbe9e7", color: "var(--color-danger)" }}
          >
            {status}
          </span>
        )}
        <h3 className="text-sm font-semibold" style={{ color: "var(--color-danger)" }}>
          {status !== null ? "The API returned an error" : "Couldn't reach the API"}
        </h3>
      </div>

      <p className="mt-2 text-sm" style={{ color: "var(--color-ink-muted)" }}>
        {message}
      </p>

      <div className="mt-4">
        <Button variant="primary" onClick={onRetry}>
          Retry
        </Button>
      </div>
    </div>
  );
}
