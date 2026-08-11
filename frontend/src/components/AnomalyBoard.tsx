import type { AnomalyBriefing, AnomalyRow } from "../api/types";
import { AnomalyBriefingPanel } from "./AnomalyBriefingPanel";

/**
 * Diverging pair for signed anomalies: warm above normal, cool below.
 *
 * Validated for colour-vision deficiency (deutan ΔE 20.9, normal ΔE 25.3
 * against a white surface) rather than picked by eye. Both hues already exist
 * in the temperature scale in lib/format.ts, so the board reads as part of the
 * same system as the cards.
 *
 * Direction is never carried by colour alone — every row also states "above" or
 * "below" in words and puts its bar on the corresponding side of the axis.
 */
const ABOVE = "#b03a12";
const BELOW = "#3b6ea5";

/** Bars are scaled against a fixed 6σ rather than the board maximum, so a quiet
 *  day looks quiet instead of being renormalised into looking dramatic. */
const FULL_SCALE_Z = 6;

function barWidth(z: number): number {
  return Math.min((z / FULL_SCALE_Z) * 100, 100);
}

function observedFor(row: AnomalyRow): { observed: string; normal: string; unit: string } {
  return row.driver === "temperature"
    ? {
        observed: row.temperature_c.toFixed(1),
        normal: row.normal_temperature_c.toFixed(1),
        unit: "°C",
      }
    : {
        observed: String(Math.round(row.humidity_pct)),
        normal: row.normal_humidity_pct.toFixed(0),
        unit: "%",
      };
}

function AnomalyRowItem({ row }: { row: AnomalyRow }) {
  const colour = row.direction === "above" ? ABOVE : BELOW;
  const { observed, normal, unit } = observedFor(row);
  const width = barWidth(row.z_score);

  return (
    <li className="grid grid-cols-[1.5rem_minmax(0,1fr)_auto] items-center gap-x-3 py-2">
      <span
        className="tabular text-[11px] tracking-wide"
        style={{ color: "var(--color-ink-faint)" }}
        aria-hidden="true"
      >
        {row.rank}
      </span>

      <div className="min-w-0">
        <div className="flex items-baseline gap-1.5">
          <span className="truncate text-[13px] font-semibold">{row.city}</span>
          <span className="tabular shrink-0 text-[10px]" style={{ color: "var(--color-ink-faint)" }}>
            {row.country}
          </span>
        </div>
        <p className="tabular mt-0.5 text-[11px]" style={{ color: "var(--color-ink-muted)" }}>
          {row.driver === "temperature" ? "temp" : "humidity"} {observed}
          {unit} · normal {normal}
          {unit}
        </p>
      </div>

      <div className="flex items-center gap-2">
        {/* Diverging axis: the centre is "normal", so a bar's side is its sign. */}
        <div className="relative hidden h-3 w-28 sm:block" aria-hidden="true">
          <div
            className="absolute inset-y-0 left-1/2 w-px"
            style={{ backgroundColor: "var(--color-edge)" }}
          />
          <div
            className="absolute top-1/2 h-1.5 -translate-y-1/2"
            style={{
              backgroundColor: colour,
              width: `${width / 2}%`,
              ...(row.direction === "above"
                ? { left: "50%", borderRadius: "0 3px 3px 0" }
                : { right: "50%", borderRadius: "3px 0 0 3px" }),
            }}
          />
        </div>
        <span
          className="tabular w-24 shrink-0 text-right text-[12px] font-medium whitespace-nowrap"
          style={{ color: colour }}
        >
          {row.z_score.toFixed(1)}σ {row.direction === "above" ? "above" : "below"}
        </span>
      </div>
    </li>
  );
}

interface AnomalyBoardProps {
  rows: AnomalyRow[];
  citiesScored: number;
  sweptAt: string | null;
  /** Null when the interpretation layer is unavailable; the board is unaffected. */
  briefing: AnomalyBriefing | null;
}

export function AnomalyBoard({ rows, citiesScored, sweptAt, briefing }: AnomalyBoardProps) {
  return (
    <section aria-labelledby="anomaly-heading" className="card mt-6 p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 id="anomaly-heading" className="text-[15px] font-semibold">
          Most anomalous cities on Earth
        </h2>
        <p className="tabular text-[11px]" style={{ color: "var(--color-ink-faint)" }}>
          {citiesScored.toLocaleString()} cities scored
          {sweptAt && ` · ${new Date(sweptAt).toLocaleTimeString()}`}
        </p>
      </header>

      <p className="mt-1 text-[12px] leading-relaxed" style={{ color: "var(--color-ink-muted)" }}>
        Ranked by standardized anomaly — how many standard deviations today's local daily mean
        sits from that city's own normal for this month. Each city is measured against itself,
        which is what makes very different climates comparable.
      </p>

      <AnomalyBriefingPanel briefing={briefing} />

      <ol className="mt-2 divide-y" style={{ borderColor: "var(--color-edge)" }}>
        {rows.map((row) => (
          <AnomalyRowItem key={`${row.city}-${row.country}-${row.rank}`} row={row} />
        ))}
      </ol>
    </section>
  );
}
