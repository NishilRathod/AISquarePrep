"""Build the climate-normals artefact backing the global anomaly board.

An anomaly is only meaningful against a baseline, and the baseline has to be a
*number*, not a judgement: ranking cities against each other requires a
standardized anomaly (z-score), which needs a mean and a standard deviation for
that city, that variable, that time of year.

    z = (observed - mean) / sd

This script fetches daily reanalysis history from Open-Meteo (ERA5, free, no API
key) into the cache in :mod:`scripts.normals_cache`, then reduces it to, per city
and per calendar month, the mean and standard deviation of daily mean temperature
and daily mean relative humidity.

The fetch and the reduction are deliberately separate. The cache holds the daily
values the API returned, so --years and --statistic are pack-time choices that
can be changed by re-running with --write-only, at no quota cost. Only the fetch
spends quota, and it only ever asks for city-years the cache does not already
hold.

Why sigma is over *daily* values within a calendar month, pooled across years,
rather than over monthly means: the question the board asks is "how unusual is
today's reading", so the reference distribution has to be the one today's
reading is drawn from. Sigma of monthly means answers a different question ("how
unusual is this month overall") and is far too small, which would make almost
every day look extreme.

A few years rather than the WMO 30-year standard is a deliberate trade. The
default of 3 gives ~90 daily samples per city-month; 5 gives ~150 and puts the
error on sigma near 6%. The shorter window is cheaper to fetch but concentrates
the influence of any single extreme event inside it -- a heat dome in the
baseline inflates that city-month's sigma, which widens the band future
anomalies are measured against and pushes real events down the board.
--statistic median-mad exists for exactly that, and because the cache holds the
daily values, widening the window later fetches only the years it lacks rather
than starting over.

Rate limits are the binding constraint, and they are a *quota* rather than a
throughput cap: Open-Meteo weights its allowance by locations x days, not by
request count, so batching reduces transfer overhead but buys no extra quota.
There are separate minutely, hourly, and daily windows. All three were observed
in one afternoon, but the **daily** one is what actually ends a run -- "Daily
API request limit exceeded. Please try again tomorrow." The hourly limit costs
an hour; the daily limit costs the rest of the day, which is why a full-index
build is measured in weeks rather than hours and why the script no longer prints
a completion estimate derived from its pacing rate.

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

    # the default: cities over 100k across 3 years, a working global board
    python scripts/build_climate_normals.py

    # the whole index; expect weeks of daily quota, re-run to resume
    python scripts/build_climate_normals.py --min-population 0

    # repack what is already cached over a longer window, or a robust
    # statistic -- neither spends any quota
    python scripts/build_climate_normals.py --write-only --years 5
    python scripts/build_climate_normals.py --write-only --statistic median-mad

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
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.city_index import CityRecord, city_records  # noqa: E402
from scripts.normals_cache import (  # noqa: E402
    CACHE_PATH,
    MIN_SAMPLES,
    MISSING,
    MONTHS,
    STATS,
    append_year,
    cached_years,
    compose,
    load_cache,
    split_response_by_year,
)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

DATA_DIR = Path(__file__).resolve().parent.parent / "app" / "data"
NORMALS_PATH = DATA_DIR / "climate_normals.bin.gz"
META_PATH = DATA_DIR / "climate_normals.meta.json"


# Coordinates go in the query string, and the server rejects a URI over 8 KB with
# a 414 -- measured: 500 cities is 8,291 characters and fails, 250 is 4,219 and
# does not. Splitting below this is far better than discovering it as a dropped
# batch, since a 414 costs the whole batch and looks like any other failure.
MAX_URL_CHARS = 7_000

# The server separately caps how much data one call may *ask for*, and that
# limit is invisible in the URL: 200 cities x 5 years is ~4,200 characters --
# nowhere near MAX_URL_CHARS -- and is still refused with 400 "Your API call
# requests too much data. Please reduce the number of variables, locations
# and/or weather models." Measured on the archive endpoint, 125 cities x 1,826
# days (228,250 location-days) is accepted and 200 x 1,826 (365,200) is not.
#
# Sitting well under the measured boundary costs almost nothing, because the
# quota is weighted by locations x days rather than by request count: splitting
# one oversized call into two spends the same allowance and just adds a little
# transfer overhead. Undershooting is therefore the cheap error and overshooting
# loses the whole batch, so take the conservative number.
MAX_LOCATION_DAYS = 200_000

class RateLimited(Exception):
    """Open-Meteo returned 429; the caller should back off and retry."""


class DailyQuotaExhausted(Exception):
    """The day's allowance is gone, so nothing will succeed until it resets.

    Distinguished from :class:`RateLimited` because the right response is the
    opposite one. A minutely or hourly refusal is worth waiting out inside the
    run. A daily refusal is not: every remaining chunk would take six attempts
    and up to ten minutes of backoff to arrive at the same answer, turning a
    finished run into hours of grinding. Ending promptly gets the artefact
    packed and lets the next run resume from the cache.
    """


def is_daily_exhaustion(reason: str | None) -> bool:
    """Whether a 429's reason names the daily window rather than a shorter one."""
    return bool(reason) and "daily" in reason.lower()


