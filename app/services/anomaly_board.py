"""Sweep orchestration and board storage.

The sweep is global, scheduled work: it scores every city the normals artefact
covers, stores the ranked board in Redis, and serves reads from there. Requests
never trigger a sweep, so no user ever waits on thousands of upstream calls or
on an LLM.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime

from redis.asyncio import Redis

from app.clients.open_meteo import CurrentReading, OpenMeteoClient
from app.models.anomaly import AnomalyBoard, AnomalyBriefing, AnomalyRow
from app.services.anomaly import Observation, rank_by, score_city
from app.services.city_index import CityRecord, city_records
from app.services.normals import NormalsStore, NormalsUnavailableError, load_normals
from app.telemetry import tracer

logger = logging.getLogger(__name__)

BOARD_KEY = "anomalies:board"
BRIEFING_KEY_PREFIX = "anomalies:briefing:"

# How many rows the briefing sees. Slightly more than are displayed, so it can
# tell that a cluster continues past the visible cut.
BRIEFING_ROWS = 15


class AnomalyBoardService:
    def __init__(
        self,
        redis: Redis,
        client: OpenMeteoClient,
        board_size: int,
        briefer: BriefingProvider | None = None,
        briefing_ttl_seconds: int = 21600,
    ):
        self._redis = redis
        self._client = client
        self._board_size = board_size
        self._briefer = briefer
        self._briefing_ttl_seconds = briefing_ttl_seconds

    async def get_board(self, limit: int) -> AnomalyBoard:
        """Serve the stored board. Never sweeps, never blocks on upstream."""
        raw = await self._redis.get(BOARD_KEY)
        if raw is None:
            return _empty_board()

        board = AnomalyBoard.model_validate_json(raw)
        # The limit applies per board, so asking for ten gets ten of each rather
        # than ten split between them.
        return board.model_copy(
            update={
                "temperature": board.temperature[:limit],
                "humidity": board.humidity[:limit],
            }
        )

    async def sweep(self) -> AnomalyBoard:
        """Fetch current conditions for every covered city, score, rank, store."""
        with tracer.start_as_current_span("anomaly.sweep") as span:
            try:
                with tracer.start_as_current_span("anomaly.load_normals"):
                    normals = load_normals()
            except NormalsUnavailableError as exc:
                span.set_attribute("sweep.skipped", True)
                logger.warning("Anomaly sweep skipped: %s", exc)
                return _empty_board()

            # Only cities with a baseline. Fetching the rest would be a request per
            # city whose reading is then discarded for having nothing to compare to.
            all_cities = city_records()
            cities = [
                all_cities[row_index]
                for row_index in normals.covered_row_indices
                if row_index < len(all_cities)
            ]
            span.set_attribute("cities.covered", len(cities))
            span.set_attribute("cities.in_index", len(all_cities))
            logger.info(
                "Anomaly sweep starting over %d cities with a baseline (of %d in the index)",
                len(cities),
                len(all_cities),
            )

            readings = await self._client.fetch_current_bulk(
                [(city.latitude, city.longitude) for city in cities]
            )

            with tracer.start_as_current_span("anomaly.score") as score_span:
                scored = self._score_all(cities, readings, normals)
                temperature = rank_by(scored, "temperature", self._board_size)
                humidity = rank_by(scored, "humidity", self._board_size)
                score_span.set_attribute("cities.scored", len(scored))

            span.set_attribute("cities.scored", len(scored))
            span.set_attribute("board.temperature_rows", len(temperature))
            span.set_attribute("board.humidity_rows", len(humidity))
            logger.info(
                "Anomaly sweep scored %d cities, kept %d temperature and %d humidity rows",
                len(scored),
                len(temperature),
                len(humidity),
            )

            briefing = await self._brief(temperature, humidity)

            board = AnomalyBoard(
                temperature=temperature,
                humidity=humidity,
                briefing=briefing,
                swept_at=datetime.now(UTC),
                cities_scored=len(scored),
                source="fresh",
            )
            await self._redis.set(BOARD_KEY, board.model_dump_json())
            return board

    @staticmethod
    def _score_all(
        cities: list[CityRecord],
        readings: list[CurrentReading | None],
        normals: NormalsStore,
    ) -> list[tuple[AnomalyRow, CityRecord]]:
        scored: list[tuple[AnomalyRow, CityRecord]] = []

        for city, reading in zip(cities, readings, strict=False):
            if reading is None:
                continue

            # Local month, from the city's own calendar day. "2026-08-10" -> 8.
            try:
                month = int(reading.local_date[5:7])
            except (ValueError, IndexError):
                continue

            baseline = normals.get(city.row_index, month)
            if baseline is None:
                continue

            row = score_city(
                city,
                Observation(
                    row_index=city.row_index,
                    temperature_c=reading.temperature_c,
                    humidity_pct=reading.humidity_pct,
                    month=month,
                ),
                baseline,
            )
            if row is not None:
                scored.append((row, city))

        return scored

    async def _brief(
        self, temperature: list[AnomalyRow], humidity: list[AnomalyRow]
    ) -> AnomalyBriefing | None:
        if self._briefer is None or not (temperature or humidity):
            return None

        with tracer.start_as_current_span("anomaly.briefing") as span:
            # Both boards, because the correlations worth naming often span them: a
            # heat dome shows up as a temperature anomaly and a humidity one in the
            # same place, and only seeing half of that would hide the connection.
            top_temperature = temperature[:BRIEFING_ROWS]
            top_humidity = humidity[:BRIEFING_ROWS]
            key = BRIEFING_KEY_PREFIX + self._briefing_digest(top_temperature + top_humidity)
            span.set_attribute("briefing.rows", len(top_temperature) + len(top_humidity))

            # Keyed by the rows themselves rather than by time, so a sweep that
            # reproduces the same board -- or a manual refresh during development --
            # costs nothing.
            cached = await self._redis.get(key)
            # The attribute is what explains an otherwise alarming trace: a sweep
            # with no model span under this one is the cache working, not the
            # briefing silently failing.
            span.set_attribute("briefing.cache_hit", cached is not None)
            if cached is not None:
                return AnomalyBriefing.model_validate_json(cached)

            try:
                briefing = await self._briefer.brief(top_temperature, top_humidity)
            except Exception:  # noqa: BLE001 - enrichment must never fail a sweep
                logger.warning("Anomaly briefing failed; serving board without it", exc_info=True)
                return None

            span.set_attribute("briefing.served", briefing is not None)
            if briefing is not None:
                await self._redis.set(
                    key, briefing.model_dump_json(), ex=self._briefing_ttl_seconds
                )
            return briefing

    @staticmethod
    def _briefing_digest(rows: list[AnomalyRow]) -> str:
        """Identity of a board, for caching.

        Only the fields that would change what a briefing says: which cities,
        how anomalous, and in which direction. Rank is excluded because a pure
        reordering of the same cities with the same magnitudes reads the same.
        """
        material = "|".join(
            f"{row.city},{row.country},{row.z_score},{row.driver},{row.direction}"
            for row in rows
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _empty_board() -> AnomalyBoard:
    """What every caller gets before the first sweep, or when normals are missing.

    A 200 with nothing in it, rather than an error: a dashboard should not break
    because a background job has not run yet.
    """
    return AnomalyBoard(
        temperature=[],
        humidity=[],
        briefing=None,
        swept_at=None,
        cities_scored=0,
        source="unavailable",
    )


class BriefingProvider:
    """Structural type for the interpretation layer (see app/clients/anthropic.py)."""

    async def brief(  # pragma: no cover
        self, temperature: list[AnomalyRow], humidity: list[AnomalyRow]
    ) -> AnomalyBriefing | None:
        raise NotImplementedError
