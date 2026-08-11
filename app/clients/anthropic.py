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

from opentelemetry.trace import Span, Status, StatusCode

from app.config import Settings
from app.models.anomaly import AnomalyBriefing, AnomalyRow
from app.telemetry import tracer

logger = logging.getLogger(__name__)

# Named rather than inline so the request and the span attribute reporting it
# cannot drift apart.
MAX_TOKENS = 4096
THINKING = "adaptive"
EFFORT = "low"

SYSTEM_PROMPT = """\
You are a meteorologist writing the editorial note for a global weather anomaly \
board.

There are two boards, one for temperature and one for humidity, each ranked by \
standardized anomaly (z-score): how many standard deviations today's local daily \
mean sits from that city's own multi-year normal for this calendar month. Each \
city's sigma is its own, which is what makes the ranking comparable across very \
different climates. The boards are ranked independently, so a city can appear on \
both -- when it does, treat it as one event.

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

    async def brief(
        self, temperature: list[AnomalyRow], humidity: list[AnomalyRow]
    ) -> AnomalyBriefing | None:
        if not (temperature or humidity):
            return None

        import anthropic

        # The span covers the whole exchange rather than just the await, so a
        # refusal and a transport failure are both visible as an errored model
        # call rather than as an absence. Everything inside it still returns
        # ``None`` on failure -- instrumentation must not become a way for this
        # method to start raising.
        with tracer.start_as_current_span(
            f"chat {self._settings.anthropic_model}",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "anthropic",
                "gen_ai.request.model": self._settings.anthropic_model,
                "gen_ai.request.max_tokens": MAX_TOKENS,
                "anthropic.thinking": THINKING,
                "anthropic.effort": EFFORT,
                "anomaly.briefing.temperature_rows": len(temperature),
                "anomaly.briefing.humidity_rows": len(humidity),
            },
        ) as span:
            try:
                response = await self._client.messages.parse(
                    model=self._settings.anthropic_model,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    thinking={"type": THINKING},
                    output_config={"effort": EFFORT},
                    output_format=AnomalyBriefing,
                    messages=[
                        {"role": "user", "content": self._render(temperature, humidity)}
                    ],
                )
            except anthropic.APIError as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, "anthropic API error"))
                logger.warning("Anomaly briefing unavailable: %s", exc)
                return None
            except Exception as exc:  # noqa: BLE001 - never let enrichment break a sweep
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, "unexpected briefing failure"))
                logger.warning("Anomaly briefing failed unexpectedly", exc_info=True)
                return None

            self._record_response(span, response)

            if response.stop_reason == "refusal":
                # An error on the span, but not an exception: nothing went wrong
                # mechanically, the model declined. Recording it as a raised
                # error would misreport a policy decision as an outage.
                span.set_status(Status(StatusCode.ERROR, "refused by safety classifiers"))
                logger.warning("Anomaly briefing refused by safety classifiers")
                return None

            return response.parsed_output

    @staticmethod
    def _record_response(span: Span, response: object) -> None:
        """Copy what came back onto the span, tolerating a thin response.

        Everything is read through ``getattr``: a refusal carries no usage, and
        a missing token count is not worth turning a served briefing into a
        failed one.
        """
        model = getattr(response, "model", None)
        if model:
            span.set_attribute("gen_ai.response.model", model)

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason:
            span.set_attribute("gen_ai.response.finish_reasons", [stop_reason])

        usage = getattr(response, "usage", None)
        for attribute, field in (
            ("gen_ai.usage.input_tokens", "input_tokens"),
            ("gen_ai.usage.output_tokens", "output_tokens"),
        ):
            value = getattr(usage, field, None)
            if isinstance(value, int):
                span.set_attribute(attribute, value)

    @classmethod
    def _render(cls, temperature: list[AnomalyRow], humidity: list[AnomalyRow]) -> str:
        """Serialize both boards as compact JSON.

        Coordinates are included because the grouping task is geographic --
        without them the model can only rely on recalling where cities are. Both
        boards go in one message so a city appearing on each can be recognised as
        one event rather than two.
        """
        return (
            "Today's anomaly boards, already ranked. Each row is one city's local "
            "daily mean against its own normal for this calendar month. The two "
            "boards are ranked independently, so a city may appear on both -- if "
            "it does, that is one event, not two.\n\n"
            "TEMPERATURE:\n"
            + json.dumps([cls._row(r) for r in temperature], ensure_ascii=False)
            + "\n\nHUMIDITY:\n"
            + json.dumps([cls._row(r) for r in humidity], ensure_ascii=False)
        )

    @staticmethod
    def _row(row: AnomalyRow) -> dict:
        return {
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
            "direction": row.direction,
        }
