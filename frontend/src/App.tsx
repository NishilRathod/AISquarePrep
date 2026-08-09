import { useEffect, useMemo, useState } from "react";
import { API_BASE_URL } from "./api/client";
import type { Weather } from "./api/types";
import { Button } from "./components/Button";
import { CitySearchBar } from "./components/CitySearchBar";
import { ErrorState } from "./components/ErrorState";
import { HealthPill } from "./components/HealthPill";
import { Pagination } from "./components/Pagination";
import { SkeletonGrid } from "./components/SkeletonGrid";
import { MissingCityCard, WeatherCard } from "./components/WeatherCard";
import { useTrackedCities, useWeather } from "./hooks/queries";
import { normalizeCity } from "./lib/format";
import {
  MAX_PINS,
  readPins,
  readTrackedMirror,
  reconcilePins,
  writePins,
  writeTrackedMirror,
} from "./lib/storage";

const PAGE_SIZE = 10;

export default function App() {
  const [page, setPage] = useState(1);
  const [pins, setPins] = useState<string[]>(() => readPins());
  const [pinNotice, setPinNotice] = useState<string | null>(null);
  const [pendingCity, setPendingCity] = useState<string | null>(null);

  const trackedQuery = useTrackedCities();

  // Fall back to the local mirror so a failed /cities still renders something
  // recognisable instead of an empty page.
  const trackedCities = trackedQuery.data?.cities ?? readTrackedMirror();

  useEffect(() => {
    if (trackedQuery.data) {
      writeTrackedMirror(trackedQuery.data.cities);
      setPins((current) => {
        const reconciled = reconcilePins(current, trackedQuery.data.cities);
        if (reconciled.length !== current.length) writePins(reconciled);
        return reconciled;
      });
    }
  }, [trackedQuery.data]);

  /**
   * Pinned cities lead, in pin order; everything else keeps the server's
   * oldest-added-first order. This ordering is why pagination is done here
   * rather than with the API's `page` param — the server can't see the pins.
   */
  const orderedCities = useMemo(() => {
    const pinKeys = new Set(pins.map(normalizeCity));
    const pinned = pins
      .map((pin) => trackedCities.find((city) => normalizeCity(city) === normalizeCity(pin)))
      .filter((city): city is string => Boolean(city));
    const rest = trackedCities.filter((city) => !pinKeys.has(normalizeCity(city)));
    return [...pinned, ...rest];
  }, [pins, trackedCities]);

  const pageCount = Math.max(1, Math.ceil(orderedCities.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const pageCities = useMemo(
    () => orderedCities.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE),
    [orderedCities, safePage],
  );

  const weatherQuery = useWeather(pageCities);

  const weatherByCity = useMemo(() => {
    const items = weatherQuery.data?.items ?? [];
    return new Map<string, Weather>(items.map((item) => [normalizeCity(item.city), item]));
  }, [weatherQuery.data]);

  // Land on whichever page the freshly added city ended up on. Resolved in an
  // effect rather than at click time because the tracked list arrives later.
  useEffect(() => {
    if (!pendingCity) return;
    const index = orderedCities.findIndex(
      (city) => normalizeCity(city) === normalizeCity(pendingCity),
    );
    if (index >= 0) {
      setPage(Math.floor(index / PAGE_SIZE) + 1);
      setPendingCity(null);
    }
  }, [pendingCity, orderedCities]);

  const togglePin = (city: string) => {
    setPinNotice(null);
    setPins((current) => {
      const key = normalizeCity(city);
      const exists = current.some((pin) => normalizeCity(pin) === key);

      if (!exists && current.length >= MAX_PINS) {
        setPinNotice(`You can pin ${MAX_PINS} cities at most — unpin one first.`);
        return current;
      }

      const next = exists ? current.filter((pin) => normalizeCity(pin) !== key) : [...current, city];
      writePins(next);
      return next;
    });
  };

  const isPinned = (city: string) =>
    pins.some((pin) => normalizeCity(pin) === normalizeCity(city));

  const isInitialLoad = trackedQuery.isPending || (weatherQuery.isPending && pageCities.length > 0);
  const isRefreshing = trackedQuery.isFetching || weatherQuery.isFetching;
  const error = trackedQuery.error ?? weatherQuery.error;

  const refresh = () => {
    void trackedQuery.refetch();
    void weatherQuery.refetch();
  };

  return (
    <div className="relative min-h-dvh">
      <div className="relative mx-auto max-w-6xl px-5 py-10 sm:px-8">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Weather Cache</h1>
            <p className="mt-1 text-sm" style={{ color: "var(--color-ink-muted)" }}>
              Tracked cities, and whether each reading came from Redis or upstream.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <HealthPill />
            <Button variant="primary" onClick={refresh} disabled={isRefreshing}>
              {isRefreshing ? "Refreshing…" : "Refresh"}
            </Button>
          </div>
        </header>

        {/* Distinguishes a background refetch from a cold load. */}
        <div className="mt-5 h-0.5 overflow-hidden rounded-full">
          {isRefreshing && !isInitialLoad && (
            <div
              className="h-full w-1/3 animate-pulse rounded-full"
              style={{ backgroundColor: "var(--color-accent)" }}
            />
          )}
        </div>

        <div className="mt-4 max-w-md">
          <CitySearchBar trackedCities={trackedCities} onCityAdded={setPendingCity} />
          {pinNotice && (
            <p role="status" className="mt-2 text-xs" style={{ color: "var(--color-cached)" }}>
              {pinNotice}
            </p>
          )}
        </div>

        <main className="mt-8">
          {error ? (
            <ErrorState error={error} onRetry={refresh} />
          ) : isInitialLoad ? (
            <SkeletonGrid count={Math.max(pageCities.length, 6)} />
          ) : pageCities.length === 0 ? (
            <p className="card text-sm" style={{ color: "var(--color-ink-muted)" }}>
              No cities are being tracked yet. Search above to add one.
            </p>
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {pageCities.map((city) => {
                  const weather = weatherByCity.get(normalizeCity(city));
                  return weather ? (
                    <WeatherCard
                      key={city}
                      weather={weather}
                      isPinned={isPinned(city)}
                      canPin={pins.length < MAX_PINS}
                      onTogglePin={() => togglePin(city)}
                    />
                  ) : (
                    <MissingCityCard key={city} city={city} />
                  );
                })}
              </div>

              <div className="mt-8">
                <Pagination page={safePage} pageCount={pageCount} onChange={setPage} />
              </div>
            </>
          )}
        </main>

        <footer
          className="mt-10 border-t pt-5 text-xs leading-relaxed"
          style={{ borderColor: "var(--color-edge)", color: "var(--color-ink-faint)" }}
        >
          <p>
            This API is cache-aside: a city already in Redis is served from cache (
            <span style={{ color: "var(--color-cached)" }}>CACHED</span>), and a miss is fetched
            from OpenWeather and then cached (
            <span style={{ color: "var(--color-live)" }}>LIVE</span>). Loading this page can
            therefore populate the cache — it isn't a passive view of it. Entries expire after 10
            minutes.
          </p>
          <p className="mt-2">
            API: <code>{API_BASE_URL}</code> · Pins are stored in this browser; tracked cities are
            stored on the server. City data ©{" "}
            <a
              href="https://www.geonames.org/"
              target="_blank"
              rel="noreferrer"
              className="underline underline-offset-2"
            >
              GeoNames
            </a>{" "}
            (CC BY 4.0).
          </p>
        </footer>
      </div>
    </div>
  );
}
