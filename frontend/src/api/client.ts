import type {
  AddCityResult,
  AnomalyBoard,
  CitySuggestion,
  Health,
  PaginatedWeather,
  TrackedCities,
} from "./types";

export const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

/** Status 0 means the request never reached the server at all. */
export const NETWORK_ERROR_STATUS = 0;

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }

  /** True when the API could not be reached, as opposed to answering with an error. */
  get isNetworkError(): boolean {
    return this.status === NETWORK_ERROR_STATUS;
  }
}

interface ValidationDetail {
  loc?: unknown[];
  msg?: string;
}

/**
 * The API returns `detail` as a string for handled errors but as an array of
 * objects for FastAPI validation failures, and /health returns neither. Collapse
 * all three into one message rather than rendering "[object Object]".
 */
function messageFromBody(body: unknown, status: number): string {
  if (typeof body === "object" && body !== null) {
    const detail = (body as { detail?: unknown }).detail;

    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }

    if (Array.isArray(detail)) {
      const parts = (detail as ValidationDetail[])
        .map((entry) => {
          const field = Array.isArray(entry.loc) ? entry.loc.slice(1).join(".") : "";
          return field ? `${field}: ${entry.msg ?? "invalid"}` : (entry.msg ?? "invalid");
        })
        .filter(Boolean);
      if (parts.length) return parts.join("; ");
    }

    // /health failure shape: {"status": "error", "redis": "unreachable"}
    const redis = (body as { redis?: unknown }).redis;
    if (typeof redis === "string") {
      return `Redis ${redis}`;
    }
  }

  return `Request failed with status ${status}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;

  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError(
      NETWORK_ERROR_STATUS,
      `Can't reach the API at ${API_BASE_URL} — is the service running?`,
    );
  }

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // Non-JSON error body (a proxy or gateway page); fall through to the default.
    }
    throw new ApiError(response.status, messageFromBody(body, response.status));
  }

  return (await response.json()) as T;
}

export function fetchWeather(cities: string[]): Promise<PaginatedWeather> {
  const params = new URLSearchParams({
    cities: cities.join(","),
    page_size: String(Math.max(cities.length, 1)),
  });
  return request<PaginatedWeather>(`/weather?${params}`);
}

export function fetchTrackedCities(): Promise<TrackedCities> {
  return request<TrackedCities>("/cities");
}

export function searchCities(query: string, signal?: AbortSignal): Promise<CitySuggestion[]> {
  const params = new URLSearchParams({ q: query, limit: "8" });
  return request<CitySuggestion[]>(`/cities/search?${params}`, { signal });
}

export function addCity(city: string): Promise<AddCityResult> {
  return request<AddCityResult>("/cities", {
    method: "POST",
    body: JSON.stringify({ city }),
  });
}

export function fetchHealth(): Promise<Health> {
  return request<Health>("/health");
}

/**
 * The global anomaly board. Unrelated to the tracked cities — it ranks every
 * city the climate-normals artefact covers, so it takes no city list.
 */
export function fetchAnomalies(limit = 10): Promise<AnomalyBoard> {
  return request<AnomalyBoard>(`/anomalies?limit=${limit}`);
}
