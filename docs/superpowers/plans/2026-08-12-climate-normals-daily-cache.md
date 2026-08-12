# Climate Normals Daily Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cache Open-Meteo's raw daily values so that window length and choice of statistic become local recomputations rather than fetch-time commitments, and no city is ever fetched twice.

**Architecture:** A new `scripts/normals_cache.py` owns encoding, storage, and composition of daily series, keyed by `(geonameid, calendar year)`. `scripts/build_climate_normals.py` keeps fetch orchestration and now writes raw years to the cache instead of finalised statistics, composing them into the existing artefact at write time. The runtime artefact format, `app/services/normals.py`, the scoring engine, and the frontend are unchanged.

**Tech Stack:** Python 3.12, stdlib only (`struct`, `base64`, `json`, `gzip`, `array`), pytest, ruff.

## Global Constraints

- Quantisation: `round(value * 10)` stored as little-endian `int16`. Resolution 0.1 units.
- Missing-day sentinel: `-32768`, excluded from every statistic, never treated as a real reading.
- Cache file: `app/data/.climate_normals.v2.daily.jsonl`, no window in the filename.
- Cache line: `{"id": <geonameid>, "y": <year>, "d": "<base64>"}`, one line per city-year.
- Byte order is explicit (`struct` with `<`), never native — this is a persisted format.
- Default statistic is `mean-sd`. `median-mad` uses sigma = `1.4826 * MAD`.
- `MIN_SAMPLES = 20` per city-month still yields `NaN`, as today.
- Scope for the first run: `--min-population 100000 --years 3` (6,226 cities, 2023-01-01..2025-12-31).
- Every commit must leave `pytest` and `ruff check .` green.

---

### Task 1: Cache primitives — quantised encode/decode

**Files:**
- Create: `scripts/normals_cache.py`
- Test: `tests/test_normals_cache.py`

**Interfaces:**
- Consumes: nothing
- Produces: `MISSING_SENTINEL: int`, `SCALE: int`, `days_in_year(year: int) -> int`, `encode_year(temps: list[float | None], humidities: list[float | None]) -> str`, `decode_year(blob: str) -> tuple[list[float | None], list[float | None]]`

- [ ] **Step 1: Write the failing test**

