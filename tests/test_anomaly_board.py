"""Sweep behaviour, and the degradation guarantees the board depends on."""

from array import array

import pytest

from app.clients.open_meteo import CurrentReading
from app.models.anomaly import AnomalyBriefing, SynopticEvent
from app.services import anomaly_board
from app.services.anomaly_board import BOARD_KEY, AnomalyBoardService
from app.services.city_index import CityRecord
from app.services.normals import NormalsStore, NormalsUnavailableError

MONTHS, STATS = 12, 4


def make_cities(n: int) -> list[CityRecord]:
    return [
        CityRecord(
            row_index=i,
            geonameid=1000 + i,
            name=f"City{i}",
            state="",
            country="XX",
            population=10_000 - i,
            latitude=float(i),
            longitude=float(i),
        )
        for i in range(n)
    ]


def make_normals(n: int, mean_t=20.0, sd_t=2.0, mean_h=60.0, sd_h=5.0) -> NormalsStore:
    values = array("f")
    for _ in range(n):
        for _ in range(MONTHS):
            values.extend([mean_t, sd_t, mean_h, sd_h])
    return NormalsStore(
        values=values, n_cities=n, window_start="", window_end="", cities_covered=n
    )


class StubClient:
    def __init__(self, readings):
        self._readings = readings
        self.calls = 0

    async def fetch_current_bulk(self, coordinates):
        self.calls += 1
        return self._readings


class StubBriefer:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    async def brief(self, rows):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


BRIEFING = AnomalyBriefing(
    headline="One system",
    events=[SynopticEvent(name="Heat dome", cities=["City0"], explanation="...")],
    notes=[],
    suspect_readings=[],
)


@pytest.fixture
def patched(monkeypatch):
    """Point the sweep at synthetic cities and normals instead of the real artefact."""

    def apply(n_cities, normals=None, readings=None):
        cities = make_cities(n_cities)
        monkeypatch.setattr(anomaly_board, "city_records", lambda: cities)
        store = normals if normals is not None else make_normals(n_cities)
        monkeypatch.setattr(anomaly_board, "load_normals", lambda: store)
        return cities, readings

    return apply


def reading(temp=20.0, humidity=60.0, date="2026-06-15"):
    return CurrentReading(temperature_c=temp, humidity_pct=humidity, local_date=date)


class TestSweep:
    async def test_scores_ranks_and_stores(self, fake_redis, patched):
        patched(3)
        readings = [reading(temp=28.0), reading(temp=24.0), reading()]
        service = AnomalyBoardService(fake_redis, StubClient(readings), 50)

        board = await service.sweep()

        assert board.source == "fresh"
        # City2 is exactly normal, so it never reaches the board.
        assert [r.city for r in board.rows] == ["City0", "City1"]
        assert board.rows[0].rank == 1
        assert await fake_redis.get(BOARD_KEY) is not None

    async def test_cities_without_a_reading_are_skipped(self, fake_redis, patched):
        patched(3)
        service = AnomalyBoardService(
            fake_redis, StubClient([reading(temp=28.0), None, reading(temp=27.0)]), 50
        )
        board = await service.sweep()
        assert [r.city for r in board.rows] == ["City0", "City2"]

    async def test_month_comes_from_the_city_local_date(self, fake_redis, patched):
        """A city scored against the wrong month's baseline is silently wrong."""
        values = array("f")
        for month in range(MONTHS):
            # Only July has a usable baseline; every other month is NaN.
            if month == 6:
                values.extend([20.0, 2.0, 60.0, 5.0])
            else:
                values.extend([float("nan")] * 4)
        store = NormalsStore(
            values=values, n_cities=1, window_start="", window_end="", cities_covered=1
        )

        patched(1, normals=store)
        june = AnomalyBoardService(
            fake_redis, StubClient([reading(temp=28.0, date="2026-06-15")]), 50
        )
        assert (await june.sweep()).rows == []

        july = AnomalyBoardService(
            fake_redis, StubClient([reading(temp=28.0, date="2026-07-15")]), 50
        )
        assert len((await july.sweep()).rows) == 1

    async def test_missing_artefact_yields_an_empty_board_not_an_error(
        self, fake_redis, patched, monkeypatch
    ):
        patched(1)

        def boom():
            raise NormalsUnavailableError("no artefact")

        monkeypatch.setattr(anomaly_board, "load_normals", boom)
        board = await AnomalyBoardService(fake_redis, StubClient([]), 50).sweep()
        assert board.source == "unavailable"
        assert board.rows == []


class TestBriefingDegradation:
    async def test_board_survives_a_briefer_that_raises(self, fake_redis, patched):
        """The acceptance property: the LLM is enrichment, never a dependency."""
        patched(2)
        briefer = StubBriefer(error=RuntimeError("API down"))
        service = AnomalyBoardService(
            fake_redis, StubClient([reading(temp=28.0), reading(temp=27.0)]), 50, briefer
        )

        board = await service.sweep()

        assert briefer.calls == 1
        assert board.briefing is None
        assert len(board.rows) == 2
        assert board.rows[0].z_score == 4.0

    async def test_no_briefer_configured_still_ranks(self, fake_redis, patched):
        patched(1)
        service = AnomalyBoardService(fake_redis, StubClient([reading(temp=28.0)]), 50, None)
        board = await service.sweep()
        assert board.briefing is None
        assert len(board.rows) == 1

    async def test_briefing_is_cached_by_board_content(self, fake_redis, patched):
        patched(1)
        briefer = StubBriefer(result=BRIEFING)
        readings = [reading(temp=28.0)]

        first = AnomalyBoardService(fake_redis, StubClient(readings), 50, briefer)
        assert (await first.sweep()).briefing is not None
        assert briefer.calls == 1

        second = AnomalyBoardService(fake_redis, StubClient(readings), 50, briefer)
        board = await second.sweep()

        assert briefer.calls == 1, "identical board should not be re-briefed"
        assert board.briefing is not None
        assert board.briefing.headline == "One system"


class TestGetBoard:
    async def test_cold_start_is_not_an_error(self, fake_redis):
        board = await AnomalyBoardService(fake_redis, StubClient([]), 50).get_board(10)
        assert board.source == "unavailable"
        assert board.rows == []
        assert board.swept_at is None

    async def test_reads_do_not_sweep(self, fake_redis, patched):
        patched(1)
        client = StubClient([reading(temp=28.0)])
        service = AnomalyBoardService(fake_redis, client, 50)
        await service.sweep()
        assert client.calls == 1

        await service.get_board(10)
        assert client.calls == 1, "serving the board must not hit upstream"

    async def test_limit_trims_the_stored_board(self, fake_redis, patched):
        patched(5)
        readings = [reading(temp=20.0 + i) for i in range(1, 6)]
        service = AnomalyBoardService(fake_redis, StubClient(readings), 50)
        await service.sweep()

        assert len((await service.get_board(2)).rows) == 2
        assert len((await service.get_board(50)).rows) == 5
