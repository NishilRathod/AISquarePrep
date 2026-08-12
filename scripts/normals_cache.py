"""Storage for the raw daily values behind the climate normals.

The artefact the board reads holds finished statistics, and finished statistics
are a dead end: a three-year normal cannot be derived from a five-year mean and
sigma, so changing the window used to mean refetching every city. Open-Meteo's
daily quota -- roughly a day per thousand cities -- makes that expensive enough
to design around.

So this cache keeps what the API actually returned, the daily series, and
reduces it on demand. Window length and choice of statistic stop being
fetch-time commitments and become local recomputations over data already held.
Widening the window later fetches only the years that were not already stored.

Values are quantised to a tenth and stored as little-endian int16: two orders of
magnitude below the ~2.4 C sigma the board ranks on, so nothing observable is
lost, at a quarter the size of float64. Byte order is explicit because this is a
persisted format and the platform's native order is not a promise.
"""

from __future__ import annotations

import base64
import calendar
import json
import math
import struct
from pathlib import Path

# A tenth of a degree, and a tenth of a humidity point.
SCALE = 10

# Unreachable by any real reading -- int16's floor is -3276.8 once scaled -- so
# a gap can never be confused with an observation. Same reasoning as the
# artefact's NaN: a missing value must not be able to masquerade as a real one.
MISSING_SENTINEL = -32768

MONTHS = 12
# Per city-month: mean temp, sd temp, mean humidity, sd humidity.
STATS = 4

# Below this many daily samples a standard deviation is not worth trusting.
MIN_SAMPLES = 20

# Written for a city-month with too little data to characterise. NaN rather
# than 0.0 so scoring skips it instead of reporting a spurious anomaly.
MISSING = float("nan")

# Makes the median absolute deviation a consistent estimator of sigma for
# normally distributed data.
MAD_TO_SIGMA = 1.4826

DATA_DIR = Path(__file__).resolve().parent.parent / "app" / "data"

# No window in the name, on purpose. The v1 checkpoint carried its date range
# because resume was keyed on "have we already done this city", so a file from a
# different range would silently blend baselines computed over different
# periods. Labelling each record with its year removes that hazard rather than
# guarding against it, and the window becomes an argument to compose() instead
# of a property of the file.
CACHE_PATH = DATA_DIR / ".climate_normals.v2.daily.jsonl"


def days_in_year(year: int) -> int:
    return 366 if calendar.isleap(year) else 365


def _quantise(value: float | None) -> int:
    if value is None:
        return MISSING_SENTINEL
    scaled = round(value * SCALE)
    # Clamp rather than let struct raise. A wild value is bad data, not a reason
    # to lose the rest of the city's year.
    return max(min(scaled, 32767), MISSING_SENTINEL + 1)


def _dequantise(raw: int) -> float | None:
    return None if raw == MISSING_SENTINEL else raw / SCALE


def encode_year(temps: list[float | None], humidities: list[float | None]) -> str:
    """One calendar year of daily readings as base64: all temps, then all humidities."""
    if len(temps) != len(humidities):
        raise ValueError(
            "temperature and humidity series differ in length: "
            f"{len(temps)} vs {len(humidities)}"
        )
    values = [_quantise(value) for value in temps] + [_quantise(value) for value in humidities]
    return base64.b64encode(struct.pack(f"<{len(values)}h", *values)).decode("ascii")


def decode_year(blob: str) -> tuple[list[float | None], list[float | None]]:
    raw = base64.b64decode(blob)
    count = len(raw) // 2
    values = struct.unpack(f"<{count}h", raw)
    half = count // 2
    return (
        [_dequantise(value) for value in values[:half]],
        [_dequantise(value) for value in values[half:]],
    )


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
        except (ValueError, TypeError):
            continue
        bucket = by_year.setdefault(year, ([], []))
        bucket[0].append(temps[index] if index < len(temps) else None)
        bucket[1].append(humidities[index] if index < len(humidities) else None)

    return {
        year: encode_year(series_t, series_h)
        for year, (series_t, series_h) in by_year.items()
        if len(series_t) == days_in_year(year)
    }


def reduce_mean_sd(samples: list[float]) -> tuple[float, float]:
    """Arithmetic mean and sample standard deviation."""
    count = len(samples)
    mean = sum(samples) / count
    variance = sum((value - mean) ** 2 for value in samples) / (count - 1) if count > 1 else 0.0
    return mean, math.sqrt(max(variance, 0.0))


def reduce_median_mad(samples: list[float]) -> tuple[float, float]:
    """Median, with sigma estimated from the median absolute deviation.

    Resistant to a single extreme inside the baseline window. A heat dome that
    would inflate sigma under the mean/sd reducer -- and so widen the band every
    future anomaly is measured against, pushing real events down the board --
    barely moves this one.

    The symmetry MAD assumes is weakest for humidity, which is bounded at 100%
    and piles up near saturation. That is why mean-sd stays the default until
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
        months.extend([month - 1] * calendar.monthrange(year, month)[1])
    return months


def compose(
    year_blobs: dict[int, str], years: list[int], statistic: str = "mean-sd"
) -> list[float]:
    """Reduce cached daily values to the artefact's 48 floats for one city.

    Pooling daily values across years within a calendar month is deliberate: the
    board asks "how unusual is today's reading", so the reference distribution
    has to be the one today's reading is drawn from. Sigma over monthly means
    answers a different question and is far too small.
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