```python
"""The cache stores raw daily values, so every downstream choice stays free."""

import pytest

from scripts.normals_cache import (
    MISSING_SENTINEL,
    days_in_year,
    decode_year,
    encode_year,
)


class TestQuantisedEncoding:
    def test_round_trip_preserves_values_to_a_tenth(self):
        temps = [12.34, -5.67, 0.0]
        humidities = [88.88, 12.11, 100.0]

        back_t, back_h = decode_year(encode_year(temps, humidities))

        assert back_t == pytest.approx([12.3, -5.7, 0.0], abs=0.05)
        assert back_h == pytest.approx([88.9, 12.1, 100.0], abs=0.05)

    def test_missing_days_survive_as_none_not_zero(self):
        """A missing reading must never be able to masquerade as a real one."""
        back_t, back_h = decode_year(encode_year([None, 5.0], [None, 50.0]))

        assert back_t == [None, pytest.approx(5.0, abs=0.05)]
        assert back_h == [None, pytest.approx(50.0, abs=0.05)]

    def test_zero_is_a_real_reading_not_a_gap(self):
        back_t, _ = decode_year(encode_year([0.0], [0.0]))
        assert back_t == [0.0]

    def test_encoding_is_little_endian_and_two_bytes_per_value(self):
        """Persisted format: byte order is explicit, never the platform's."""
        import base64

        raw = base64.b64decode(encode_year([1.0], [2.0]))
        assert raw == b"\x0a\x00\x14\x00"

    def test_sentinel_is_unreachable_by_any_real_reading(self):
        assert MISSING_SENTINEL == -32768
        assert MISSING_SENTINEL / 10 < -3000  # far colder than any surface reading

    def test_mismatched_series_lengths_are_rejected(self):
        with pytest.raises(ValueError):
            encode_year([1.0, 2.0], [50.0])


class TestCalendar:
    def test_ordinary_year(self):
        assert days_in_year(2023) == 365

    def test_leap_year(self):
        assert days_in_year(2024) == 366
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normals_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.normals_cache'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Storage for the raw daily values behind the climate normals.

The artefact the board reads holds finished statistics, and finished
statistics are a dead end: a three-year normal cannot be derived from a
five-year mean and sigma, so every change to the window used to mean
refetching every city. Open-Meteo's daily quota makes that expensive enough
to design around.

So the cache keeps what the API actually returned -- the daily series --
and reduces it on demand. Window length and choice of statistic become
local recomputations over data already held.

Values are quantised to a tenth and stored as little-endian int16: two
orders of magnitude below the ~2.4 C sigma the board ranks on, so nothing
observable is lost, at a quarter the size of float64. Byte order is
explicit because this is a persisted format and the platform's native order
is not a promise.
"""

from __future__ import annotations

import base64
import calendar
import struct

# A tenth of a degree, and a tenth of a humidity point.
SCALE = 10

# Unreachable by any real reading -- int16's floor is -3276.8 after scaling --
# so a gap can never be confused with an observation. The same reasoning as the
# artefact's NaN: a missing baseline must not be able to masquerade as a real
# one.
MISSING_SENTINEL = -32768


def days_in_year(year: int) -> int:
    return 366 if calendar.isleap(year) else 365


def _quantise(value: float | None) -> int:
    if value is None:
        return MISSING_SENTINEL
    scaled = round(value * SCALE)
    # Clamp rather than let struct raise. A wild value is bad data, not a
    # reason to lose the rest of the city's year.
    return max(min(scaled, 32767), MISSING_SENTINEL + 1)


def _dequantise(raw: int) -> float | None:
    if raw == MISSING_SENTINEL:
        return None
    return raw / SCALE


def encode_year(temps: list[float | None], humidities: list[float | None]) -> str:
    """One calendar year of daily readings as base64: all temps, then all humidities."""
    if len(temps) != len(humidities):
        raise ValueError(
            f"temperature and humidity series differ in length: "
            f"{len(temps)} vs {len(humidities)}"
        )
    values = [_quantise(v) for v in temps] + [_quantise(v) for v in humidities]
    packed = struct.pack(f"<{len(values)}h", *values)
    return base64.b64encode(packed).decode("ascii")


def decode_year(blob: str) -> tuple[list[float | None], list[float | None]]:
    raw = base64.b64decode(blob)
    count = len(raw) // 2
    values = struct.unpack(f"<{count}h", raw)
    half = count // 2
    return (
        [_dequantise(v) for v in values[:half]],
        [_dequantise(v) for v in values[half:]],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normals_cache.py -v && .venv/Scripts/python.exe -m ruff check .`
Expected: PASS, lint clean

- [ ] **Step 5: Commit**

```bash
git add scripts/normals_cache.py tests/test_normals_cache.py
git commit -m "Store daily readings quantised to a tenth, with an unreachable gap sentinel"
```

---

### Task 2: Split an API response into calendar years

**Files:**
- Modify: `scripts/normals_cache.py`
- Test: `tests/test_normals_cache.py`

**Interfaces:**
- Consumes: `encode_year`, `days_in_year` from Task 1
- Produces: `split_response_by_year(daily: dict) -> dict[int, str]` — year to base64 blob, containing only years whose day count is complete

- [ ] **Step 1: Write the failing test**