class ArchiveError(Exception):
    """A non-429 HTTP error, carrying the reason Open-Meteo put in the body."""

    def __init__(self, code: int, reason: str | None):
        self.code = code
        self.reason = reason
        super().__init__(f"HTTP {code}: {reason or 'no reason given'}")


def _fetch(url: str, *, timeout: float) -> object:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            # The reason distinguishes the minutely, hourly, and daily windows,
            # which need opposite responses -- wait it out, or stop for today.
            raise RateLimited(_reason_of(exc)) from exc
        raise ArchiveError(exc.code, _reason_of(exc)) from exc


def _reason_of(exc: urllib.error.HTTPError) -> str | None:
    """The explanation Open-Meteo put in the body.

    urllib puts none of it in the exception's ``str()``, so a bare HTTPError
    reads as "400: Bad Request" and says nothing about which parameter the
    server objected to, or which of the three rate-limit windows was hit.
    """
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except ValueError:
        return body[:200]
    reason = parsed.get("reason") if isinstance(parsed, dict) else None
    return reason or body[:200]


def _batch_url(batch: list[CityRecord], start: str, end: str) -> str:
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
    return f"{ARCHIVE_URL}?{query}"


def split_to_url_limit(
    batch: list[CityRecord], start: str, end: str
) -> list[list[CityRecord]]:
    """Break a batch into chunks whose URLs the server will accept.

    Coordinate strings vary in length -- a negative three-digit longitude is
    nearly twice a short positive one -- so a fixed city count is not a safe
    proxy for URL size. Measure the real URL and halve until it fits.
    """
    if not batch:
        return []
    if len(_batch_url(batch, start, end)) <= MAX_URL_CHARS or len(batch) == 1:
        return [batch]

    midpoint = len(batch) // 2
    return split_to_url_limit(batch[:midpoint], start, end) + split_to_url_limit(
        batch[midpoint:], start, end
    )


def _window_days(start: str, end: str) -> int:
    """Days the request covers, inclusive of both endpoints."""
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    return (last - first).days + 1


