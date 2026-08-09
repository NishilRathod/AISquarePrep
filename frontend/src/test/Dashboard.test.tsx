import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import type { CitySuggestion, PaginatedWeather, TrackedCities, Weather } from "../api/types";

function weather(city: string, overrides: Partial<Weather> = {}): Weather {
  return {
    city,
    country: "GB",
    temperature_c: 14.2,
    feels_like_c: 12.8,
    humidity_pct: 76,
    condition: "Clouds",
    wind_speed_mps: 4.1,
    observed_at: new Date().toISOString(),
    source: "cache",
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

interface RouteConfig {
  tracked?: TrackedCities;
  weather?: PaginatedWeather | { status: number; body: unknown };
  search?: CitySuggestion[];
}

/**
 * Route by URL rather than by call order — the app fires /health, /cities and
 * /weather concurrently, so a queue of sequential mocks would be flaky.
 */
function mockApi(config: RouteConfig) {
  const postedCities: string[] = [];
  // Stateful, so a city added by POST is still there when /cities is refetched.
  const tracked: TrackedCities = {
    cities: [...(config.tracked?.cities ?? [])],
    defaults: [...(config.tracked?.defaults ?? [])],
  };

  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);

    if (url.includes("/health")) {
      return jsonResponse({ status: "ok", redis: "connected" });
    }

    if (url.includes("/cities/search")) {
      return jsonResponse(config.search ?? []);
    }

    if (url.includes("/cities")) {
      if (init?.method === "POST") {
        const city = (JSON.parse(String(init.body)) as { city: string }).city;
        postedCities.push(city);
        tracked.cities = [...tracked.cities, city];
        return jsonResponse({ city, added: true, cities: tracked.cities });
      }
      return jsonResponse({ cities: [...tracked.cities], defaults: [...tracked.defaults] });
    }

    if (url.includes("/weather")) {
      const configured = config.weather;
      if (configured && "status" in configured) {
        return jsonResponse(configured.body, configured.status);
      }
      return jsonResponse(configured ?? { items: [], page: 1, page_size: 10, total: 0 });
    }

    throw new Error(`Unexpected request: ${url}`);
  });

  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, postedCities };
}

function renderApp(ui: ReactElement = <App />) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Weather dashboard", () => {
  it("shows skeletons first, then previously fetched data badged by source", async () => {
    mockApi({
      tracked: { cities: ["London", "Paris"], defaults: ["London", "Paris"] },
      weather: {
        items: [weather("London"), weather("Paris", { source: "upstream", temperature_c: 18.9 })],
        page: 1,
        page_size: 10,
        total: 2,
      },
    });

    renderApp();

    expect(screen.getByTestId("skeleton-grid")).toBeInTheDocument();

    await screen.findByRole("heading", { name: "London" });

    // Scoped to the cards: the footer legend also spells out CACHED and LIVE.
    const [londonCard, parisCard] = screen.getAllByRole("article");
    expect(within(londonCard).getByText("14.2°")).toBeInTheDocument();
    expect(within(londonCard).getByText("CACHED")).toBeInTheDocument();
    expect(within(parisCard).getByRole("heading", { name: "Paris" })).toBeInTheDocument();
    expect(within(parisCard).getByText("LIVE")).toBeInTheDocument();
    expect(screen.queryByTestId("skeleton-grid")).not.toBeInTheDocument();
  });

  it("pairs a tracked city with the canonical name OpenWeather answers with", async () => {
    // Tracking "Mysuru" comes back as "Mysore"; a name-only lookup would drop it.
    mockApi({
      tracked: { cities: ["London", "Mysuru"], defaults: ["London"] },
      weather: {
        items: [weather("London"), weather("Mysore", { country: "IN", temperature_c: 29.1 })],
        page: 1,
        page_size: 10,
        total: 2,
      },
    });

    renderApp();
    await screen.findByRole("heading", { name: "London" });

    expect(screen.getByRole("heading", { name: "Mysore" })).toBeInTheDocument();
    expect(screen.getByText("29.1°")).toBeInTheDocument();
    expect(screen.queryByText(/No data returned/)).not.toBeInTheDocument();
  });

  it("surfaces the real status code and backend detail when the API errors", async () => {
    mockApi({
      tracked: { cities: ["London"], defaults: ["London"] },
      weather: {
        status: 503,
        body: { detail: "Could not reach OpenWeather: connection timed out" },
      },
    });

    renderApp();

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("503")).toBeInTheDocument();
    expect(
      within(alert).getByText("Could not reach OpenWeather: connection timed out"),
    ).toBeInTheDocument();
    expect(screen.queryByText("London")).not.toBeInTheDocument();
  });

  it("marks an already-tracked suggestion disabled and refuses to add it", async () => {
    const { postedCities } = mockApi({
      tracked: { cities: ["London"], defaults: ["London"] },
      weather: { items: [weather("London")], page: 1, page_size: 10, total: 1 },
      search: [{ name: "London", state: "England", country: "GB", population: 8961989 }],
    });

    const user = userEvent.setup();
    renderApp();
    await screen.findByRole("heading", { name: "London" });

    await user.type(screen.getByRole("combobox"), "lond");

    const option = await screen.findByRole("option");
    expect(option).toHaveAttribute("aria-disabled", "true");
    expect(within(option).getByText("Tracked")).toBeInTheDocument();

    await user.click(option);

    expect(postedCities).toEqual([]);
  });

  it("adds an untracked city from the suggestions and shows it on the page", async () => {
    const { postedCities } = mockApi({
      tracked: { cities: ["London"], defaults: ["London"] },
      weather: {
        items: [weather("London"), weather("Berlin", { country: "DE" })],
        page: 1,
        page_size: 10,
        total: 2,
      },
      search: [{ name: "Berlin", state: "State of Berlin", country: "DE", population: 3426354 }],
    });

    const user = userEvent.setup();
    renderApp();
    await screen.findByRole("heading", { name: "London" });

    await user.type(screen.getByRole("combobox"), "berl");

    const option = await screen.findByRole("option");
    expect(option).toHaveAttribute("aria-disabled", "false");

    await user.click(option);

    await waitFor(() => expect(postedCities).toEqual(["Berlin"]));
    expect(await screen.findByRole("heading", { name: "Berlin" })).toBeInTheDocument();
  });
});
