import pytest

from app.models.anomaly import AnomalyRow
from app.services.anomaly import MAX_PLAUSIBLE_Z, Observation, rank_rows, score_city, z_score
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
    def test_ranks_on_the_stronger_departure_not_the_average(self):
        """Averaging would let a normal humidity halve a real temperature extreme."""
        row = score_city(city("A"), observe(temp=28.0, humidity=60.0), normals())
        assert row is not None
        assert row.z_temperature == 4.0
        assert row.z_humidity == 0.0
        assert row.z_score == 4.0  # not 2.0
        assert row.driver == "temperature"

    def test_humidity_can_drive_the_ranking(self):
        row = score_city(city("A"), observe(temp=20.5, humidity=80.0), normals())
        assert row is not None
        assert row.driver == "humidity"
        assert row.z_score == 4.0

    def test_direction_reflects_sign_but_score_is_magnitude(self):
        cold = score_city(city("A"), observe(temp=12.0), normals())
        assert cold is not None
        assert cold.direction == "below"
        assert cold.z_score == 4.0
        assert cold.z_temperature == -4.0

    def test_implausible_departures_are_dropped_as_likely_faults(self):
        beyond = 20.0 + (MAX_PLAUSIBLE_Z + 1) * 2.0
        assert score_city(city("A"), observe(temp=beyond), normals()) is None

    def test_unremarkable_city_is_dropped(self):
        assert score_city(city("A"), observe(), normals()) is None


class TestRankRows:
    def make(self, name, z, *, population, geonameid):
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
            z_temperature=z,
            z_humidity=0.0,
            z_score=z,
            driver="temperature",
            direction="above",
        )
        return row, record

    def test_orders_by_magnitude_and_assigns_ranks(self):
        rows = [
            self.make("Low", 1.0, population=100, geonameid=1),
            self.make("High", 5.0, population=100, geonameid=2),
            self.make("Mid", 3.0, population=100, geonameid=3),
        ]
        ranked = rank_rows(rows, 10)
        assert [r.city for r in ranked] == ["High", "Mid", "Low"]
        assert [r.rank for r in ranked] == [1, 2, 3]

    def test_ties_break_deterministically(self):
        """Two sweeps over identical data must not reshuffle the leaderboard."""
        rows = [
            self.make("Small", 3.0, population=100, geonameid=9),
            self.make("Big", 3.0, population=900, geonameid=8),
        ]
        assert [r.city for r in rank_rows(rows, 10)] == ["Big", "Small"]
        assert [r.city for r in rank_rows(list(reversed(rows)), 10)] == ["Big", "Small"]

    def test_respects_the_limit(self):
        rows = [self.make(f"C{i}", float(i), population=1, geonameid=i) for i in range(1, 20)]
        assert len(rank_rows(rows, 5)) == 5


@pytest.mark.parametrize("month", [0, 13, -1])
def test_normals_reject_out_of_range_months(month, tmp_path):
    from array import array

    from app.services.normals import NormalsStore

    store = NormalsStore(
        values=array("f", [0.0] * 48),
        n_cities=1,
        window_start="",
        window_end="",
        cities_covered=1,
    )
    assert store.get(0, month) is None
