const CONDITION_ICONS: Record<string, string> = {
  clear: "☀️",
  clouds: "☁️",
  rain: "🌧️",
  drizzle: "🌦️",
  thunderstorm: "⛈️",
  snow: "❄️",
  mist: "🌫️",
  haze: "🌫️",
  fog: "🌫️",
  smoke: "🌫️",
  dust: "🌪️",
  sand: "🌪️",
  ash: "🌋",
  squall: "💨",
  tornado: "🌪️",
};

export function conditionIcon(condition: string): string {
  return CONDITION_ICONS[condition.trim().toLowerCase()] ?? "🌡️";
}

const UNITS: [limit: number, seconds: number, name: Intl.RelativeTimeFormatUnit][] = [
  [60, 1, "second"],
  [3600, 60, "minute"],
  [86400, 3600, "hour"],
  [604800, 86400, "day"],
];

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

/** "3 minutes ago" from an ISO-8601 timestamp; falsy input renders as an em dash. */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return "—";

  const elapsed = (parsed - now) / 1000;
  const magnitude = Math.abs(elapsed);

  for (const [limit, seconds, name] of UNITS) {
    if (magnitude < limit) {
      return relative.format(Math.round(elapsed / seconds), name);
    }
  }
  return relative.format(Math.round(elapsed / 604800), "week");
}

export function formatTemperature(celsius: number): string {
  return `${celsius.toFixed(1)}°`;
}

/**
 * Colour for a card's climate band, cold blue through hot rust.
 *
 * Redundant with the number by design: it lets the grid be read as a
 * temperature map before any figure is, which is the whole point of showing
 * nine cities at once. Stops rather than interpolation so the bands stay
 * distinguishable from each other instead of blurring into one gradient.
 */
export function temperatureColor(celsius: number): string {
  if (!Number.isFinite(celsius)) return "#a48f74";
  if (celsius < 0) return "#3b6ea5";
  if (celsius < 10) return "#5b9bc4";
  if (celsius < 18) return "#56a78c";
  if (celsius < 24) return "#c8973a";
  if (celsius < 30) return "#d9722c";
  return "#b03a12";
}

/**
 * Fold case and strip accents, mirroring `normalize()` in app/services/city_index.py
 * so the client and server agree on when two spellings are the same city.
 */
export function normalizeCity(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .normalize("NFKD")
    .replace(/\p{Diacritic}/gu, "");
}

export function describeSuggestion(name: string, state: string, country: string): string {
  return [name, state, country].filter(Boolean).join(" · ");
}
