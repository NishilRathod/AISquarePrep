"""Sweep orchestration and board storage.

The sweep is global, scheduled work: it scores every city the normals artefact
covers, stores the ranked board in Redis, and serves reads from there. Requests
never trigger a sweep, so no user ever waits on thousands of upstream calls or
on an LLM.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from redis.asyncio import Redis

from app.clients.open_meteo import CurrentReading, OpenMeteoClient
from app.models.anomaly import AnomalyBoard, AnomalyBriefing, AnomalyRow
from app.services.anomaly import Observation, rank_rows, score_city
from app.services.city_index import CityRecord, city_records
from app.services.normals import NormalsStore, NormalsUnavailableError, load_normals

logger = logging.getLogger(__name__)

BOARD_KEY = "anomalies:board"


class AnomalyBoardService:
    def __init__(
        self,
        redis: Redis,
        client: OpenMeteoClient,
        board_size: int,
        briefer: BriefingProvider | None = None,
    ):
        self._redis = redis
        self._client = client
        self._board_size = board_size
        self._briefer = briefer

    async def get_board(self, limit: int) -> AnomalyBoard:
        """Serve the stored board. Never sweeps, never blocks on upstream."""
        raw = await self._redis.get(BOARD_KEY)
        if raw is None:
            return AnomalyBoard(
                rows=[], briefing=None, swept_at=None, cities_scored=0, source="unavailable"
            )

        board = AnomalyBoard.model_validate_json(raw)
        return board.model_copy(update={"rows": board.rows[:limit]})

    async def sweep(self) -> AnomalyBoard:
        """Fetch current conditions for every covered city, score, rank, store."""
        try:
            normals = load_normals()
        except NormalsUnavailableError as exc:
            logger.warning("Anomaly sweep skipped: %s", exc)
            return AnomalyBoard(
                rows=[], briefing=None, swept_at=None, cities_scored=0, source="unavailable"
            )

        cities = city_records()[: normals.n_cities]
        logger.info("Anomaly sweep starting over %d cities", len(cities))

        readings = await self._client.fetch_current_bulk(
            [(city.latitude, city.longitude) for city in cities]
        )

        scored = self._score_all(cities, readings, normals)
        rows = rank_rows(scored, self._board_size)
        logger.info("Anomaly sweep scored %d cities, kept %d", len(scored), len(rows))

        briefing = await self._brief(rows)

        board = AnomalyBoard(
            rows=rows,
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

    async def _brief(self, rows: list[AnomalyRow]) -> AnomalyBriefing | None:
        if self._briefer is None or not rows:
            return None
        try:
            return await self._briefer.brief(rows)
        except Exception:  # noqa: BLE001 - enrichment must never fail a sweep
            logger.warning("Anomaly briefing failed; serving board without it", exc_info=True)
            return None


class BriefingProvider:
    """Structural type for the interpretation layer (see app/clients/anthropic.py)."""

    async def brief(self, rows: list[AnomalyRow]) -> AnomalyBriefing | None:  # pragma: no cover
        raise NotImplementedError
