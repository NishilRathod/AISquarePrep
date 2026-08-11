"""Build the climate-normals artefact backing the global anomaly board.

An anomaly is only meaningful against a baseline, and the baseline has to be a
*number*, not a judgement: ranking cities against each other requires a
standardized anomaly (z-score), which needs a mean and a standard deviation for
that city, that variable, that time of year.

    z = (observed - mean) / sd

This script fetches daily reanalysis history from Open-Meteo (ERA5, free, no API
key) and reduces it to, per city and per calendar month, the mean and standard
deviation of daily mean temperature and daily mean relative humidity.

Why sigma is over *daily* values within a calendar month, pooled across years,
rather than over monthly means: the question the board asks is "how unusual is
today's reading", so the reference distribution has to be the one today's
reading is drawn from. Sigma of monthly means answers a different question ("how
unusual is this month overall") and is far too small, which would make almost
every day look extreme.

Five years rather than the WMO 30-year standard is a deliberate trade. It is a
sixth of the fetch volume, and ~150 daily samples per city-month already puts the
error on sigma near 6%; the mean drifts slightly warm relative to a 1961-1990
baseline, which matters for climate-change attribution but not for "is today
unusual for this place". --years 3 halves the fetch again at some cost to that
stability, but note it starts a new window and therefore a new checkpoint.

Rate limits are the binding constraint, and they are a *quota* rather than a
throughput cap: Open-Meteo weights its allowance by locations x days, not by
request count, so batching reduces transfer overhead but buys no extra quota.
There are separate minutely, hourly, and daily windows; measured against the
archive endpoint, the **hourly** one binds first, and it is easy to exhaust in a
few minutes of enthusiastic requests and then be locked out for the rest of the
hour.

That makes pacing, not batch size, the thing that matters. Firing large requests
as fast as possible is the worst available strategy: each trips the limit, backs
off, retries the same large request, and makes no progress while still burning
quota. Open-Meteo publishes no number for the allowance, so :class:`Pacer` finds
it -- additive increase while requests succeed, multiplicative decrease on a 429
-- and the run prints the rate it settled on so the next one can start there.

**Deduplicating nearby cities onto a grid was tried and rejected.** The idea was
that cities sharing a reanalysis cell could share one request. Measurement killed
it: Hong Kong and Kowloon are 4 km apart and their daily means still differ by
0.56 C on average, against a local sigma of 1.1 C -- roughly half a sigma of
fabricated error injected into a board whose entire top ten spans about one
sigma. Shenzhen and Bao'an, 19 km apart, differ by up to 12 humidity points.
Open-Meteo's grid is also irregular (a 0.02 degree step can cross a cell
boundary, and spacing near Paris measured ~0.149 degrees), so cells cannot be
predicted by rounding anyway. It would have saved 12-25% of requests and silently
corrupted the rankings.

Scope is therefore the main lever, and --min-population is the knob: the default
of 100,000 covers ~6,200 cities, a fifth of the index, and drops the towns whose
names would mean nothing at the top of a global board.

Coverage is partial-friendly at every stage -- cities not yet fetched are NaN and
simply absent from the board rather than wrong on it -- so a long run can be
stopped, packed with --write-only, and resumed later.

    # the default: cities over 100k, a working global board
    python scripts/build_climate_normals.py

    # the whole index; expect a day or more, re-run to resume
    python scripts/build_climate_normals.py --min-population 0

Data: Open-Meteo ERA5 reanalysis, CC BY 4.0 -- see app/data/ATTRIBUTION.md.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from array import array
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.city_index import CityRecord, city_records  # noqa: E402

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

DATA_DIR = Path(__file__).resolve().parent.parent / "app" / "data"
NORMALS_PATH = DATA_DIR / "climate_normals.bin.gz"
META_PATH = DATA_DIR / "climate_normals.meta.json"


MONTHS = 12
# Per city-month we store: mean temp, sd temp, mean humidity, sd humidity.
STATS = 4

# Sentinel written for a city-month with too little data to characterise. NaN
# rather than 0.0 so a missing baseline can never masquerade as a real one --
# scoring skips these instead of reporting a spurious anomaly.
MISSING = float("nan")

# Below this many daily samples a standard deviation is not worth trusting.
MIN_SAMPLES = 20


def _checkpoint_path(start: str, end: str) -> Path:
    """Checkpoints are per-window.

    Resuming is keyed on "have we already done this city", so a checkpoint from
    a different date range would silently satisfy that check and mix baselines
    computed over different periods into one artefact. Putting the window in the
    filename makes that impossible rather than merely unlikely.
    """
    return DATA_DIR / f".climate_normals.{start}_{end}.checkpoint.jsonl"


class RateLimited(Exception):
    """Open-Meteo returned 429; the caller should back off and retry."""


def _fetch(url: str, *, timeout: float) -> object:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RateLimited from exc
        raise


def _fetch_batch(
    batch: list[CityRecord], start: str, end: str, *, timeout: float
) -> tuple[list[dict] | None, bool]:
    """One archive request covering every city in ``batch``.

    Returns ``(results, throttled)``. ``results`` is ``None`` for a non-retryable
    failure, in which case the caller drops the batch rather than aborting the
    run -- a handful of missing cities is a far better outcome than losing hours
    of accumulated progress. ``throttled`` tells the pacer it overshot, whether
    or not the batch eventually succeeded.
    """
    query = urllib.parse.urlencode(
        {
            "latitude": ",".join(str(city.latitude) for city in batch),
            "longitude": ",".join(str(city.longitude) for city in batch),
            "start_date": start,
            "end_date": end,
            "daily": "temperature_2m_mean,relative_humidity_2m_mean",
            # Local days, so a reading is bucketed into the month it happened in
            # locally rather than in UTC.
            "timezone": "auto",
        }
    )
    url = f"{ARCHIVE_URL}?{query}"

    delay = 60.0
    throttled = False
    for attempt in range(6):
        try:
            payload = _fetch(url, timeout=timeout)
        except RateLimited:
            throttled = True
            # Skip the batch rather than raise. This job runs for hours, and
            # letting one exhausted batch abort it would throw away every city
            # fetched since the last restart -- the opposite of resumable. The
            # cities land in a later run instead.
            if attempt == 5:
                print("    still rate limited after backoff; batch deferred", flush=True)
                return None, throttled
            print(f"    rate limited, sleeping {delay:.0f}s", flush=True)
            time.sleep(delay)
            delay = min(delay * 1.5, 600.0)
            continue
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == 5:
                print(f"    giving up on batch: {type(exc).__name__} {exc}", flush=True)
                return None, throttled
            time.sleep(10.0 * (attempt + 1))
            continue

        # A single-location request returns an object, not a list.
        if isinstance(payload, dict):
            return [payload], throttled
        if isinstance(payload, list):
            return payload, throttled
        return None, throttled
    return None, throttled


def _accumulate(daily: dict) -> list[list[float]]:
    """Reduce one city's daily series to per-month running sums.

    Returns 12 rows of ``[n_t, sum_t, sumsq_t, n_h, sum_h, sumsq_h]``. Sums
    rather than retained samples because the whole point is to never hold ten
    years of daily values for 34k cities in memory at once.
    """
    months = [[0.0] * 6 for _ in range(MONTHS)]

    times = daily.get("time") or []
    temps = daily.get("temperature_2m_mean") or []
    humidities = daily.get("relative_humidity_2m_mean") or []

    for index, stamp in enumerate(times):
        # "YYYY-MM-DD" -> 0-based month. Cheaper than parsing a date, and the
        # archive API's format is fixed.
        try:
            month = int(stamp[5:7]) - 1
        except (ValueError, IndexError):
            continue
        if not 0 <= month < MONTHS:
            continue
        bucket = months[month]

        temp = temps[index] if index < len(temps) else None
        if temp is not None:
            bucket[0] += 1
            bucket[1] += temp
            bucket[2] += temp * temp

        humidity = humidities[index] if index < len(humidities) else None
        if humidity is not None:
            bucket[3] += 1
            bucket[4] += humidity
            bucket[5] += humidity * humidity

    return months


def _finalize(months: list[list[float]]) -> list[float]:
    """Turn running sums into ``[mean_t, sd_t, mean_h, sd_h]`` per month."""
    out: list[float] = []
    for bucket in months:
        pairs = ((bucket[0], bucket[1], bucket[2]), (bucket[3], bucket[4], bucket[5]))
        for count, total, total_sq in pairs:
            if count < MIN_SAMPLES:
                out.extend((MISSING, MISSING))
                continue
            mean = total / count
            # Sample variance; clamped because catastrophic cancellation in the
            # sum-of-squares form can land a hair below zero on a near-constant
            # month.
            variance = max((total_sq - count * mean * mean) / (count - 1), 0.0)
            out.extend((mean, math.sqrt(variance)))
    return out


def _load_checkpoint(path: Path) -> dict[int, list[float]]:
    if not path.exists():
        return {}
    done: dict[int, list[float]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                done[int(record["id"])] = record["v"]
            except (ValueError, KeyError):
                continue  # truncated final line from an interrupted run
    return done


def _geonameid_digest(cities: list[CityRecord]) -> str:
    joined = ",".join(str(city.geonameid) for city in cities)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class Pacer:
    """Finds the quota ceiling instead of guessing under it.

    Open-Meteo publishes no number for the weighted hourly allowance, so a fixed
    rate is either too slow (most of the budget unused, for hours) or too fast
    (throttled, and then the whole hour is spent being refused). Neither is
    discoverable in advance.

    So: additive increase, multiplicative decrease -- the same shape as TCP
    congestion control, for the same reason. Nudge the rate up while requests
    succeed, halve it the moment one is refused, and settle just under whatever
    the real limit turns out to be. Decrease is aggressive and increase is gentle
    because the two errors are not symmetric: overshooting costs the remainder of
    the hour, undershooting costs a few seconds.
    """

    def __init__(self, rate: float, ceiling: float = 600.0):
        self.rate = rate
        self._ceiling = ceiling
        self._observed_limit: float | None = None

    def on_success(self) -> None:
        # Once throttling has been seen, stay below where it happened rather
        # than climbing back into it.
        cap = self._observed_limit * 0.9 if self._observed_limit else self._ceiling
        self.rate = min(self.rate * 1.08, cap)

    def on_throttled(self) -> None:
        self._observed_limit = self.rate
        self.rate = max(self.rate * 0.5, 5.0)

    def seconds_between(self, location_years: int) -> float:
        return location_years / self.rate * 60.0


def _select_cities(city_limit: int | None, min_population: int) -> list[CityRecord]:
    """The cities to cover, in index order.

    Index order is the contract -- the artefact is written by position -- so this
    filters rather than reorders. Population is the natural scope knob: "the most
    anomalous city on Earth" means more as Lisbon than as a town of 15,000, and
    every city dropped is quota spent somewhere more interesting.
    """
    cities = city_records()
    if min_population > 0:
        cities = [city for city in cities if city.population >= min_population]
    if city_limit is not None:
        cities = cities[:city_limit]
    return cities


def build(
    city_limit: int | None,
    min_population: int,
    years: int,
    batch_size: int,
    timeout: float,
    rate: float,
) -> None:
    cities = _select_cities(city_limit, min_population)

    end_year = datetime.now(UTC).year - 1
    start = f"{end_year - years + 1}-01-01"
    end = f"{end_year}-12-31"

    checkpoint_path = _checkpoint_path(start, end)
    done = _load_checkpoint(checkpoint_path)
    pending = [city for city in cities if city.geonameid not in done]

    pacer = Pacer(rate)

    print(f"cities={len(cities):,} window={start}..{end}")
    print(f"already checkpointed={len(done):,} remaining={len(pending):,}")
    if pending:
        print(f"starting at {rate:.0f} location-years/min, adapting from there")
        print(f"~{len(pending) * years / rate / 60:.1f}h remaining at the starting rate\n")

    started = time.time()
    with checkpoint_path.open("a", encoding="utf-8") as checkpoint:
        for offset in range(0, len(pending), batch_size):
            batch = pending[offset : offset + batch_size]
            batch_started = time.time()
            results, throttled = _fetch_batch(batch, start, end, timeout=timeout)

            if throttled:
                pacer.on_throttled()
            else:
                pacer.on_success()

            if results is None:
                continue

            # Cities are matched to results by position, so a short or long
            # response would shift every city onto its neighbour's climate --
            # wrong in a way nothing downstream could detect. Drop the batch.
            if len(results) != len(batch):
                print(
                    f"    length mismatch: sent {len(batch)}, got {len(results)} -- batch dropped",
                    flush=True,
                )
                continue

            for city, result in zip(batch, results, strict=True):
                daily = result.get("daily") if isinstance(result, dict) else None
                if not daily:
                    continue
                values = _finalize(_accumulate(daily))
                checkpoint.write(json.dumps({"id": city.geonameid, "v": values}) + "\n")
                done[city.geonameid] = values
            checkpoint.flush()

            complete = offset + len(batch)
            elapsed = time.time() - started
            observed = complete / elapsed if elapsed else 0
            eta = (len(pending) - complete) / observed / 60 if observed else 0
            print(
                f"  {complete:>6,}/{len(pending):,} cities  "
                f"{observed * 60:>5.0f} cities/min  "
                f"pace {pacer.rate:>4.0f} loc-yr/min  eta {eta:>5.1f}m",
                flush=True,
            )

            if offset + batch_size < len(pending):
                remaining = pacer.seconds_between(len(batch) * years) - (
                    time.time() - batch_started
                )
                if remaining > 0:
                    time.sleep(remaining)

    print(f"\nsettled pacing rate: {pacer.rate:.0f} location-years/min")
    print(f"  pass --rate {pacer.rate:.0f} on the next run to start there")

    _write_artifact(cities, done, start, end, years)


def _write_artifact(
    _selected: list[CityRecord], done: dict[int, list[float]], start: str, end: str, years: int
) -> None:
    """Write the packed array over the **whole** city index, in index order.

    Order is the contract: the runtime store looks a city up by its position in
    the city index, so a row here must correspond to the same position there.

    That is why this writes every city rather than just the ones selected for
    this run. Once the selection can be a population filter rather than a prefix,
    position-within-the-selection stops matching position-within-the-index, and
    an artefact packed densely would hand each city its neighbour's climate --
    silently, since nothing downstream could detect it. Cities never fetched are
    NaN, which costs little (they compress to almost nothing) and keeps the
    correspondence exact.
    """
    cities = city_records()

    flat = array("f")
    covered = 0
    for city in cities:
        values = done.get(city.geonameid)
        if values is None:
            flat.extend([MISSING] * (MONTHS * STATS))
        else:
            flat.extend(values)
            covered += 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(NORMALS_PATH, "wb", compresslevel=9) as handle:
        handle.write(flat.tobytes())

    meta = {
        "version": 1,
        "n_cities": len(cities),
        "months": MONTHS,
        "stats": STATS,
        "stat_order": [
            "mean_temperature_c",
            "sd_temperature_c",
            "mean_humidity_pct",
            "sd_humidity_pct",
        ],
        "cities_covered": covered,
        "window_start": start,
        "window_end": end,
        "years": years,
        "min_samples": MIN_SAMPLES,
        # Guards against the city index being rebuilt without this artefact.
        # Positional joins are silent when they go wrong, so this has to fail loud.
        "geonameid_sha256": _geonameid_digest(cities),
        "built_at": datetime.now(UTC).isoformat(),
        "source": "Open-Meteo ERA5 reanalysis (archive-api.open-meteo.com)",
    }
    META_PATH.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    size_mb = NORMALS_PATH.stat().st_size / 1_000_000
    print(f"\nWrote {NORMALS_PATH} ({size_mb:.1f} MB)")
    print(f"  {covered:,}/{len(cities):,} cities covered")
    print(f"  meta -> {META_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cities",
        type=int,
        default=None,
        help="cap at the N most populous cities after --min-population is applied",
    )
    parser.add_argument(
        "--min-population",
        type=int,
        default=100_000,
        help=(
            "only cities at least this populous (default: 100000, ~6,200 cities). "
            "Pass 0 for the whole 33,957-city index."
        ),
    )
    parser.add_argument("--years", type=int, default=10, help="years of history (default: 10)")
    parser.add_argument("--batch", type=int, default=40, help="cities per request (default: 40)")
    parser.add_argument("--timeout", type=float, default=180.0, help="per-request timeout seconds")
    parser.add_argument(
        "--rate",
        type=float,
        default=60.0,
        help=(
            "location-years per minute to pace at (default: 60, i.e. ~3600/hour). "
            "The hourly quota binds before the minutely one; if you see repeated "
            "rate-limit messages, lower this rather than raising it."
        ),
    )
    parser.add_argument(
        "--write-only",
        action="store_true",
        help="skip fetching and just pack whatever the checkpoint already holds",
    )
    args = parser.parse_args()

    if args.write_only:
        cities = _select_cities(args.cities, args.min_population)
        end_year = datetime.now(UTC).year - 1
        start = f"{end_year - args.years + 1}-01-01"
        end = f"{end_year}-12-31"
        checkpoint = _load_checkpoint(_checkpoint_path(start, end))
        _write_artifact(cities, checkpoint, start, end, args.years)
        return

    build(
        args.cities,
        args.min_population,
        args.years,
        args.batch,
        args.timeout,
        args.rate,
    )


if __name__ == "__main__":
    main()
