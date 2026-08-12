"""The cache holds raw daily values, so every downstream choice stays free.

The artefact the board reads holds finished statistics, and finished statistics
are a dead end: a three-year normal cannot be derived from a five-year mean and
sigma. These tests pin the properties that make the window and the choice of
statistic recomputations rather than refetches.
"""

import math
from datetime import date, timedelta

import pytest

from scripts.normals_cache import (
    MISSING_SENTINEL,
    append_year,
    cached_years,
    compose,
    days_in_year,
    decode_year,
    encode_year,
    load_cache,
    reduce_mean_sd,
    reduce_median_mad,
    split_response_by_year,
)


def _response(start_year, n_years, temp=lambda day: 10.0, humidity=lambda day: 50.0):
    """A synthetic archive response covering whole calendar years."""
    times, temps, humidities = [], [], []
    day = date(start_year, 1, 1)
    end = date(start_year + n_years - 1, 12, 31)
    while day <= end:
        times.append(day.isoformat())
        temps.append(temp(day))
        humidities.append(humidity(day))
        day += timedelta(days=1)
    return {
        "time": times,
        "temperature_2m_mean": temps,
        "relative_humidity_2m_mean": humidities,
    }


class TestQuantisedEncoding:
    def test_round_trip_preserves_values_to_a_tenth(self):
        back_t, back_h = decode_year(
            encode_year([12.34, -5.67, 0.0], [88.88, 12.11, 100.0])
        )

        assert back_t == pytest.approx([12.3, -5.7, 0.0], abs=0.05)
        assert back_h == pytest.approx([88.9, 12.1, 100.0], abs=0.05)

    def test_missing_days_survive_as_none_not_zero(self):
        """A gap must never be able to masquerade as a real reading."""
        back_t, back_h = decode_year(encode_year([None, 5.0], [None, 50.0]))

        assert back_t == [None, pytest.approx(5.0, abs=0.05)]
        assert back_h == [None, pytest.approx(50.0, abs=0.05)]

    def test_zero_is_a_real_reading_not_a_gap(self):
        back_t, _ = decode_year(encode_year([0.0], [0.0]))
        assert back_t == [0.0]

    def test_encoding_is_little_endian_and_two_bytes_per_value(self):
        """Persisted format: byte order is explicit, never the platform's."""
        import base64

        assert base64.b64decode(encode_year([1.0], [2.0])) == b"\x0a\x00\x14\x00"

    def test_sentinel_is_unreachable_by_any_real_reading(self):
        assert MISSING_SENTINEL == -32768
        assert MISSING_SENTINEL / 10 < -3000

    def test_mismatched_series_lengths_are_rejected(self):
        with pytest.raises(ValueError):
            encode_year([1.0, 2.0], [50.0])


class TestCalendar:
    def test_ordinary_year(self):
        assert days_in_year(2023) == 365

    def test_leap_year(self):
        assert days_in_year(2024) == 366


class TestSplittingByYear:
    def test_a_multi_year_response_becomes_one_blob_per_year(self):
        assert sorted(split_response_by_year(_response(2023, 3))) == [2023, 2024, 2025]

    def test_each_year_holds_its_own_day_count(self):
        blobs = split_response_by_year(_response(2023, 3))

        assert len(decode_year(blobs[2023])[0]) == 365
        assert len(decode_year(blobs[2024])[0]) == 366
        assert len(decode_year(blobs[2025])[0]) == 365

    def test_days_land_in_the_year_they_belong_to(self):
        counter = {"n": -1}

        def rising(_day):
            counter["n"] += 1
            return counter["n"] / 10

        blobs = split_response_by_year(_response(2023, 3, temp=rising))

        assert decode_year(blobs[2023])[0][0] == pytest.approx(0.0, abs=0.05)
        assert decode_year(blobs[2024])[0][0] == pytest.approx(36.5, abs=0.05)

    def test_an_incomplete_year_is_dropped_rather_than_composed(self):
        """A short year would shift every later day into the wrong month."""
        daily = _response(2023, 1)
        for key in ("time", "temperature_2m_mean", "relative_humidity_2m_mean"):
            daily[key] = daily[key][:200]

        assert split_response_by_year(daily) == {}

    def test_an_empty_response_yields_nothing(self):
        assert split_response_by_year({}) == {}


