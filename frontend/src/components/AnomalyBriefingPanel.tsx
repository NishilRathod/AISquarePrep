import type { AnomalyBriefing } from "../api/types";

/**
 * The interpretation layer's output.
 *
 * Renders nothing at all when the briefing is absent — no placeholder, no
 * "unavailable" notice. The ranked board below it is the product; this is
 * commentary on top of it, and commentary that failed to arrive should be
 * silent rather than apologetic.
 */
export function AnomalyBriefingPanel({ briefing }: { briefing: AnomalyBriefing | null }) {
  if (!briefing) return null;

  const risks = briefing.notes.filter((note) => note.significance === "health_risk");

  return (
    <section
      aria-labelledby="briefing-heading"
      className="mt-3 rounded-lg p-3"
      style={{ backgroundColor: "var(--color-panel-raised)" }}
    >
      <h3 id="briefing-heading" className="text-[13px] leading-snug font-semibold">
        {briefing.headline}
      </h3>

      {briefing.events.length > 0 && (
        <ul className="mt-2 space-y-1.5">
          {briefing.events.map((event) => (
            <li key={event.name} className="text-[12px] leading-relaxed">
              <span className="font-medium">{event.name}</span>
              <span style={{ color: "var(--color-ink-muted)" }}> — {event.explanation}</span>
              {event.cities.length > 0 && (
                <span className="tabular text-[11px]" style={{ color: "var(--color-ink-faint)" }}>
                  {" "}
                  ({event.cities.join(", ")})
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {risks.length > 0 && (
        <ul className="mt-2 space-y-1">
          {risks.map((note) => (
            <li
              key={note.city}
              className="text-[12px] leading-relaxed"
              style={{ color: "var(--color-danger)" }}
            >
              <span aria-hidden="true">▲ </span>
              <span className="font-medium">{note.city}</span> — {note.note}
            </li>
          ))}
        </ul>
      )}

      {briefing.suspect_readings.length > 0 && (
        <p className="mt-2 text-[11px]" style={{ color: "var(--color-ink-faint)" }}>
          Readings that may be instrument faults rather than weather:{" "}
          {briefing.suspect_readings.join(", ")}.
        </p>
      )}
    </section>
  );
}
