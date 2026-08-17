"""Shared Open-Meteo transport: limits, retry policy, and pacing.

Two fetchers now talk to Open-Meteo under the same quota -- the baseline builder
(three years of archive per city) and the daily top-up that keeps the board
current -- and every hard-won constant here was measured against the live API
rather than read from documentation. Duplicating them would mean one copy
drifting, and the failure that follows is a whole batch refused with a message
that looks like any other error.

URL construction stays with each caller, since the archive and forecast
endpoints take different parameters. Everything that decides *how much* to ask
for, *when* to retry, and *how fast* to go is here.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

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


class ArchiveError(Exception):
    """A non-429 HTTP error, carrying the reason Open-Meteo put in the body."""

    def __init__(self, code: int, reason: str | None):
        self.code = code
        self.reason = reason
        super().__init__(f"HTTP {code}: {reason or 'no reason given'}")


def is_daily_exhaustion(reason: str | None) -> bool:
    """Whether a 429's reason names the daily window rather than a shorter one."""
    return bool(reason) and "daily" in reason.lower()


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


def fetch_json(url: str, *, timeout: float) -> object:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            # The reason distinguishes the minutely, hourly, and daily windows,
            # which need opposite responses -- wait it out, or stop for today.
            raise RateLimited(_reason_of(exc)) from exc
        raise ArchiveError(exc.code, _reason_of(exc)) from exc


def split_to_url_limit[T](
    batch: Sequence[T], make_url: Callable[[Sequence[T]], str]
) -> list[list[T]]:
    """Break a batch into chunks whose URLs the server will accept.

    Coordinate strings vary in length -- a negative three-digit longitude is
    nearly twice a short positive one -- so a fixed city count is not a safe
    proxy for URL size. Measure the real URL and halve until it fits.
    """
    if not batch:
        return []
    if len(make_url(batch)) <= MAX_URL_CHARS or len(batch) == 1:
        return [list(batch)]

    midpoint = len(batch) // 2
    return split_to_url_limit(batch[:midpoint], make_url) + split_to_url_limit(
        batch[midpoint:], make_url
    )


def split_to_limits[T](
    batch: Sequence[T], days: int, make_url: Callable[[Sequence[T]], str]
) -> list[list[T]]:
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

    days = max(days, 1)
    # At least one city per request even for a window so long that a single
    # location exceeds the budget -- dropping it would silently lose coverage,
    # and a request that large is the server's call to refuse, not ours.
    per_request = max(MAX_LOCATION_DAYS // days, 1)

    chunks: list[list[T]] = []
    for offset in range(0, len(batch), per_request):
        chunks.extend(split_to_url_limit(batch[offset : offset + per_request], make_url))
    return chunks


def fetch_batch[T](
    batch: Sequence[T], make_url: Callable[[Sequence[T]], str], *, timeout: float
) -> tuple[list[dict] | None, bool]:
    """One request covering every city in ``batch``.

    Returns ``(results, throttled)``. ``results`` is ``None`` for a non-retryable
    failure, in which case the caller drops the batch rather than aborting the
    run -- a handful of missing cities is a far better outcome than losing hours
    of accumulated progress. ``throttled`` tells the pacer it overshot, whether
    or not the batch eventually succeeded.
    """
    url = make_url(batch)

    delay = 60.0
    throttled = False
    for attempt in range(6):
        try:
            payload = fetch_json(url, timeout=timeout)
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
