import { useEffect } from "react";
import { AnomalyBoard } from "../components/AnomalyBoard";
import { AnomalyBriefingPanel } from "../components/AnomalyBriefingPanel";
import { SiteHeader } from "../components/SiteHeader";
import { useAnomalies } from "../hooks/queries";

export default function AnomaliesPage() {
  useEffect(() => {
    document.title = "Anomalies — Weather Cache";
  }, []);

  const { data: board, isPending, error } = useAnomalies();

  const hasRows = Boolean(board && (board.temperature.length > 0 || board.humidity.length > 0));

  return (
    <>
      <SiteHeader
        title="Global anomalies"
        subtitle="How far the latest full day sits from each city's own normal for this month."
      />

      <main className="mt-6">
        {isPending ? (
          <p className="card text-sm" style={{ color: "var(--color-ink-muted)" }}>
            Loading the anomaly board…
          </p>
        ) : error ? (
          /* Scoped to this page: an unreachable board says so here rather than
             taking the tracked-city dashboard down with it. */
          <p role="alert" className="card text-sm" style={{ color: "var(--color-ink-muted)" }}>
            The anomaly board is unavailable right now. Tracked cities on{" "}
            <strong>Home</strong> are unaffected.
          </p>
        ) : !hasRows ? (
          <p className="card text-sm" style={{ color: "var(--color-ink-muted)" }}>
            No sweep has completed yet. The server scores every city with a climate baseline on a
            timer; the board appears once the first sweep finishes.
          </p>
        ) : (
          <>
            {board && <AnomalyBriefingPanel briefing={board.briefing} />}

            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <AnomalyBoard
                title="Temperature"
                unit="°C"
                rows={board!.temperature}
                citiesScored={board!.cities_scored}
                observedDate={board!.observed_date}
              />
              <AnomalyBoard
                title="Humidity"
                unit="%"
                rows={board!.humidity}
                citiesScored={board!.cities_scored}
                observedDate={board!.observed_date}
              />
            </div>

            <p
              className="mt-4 text-[12px] leading-relaxed"
              style={{ color: "var(--color-ink-faint)" }}
            >
              Ranked by standardized anomaly — how many standard deviations the most recent
              complete local day's mean sits from that city's own normal for this calendar month.
              The last complete day rather than today, because a part-day mean is not the same
              statistic as the baseline and would read as an anomaly on its own. Each city is measured
              against itself, which is what lets very different climates be compared: eight degrees
              above normal is routine somewhere with a wide seasonal spread and unprecedented
              somewhere without one. The two boards are ranked independently, so a city can appear
              on both.
            </p>
          </>
        )}
      </main>
    </>
  );
}
