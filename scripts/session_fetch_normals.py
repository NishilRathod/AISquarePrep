"""Resume the climate-normals fetch when a Claude Code session opens.

Wired to the SessionStart hook in .claude/settings.json. The full 6,226-city
sweep is bounded by Open-Meteo's daily quota, not by pace -- a run ends because
the API says "try again tomorrow" -- so it takes a week or more of separate
runs. Asking the user to remember to start each one is the part worth
automating.

Three guards, because a hook fires on every session and an unattended fetch
that misbehaves is worse than no fetch at all:

* Done: once every selected city holds every year in the window there is
  nothing to ask for, and this exits without spawning anything.
* Concurrent: an exclusive lock on a file held for the fetch's lifetime, so
  opening a second session does not put two runs on the same append-mode cache
  competing for the same quota. The OS releases it if we die, which a PID file
  cannot promise.
* Spent: nothing here needs to detect an exhausted quota. The fetch discovers
  it in one request, prints, and exits 0 -- and its own guard leaves the
  artefact alone when a run caches nothing.

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


def main() -> None:
    missing, total = _remaining()
    if not missing:
        _emit(
            f"Climate normals: complete -- all {total:,} cities cached over "
            f"{YEARS} years. The SessionStart hook can be removed from "
            ".claude/settings.json."
        )
        return

    lock = _acquire_lock(LOCK_PATH)
    if lock is None:
        _emit("Climate normals: a fetch is already running in another session.")
        return

    _emit(
        f"Climate normals: fetching -- {total - missing:,}/{total:,} cities cached, "
        f"{missing:,} to go. Runs until Open-Meteo's daily quota is spent; "
        f"progress in {LOG_PATH.name}.",
        context=(
            f"A climate-normals fetch was started automatically by the SessionStart "
            f"hook ({total - missing:,}/{total:,} cities already cached). It appends "
            f"to {CACHE_PATH.name} and stops on its own when the daily quota is "
            f"exhausted. Output goes to {LOG_PATH}. Do not start a second one."
        ),
    )

    # The fetch's own output cannot share this stdout -- the hook contract wants
    # a single JSON object -- so it goes to a log the user can tail.
    with LOG_PATH.open("a", encoding="utf-8") as log:
        log.write(f"\n=== session fetch started {datetime.now(UTC).isoformat()} ===\n")
        log.flush()
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "build_climate_normals.py"),
                "--years",
                str(YEARS),
                "--rate",
                str(RATE),
            ],
            cwd=str(ROOT),
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )

    lock.close()


if __name__ == "__main__":
    main()
