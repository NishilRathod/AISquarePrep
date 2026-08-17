import type { AnomalyRow } from "../api/types";

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

function observedFor(row: AnomalyRow): { observed: string; normal: string } {
  return row.driver === "temperature"
    ? {
        observed: row.temperature_c.toFixed(1),
        normal: row.normal_temperature_c.toFixed(1),
      }
    : {
        observed: String(Math.round(row.humidity_pct)),
        normal: row.normal_humidity_pct.toFixed(0),
      };
}

function AnomalyRowItem({ row, unit }: { row: AnomalyRow; unit: string }) {
  const colour = row.direction === "above" ? ABOVE : BELOW;
  const { observed, normal } = observedFor(row);
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
          {observed}
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
  /** "Temperature" or "Humidity" — the variable this board is ranked on. */
  title: string;
  unit: string;
  rows: AnomalyRow[];
  citiesScored: number;
  observedDate: string | null;
}

export function AnomalyBoard({
  title,
  unit,
  rows,
  citiesScored,
  observedDate,
}: AnomalyBoardProps) {
  const headingId = `anomaly-${title.toLowerCase()}`;

  return (
    <section aria-labelledby={headingId} className="card p-4">
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 id={headingId} className="text-[15px] font-semibold">
          Most anomalous — {title.toLowerCase()}
        </h2>
        <p className="tabular text-[11px]" style={{ color: "var(--color-ink-faint)" }}>
          {citiesScored.toLocaleString()} scored
          {/* The day being scored, not the time the scoring ran: a board
              recomputed at noon still describes yesterday's weather, and
              showing the clock time invited reading it as "right now". */}
          {observedDate &&
            ` · ${new Date(`${observedDate}T00:00:00`).toLocaleDateString(undefined, {
              month: "short",
              day: "numeric",
            })}`}
        </p>
      </header>

      {rows.length === 0 ? (
        <p className="mt-3 text-[12px]" style={{ color: "var(--color-ink-faint)" }}>
          Nothing unusual in {title.toLowerCase()} right now.
        </p>
      ) : (
        <ol className="mt-2 divide-y" style={{ borderColor: "var(--color-edge)" }}>
          {rows.map((row) => (
            <AnomalyRowItem key={`${row.city}-${row.country}-${row.rank}`} row={row} unit={unit} />
          ))}
        </ol>
      )}
    </section>
  );
}
