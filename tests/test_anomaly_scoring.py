import pytest

from app.models.anomaly import AnomalyRow
from app.services.anomaly import MAX_PLAUSIBLE_Z, Observation, rank_by, score_city, z_score
from app.services.city_index import CityRecord
from app.services.normals import Normals


def city(name: str, *, population: int = 1000, geonameid: int = 1, row_index: int = 0):
    return CityRecord(
        row_index=row_index,
        geonameid=geonameid,
        name=name,
        state="",
        country="XX",
        population=population,
        latitude=0.0,
        longitude=0.0,
    )


def normals(mean_t=20.0, sd_t=2.0, mean_h=60.0, sd_h=5.0):
    return Normals(
        mean_temperature_c=mean_t,
        sd_temperature_c=sd_t,
        mean_humidity_pct=mean_h,
        sd_humidity_pct=sd_h,
    )


def observe(temp=20.0, humidity=60.0, month=6):
    return Observation(row_index=0, temperature_c=temp, humidity_pct=humidity, month=month)


class TestZScore:
    def test_measures_departure_in_standard_deviations(self):
        assert z_score(26.0, 20.0, 2.0) == 3.0
        assert z_score(14.0, 20.0, 2.0) == -3.0
        assert z_score(20.0, 20.0, 2.0) == 0.0

    def test_same_departure_ranks_differently_per_city(self):
        """The whole reason the board can compare cities at all.

        Eight degrees above normal is unprecedented where sigma is small and
        unremarkable where it is large.
        """
        reykjavik = z_score(28.0, 20.0, 1.0)
        delhi = z_score(28.0, 20.0, 8.0)
        assert reykjavik == 8.0
        assert delhi == 1.0
        assert reykjavik > delhi

    def test_near_zero_sigma_does_not_divide(self):
        """A tiny sigma would turn measurement noise into a headline anomaly."""
        assert z_score(25.0, 20.0, 0.0) == 0.0
        assert z_score(25.0, 20.0, 0.01) == 0.0

    def test_nan_inputs_score_zero(self):
        assert z_score(float("nan"), 20.0, 2.0) == 0.0
        assert z_score(25.0, float("nan"), 2.0) == 0.0
        assert z_score(25.0, 20.0, float("nan")) == 0.0


class TestScoreCity:
    def test_carries_both_variables_independently(self):
        """Neither variable is collapsed into the other at scoring time."""
        row = score_city(city("A"), observe(temp=28.0, humidity=80.0), normals())
        assert row is not None
        assert row.z_temperature == 4.0
        assert row.z_humidity == 4.0

    def test_a_normal_variable_does_not_dilute_an_extreme_one(self):
        row = score_city(city("A"), observe(temp=28.0, humidity=60.0), normals())
        assert row is not None
        assert row.z_temperature == 4.0
        assert row.z_humidity == 0.0

    def test_signs_are_preserved(self):
        row = score_city(city("A"), observe(temp=12.0), normals())
        assert row is not None
        assert row.z_temperature == -4.0

    def test_city_with_neither_variable_usable_is_dropped(self):
        assert score_city(city("A"), observe(), normals()) is None

    def test_city_with_only_an_implausible_reading_is_dropped(self):
        beyond = 20.0 + (MAX_PLAUSIBLE_Z + 1) * 2.0
        assert score_city(city("A"), observe(temp=beyond), normals()) is None


