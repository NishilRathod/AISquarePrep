"""Read access to the running year's daily cache.

The baseline cache (``scripts/normals_cache.py``) stores whole calendar years and
drops anything short, because composition maps a day's position to its month
using that year's own calendar -- a gap-shortened series would silently
attribute readings to the wrong month. The current year is short by definition,
so it cannot live there without giving up the property that makes the baselines
trustworthy. It gets its own file instead, with explicit date bounds on every
record.

That file is what the anomaly board scores against its baselines, which is why
the read side lives in ``app/`` rather than ``scripts/``: the deployed app reads
it directly, and only the fetcher writes it.

Unlike the packed artefact, records are keyed by geonameid rather than by
position in the city index. The artefact is joined positionally and needs a
digest guard to catch the index drifting underneath it; keying by id removes
that hazard rather than guarding against it.
"""

from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from app.services.city_index import city_records

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RECENT_PATH = DATA_DIR / ".climate_recent.v1.jsonl"

# Same quantisation as the baseline cache: a tenth of a degree, and a value
# int16 cannot otherwise produce, so a gap can never be read as an observation.
SCALE = 10
MISSING_SENTINEL = -32768


class RecentUnavailableError(RuntimeError):
    """The recent cache is missing or holds nothing scoreable."""


@dataclass(frozen=True, slots=True)
class Reading:
    """One complete local day for one city."""

    local_date: date
    temperature_c: float
    humidity_pct: float


@dataclass(frozen=True, slots=True)
class RecentRecord:
    start: date
    reading: Reading


@dataclass(frozen=True, slots=True)
class Run:
    """One city's stored run of consecutive days, still in full.

    The board only ever wants the last complete day, but the fetcher needs the
    whole series so it can extend it, so both sides read the file through the
    same parser rather than each having its own idea of the format.
    """

    start: date
    temps: list[float | None]
    humidities: list[float | None]

    @property
    def end(self) -> date:
        return self.start + timedelta(days=max(len(self.temps) - 1, 0))


def _quantise(value: float | None) -> int:
    if value is None:
        return MISSING_SENTINEL
    scaled = round(value * SCALE)
    return max(min(scaled, 32767), MISSING_SENTINEL + 1)


def _dequantise(raw: int) -> float | None:
    return None if raw == MISSING_SENTINEL else raw / SCALE


def encode_run(temps: list[float | None], humidities: list[float | None]) -> str:
    """A run of consecutive days as base64: all temps, then all humidities."""
    if len(temps) != len(humidities):
        raise ValueError(
            "temperature and humidity series differ in length: "
            f"{len(temps)} vs {len(humidities)}"
        )
    values = [_quantise(value) for value in temps] + [_quantise(value) for value in humidities]
    return base64.b64encode(struct.pack(f"<{len(values)}h", *values)).decode("ascii")


def decode_run(blob: str) -> tuple[list[float | None], list[float | None]]:
    raw = base64.b64decode(blob)
    count = len(raw) // 2
    values = struct.unpack(f"<{count}h", raw)
    half = count // 2
    return (
        [_dequantise(value) for value in values[:half]],
        [_dequantise(value) for value in values[half:]],
    )


def latest_complete(
    temps: list[float | None], humidities: list[float | None], start: date
) -> Reading | None:
    """The most recent day holding *both* variables, scanning backwards.

    Trailing days are the unreliable ones: the last day of a forecast response
    is a partial day, and a response can simply stop short. A partial day's mean
    is not the statistic the baseline was built from, and dividing one by the
    other manufactures multi-sigma anomalies that track the clock rather than the
    weather -- so walk back to a day that is genuinely complete instead of
    scoring whatever happens to be last.
    """
    for offset in range(len(temps) - 1, -1, -1):
        temperature = temps[offset]
        humidity = humidities[offset] if offset < len(humidities) else None
        if temperature is None or humidity is None:
            continue
        return Reading(
            local_date=start + timedelta(days=offset),
            temperature_c=temperature,
            humidity_pct=humidity,
        )
    return None


