import type { Weather } from "../api/types";
import { conditionIcon, formatTemperature, relativeTime } from "../lib/format";
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
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-base font-semibold">{weather.city}</h3>
          {weather.country && (
            <p className="text-xs" style={{ color: "var(--color-ink-faint)" }}>
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
          <span aria-hidden="true" style={{ opacity: isPinned ? 1 : 0.45 }}>
            📌
          </span>
        </button>
      </header>

      <div className="mt-4 flex items-baseline gap-2">
        <span className="text-4xl font-semibold tracking-tight">
          {formatTemperature(weather.temperature_c)}
        </span>
        <span className="text-sm" style={{ color: "var(--color-ink-muted)" }}>
          feels {formatTemperature(weather.feels_like_c)}
        </span>
      </div>

      <p className="mt-2 flex items-center gap-2 text-sm" style={{ color: "var(--color-ink-muted)" }}>
        <span aria-hidden="true">{conditionIcon(weather.condition)}</span>
        {weather.condition}
      </p>

      <p className="mt-4 text-xs" style={{ color: "var(--color-ink-faint)" }}>
        {weather.humidity_pct}% humidity · {weather.wind_speed_mps.toFixed(1)} m/s wind
      </p>

      <footer
        className="mt-4 flex items-center justify-between border-t pt-3 text-xs"
        style={{ borderColor: "var(--color-edge)", color: "var(--color-ink-faint)" }}
      >
        <SourceBadge source={weather.source} />
        <time dateTime={weather.observed_at} title={new Date(weather.observed_at).toLocaleString()}>
          {relativeTime(weather.observed_at)}
        </time>
      </footer>
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
    <article
      className="card flex flex-col justify-center border-dashed"
      style={{ color: "var(--color-ink-faint)" }}
    >
      <h3 className="truncate text-base font-semibold" style={{ color: "var(--color-ink-muted)" }}>
        {city}
      </h3>
      <p className="mt-2 text-xs leading-relaxed">
        No data returned. OpenWeather didn't recognise this city, so it was dropped from the
        response.
      </p>
    </article>
  );
}
