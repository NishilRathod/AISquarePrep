"""Run the daily climate pipeline when a Claude Code session opens.

Wired to the SessionStart hook in .claude/settings.json. Three steps, in an
order chosen by what happens when the quota runs out:

1. Repack the artefact from the cache already on disk. Free -- no request --
   and it widens coverage to every city fetched since the last repack, which
   decides what step 2 is willing to spend quota on.
2. Top up the running year's daily cache so the anomaly board has a recent
   reading for every city with a baseline. Cheap: one day for every city in the
   index is about six cities' worth of the baseline fetch.
3. Resume the baseline backfill, which runs until Open-Meteo says "try again
   tomorrow".

Step 3 is last because it is designed to consume the entire remaining
allowance; anything queued behind it would never run. Step 2 is what the board
actually reads, so it must not be the thing that gets starved.

Three guards, because a hook fires on every session and an unattended fetch
that misbehaves is worse than no fetch at all:

* Done: once every selected city holds every year in the window, the backfill
  is skipped -- but the top-up still runs, because the board goes stale whether
  or not the baselines are finished.
* Concurrent: an exclusive lock on a file held for the pipeline's lifetime, so
  opening a second session does not put two runs on the same append-mode cache
  competing for the same quota. The OS releases it if we die, which a PID file
  cannot promise.
* Spent: nothing here needs to detect an exhausted quota. Each step discovers
  it in one request, prints, and exits 0 -- and each leaves its own output file
  alone when a run caches nothing.

Deliberately not detached. The daily allowance is consumed in roughly half an
hour of wall time, so a process living as long as the session gets the whole
day's quota anyway, and dies visibly with the session instead of lingering as
an orphan nobody knows to kill.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.city_index import city_records  # noqa: E402
from scripts.normals_cache import CACHE_PATH, cached_years, load_cache  # noqa: E402

# Matches the sweep already under way: cities over 100k, a three-year window,
# starting at the pace the last run settled on rather than re-climbing from the
# default and paying for the same throttle twice.
MIN_POPULATION = 100_000
YEARS = 3
RATE = 44

LOCK_PATH = CACHE_PATH.parent / ".normals_fetch.lock"
LOG_PATH = CACHE_PATH.parent / ".normals_fetch.log"
RECENT_NAME = ".climate_recent.v1.jsonl"


def _emit(message: str, *, context: str | None = None) -> None:
    """Speak to the user, and to Claude, in the one JSON object stdout allows."""
    payload: dict[str, object] = {"systemMessage": message}
    if context is not None:
        payload["hookSpecificOutput"] = {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    print(json.dumps(payload))


def _acquire_lock(path: Path):
    """An exclusive lock held for as long as this process lives, or None.

    A lock file holding a PID would need liveness checks that are awkward and
    unsafe on Windows -- os.kill there terminates rather than probes. Letting
    the OS own the lock makes a crashed run self-healing instead.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _remaining() -> tuple[int, int]:
    """How many selected cities still lack a year, and how many were selected."""
    cities = [city for city in city_records() if city.population >= MIN_POPULATION]
    cache = load_cache(CACHE_PATH)

    end_year = datetime.now(UTC).year - 1
    wanted = set(range(end_year - YEARS + 1, end_year + 1))

    missing = sum(1 for city in cities if wanted - cached_years(cache, city.geonameid))
    return missing, len(cities)


def _step(log, label: str, argv: list[str]) -> None:
    log.write(f"\n--- {label} ---\n")
    log.flush()
    subprocess.run(
        [sys.executable, *argv],
        cwd=str(ROOT),
        stdout=log,
        stderr=subprocess.STDOUT,
        check=False,
    )


def main() -> None:
    missing, total = _remaining()

    lock = _acquire_lock(LOCK_PATH)
    if lock is None:
        _emit("Climate normals: a fetch is already running in another session.")
        return

    if missing:
        _emit(
            f"Climate normals: fetching -- {total - missing:,}/{total:,} cities cached, "
            f"{missing:,} to go, plus the daily top-up that keeps the anomaly board "
            f"current. Progress in {LOG_PATH.name}.",
            context=(
                f"The SessionStart hook started the daily climate pipeline "
                f"({total - missing:,}/{total:,} cities have a baseline). It repacks the "
                f"artefact, tops up {RECENT_NAME} so the board can be scored offline, then "
                f"resumes the baseline backfill until Open-Meteo's daily quota is spent. "
                f"Output goes to {LOG_PATH}. Do not start a second one."
            ),
        )
    else:
        _emit(
            f"Climate normals: baselines complete ({total:,} cities). Running the daily "
            f"top-up only, which keeps the anomaly board current."
        )

    # Output cannot share this stdout -- the hook contract wants a single JSON
    # object -- so it goes to a log the user can tail.
    with LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"\n=== session fetch started {datetime.now(UTC).isoformat()} ===\n")
        log.flush()

        # Repack first, at no quota cost. Coverage is what decides which cities
        # the top-up bothers with, so packing yesterday's newly cached cities
        # before asking for readings is a free way to widen the board.
        _step(
            log,
            "repack artefact from cache",
            [
                str(ROOT / "scripts" / "build_climate_normals.py"),
                "--write-only",
                "--years",
                str(YEARS),
            ],
        )

        # Then the top-up, before the backfill rather than after it. The backfill
        # deliberately runs until the daily allowance is gone, so anything queued
        # behind it would never run at all -- and the top-up is what the board
        # actually reads.
        _step(log, "top up recent readings", [str(ROOT / "scripts" / "fetch_recent_daily.py")])

        if missing:
            _step(
                log,
                "baseline backfill",
                [
                    str(ROOT / "scripts" / "build_climate_normals.py"),
                    "--years",
                    str(YEARS),
                    "--rate",
                    str(RATE),
                ],
            )

    lock.close()


if __name__ == "__main__":
    main()
