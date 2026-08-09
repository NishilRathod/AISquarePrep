import type { Weather } from "../api/types";
import {
  conditionIcon,
  formatTemperature,
  relativeTime,
  temperatureColor,
} from "../lib/format";
import { SourceBadge } from "./SourceBadge";

interface WeatherCardProps {
  weather: Weather;
  isPinned: boolean;
  canPin: boolean;
  onTogglePin: () => void;
}

export function WeatherCard({ weather, isPinned, canPin, onTogglePin }: WeatherCardProps) {
  const pinBlocked = !isPinned && !canPin;

  return (
    <article className="card flex flex-col">
      {/* Climate band: colour tracks the actual reading, so the grid reads as a
          temperature map before any number is. */}
      <div
        aria-hidden="true"
        className="h-[3px] w-full"
        style={{ backgroundColor: temperatureColor(weather.temperature_c) }}
      />

      <div className="flex flex-1 flex-col p-4">
        <header className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate text-[15px] leading-tight font-semibold">{weather.city}</h3>
            {weather.country && (
              <p
                className="tabular mt-0.5 text-[11px] tracking-wide"
                style={{ color: "var(--color-ink-faint)" }}
              >
                {weather.country}
              </p>
            )}
          </div>

          <button
            type="button"
            onClick={onTogglePin}
            disabled={pinBlocked}
            aria-pressed={isPinned}
            title={
              pinBlocked
                ? "Pin limit reached — unpin one first (max 5)"
                : isPinned
                  ? `Unpin ${weather.city}`
                  : `Pin ${weather.city} to the top`
            }
            aria-label={isPinned ? `Unpin ${weather.city}` : `Pin ${weather.city}`}
            className="btn btn-ghost shrink-0 !px-2 !py-1 text-sm"
            style={isPinned ? { borderColor: "var(--color-accent)" } : undefined}
          >
            <span aria-hidden="true" style={{ opacity: isPinned ? 1 : 0.4 }}>
              📌
            </span>
          </button>
        </header>

        <div className="mt-3 flex items-baseline gap-2">
          <span className="tabular text-3xl leading-none font-medium tracking-tight">
            {formatTemperature(weather.temperature_c)}
          </span>
          <span className="tabular text-xs" style={{ color: "var(--color-ink-muted)" }}>
            feels {formatTemperature(weather.feels_like_c)}
          </span>
        </div>

        <p
          className="mt-2.5 flex items-center gap-1.5 text-[13px]"
          style={{ color: "var(--color-ink-muted)" }}
        >
          <span aria-hidden="true">{conditionIcon(weather.condition)}</span>
          {weather.condition}
        </p>

        <p className="tabular mt-1.5 text-[11px]" style={{ color: "var(--color-ink-faint)" }}>
          {weather.humidity_pct}% humidity · {weather.wind_speed_mps.toFixed(1)} m/s
        </p>

        <footer
          className="mt-auto flex items-center justify-between gap-2 border-t pt-2.5 text-[11px]"
          style={{ borderColor: "var(--color-edge)", color: "var(--color-ink-faint)" }}
        >
          <SourceBadge source={weather.source} />
          <time
            className="tabular"
            dateTime={weather.observed_at}
            title={new Date(weather.observed_at).toLocaleString()}
          >
            {relativeTime(weather.observed_at)}
          </time>
        </footer>
      </div>
    </article>
  );
}

/**
 * Rendered for a city the server was asked about but left out of `items`.
 * /weather swallows a single unknown city and still returns 200, so silence here
 * would be the page quietly losing a city the user added.
 */
export function MissingCityCard({ city }: { city: string }) {
  return (
    <article className="card flex flex-col border-dashed">
      <div aria-hidden="true" className="h-[3px] w-full" style={{ backgroundColor: "#d8ccbb" }} />
      <div className="flex flex-1 flex-col justify-center p-4">
        <h3
          className="truncate text-[15px] font-semibold"
          style={{ color: "var(--color-ink-muted)" }}
        >
          {city}
        </h3>
        <p className="mt-1.5 text-[12px] leading-relaxed" style={{ color: "var(--color-ink-faint)" }}>
          No data returned. OpenWeather didn't recognise this city, so it was dropped from the
          response.
        </p>
      </div>
    </article>
  );
}