def split_to_limits(batch: list[CityRecord], start: str, end: str) -> list[list[CityRecord]]:
    """Break a batch into chunks the server will accept, on *both* limits.

    Two independent constraints, and neither implies the other. URL length
    binds on short windows with many cities; the data budget binds on long
    windows, where a modest, short URL can still ask for more than the server
    will assemble. Checking only the first is how a 200-city batch over five
    years -- 4 KB of URL, comfortably legal -- got refused every time.

    The data budget is applied first, by count: with the window fixed, cities
    per request is just a division, so there is no need to search for it. The
    URL splitter then runs over each chunk, since coordinate strings vary in
    length and only measurement settles that one.
    """
    if not batch:
        return []

    days = max(_window_days(start, end), 1)
    # At least one city per request even for a window so long that a single
    # location exceeds the budget -- dropping it would silently lose coverage,
    # and a request that large is the server's call to refuse, not ours.
    per_request = max(MAX_LOCATION_DAYS // days, 1)

    chunks: list[list[CityRecord]] = []
    for offset in range(0, len(batch), per_request):
        chunks.extend(split_to_url_limit(batch[offset : offset + per_request], start, end))
    return chunks


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
    url = _batch_url(batch, start, end)

    delay = 60.0
    throttled = False
    for attempt in range(6):
        try:
            payload = _fetch(url, timeout=timeout)
        except RateLimited as exc:
            reason = str(exc) or None
            if is_daily_exhaustion(reason):
                # Retrying costs six attempts and ten minutes of backoff per
                # remaining chunk to learn what this one already told us.
                raise DailyQuotaExhausted(reason) from exc
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
        except (ArchiveError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
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


def group_by_missing_years(
    cities: list[CityRecord], cache: dict[int, dict[int, str]], years: list[int]
) -> dict[tuple[int, ...], list[CityRecord]]:
    """Bucket cities by which years they still lack.

    A fresh run is one bucket wanting the whole window, and a widened window is
    also one bucket, wanting only the years added. Grouping keeps the request
    shape uniform in both cases while letting a city interrupted mid-window keep
    the years it already received instead of starting over.
    """
    groups: dict[tuple[int, ...], list[CityRecord]] = {}
    for city in cities:
        missing = tuple(sorted(set(years) - cached_years(cache, city.geonameid)))
        if missing:
            groups.setdefault(missing, []).append(city)
    return groups


def build(
    city_limit: int | None,
    min_population: int,
    years_wanted: int,
    batch_size: int,
    timeout: float,
    rate: float,
    statistic: str,
) -> None:
    cities = _select_cities(city_limit, min_population)

    end_year = datetime.now(UTC).year - 1
    years = list(range(end_year - years_wanted + 1, end_year + 1))

    cache = load_cache(CACHE_PATH)
    groups = group_by_missing_years(cities, cache, years)
    pending = sum(len(group) for group in groups.values())

    pacer = Pacer(rate)

    print(f"cities={len(cities):,} window={years[0]}..{years[-1]} statistic={statistic}")
    print(f"already cached={len(cities) - pending:,} remaining={pending:,}")
    if pending:
        print(f"starting at {rate:.0f} location-years/min, adapting from there")
        # No completion estimate. Open-Meteo's *daily* quota binds long before
        # the pacing rate does -- a run ends because the API says "try again
        # tomorrow", not because of how fast it asked -- so any hours figure
        # derived from the rate alone understates the wall clock by more than an
        # order of magnitude. It used to promise 46 hours for a job measured in
        # weeks.
        print("runs until the daily quota is exhausted; re-run to resume\n")

    started = time.time()
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    done_count = 0
    cached_this_run = 0
    exhausted = False
    with CACHE_PATH.open("a", encoding="utf-8") as handle:
        for missing, group_cities in groups.items():
            if exhausted:
                break
            start = f"{missing[0]}-01-01"
            end = f"{missing[-1]}-12-31"
            wanted = set(missing)

            chunks: list[list[CityRecord]] = []
            for offset in range(0, len(group_cities), batch_size):
                chunks.extend(
                    split_to_limits(group_cities[offset : offset + batch_size], start, end)
                )

            for chunk_index, batch in enumerate(chunks):
                batch_started = time.time()
                try:
                    results, throttled = _fetch_batch(batch, start, end, timeout=timeout)
                except DailyQuotaExhausted as exc:
                    print(f"\n  daily quota exhausted: {exc}", flush=True)
                    print("  stopping here; re-run after it resets to continue", flush=True)
                    exhausted = True
                    break

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
                        f"    length mismatch: sent {len(batch)}, "
                        f"got {len(results)} -- batch dropped",
                        flush=True,
                    )
                    continue

                for city, result in zip(batch, results, strict=True):
                    daily = result.get("daily") if isinstance(result, dict) else None
                    if not daily:
                        continue
                    for year, blob in split_response_by_year(daily).items():
                        if year in wanted:
                            append_year(handle, city.geonameid, year, blob)
                            cache.setdefault(city.geonameid, {})[year] = blob
                            cached_this_run += 1
                handle.flush()

                done_count += len(batch)
                elapsed = time.time() - started
                observed = done_count / elapsed if elapsed else 0
                print(
                    f"  {done_count:>6,}/{pending:,} cities  "
                    f"{observed * 60:>5.0f} cities/min  "
                    f"pace {pacer.rate:>4.0f} loc-yr/min",
                    flush=True,
                )

                if chunk_index + 1 < len(chunks):
                    remaining = pacer.seconds_between(len(batch) * len(missing)) - (
                        time.time() - batch_started
                    )
                    if remaining > 0:
                        time.sleep(remaining)

    print(f"\nsettled pacing rate: {pacer.rate:.0f} location-years/min")
    print(f"  pass --rate {pacer.rate:.0f} on the next run to start there")

    if not cached_this_run:
        # Repacking from an unchanged cache cannot improve the artefact and can
        # destroy it: a run that gets no quota at all would overwrite a good
        # baseline with all-NaN and blank the board until the next successful
        # fetch. Since this loop is meant to be re-run unattended, that would
        # happen every time the daily allowance is already spent.
        print("\nno new city-years cached; leaving the artefact as it is")
        print("  pack explicitly with --write-only if that is what you want")
        return

    print(f"\ncached {cached_this_run:,} new city-years")
    _write_artifact(cities, cache, years, statistic)


def _write_artifact(
    _selected: list[CityRecord],
    cache: dict[int, dict[int, str]],
    years: list[int],
    statistic: str,
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
    start = f"{years[0]}-01-01"
    end = f"{years[-1]}-12-31"

    flat = array("f")
    covered = 0
    for city in cities:
        year_blobs = cache.get(city.geonameid)
        if not year_blobs:
            flat.extend([MISSING] * (MONTHS * STATS))
            continue
        # Composed here rather than at fetch time: the cache holds the daily
        # values, so the window and the statistic are chosen at pack time and
        # can be changed by repacking, without spending any quota.
        values = compose(year_blobs, years, statistic)
        flat.extend(values)
        if not all(math.isnan(value) for value in values):
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
        "years": len(years),
        # Which reducer produced these numbers. The cache holds the daily
        # values either way, so this is a property of the pack, not the fetch.
        "statistic": statistic,
        "cache_version": 2,
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
    parser.add_argument(
        "--years",
        type=int,
        default=3,
        help=(
            "years of history (default: 3). Cheap to change: the cache holds the "
            "daily values, so a longer window fetches only the years it does not "
            "already have and a shorter one costs nothing at all."
        ),
    )
    parser.add_argument(
        "--statistic",
        choices=("mean-sd", "median-mad"),
        default="mean-sd",
        help=(
            "how each city-month is summarised (default: mean-sd, the plain "
            "arithmetic baseline). median-mad resists a single extreme event in "
            "the window inflating sigma, which is how a past heat dome ends up "
            "suppressing a future real anomaly. Switching is a --write-only "
            "repack and costs no quota."
        ),
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=200,
        help=(
            "upper bound on cities per request (default: 200). Split further as "
            "needed to stay under both server limits: the 8 KB URI cap and the "
            "per-call data budget, which at a 5-year window allows ~109 cities. "
            "Raising this past what the window permits therefore changes nothing."
        ),
    )
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
        help=(
            "skip fetching and just pack whatever the cache already holds. This is "
            "how --years and --statistic are changed after the fact: both are pack-"
            "time choices over cached daily values, so neither spends any quota."
        ),
    )
    args = parser.parse_args()

    if args.write_only:
        cities = _select_cities(args.cities, args.min_population)
        end_year = datetime.now(UTC).year - 1
        years = list(range(end_year - args.years + 1, end_year + 1))
        _write_artifact(cities, load_cache(CACHE_PATH), years, args.statistic)
        return

    build(
        args.cities,
        args.min_population,
        args.years,
        args.batch,
        args.timeout,
        args.rate,
        args.statistic,
    )


if __name__ == "__main__":
    main()
