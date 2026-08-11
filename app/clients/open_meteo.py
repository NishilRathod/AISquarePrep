"""Bulk current-conditions client for the global anomaly sweep.

OpenWeather stays the source of truth for the tracked-city cards, but it cannot
feed this: one city per call behind a 60/min limiter is a nine-hour sweep of the
city index. Open-Meteo takes many coordinates per request, which is what makes
scoring the whole index feasible at all.

Failure policy here is deliberately the opposite of OpenWeather's. A single
tracked city failing should surface to the user who asked for it; a single batch
of 40 out of thousands failing should not cost the entire sweep. So batch errors
degrade to ``None`` for those cities and the sweep continues with a smaller
field.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx
from opentelemetry import trace

from app.config import Settings
from app.telemetry import tracer

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CurrentReading:
    """Today's *daily mean* temperature and humidity in the city's local day.

    Daily means rather than instantaneous readings, because the baseline they
    are scored against is built from daily means and the two must be the same
    statistic. Relative humidity swings 30-40 points over a single day, peaking
    near dawn and troughing mid-afternoon; the standard deviation of daily means
    has all of that averaged out of it. Dividing an instantaneous afternoon
    reading by that much smaller sigma manufactures 5-sigma "anomalies" for
    every city that happens to be in its afternoon -- which is a clock, not
    weather. Temperature carries the same bias, upward.
    """

    temperature_c: float
    humidity_pct: float
    # The city's local calendar day, "YYYY-MM-DD". The month comes from this
    # rather than from UTC: near the dateline the two disagree, and a reading
    # scored against the wrong month's baseline is silently wrong.
    local_date: str


class OpenMeteoClient:
    def __init__(self, http_client: httpx.AsyncClient, settings: Settings):
        self._http_client = http_client
        self._settings = settings

    async def fetch_current_bulk(
        self, coordinates: list[tuple[float, float]]
    ) -> list[CurrentReading | None]:
        """Current conditions for many coordinates, in the order given.

        The returned list is always the same length as ``coordinates`` -- callers
        join results back to cities by position, so a short list would shift
        every subsequent city onto its neighbour's reading.
        """
        if not coordinates:
            return []

        batch_size = self._settings.anomaly_sweep_batch_size
        with tracer.start_as_current_span("open_meteo.fetch_current_bulk") as span:
            span.set_attribute("coordinates.count", len(coordinates))
            span.set_attribute("batch.size", batch_size)

            results: list[CurrentReading | None] = []
            for start in range(0, len(coordinates), batch_size):
                batch = coordinates[start : start + batch_size]
                results.extend(await self._fetch_one_batch(batch))

            # Counted rather than inferred from the child spans: a batch that
            # degraded to None never issued a request, so the HTTP children
            # alone understate how much of the field is missing.
            span.set_attribute("readings.missing", sum(1 for r in results if r is None))
            return results

    async def _fetch_one_batch(
        self, batch: list[tuple[float, float]]
    ) -> list[CurrentReading | None]:
        empty: list[CurrentReading | None] = [None] * len(batch)
        max_attempts = self._settings.open_meteo_max_retries + 1

        for attempt in range(max_attempts):
            try:
                response = await self._http_client.get(
                    f"{self._settings.open_meteo_base_url}/forecast",
                    params={
                        "latitude": ",".join(str(lat) for lat, _ in batch),
                        "longitude": ",".join(str(lon) for _, lon in batch),
                        "daily": "temperature_2m_mean,relative_humidity_2m_mean",
                        "forecast_days": 1,
                        "timezone": "auto",
                    },
                    timeout=self._settings.open_meteo_timeout_seconds,
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                logger.warning("Open-Meteo batch unreachable: %s", exc)
                return empty

            if response.status_code == 429 or response.status_code >= 500:
                if attempt == max_attempts - 1:
                    logger.warning(
                        "Open-Meteo batch failed with %s after retries", response.status_code
                    )
                    return empty
                delay = self._retry_delay(response, attempt)
                # An event rather than a span: the retried request already shows
                # up twice via httpx instrumentation, so the only part that would
                # otherwise read as unexplained dead time is this sleep.
                trace.get_current_span().add_event(
                    "open_meteo.backoff",
                    {"http.status_code": response.status_code, "attempt": attempt, "delay": delay},
                )
                await asyncio.sleep(delay)
                continue

            if response.status_code != 200:
                logger.warning("Open-Meteo returned %s for a batch", response.status_code)
                return empty

            return self._parse(response, len(batch))

        return empty

    def _parse(self, response: httpx.Response, expected: int) -> list[CurrentReading | None]:
        empty: list[CurrentReading | None] = [None] * expected
        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning("Open-Meteo returned unparseable JSON: %s", exc)
            return empty

        # A single-coordinate request returns an object rather than a list.
        locations = payload if isinstance(payload, list) else [payload]
        if len(locations) != expected:
            logger.warning(
                "Open-Meteo returned %d locations for %d coordinates; dropping batch "
                "rather than misaligning cities",
                len(locations),
                expected,
            )
            return empty

        readings: list[CurrentReading | None] = []
        for location in locations:
            readings.append(self._reading(location))
        return readings

    @staticmethod
    def _reading(location: object) -> CurrentReading | None:
        if not isinstance(location, dict):
            return None
        daily = location.get("daily")
        if not isinstance(daily, dict):
            return None

        temperatures = daily.get("temperature_2m_mean") or []
        humidities = daily.get("relative_humidity_2m_mean") or []
        dates = daily.get("time") or []
        if not temperatures or not humidities or not dates:
            return None

        temperature, humidity, date = temperatures[0], humidities[0], dates[0]
        if temperature is None or humidity is None or not isinstance(date, str):
            return None

        try:
            return CurrentReading(
                temperature_c=float(temperature),
                humidity_pct=float(humidity),
                local_date=date,
            )
        except (TypeError, ValueError):
            return None

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return self._settings.open_meteo_backoff_base_seconds * (2**attempt)
