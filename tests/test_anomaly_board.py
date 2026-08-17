"""Sweep behaviour, and the degradation guarantees the board depends on.

There is no HTTP stub anywhere in this file, and that is the point: both halves
of a z-score come from local files now, so a sweep that tried to reach the
network would fail these tests by hanging rather than by passing quietly.
"""

from array import array
from datetime import date

import pytest

from app.models.anomaly import AnomalyBriefing, SynopticEvent
from app.services import anomaly_board
from app.services.anomaly_board import BOARD_KEY, AnomalyBoardService
from app.services.city_index import CityRecord
from app.services.normals import NormalsStore, NormalsUnavailableError
from app.services.recent import Reading, RecentStore, RecentUnavailableError

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
    return NormalsStore.from_values(values, n)


def reading(temp=20.0, humidity=60.0, day=date(2026, 6, 15)) -> Reading:
    return Reading(local_date=day, temperature_c=temp, humidity_pct=humidity)


def make_recent(readings: list[Reading | None]) -> RecentStore:
    """A store holding one reading per row index, ``None`` meaning no reading."""
    kept = {row: value for row, value in enumerate(readings) if value is not None}
    return RecentStore(
        readings=kept,
        as_of=max((value.local_date for value in kept.values()), default=None),
    )


class CountingLoader:
    """Stands in for ``load_recent`` and remembers how often it was asked."""

    def __init__(self, store: RecentStore):
        self.store = store
        self.calls = 0

    def __call__(self) -> RecentStore:
        self.calls += 1
        return self.store


class StubBriefer:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    async def brief(self, temperature, humidity):
        self.calls += 1
        self.seen = (temperature, humidity)
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
    """Point the sweep at synthetic cities, normals and readings.

    Returns the loader so a test can assert how many times the sweep went to
    disk -- the local equivalent of counting upstream calls.
    """

    def apply(n_cities, normals=None, readings=None):
        cities = make_cities(n_cities)
        monkeypatch.setattr(anomaly_board, "city_records", lambda: cities)
        store = normals if normals is not None else make_normals(n_cities)
        monkeypatch.setattr(anomaly_board, "load_normals", lambda: store)

        if readings is None:
            readings = [reading() for _ in range(n_cities)]
        loader = CountingLoader(make_recent(readings))
        monkeypatch.setattr(anomaly_board, "load_recent", loader)
        return loader

    return apply


class TestSweep:
    async def test_scores_ranks_and_stores(self, fake_redis, patched):
        patched(3, readings=[reading(temp=28.0), reading(temp=24.0), reading()])
        service = AnomalyBoardService(fake_redis, 50)

        board = await service.sweep()

        assert board.source == "fresh"
        # City2 is exactly normal, so it never reaches the board.
        assert [r.city for r in board.temperature] == ["City0", "City1"]
        assert board.temperature[0].rank == 1
        assert await fake_redis.get(BOARD_KEY) is not None

    async def test_cities_without_a_reading_are_skipped(self, fake_redis, patched):
        patched(3, readings=[reading(temp=28.0), None, reading(temp=27.0)])
        board = await AnomalyBoardService(fake_redis, 50).sweep()
        assert [r.city for r in board.temperature] == ["City0", "City2"]

    async def test_the_observed_day_is_reported_not_the_sweep_time(self, fake_redis, patched):
        """"How anomalous is it" is meaningless without saying when."""
        patched(1, readings=[reading(temp=28.0, day=date(2026, 8, 16))])

        board = await AnomalyBoardService(fake_redis, 50).sweep()

        assert board.observed_date == date(2026, 8, 16)
        assert board.swept_at is not None

    async def test_month_comes_from_the_city_local_date(self, fake_redis, patched):
        """A city scored against the wrong month's baseline is silently wrong."""
        values = array("f")
        for month in range(MONTHS):
            # Only July has a usable baseline; every other month is NaN.
            if month == 6:
                values.extend([20.0, 2.0, 60.0, 5.0])
            else:
                values.extend([float("nan")] * 4)
        store = NormalsStore.from_values(values, 1)

        patched(1, normals=store, readings=[reading(temp=28.0, day=date(2026, 6, 15))])
        assert (await AnomalyBoardService(fake_redis, 50).sweep()).temperature == []

        patched(1, normals=store, readings=[reading(temp=28.0, day=date(2026, 7, 15))])
        assert len((await AnomalyBoardService(fake_redis, 50).sweep()).temperature) == 1

    async def test_missing_artefact_yields_an_empty_board_not_an_error(
        self, fake_redis, patched, monkeypatch
    ):
        patched(1)

        def boom():
            raise NormalsUnavailableError("no artefact")

        monkeypatch.setattr(anomaly_board, "load_normals", boom)
        board = await AnomalyBoardService(fake_redis, 50).sweep()
        assert board.source == "unavailable"
        assert board.temperature == [] and board.humidity == []

    async def test_missing_recent_cache_yields_an_empty_board_not_an_error(
        self, fake_redis, patched, monkeypatch
    ):
        """Before the first top-up there is nothing to score, which is not a fault."""
        patched(1)

        def boom():
            raise RecentUnavailableError("no recent cache")

        monkeypatch.setattr(anomaly_board, "load_recent", boom)
        board = await AnomalyBoardService(fake_redis, 50).sweep()
        assert board.source == "unavailable"
        assert board.observed_date is None


