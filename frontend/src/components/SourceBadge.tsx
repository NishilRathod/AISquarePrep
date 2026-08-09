import type { WeatherSource } from "../api/types";

const LABELS: Record<WeatherSource, { text: string; title: string; color: string }> = {
  cache: {
    text: "CACHED",
    title: "Served from Redis without calling OpenWeather",
    color: "var(--color-cached)",
  },
  upstream: {
    text: "LIVE",
    title: "Cache miss — fetched from OpenWeather just now, then cached",
    color: "var(--color-live)",
  },
};

export function SourceBadge({ source }: { source: WeatherSource }) {
  const { text, title, color } = LABELS[source];

  return (
    <span
      title={title}
      className="inline-flex items-center gap-1.5 text-[11px] font-semibold tracking-wider"
      style={{ color }}
    >
      <span
        aria-hidden="true"
        className="size-1.5 rounded-full"
        style={{ backgroundColor: color, boxShadow: `0 0 8px ${color}` }}
      />
      {text}
    </span>
  );
}