def record_line(
    geonameid: int,
    start: date,
    temps: list[float | None],
    humidities: list[float | None],
) -> str:
    """One city's run as a JSON line.

    The end date is written even though it is derivable from the start and the
    length, so the two can be checked against each other -- a record whose bounds
    disagree with its payload is corrupt, and dropping it is better than
    attributing its readings to the wrong days.
    """
    end = start + timedelta(days=max(len(temps) - 1, 0))
    return json.dumps(
        {
            "id": geonameid,
            "s": start.isoformat(),
            "e": end.isoformat(),
            "d": encode_run(temps, humidities),
        }
    )


def parse_runs(lines: list[str]) -> dict[int, Run]:
    """Reduce raw lines to one run per city, last line winning.

    Last-wins is what makes the daily top-up an append rather than a rewrite:
    today's record supersedes yesterday's without touching the rest of the file.
    """
    runs: dict[int, Run] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
            geonameid = int(raw["id"])
            start = date.fromisoformat(raw["s"])
            end = date.fromisoformat(raw["e"])
            temps, humidities = decode_run(raw["d"])
        except (ValueError, KeyError, TypeError, struct.error):
            continue  # truncated final line from an interrupted run

        # Bounds and payload must agree, or the day each reading belongs to is
        # guesswork.
        if (end - start).days + 1 != len(temps):
            continue

        runs[geonameid] = Run(start=start, temps=temps, humidities=humidities)
    return runs


def parse_records(lines: list[str]) -> dict[int, RecentRecord]:
    """The scoreable part of each run: its last complete day."""
    records: dict[int, RecentRecord] = {}
    for geonameid, run in parse_runs(lines).items():
        reading = latest_complete(run.temps, run.humidities, run.start)
        if reading is None:
            continue  # nothing scoreable in this run
        records[geonameid] = RecentRecord(start=run.start, reading=reading)
    return records


@dataclass(frozen=True, slots=True)
class RecentStore:
    readings: dict[int, Reading]
    as_of: date | None

    @property
    def cities_covered(self) -> int:
        return len(self.readings)

    @classmethod
    def from_records(
        cls, records: dict[int, RecentRecord], row_by_geonameid: dict[int, int]
    ) -> RecentStore:
        """Join records to the city index by id.

        A city the index does not know is dropped rather than kept: it cannot be
        scored without coordinates and a name, and carrying it would only inflate
        the coverage count.
        """
        readings: dict[int, Reading] = {}
        for geonameid, record in records.items():
            row_index = row_by_geonameid.get(geonameid)
            if row_index is None:
                continue
            readings[row_index] = record.reading

        as_of = max((reading.local_date for reading in readings.values()), default=None)
        return cls(readings=readings, as_of=as_of)

    def get(self, row_index: int) -> Reading | None:
        return self.readings.get(row_index)


_cache: tuple[tuple[float, int], RecentStore] | None = None


def load_recent(path: Path | None = None) -> RecentStore:
    """Load the recent cache, re-reading it when the file has changed.

    Keyed on mtime and size rather than cached outright: the fetcher appends to
    this file daily while the app is running, and a board recomputed from a
    snapshot taken at boot would silently stop advancing.
    """
    global _cache

    target = path or RECENT_PATH
    if not target.exists():
        raise RecentUnavailableError(
            f"recent daily cache missing at {target}; run scripts/fetch_recent_daily.py"
        )

    stat = target.stat()
    stamp = (stat.st_mtime, stat.st_size)
    if path is None and _cache is not None and _cache[0] == stamp:
        return _cache[1]

    with target.open(encoding="utf-8") as handle:
        records = parse_records(handle.readlines())

    store = RecentStore.from_records(
        records, {record.geonameid: record.row_index for record in city_records()}
    )
    if not store.readings:
        raise RecentUnavailableError(
            f"recent daily cache at {target} holds no scoreable readings"
        )

    if path is None:
        _cache = (stamp, store)
    return store