```python
from scripts.normals_cache import decode_year, split_response_by_year


def _daily(start_year, n_years):
    """A synthetic archive response covering whole calendar years."""
    from datetime import date, timedelta

    times, temps, hums = [], [], []
    day = date(start_year, 1, 1)
    end = date(start_year + n_years - 1, 12, 31)
    value = 0.0
    while day <= end:
        times.append(day.isoformat())
        temps.append(value)
        hums.append(50.0)
        value += 0.1
        day += timedelta(days=1)
    return {
        "time": times,
        "temperature_2m_mean": temps,
        "relative_humidity_2m_mean": hums,
    }


class TestSplittingByYear:
    def test_a_multi_year_response_becomes_one_blob_per_year(self):
        blobs = split_response_by_year(_daily(2023, 3))
        assert sorted(blobs) == [2023, 2024, 2025]

    def test_each_year_holds_its_own_day_count(self):
        blobs = split_response_by_year(_daily(2023, 3))

        assert len(decode_year(blobs[2023])[0]) == 365
        assert len(decode_year(blobs[2024])[0]) == 366  # leap
        assert len(decode_year(blobs[2025])[0]) == 365

    def test_days_land_in_the_year_they_belong_to(self):
        blobs = split_response_by_year(_daily(2023, 3))
        temps_2023, _ = decode_year(blobs[2023])
        temps_2024, _ = decode_year(blobs[2024])

        # The series increments 0.1 per day from 0.0 on 2023-01-01.
        assert temps_2023[0] == pytest.approx(0.0, abs=0.05)
        assert temps_2024[0] == pytest.approx(36.5, abs=0.05)

    def test_an_incomplete_year_is_dropped_rather_than_composed(self):
        """A short year would shift every later day into the wrong month."""
        daily = _daily(2023, 1)
        for key in ("time", "temperature_2m_mean", "relative_humidity_2m_mean"):
            daily[key] = daily[key][:200]

        assert split_response_by_year(daily) == {}

    def test_an_empty_response_yields_nothing(self):
        assert split_response_by_year({}) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normals_cache.py -k Splitting -v`