class TestReducers:
    def test_mean_and_sample_sd(self):
        mean, sd = reduce_mean_sd([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])

        assert mean == pytest.approx(5.0)
        assert sd == pytest.approx(2.138, abs=0.01)

    def test_near_constant_month_does_not_produce_negative_variance(self):
        mean, sd = reduce_mean_sd([20.0] * 30)

        assert mean == pytest.approx(20.0)
        assert sd == pytest.approx(0.0, abs=1e-9)

    def test_median_and_mad_ignore_a_single_extreme(self):
        """The point of the robust path: one heat dome must not widen sigma."""
        with_event = [20.0] * 30 + [21.0] * 30 + [45.0]

        _, sd_plain = reduce_mean_sd(with_event)
        _, sd_robust = reduce_median_mad(with_event)

        assert sd_robust < sd_plain

    def test_mad_sigma_matches_sd_on_normal_looking_data(self):
        _, sd_robust = reduce_median_mad([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
        assert sd_robust == pytest.approx(2.9652, abs=0.001)


class TestComposition:
    def test_shape_is_twelve_months_of_four_stats(self):
        assert len(compose(split_response_by_year(_response(2023, 3)), [2023, 2024, 2025])) == 48

    def test_a_constant_series_gives_that_mean_and_zero_sigma(self):
        values = compose(split_response_by_year(_response(2023, 3)), [2023, 2024, 2025])

        for month in range(12):
            assert values[month * 4] == pytest.approx(10.0, abs=0.05)
            assert values[month * 4 + 1] == pytest.approx(0.0, abs=1e-6)
            assert values[month * 4 + 2] == pytest.approx(50.0, abs=0.05)

    def test_a_subset_of_years_equals_a_cache_built_from_only_those_years(self):
        """Composability is the reason the cache exists."""
        by_month = lambda day: float(day.month)  # noqa: E731
        five = split_response_by_year(_response(2021, 5, temp=by_month))
        three = split_response_by_year(_response(2023, 3, temp=by_month))

        assert compose(five, [2023, 2024, 2025]) == compose(three, [2023, 2024, 2025])

    def test_months_are_bucketed_by_the_years_own_calendar(self):
        """2024 has a leap day; 2023's boundaries would shift March onward."""
        blobs = split_response_by_year(_response(2024, 1, temp=lambda day: float(day.month)))
        values = compose(blobs, [2024])

        for month in range(12):
            assert values[month * 4] == pytest.approx(month + 1, abs=0.05)

    def test_a_month_below_min_samples_is_missing_not_zero(self):
        blobs = split_response_by_year(
            _response(2023, 1, temp=lambda day: None if day.month == 6 else 10.0)
        )
        values = compose(blobs, [2023])

        assert math.isnan(values[5 * 4])
        assert math.isnan(values[5 * 4 + 1])
        assert values[5 * 4 + 2] == pytest.approx(50.0, abs=0.05)

    def test_years_absent_from_the_cache_are_skipped(self):
        values = compose(split_response_by_year(_response(2023, 1)), [2023, 2024, 2025])
        assert values[0] == pytest.approx(10.0, abs=0.05)

    def test_an_empty_cache_is_all_missing(self):
        values = compose({}, [2023])

        assert len(values) == 48
        assert all(math.isnan(value) for value in values)

    def test_the_robust_reducer_is_selectable(self):
        blobs = split_response_by_year(_response(2023, 1))
        values = compose(blobs, [2023], statistic="median-mad")

        assert values[0] == pytest.approx(10.0, abs=0.05)
        assert values[1] == pytest.approx(0.0, abs=1e-6)


class TestCacheIO:
    def test_round_trip_through_a_file(self, tmp_path):
        path = tmp_path / "cache.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            append_year(handle, 123, 2024, "AAAA")
            append_year(handle, 123, 2025, "BBBB")
            append_year(handle, 456, 2024, "CCCC")

        assert load_cache(path) == {
            123: {2024: "AAAA", 2025: "BBBB"},
            456: {2024: "CCCC"},
        }

    def test_a_missing_file_is_an_empty_cache(self, tmp_path):
        assert load_cache(tmp_path / "absent.jsonl") == {}

    def test_a_truncated_final_line_is_skipped_not_fatal(self, tmp_path):
        """An interrupted run leaves a partial line; the rest must still load."""
        path = tmp_path / "cache.jsonl"
        path.write_text(
            '{"id": 1, "y": 2024, "d": "AAAA"}\n{"id": 2, "y": 20',
            encoding="utf-8",
        )

        assert load_cache(path) == {1: {2024: "AAAA"}}

    def test_a_later_line_supersedes_an_earlier_one_for_the_same_city_year(self, tmp_path):
        path = tmp_path / "cache.jsonl"
        path.write_text(
            '{"id": 1, "y": 2024, "d": "OLD="}\n{"id": 1, "y": 2024, "d": "NEW="}\n',
            encoding="utf-8",
        )

        assert load_cache(path)[1][2024] == "NEW="

    def test_cached_years_for_an_unknown_city_is_empty(self):
        assert cached_years({}, 999) == set()

    def test_cached_years_reports_what_is_held(self):
        assert cached_years({7: {2023: "x", 2024: "y"}}, 7) == {2023, 2024}