class TestBriefingDegradation:
    async def test_board_survives_a_briefer_that_raises(self, fake_redis, patched):
        """The acceptance property: the LLM is enrichment, never a dependency."""
        patched(2, readings=[reading(temp=28.0), reading(temp=27.0)])
        briefer = StubBriefer(error=RuntimeError("API down"))
        service = AnomalyBoardService(fake_redis, 50, briefer)

        board = await service.sweep()

        assert briefer.calls == 1
        assert board.briefing is None
        assert len(board.temperature) == 2
        assert board.temperature[0].z_score == 4.0

    async def test_no_briefer_configured_still_ranks(self, fake_redis, patched):
        patched(1, readings=[reading(temp=28.0)])
        service = AnomalyBoardService(fake_redis, 50, None)
        board = await service.sweep()
        assert board.briefing is None
        assert len(board.temperature) == 1

    async def test_briefing_is_cached_by_board_content(self, fake_redis, patched):
        patched(1, readings=[reading(temp=28.0)])
        briefer = StubBriefer(result=BRIEFING)

        first = AnomalyBoardService(fake_redis, 50, briefer)
        assert (await first.sweep()).briefing is not None
        assert briefer.calls == 1

        second = AnomalyBoardService(fake_redis, 50, briefer)
        board = await second.sweep()

        assert briefer.calls == 1, "identical board should not be re-briefed"
        assert board.briefing is not None
        assert board.briefing.headline == "One system"


class TestGetBoard:
    async def test_cold_start_is_not_an_error(self, fake_redis):
        board = await AnomalyBoardService(fake_redis, 50).get_board(10)
        assert board.source == "unavailable"
        assert board.temperature == [] and board.humidity == []
        assert board.swept_at is None

    async def test_reads_do_not_sweep(self, fake_redis, patched):
        loader = patched(1, readings=[reading(temp=28.0)])
        service = AnomalyBoardService(fake_redis, 50)
        await service.sweep()
        assert loader.calls == 1

        await service.get_board(10)
        assert loader.calls == 1, "serving the board must not rescore it"

    async def test_limit_trims_the_stored_board(self, fake_redis, patched):
        patched(5, readings=[reading(temp=20.0 + i) for i in range(1, 6)])
        service = AnomalyBoardService(fake_redis, 50)
        await service.sweep()

        assert len((await service.get_board(2)).temperature) == 2
        assert len((await service.get_board(50)).temperature) == 5


class TestBothBoards:
    async def test_one_sweep_populates_both_boards_independently(self, fake_redis, patched):
        """The reason the boards were split.

        City0 is a temperature extreme with normal humidity; City1 is the
        reverse. A single combined ranking would order them by whichever
        departure was larger and let one variable own the visible top of the
        board; ranking each variable separately keeps both visible.
        """
        patched(
            2,
            readings=[
                reading(temp=28.0, humidity=60.0),  # 4.0 sigma temp, flat humidity
                reading(temp=20.0, humidity=85.0),  # flat temp, 5.0 sigma humidity
            ],
        )
        service = AnomalyBoardService(fake_redis, 50)

        board = await service.sweep()

        assert [r.city for r in board.temperature] == ["City0"]
        assert [r.city for r in board.humidity] == ["City1"]
        assert board.temperature[0].z_score == 4.0
        assert board.humidity[0].z_score == 5.0

    async def test_a_city_extreme_in_both_appears_on_both(self, fake_redis, patched):
        patched(1, readings=[reading(temp=28.0, humidity=85.0)])
        board = await AnomalyBoardService(fake_redis, 50).sweep()

        assert board.temperature[0].city == "City0"
        assert board.humidity[0].city == "City0"
        # Same city, different headline per board.
        assert board.temperature[0].z_score == 4.0
        assert board.humidity[0].z_score == 5.0

    async def test_briefing_receives_both_boards(self, fake_redis, patched):
        patched(
            2,
            readings=[reading(temp=28.0, humidity=60.0), reading(temp=20.0, humidity=85.0)],
        )
        briefer = StubBriefer(result=BRIEFING)
        service = AnomalyBoardService(fake_redis, 50, briefer)

        await service.sweep()

        temperature, humidity = briefer.seen
        assert [r.city for r in temperature] == ["City0"]
        assert [r.city for r in humidity] == ["City1"]
