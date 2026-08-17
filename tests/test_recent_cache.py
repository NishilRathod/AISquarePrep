"""The running year is cached separately from the finished ones.

The baseline cache stores whole calendar years and drops anything short, because
composition maps a day's position to its month using that year's own calendar --
a gap-shortened series would silently attribute readings to the wrong month.
That rule is what makes the baselines trustworthy, so the current year, which is
short by definition, cannot live in that file.

These tests pin the properties of the file that does hold it: explicit date
bounds on every record, a reading that is a *complete* day, and a gap that stays
a gap.
"""

from datetime import date

import pytest

from app.services.recent import (
    Reading,
    RecentStore,
    decode_run,
    encode_run,
    latest_complete,
    parse_records,
    record_line,
)


class TestRunEncoding:
    """Same quantised int16 encoding as the baseline cache, over arbitrary spans."""

    def test_round_trip_preserves_values_to_a_tenth(self):
        back_t, back_h = decode_run(encode_run([12.34, -5.67], [88.88, 12.11]))

        assert back_t == pytest.approx([12.3, -5.7], abs=0.05)
        assert back_h == pytest.approx([88.9, 12.1], abs=0.05)

    def test_a_gap_survives_as_none_not_zero(self):
        back_t, back_h = decode_run(encode_run([None, 5.0], [None, 50.0]))

        assert back_t == [None, pytest.approx(5.0, abs=0.05)]
        assert back_h == [None, pytest.approx(50.0, abs=0.05)]

    def test_an_odd_length_run_is_rejected_rather_than_split_wrong(self):
        """Decoding halves the buffer, so a temperature-only run must not parse."""
        with pytest.raises(ValueError):
            encode_run([1.0, 2.0], [1.0])


class TestLatestCompleteDay:
    """The board scores the last day that actually has both readings.

    Today's daily mean is a partial day, and the trailing days of a response can
    be missing outright. Scoring either against a baseline built from whole-day
    means is the mistake the client docstring warns about: it manufactures
    multi-sigma anomalies that track the clock rather than the weather.
    """

    def test_it_returns_the_final_day_when_that_day_is_complete(self):
        reading = latest_complete(
            [10.0, 11.0, 12.0], [50.0, 55.0, 60.0], date(2026, 8, 14)
        )

        assert reading == Reading(
            local_date=date(2026, 8, 16), temperature_c=12.0, humidity_pct=60.0
        )

    def test_it_walks_back_past_a_trailing_gap(self):
        reading = latest_complete([10.0, 11.0, None], [50.0, 55.0, None], date(2026, 8, 14))

        assert reading is not None
        assert reading.local_date == date(2026, 8, 15)
        assert reading.temperature_c == pytest.approx(11.0)

    def test_a_day_missing_either_variable_is_not_complete(self):
        """Half a reading cannot be scored: both boards need both numbers."""
        reading = latest_complete([10.0, 11.0], [50.0, None], date(2026, 8, 15))

        assert reading is not None
        assert reading.local_date == date(2026, 8, 15)

    def test_an_entirely_empty_run_scores_nothing(self):
        assert latest_complete([None, None], [None, None], date(2026, 8, 15)) is None


class TestRecordFile:
    def _line(self, geonameid, start, temps, humidities):
        return record_line(geonameid, date.fromisoformat(start), temps, humidities)

    def test_a_record_round_trips_through_a_line(self):
        line = self._line(2643743, "2026-08-15", [10.0, 11.0], [50.0, 55.0])

        records = parse_records([line])

        assert records[2643743].start == date(2026, 8, 15)
        assert records[2643743].reading.local_date == date(2026, 8, 16)

    def test_the_last_record_for_a_city_wins(self):
        """A daily top-up appends; it must supersede yesterday's record."""
        old = self._line(1, "2026-08-14", [10.0], [50.0])
        new = self._line(1, "2026-08-14", [10.0, 20.0], [50.0, 60.0])

        records = parse_records([old, new])

        assert records[1].reading.temperature_c == pytest.approx(20.0)
        assert records[1].reading.local_date == date(2026, 8, 15)

    def test_a_truncated_final_line_is_skipped_not_fatal(self):
        """An interrupted run leaves half a line; the rest of the file is good."""
        good = self._line(1, "2026-08-15", [10.0], [50.0])

        records = parse_records([good, '{"id": 2, "s": "2026-08'])

        assert set(records) == {1}


class TestStoreLookup:
    """Records are keyed by geonameid, so nothing here depends on index position.

    The baseline artefact is joined by position and needs a digest guard to catch
    the index drifting underneath it. Keying by geonameid removes that hazard
    rather than guarding against it -- an index rebuild changes which row a city
    sits at, and a lookup by id is simply unaffected.
    """

    def test_unknown_ids_are_dropped_rather_than_misattributed(self):
        store = RecentStore.from_records(
            {1: _record(date(2026, 8, 16), 10.0, 50.0), 999: _record(date(2026, 8, 16), 5.0, 5.0)},
            {1: 0},
        )

        assert store.get(0) is not None
        assert store.cities_covered == 1

    def test_as_of_is_the_newest_day_any_city_holds(self):
        store = RecentStore.from_records(
            {
                1: _record(date(2026, 8, 14), 10.0, 50.0),
                2: _record(date(2026, 8, 16), 10.0, 50.0),
            },
            {1: 0, 2: 1},
        )

        assert store.as_of == date(2026, 8, 16)


def _record(day, temperature, humidity):
    from app.services.recent import RecentRecord

    return RecentRecord(
        start=day,
        reading=Reading(local_date=day, temperature_c=temperature, humidity_pct=humidity),
    )
