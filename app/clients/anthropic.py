"""The interpretation layer over the computed anomaly board.

This is the one place a model is asked anything, and it is deliberately narrow.
Everything the board *ranks* on is arithmetic that already happened: the
z-scores, the ordering, the top-N cut. Asking a model to redo any of that would
be slower, costlier, and occasionally wrong, for a result a sort already gives
exactly.

What it is asked for is the part arithmetic cannot supply:

* **Grouping.** Five Pearl River Delta cities in the top ten are not five
  independent facts, they are one weather system. No sort can collapse them,
  because the relationship is geographic and meteorological rather than
  numerical.
* **Severity.** A 3-sigma warm anomaly in a dry shoulder season is trivia; the
  same departure with high humidity in a dense tropical city is a heat-health
  event. Magnitude and significance are different quantities and only one of
  them is in the data.
* **Doubt.** A reading that survives the plausibility filter can still be a
  broken station rather than weather.

Failure is always tolerable. Every path returns ``None`` rather than raising,
and the board renders without a briefing.
"""

from __future__ import annotations

import json
import logging

from app.config import Settings
from app.models.anomaly import AnomalyBriefing, AnomalyRow

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a meteorologist writing the editorial note for a global weather anomaly \
board.

The board ranks cities by standardized anomaly (z-score): how many standard \
deviations today's local daily mean sits from that city's own 5-year normal for \
this calendar month. Each city's sigma is its own, which is what makes the \
ranking comparable across very different climates.

The ranks and z-scores are established fact. Do not recompute, re-rank, or \
second-guess them, and do not do arithmetic on them. Your job is the part the \
numbers cannot express:

1. Group rows that are one weather system rather than independent events. \
Cities close together with anomalies in the same direction are usually a single \
synoptic feature - a heat dome, a cold outbreak, a monsoon surge, a dry \
intrusion. Name it in plain language and say which listed cities it accounts \
for. Leave genuinely independent rows ungrouped rather than inventing a link.

2. Say which anomalies actually matter, and which are merely large. Consider \
population, absolute conditions rather than just the departure, and whether \
temperature and humidity compound each other. A large departure in a mild \
absolute range is not a health story; a smaller one that pushes a dense humid \
city past human tolerance is.

3. Flag any reading that looks more like an instrument or grid-cell fault than \
weather - a value inconsistent with its neighbours or implausible for the \
location and season.

Write for a general reader. Be specific and brief: no restating the numbers back, \
no hedging, no summary of what you are about to say. If nothing on the board is \
genuinely remarkable, say that plainly rather than inflating it."""


class AnomalyBriefingClient:
    def __init__(self, settings: Settings):
        # Imported here so the package is only needed by deployments that
        # configure a key; app.main degrades when the import fails.
        from anthropic import AsyncAnthropic

        self._settings = settings
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def brief(self, rows: list[AnomalyRow]) -> AnomalyBriefing | None:
        if not rows:
            return None

        import anthropic

        try:
            response = await self._client.messages.parse(
                model=self._settings.anthropic_model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                thinking={"type": "adaptive"},
                output_config={"effort": "low"},
                output_format=AnomalyBriefing,
                messages=[{"role": "user", "content": self._render(rows)}],
            )
        except anthropic.APIError as exc:
            logger.warning("Anomaly briefing unavailable: %s", exc)
            return None
        except Exception:  # noqa: BLE001 - never let enrichment break a sweep
            logger.warning("Anomaly briefing failed unexpectedly", exc_info=True)
            return None

        if response.stop_reason == "refusal":
            logger.warning("Anomaly briefing refused by safety classifiers")
            return None

        return response.parsed_output

    @staticmethod
    def _render(rows: list[AnomalyRow]) -> str:
        """Serialize the board as compact JSON.

        Coordinates are included because the grouping task is geographic --
        without them the model can only rely on recalling where cities are.
        """
        payload = [
            {
                "rank": row.rank,
                "city": row.city,
                "state": row.state,
                "country": row.country,
                "lat": row.latitude,
                "lon": row.longitude,
                "temperature_c": row.temperature_c,
                "normal_temperature_c": row.normal_temperature_c,
                "humidity_pct": row.humidity_pct,
                "normal_humidity_pct": row.normal_humidity_pct,
                "z_temperature": row.z_temperature,
                "z_humidity": row.z_humidity,
                "z_score": row.z_score,
                "driver": row.driver,
                "direction": row.direction,
            }
            for row in rows
        ]
        return (
            "Today's anomaly board, already ranked. Each row is one city's local "
            "daily mean against its own normal for this month.\n\n"
            + json.dumps(payload, ensure_ascii=False)
        )