class TestRankBy:
    def make(self, name, *, z_t=0.0, z_h=0.0, population=100, geonameid=1):
        record = city(name, population=population, geonameid=geonameid)
        row = AnomalyRow(
            rank=0,
            city=name,
            state="",
            country="XX",
            latitude=0.0,
            longitude=0.0,
            temperature_c=20.0,
            humidity_pct=60,
            normal_temperature_c=20.0,
            normal_humidity_pct=60.0,
            sd_temperature_c=2.0,
            sd_humidity_pct=5.0,
            z_temperature=z_t,
            z_humidity=z_h,
            z_score=0.0,
            driver="temperature",
            direction="above",
        )
        return row, record

    def test_orders_by_magnitude_and_assigns_ranks(self):
        rows = [
            self.make("Low", z_t=1.0, geonameid=1),
            self.make("High", z_t=5.0, geonameid=2),
            self.make("Mid", z_t=3.0, geonameid=3),
        ]
        ranked = rank_by(rows, "temperature", 10)
        assert [r.city for r in ranked] == ["High", "Mid", "Low"]
        assert [r.rank for r in ranked] == [1, 2, 3]

    def test_each_board_ranks_on_its_own_variable(self):
        """The point of splitting: one variable cannot sweep the other's board."""
        rows = [
            self.make("Muggy", z_t=0.5, z_h=6.0, geonameid=1),
            self.make("Scorching", z_t=5.0, z_h=0.4, geonameid=2),
        ]
        assert [r.city for r in rank_by(rows, "temperature", 10)] == ["Scorching", "Muggy"]
        assert [r.city for r in rank_by(rows, "humidity", 10)] == ["Muggy", "Scorching"]

    def test_a_city_can_top_both_boards(self):
        rows = [self.make("Extreme", z_t=5.0, z_h=6.0, geonameid=1)]
        assert rank_by(rows, "temperature", 10)[0].z_score == 5.0
        assert rank_by(rows, "humidity", 10)[0].z_score == 6.0

    def test_headline_fields_describe_the_board_the_row_came_from(self):
        rows = [self.make("City", z_t=-4.0, z_h=3.0, geonameid=1)]

        temp = rank_by(rows, "temperature", 10)[0]
        assert (temp.driver, temp.z_score, temp.direction) == ("temperature", 4.0, "below")

        hum = rank_by(rows, "humidity", 10)[0]
        assert (hum.driver, hum.z_score, hum.direction) == ("humidity", 3.0, "above")

        # Both raw z-scores survive on either copy, so a row stays auditable.
        assert temp.z_humidity == 3.0 and hum.z_temperature == -4.0

    def test_a_variable_with_no_departure_is_excluded_from_its_board(self):
        rows = [self.make("TempOnly", z_t=4.0, z_h=0.0, geonameid=1)]
        assert len(rank_by(rows, "temperature", 10)) == 1
        assert rank_by(rows, "humidity", 10) == []

    def test_an_implausible_reading_does_not_suppress_the_other_variable(self):
        """A broken humidity sensor must not cost the city its temperature row."""
        rows = [self.make("Half", z_t=4.0, z_h=MAX_PLAUSIBLE_Z + 5, geonameid=1)]
        assert [r.city for r in rank_by(rows, "temperature", 10)] == ["Half"]
        assert rank_by(rows, "humidity", 10) == []

    def test_ties_break_deterministically(self):
        """Two sweeps over identical data must not reshuffle the leaderboard."""
        rows = [
            self.make("Small", z_t=3.0, population=100, geonameid=9),
            self.make("Big", z_t=3.0, population=900, geonameid=8),
        ]
        assert [r.city for r in rank_by(rows, "temperature", 10)] == ["Big", "Small"]
        assert [r.city for r in rank_by(list(reversed(rows)), "temperature", 10)] == [
            "Big",
            "Small",
        ]

    def test_respects_the_limit(self):
        rows = [self.make(f"C{i}", z_t=float(i), geonameid=i) for i in range(1, 20)]
        assert len(rank_by(rows, "temperature", 5)) == 5


@pytest.mark.parametrize("month", [0, 13, -1])
def test_normals_reject_out_of_range_months(month, tmp_path):
    from array import array

    from app.services.normals import NormalsStore

    store = NormalsStore.from_values(array("f", [0.0] * 48), 1)
    assert store.get(0, month) is None


class TestUrlSplitting:
    """The archive server rejects URIs over 8 KB, and a 414 costs the whole batch."""

    def _script(self):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "scripts" / "build_climate_normals.py"
        spec = importlib.util.spec_from_file_location("build_climate_normals", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _cities(self, n, lat=-123.4567, lon=-123.4567):
        return [
            CityRecord(
                row_index=i,
                geonameid=i,
                name=f"C{i}",
                state="",
                country="XX",
                population=1,
                latitude=lat,
                longitude=lon,
            )
            for i in range(n)
        ]

    def test_oversized_batches_are_split_until_they_fit(self):
        script = self._script()
        chunks = script.split_to_url_limit(self._cities(500), "2021-01-01", "2025-12-31")

        assert len(chunks) > 1
        for chunk in chunks:
            assert len(script._batch_url(chunk, "2021-01-01", "2025-12-31")) <= script.MAX_URL_CHARS

    def test_every_city_survives_the_split(self):
        """Splitting must not drop or duplicate anyone."""
        script = self._script()
        cities = self._cities(500)
        chunks = script.split_to_url_limit(cities, "2021-01-01", "2025-12-31")

        flattened = [city for chunk in chunks for city in chunk]
        assert [c.geonameid for c in flattened] == [c.geonameid for c in cities]

    def test_a_batch_that_already_fits_is_left_alone(self):
        script = self._script()
        cities = self._cities(20)
        assert script.split_to_url_limit(cities, "2021-01-01", "2025-12-31") == [cities]

    def test_empty_batch(self):
        assert self._script().split_to_url_limit([], "2021-01-01", "2025-12-31") == []
