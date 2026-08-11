import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import type {
  AnomalyBoard,
  AnomalyRow,
  CitySuggestion,
  PaginatedWeather,
  TrackedCities,
  Weather,
} from "../api/types";

function anomalyRow(city: string, overrides: Partial<AnomalyRow> = {}): AnomalyRow {
  return {
    rank: 1,
    city,
    state: "",
    country: "HK",
    latitude: 22.28,
    longitude: 114.15,
    temperature_c: 27.0,
    humidity_pct: 62,
    normal_temperature_c: 28.4,
    normal_humidity_pct: 87.6,
    sd_temperature_c: 1.1,
    sd_humidity_pct: 4.3,
    z_temperature: -1.27,
    z_humidity: -5.95,
    z_score: 5.95,
    driver: "humidity",
    direction: "below",
    ...overrides,
  };
}

function anomalyBoard(overrides: Partial<AnomalyBoard> = {}): AnomalyBoard {
  return {
    rows: [anomalyRow("Hong Kong")],
    briefing: null,
    swept_at: new Date().toISOString(),
    cities_scored: 2000,
    source: "fresh",
    ...overrides,
  };
}

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
  anomalies?: AnomalyBoard | { status: number; body: unknown };
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

    if (url.includes("/anomalies")) {
      const configured = config.anomalies;
      if (configured && "status" in configured) {
        return jsonResponse(configured.body, configured.status);
      }
      // Default to the cold-start shape: a board exists but has not swept yet.
      return jsonResponse(
        configured ??
          ({
            rows: [],
            briefing: null,
            swept_at: null,
            cities_scored: 0,
            source: "unavailable",
          } satisfies AnomalyBoard),
      );
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

describe("Global anomaly board", () => {
  const tracked = { cities: ["London"], defaults: ["London"] };
  const weatherPage = {
    items: [weather("London")],
    page: 1,
    page_size: 10,
    total: 1,
  };

  it("ranks cities and shows the numbers the ranking is derived from", async () => {
    mockApi({
      tracked,
      weather: weatherPage,
      anomalies: anomalyBoard({
        rows: [
          anomalyRow("Hong Kong"),
          anomalyRow("Shenzhen", {
            rank: 2,
            country: "CN",
            z_score: 4.62,
            humidity_pct: 64,
            normal_humidity_pct: 86.2,
          }),
        ],
      }),
    });

    renderApp();

    const board = await screen.findByRole("region", { name: /most anomalous cities/i });
    expect(within(board).getByText("Hong Kong")).toBeInTheDocument();
    expect(within(board).getByText("Shenzhen")).toBeInTheDocument();

    // The observation and the normal are both present, so a reader can check
    // the ranking rather than take it on trust.
    expect(within(board).getByText(/humidity 62% · normal 88%/)).toBeInTheDocument();
    expect(within(board).getByText(/6\.0σ below/)).toBeInTheDocument();
  });

  it("renders the ranked board when no briefing is available", async () => {
    /* The acceptance property, at the UI level: the LLM layer is commentary. */
    mockApi({
      tracked,
      weather: weatherPage,
      anomalies: anomalyBoard({ briefing: null }),
    });

    renderApp();

    const board = await screen.findByRole("region", { name: /most anomalous cities/i });
    expect(within(board).getByText("Hong Kong")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /briefing/i })).not.toBeInTheDocument();
  });

  it("shows the briefing's grouped events when one is available", async () => {
    mockApi({
      tracked,
      weather: weatherPage,
      anomalies: anomalyBoard({
        briefing: {
          headline: "A dry intrusion over the Pearl River Delta",
          events: [
            {
              name: "Pearl River Delta dry intrusion",
              cities: ["Hong Kong", "Shenzhen"],
              explanation: "One continental air mass, not two separate events.",
            },
          ],
          notes: [
            { city: "Hong Kong", significance: "health_risk", note: "Wildfire risk elevated." },
            { city: "Shenzhen", significance: "routine", note: "Unremarkable in absolute terms." },
          ],
          suspect_readings: ["Foshan"],
        },
      }),
    });

    renderApp();

    expect(
      await screen.findByRole("heading", { name: /dry intrusion over the Pearl River Delta/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/One continental air mass/)).toBeInTheDocument();
    // Health risks are surfaced; routine notes are not worth the space.
    expect(screen.getByText(/Wildfire risk elevated/)).toBeInTheDocument();
    expect(screen.queryByText(/Unremarkable in absolute terms/)).not.toBeInTheDocument();
    expect(screen.getByText(/Foshan/)).toBeInTheDocument();
  });

  it("hides the board before the first sweep instead of showing an empty section", async () => {
    mockApi({ tracked, weather: weatherPage });

    renderApp();
    await screen.findByRole("heading", { name: "London" });

    expect(
      screen.queryByRole("region", { name: /most anomalous cities/i }),
    ).not.toBeInTheDocument();
  });

  it("keeps the dashboard intact when the anomaly board fails", async () => {
    /* A failing global board must never reach the error chain that replaces
       the whole grid with an ErrorState. */
    mockApi({
      tracked,
      weather: weatherPage,
      anomalies: { status: 503, body: { detail: "Board unavailable" } },
    });

    renderApp();

    expect(await screen.findByRole("heading", { name: "London" })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: /most anomalous cities/i })).not.toBeInTheDocument(),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(/Board unavailable/)).not.toBeInTheDocument();
  });
});