Expected: FAIL — `ImportError: cannot import name 'split_response_by_year'`

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/normals_cache.py`:

```python
def split_response_by_year(daily: dict) -> dict[int, str]:
    """Group one city's response into whole calendar years.

    Storage is per year even though a request covers the whole window: it is
    what lets a widened window fetch only the years it lacks, and what lets an
    interrupted city keep the years it already received.

    A year whose day count is short is dropped rather than stored. Composition
    maps position to month using that year's own calendar, so a gap-shortened
    series would silently attribute readings to the wrong month -- the same
    class of error as a misaligned batch, and just as undetectable downstream.
    """
    times = daily.get("time") or []
    temps = daily.get("temperature_2m_mean") or []
    humidities = daily.get("relative_humidity_2m_mean") or []

    by_year: dict[int, tuple[list[float | None], list[float | None]]] = {}
    for index, stamp in enumerate(times):
        try:
            year = int(stamp[:4])
        except (ValueError, IndexError):
            continue
        bucket = by_year.setdefault(year, ([], []))
        bucket[0].append(temps[index] if index < len(temps) else None)
        bucket[1].append(humidities[index] if index < len(humidities) else None)

    return {
        year: encode_year(series_t, series_h)
        for year, (series_t, series_h) in by_year.items()
        if len(series_t) == days_in_year(year)
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normals_cache.py -v && .venv/Scripts/python.exe -m ruff check .`
Expected: PASS, lint clean

- [ ] **Step 5: Commit**

```bash
git add scripts/normals_cache.py tests/test_normals_cache.py
git commit -m "Split responses into whole calendar years, dropping short ones"
```

---

### Task 3: Composition and the two reducers

**Files:**
- Modify: `scripts/normals_cache.py`
- Test: `tests/test_normals_cache.py`

**Interfaces:**
- Consumes: `decode_year`, `days_in_year` from Task 1
- Produces: `MONTHS: int`, `STATS: int`, `MIN_SAMPLES: int`, `MISSING: float`, `reduce_mean_sd(samples: list[float]) -> tuple[float, float]`, `reduce_median_mad(samples: list[float]) -> tuple[float, float]`, `compose(year_blobs: dict[int, str], years: list[int], statistic: str = "mean-sd") -> list[float]` returning 48 floats ordered `[mean_t, sd_t, mean_h, sd_h]` per month, January first

- [ ] **Step 1: Write the failing test**

```python
import math

from scripts.normals_cache import (
    MISSING,
    compose,
    reduce_mean_sd,
    reduce_median_mad,
    split_response_by_year,
)


class TestReducers:
    def test_mean_and_sample_sd(self):
        mean, sd = reduce_mean_sd([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
        assert mean == pytest.approx(5.0)
        assert sd == pytest.approx(2.138, abs=0.01)  # sample sd, n-1

    def test_near_constant_month_does_not_produce_negative_variance(self):
        mean, sd = reduce_mean_sd([20.0] * 30)
        assert mean == pytest.approx(20.0)
        assert sd == pytest.approx(0.0, abs=1e-9)

    def test_median_and_mad_ignore_a_single_extreme(self):
        """The whole point of the robust path: one heat dome must not widen sigma."""
        ordinary = [20.0] * 30 + [21.0] * 30
        with_event = [*ordinary, 45.0]

        _, sd_plain = reduce_mean_sd(with_event)
        _, sd_robust = reduce_median_mad(with_event)

        assert sd_robust < sd_plain

    def test_mad_sigma_matches_sd_on_normal_looking_data(self):
        samples = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        _, sd_robust = reduce_median_mad(samples)
        assert sd_robust == pytest.approx(2.9652, abs=0.001)  # 1.4826 * MAD(=2.0)


class TestComposition:
    def _blobs(self, start_year, n_years, temp=lambda d: 10.0):
        from datetime import date, timedelta

        times, temps, hums = [], [], []
        day = date(start_year, 1, 1)
        end = date(start_year + n_years - 1, 12, 31)
        while day <= end:
            times.append(day.isoformat())
            temps.append(temp(day))
            hums.append(50.0)
            day += timedelta(days=1)
        return split_response_by_year(
            {
                "time": times,
                "temperature_2m_mean": temps,
                "relative_humidity_2m_mean": hums,
            }
        )

    def test_shape_is_twelve_months_of_four_stats(self):
        values = compose(self._blobs(2023, 3), [2023, 2024, 2025])
        assert len(values) == 48

    def test_a_constant_series_gives_that_mean_and_zero_sigma(self):
        values = compose(self._blobs(2023, 3), [2023, 2024, 2025])
        for month in range(12):
            assert values[month * 4] == pytest.approx(10.0, abs=0.05)
            assert values[month * 4 + 1] == pytest.approx(0.0, abs=1e-6)
            assert values[month * 4 + 2] == pytest.approx(50.0, abs=0.05)

    def test_a_subset_of_years_equals_a_cache_built_from_only_those_years(self):
        """Composability is the reason the cache exists."""
        five = self._blobs(2021, 5, temp=lambda d: float(d.month))
        three = self._blobs(2023, 3, temp=lambda d: float(d.month))

        assert compose(five, [2023, 2024, 2025]) == compose(three, [2023, 2024, 2025])

    def test_months_are_bucketed_by_the_years_own_calendar(self):
        """2024 has a leap day; using 2023's boundaries would shift March onward."""
        blobs = self._blobs(2024, 1, temp=lambda d: float(d.month))
        values = compose(blobs, [2024])

        for month in range(12):
            assert values[month * 4] == pytest.approx(month + 1, abs=0.05)

    def test_a_month_below_min_samples_is_missing_not_zero(self):
        blobs = self._blobs(2023, 1, temp=lambda d: 10.0 if d.month != 6 else None)
        values = compose(blobs, [2023])

        assert math.isnan(values[5 * 4])  # June mean temperature
        assert math.isnan(values[5 * 4 + 1])
        assert values[5 * 4 + 2] == pytest.approx(50.0, abs=0.05)  # humidity survives

    def test_years_absent_from_the_cache_are_skipped(self):
        values = compose(self._blobs(2023, 1), [2023, 2024, 2025])
        assert values[0] == pytest.approx(10.0, abs=0.05)

    def test_an_empty_cache_is_all_missing(self):
        values = compose({}, [2023])
        assert len(values) == 48
        assert all(math.isnan(v) for v in values)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normals_cache.py -k "Reducers or Composition" -v`
Expected: FAIL — `ImportError: cannot import name 'compose'`

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/normals_cache.py` (add `import math` and `from datetime import date` at the top):

```python
MONTHS = 12
# Per city-month: mean temp, sd temp, mean humidity, sd humidity.
STATS = 4

# Below this many daily samples a standard deviation is not worth trusting.
MIN_SAMPLES = 20

# Written for a city-month with too little data to characterise. NaN rather
# than 0.0 so scoring skips it instead of reporting a spurious anomaly.
MISSING = float("nan")

# Scale factor making the median absolute deviation a consistent estimator of
# sigma for normally distributed data.
MAD_TO_SIGMA = 1.4826


def reduce_mean_sd(samples: list[float]) -> tuple[float, float]:
    """Arithmetic mean and sample standard deviation."""
    count = len(samples)
    mean = sum(samples) / count
    variance = sum((value - mean) ** 2 for value in samples) / (count - 1) if count > 1 else 0.0
    return mean, math.sqrt(max(variance, 0.0))


def reduce_median_mad(samples: list[float]) -> tuple[float, float]:
    """Median, with sigma estimated from the median absolute deviation.

    Resistant to a single extreme in the baseline window -- a heat dome that
    would inflate sigma under the mean/sd reducer, and so widen the band every
    future anomaly is measured against, barely moves this one.

    The symmetry MAD assumes is weakest for humidity, which is bounded at 100%
    and piles up near saturation, which is why mean-sd stays the default until
    this is measured rather than assumed.
    """
    ordered = sorted(samples)
    median = _median(ordered)
    deviations = sorted(abs(value - median) for value in ordered)
    return median, MAD_TO_SIGMA * _median(deviations)


def _median(ordered: list[float]) -> float:
    count = len(ordered)
    midpoint = count // 2
    if count % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


_REDUCERS = {"mean-sd": reduce_mean_sd, "median-mad": reduce_median_mad}


def _month_of_day_index(year: int) -> list[int]:
    """Zero-based month for each day position in a calendar year."""
    months: list[int] = []
    for month in range(1, MONTHS + 1):
        days = calendar.monthrange(year, month)[1]
        months.extend([month - 1] * days)
    return months


def compose(
    year_blobs: dict[int, str], years: list[int], statistic: str = "mean-sd"
) -> list[float]:
    """Reduce cached daily values to the artefact's 48 floats per city.

    Pooling daily values across years within a calendar month is deliberate:
    the board asks "how unusual is today's reading", so the reference
    distribution has to be the one today's reading is drawn from. Sigma over
    monthly means answers a different question and is far too small.
    """
    reduce = _REDUCERS[statistic]

    samples: list[tuple[list[float], list[float]]] = [([], []) for _ in range(MONTHS)]
    for year in years:
        blob = year_blobs.get(year)
        if blob is None:
            continue
        temps, humidities = decode_year(blob)
        if len(temps) != days_in_year(year):
            continue
        for index, month in enumerate(_month_of_day_index(year)):
            if temps[index] is not None:
                samples[month][0].append(temps[index])
            if humidities[index] is not None:
                samples[month][1].append(humidities[index])

    out: list[float] = []
    for month_temps, month_humidities in samples:
        for series in (month_temps, month_humidities):
            if len(series) < MIN_SAMPLES:
                out.extend((MISSING, MISSING))
                continue
            centre, spread = reduce(series)
            out.extend((centre, spread))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normals_cache.py -v && .venv/Scripts/python.exe -m ruff check .`
Expected: PASS, lint clean

- [ ] **Step 5: Commit**

```bash
git add scripts/normals_cache.py tests/test_normals_cache.py
git commit -m "Compose normals from cached days, with a robust reducer alongside"
```

---

### Task 4: Cache file IO

**Files:**
- Modify: `scripts/normals_cache.py`
- Test: `tests/test_normals_cache.py`

**Interfaces:**
- Consumes: nothing from earlier tasks beyond the module
- Produces: `CACHE_PATH: Path`, `load_cache(path: Path) -> dict[int, dict[int, str]]`, `append_year(handle, geonameid: int, year: int, blob: str) -> None`, `cached_years(cache: dict[int, dict[int, str]], geonameid: int) -> set[int]`

- [ ] **Step 1: Write the failing test**

```python
from scripts.normals_cache import append_year, cached_years, load_cache


class TestCacheIO:
    def test_round_trip_through_a_file(self, tmp_path):
        path = tmp_path / "cache.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            append_year(handle, 123, 2024, "AAAA")
            append_year(handle, 123, 2025, "BBBB")
            append_year(handle, 456, 2024, "CCCC")

        cache = load_cache(path)
        assert cache == {123: {2024: "AAAA", 2025: "BBBB"}, 456: {2024: "CCCC"}}

    def test_a_missing_file_is_an_empty_cache(self, tmp_path):
        assert load_cache(tmp_path / "absent.jsonl") == {}

    def test_a_truncated_final_line_is_skipped_not_fatal(self, tmp_path):
        """An interrupted run leaves a partial line; the rest must still load."""
        path = tmp_path / "cache.jsonl"
        path.write_text('{"id": 1, "y": 2024, "d": "AAAA"}\n{"id": 2, "y": 20', encoding="utf-8")

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normals_cache.py -k CacheIO -v`
Expected: FAIL — `ImportError: cannot import name 'load_cache'`

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/normals_cache.py` (add `import json` and `from pathlib import Path` at the top):

```python
DATA_DIR = Path(__file__).resolve().parent.parent / "app" / "data"

# No window in the name, on purpose. The v1 checkpoint carried its date range
# because resume was keyed on "have we done this city", so a file from another
# range would silently blend baselines computed over different periods.
# Labelling each record with its year removes that hazard rather than guarding
# against it, and the window becomes an argument to compose() instead of a
# property of the file.
CACHE_PATH = DATA_DIR / ".climate_normals.v2.daily.jsonl"


def load_cache(path: Path) -> dict[int, dict[int, str]]:
    """Read the whole cache as ``{geonameid: {year: blob}}``."""
    if not path.exists():
        return {}

    cache: dict[int, dict[int, str]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                cache.setdefault(int(record["id"]), {})[int(record["y"])] = record["d"]
            except (ValueError, KeyError, TypeError):
                continue  # truncated final line from an interrupted run
    return cache


def append_year(handle, geonameid: int, year: int, blob: str) -> None:
    handle.write(json.dumps({"id": geonameid, "y": year, "d": blob}) + "\n")


def cached_years(cache: dict[int, dict[int, str]], geonameid: int) -> set[int]:
    return set(cache.get(geonameid, {}))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normals_cache.py -v && .venv/Scripts/python.exe -m ruff check .`
Expected: PASS, lint clean

- [ ] **Step 5: Commit**

```bash
git add scripts/normals_cache.py tests/test_normals_cache.py
git commit -m "Append-only cache keyed by city and year, tolerant of a torn tail"
```

---

### Task 5: Fetch into the cache, grouped by missing years

**Files:**
- Modify: `scripts/build_climate_normals.py:431-520` (the `build` function), plus imports
- Test: `tests/test_normals_cache.py`

**Interfaces:**
- Consumes: `load_cache`, `append_year`, `cached_years`, `split_response_by_year`, `CACHE_PATH` from Tasks 2 and 4
- Produces: `group_by_missing_years(cities: list[CityRecord], cache: dict, years: list[int]) -> dict[tuple[int, ...], list[CityRecord]]`

- [ ] **Step 1: Write the failing test**

```python
from app.services.city_index import CityRecord
from scripts.build_climate_normals import group_by_missing_years


def _city(geonameid):
    return CityRecord(
        row_index=geonameid,
        geonameid=geonameid,
        name=f"C{geonameid}",
        state="",
        country="XX",
        population=1,
        latitude=0.0,
        longitude=0.0,
    )


class TestMissingYearGrouping:
    def test_a_fresh_run_is_one_group_wanting_everything(self):
        groups = group_by_missing_years([_city(1), _city(2)], {}, [2023, 2024, 2025])
        assert groups == {(2023, 2024, 2025): [_city(1), _city(2)]}

    def test_a_fully_cached_city_is_absent(self):
        cache = {1: {2023: "a", 2024: "b", 2025: "c"}}
        assert group_by_missing_years([_city(1)], cache, [2023, 2024, 2025]) == {}

    def test_a_partly_cached_city_asks_only_for_its_gaps(self):
        cache = {1: {2023: "a"}}
        groups = group_by_missing_years([_city(1)], cache, [2023, 2024, 2025])
        assert list(groups) == [(2024, 2025)]

    def test_cities_wanting_the_same_years_share_a_group(self):
        cache = {1: {2023: "a"}, 2: {2023: "a"}, 3: {}}
        groups = group_by_missing_years(
            [_city(1), _city(2), _city(3)], cache, [2023, 2024, 2025]
        )
        assert len(groups[(2024, 2025)]) == 2
        assert len(groups[(2023, 2024, 2025)]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normals_cache.py -k MissingYear -v`
Expected: FAIL — `ImportError: cannot import name 'group_by_missing_years'`

- [ ] **Step 3: Write minimal implementation**

Add to `scripts/build_climate_normals.py`, importing from the cache module:

```python
from scripts.normals_cache import (  # noqa: E402
    CACHE_PATH,
    append_year,
    cached_years,
    compose,
    load_cache,
    split_response_by_year,
)


def group_by_missing_years(
    cities: list[CityRecord], cache: dict[int, dict[int, str]], years: list[int]
) -> dict[tuple[int, ...], list[CityRecord]]:
    """Bucket cities by which years they still lack.

    A fresh run is one bucket wanting the whole window; a widened window is
    also one bucket, wanting only the years added. Grouping keeps the request
    shape uniform in both cases while letting an interrupted city keep what it
    already has.
    """
    groups: dict[tuple[int, ...], list[CityRecord]] = {}
    for city in cities:
        missing = tuple(sorted(set(years) - cached_years(cache, city.geonameid)))
        if missing:
            groups.setdefault(missing, []).append(city)
    return groups
```

Then replace the body of `build` so that, for each group, it requests
`min(missing)-01-01 .. max(missing)-12-31`, splits each city's response with
`split_response_by_year`, and appends only years in the missing set:

```python
    cache = load_cache(CACHE_PATH)
    years = list(range(end_year - years_wanted + 1, end_year + 1))
    groups = group_by_missing_years(cities, cache, years)

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("a", encoding="utf-8") as handle:
        for missing, group_cities in groups.items():
            start = f"{missing[0]}-01-01"
            end = f"{missing[-1]}-12-31"
            wanted = set(missing)
            chunks: list[list[CityRecord]] = []
            for offset in range(0, len(group_cities), batch_size):
                chunks.extend(
                    split_to_limits(group_cities[offset : offset + batch_size], start, end)
                )

            for batch in chunks:
                results, throttled = _fetch_batch(batch, start, end, timeout=timeout)
                pacer.on_throttled() if throttled else pacer.on_success()
                if results is None or len(results) != len(batch):
                    continue
                for city, result in zip(batch, results, strict=True):
                    daily = result.get("daily") if isinstance(result, dict) else None
                    if not daily:
                        continue
                    for year, blob in split_response_by_year(daily).items():
                        if year in wanted:
                            append_year(handle, city.geonameid, year, blob)
                handle.flush()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check .`
Expected: PASS, lint clean

- [ ] **Step 5: Commit**

```bash
git add scripts/build_climate_normals.py tests/test_normals_cache.py
git commit -m "Fetch only the years a city lacks, storing raw days as they arrive"
```

---

### Task 6: Compose the artefact, add --statistic, and stop over-promising the ETA

**Files:**
- Modify: `scripts/build_climate_normals.py` (`_write_artifact`, `main`, the startup print)
- Test: `tests/test_normals_cache.py`

**Interfaces:**
- Consumes: `compose` from Task 3
- Produces: artefact meta gaining `"statistic"` and `"cache_version": 2`

- [ ] **Step 1: Write the failing test**

```python
import json

from scripts.build_climate_normals import _write_artifact


class TestArtefactFromCache:
    def test_meta_records_which_statistic_produced_it(self, tmp_path, monkeypatch):
        import scripts.build_climate_normals as script

        monkeypatch.setattr(script, "NORMALS_PATH", tmp_path / "n.bin.gz")
        monkeypatch.setattr(script, "META_PATH", tmp_path / "n.meta.json")

        cities = [_city(1)]
        _write_artifact(cities, {1: {}}, [2023], "median-mad")

        meta = json.loads((tmp_path / "n.meta.json").read_text(encoding="utf-8"))
        assert meta["statistic"] == "median-mad"
        assert meta["cache_version"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_normals_cache.py -k ArtefactFromCache -v`
Expected: FAIL — `_write_artifact` takes the old signature

- [ ] **Step 3: Write minimal implementation**

Change `_write_artifact` to take `(cities, cache, years, statistic)` and build each
city's 48 floats with `compose(cache.get(city.geonameid, {}), years, statistic)`;
add `"statistic": statistic` and `"cache_version": 2` to meta alongside the existing
`window_start` / `window_end` / `years`. Add the flag in `main`:

```python
    parser.add_argument(
        "--statistic",
        choices=("mean-sd", "median-mad"),
        default="mean-sd",
        help=(
            "how each city-month is summarised (default: mean-sd, the plain "
            "arithmetic baseline). median-mad resists a single extreme event in "
            "the window inflating sigma. Switching is a --write-only repack: the "
            "cache holds the daily values either way, so it costs no quota."
        ),
    )
```

Replace the ETA print, which models only the pacing rate:

```python
    if pending:
        print(f"starting at {rate:.0f} location-years/min, adapting from there")
        # No completion estimate. Open-Meteo's *daily* quota binds long before
        # the pacing rate does -- a run is stopped by "try again tomorrow", not
        # by how fast it asks -- so any hours figure derived from the rate alone
        # understates the wall-clock by more than an order of magnitude.
        print("stops when the daily quota is exhausted; re-run to resume\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check .`
Expected: PASS, lint clean

- [ ] **Step 5: Commit**

```bash
git add scripts/build_climate_normals.py tests/test_normals_cache.py
git commit -m "Compose the artefact from the cache; pick the statistic at pack time"
```

---

### Task 7: End-to-end offline verification and README

**Files:**
- Modify: `README.md` (the "Coverage is whatever the normals artefact holds" bullet)
- Test: manual run

- [ ] **Step 1: Verify `--write-only` composes with no network**

Run: `.venv/Scripts/python.exe scripts/build_climate_normals.py --write-only --min-population 100000 --years 3`
Expected: writes the artefact from whatever the cache holds, no HTTP

- [ ] **Step 2: Confirm the service still loads the artefact**

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health` after restarting uvicorn
Expected: `200`

- [ ] **Step 3: Update the README**

Document the cache: where it lives, that the window is a compose-time choice,
that `--statistic` switches the reducer with a free repack, and that widening
the window fetches only the years added.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Document the daily cache and what it makes free"
```

---

## Self-Review

**Spec coverage:** cache layout (Tasks 1, 4), fetch/storage granularity (Task 2), composition and both reducers (Task 3), resume and widening (Task 5), artefact and meta (Task 6), migration — v1 file left untouched, new filename (Task 4), error handling — short years dropped (Task 2), length mismatch retained from existing code (Task 5), `MIN_SAMPLES` NaN (Task 3), ETA honesty (Task 6), README (Task 7).

**Placeholders:** none — every code step carries real code.

**Type consistency:** `compose(year_blobs, years, statistic)` is used with that
signature in Tasks 3 and 6. `load_cache` returns `dict[int, dict[int, str]]`,
consumed as such by `cached_years`, `group_by_missing_years`, and
`_write_artifact`. `split_response_by_year` returns `dict[int, str]`, consumed as
such in Task 5.
