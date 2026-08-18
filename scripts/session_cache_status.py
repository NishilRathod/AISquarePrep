"""Tell a new session what the climate cache already holds.

The pipeline hook next to this one does the fetching, and it has to be async
because a baseline backfill runs for half an hour -- but an async hook's stdout
never reaches the session. So the work happened invisibly every morning and the
state had to be asked for out loud instead. This hook is the visible half: no
network, no lock, just what is on disk, printed synchronously so it lands in
context before the first prompt.

It deliberately does not probe .normals_fetch.lock. Probing means holding it,
however briefly, and losing that race with the pipeline hook would cost a whole
day's quota -- the pipeline would find the lock taken and skip the day. Liveness
is not worth that. The log says what the last run did, and the pipeline hook
fires on this same event anyway.

It also never opens the 22 MB daily cache. Everything here comes from the packed
artefact's metadata and the running year's file, which is why this can stay
synchronous while the fetch cannot.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.recent import RECENT_PATH, parse_runs  # noqa: E402

META_PATH = ROOT / "app" / "data" / "climate_normals.meta.json"
LOG_PATH = RECENT_PATH.parent / ".normals_fetch.log"

# Enough of the last run to show how it ended -- a quota message, a traceback, a
# final count -- without pasting a half-hour of progress lines into the context.
LOG_TAIL_LINES = 8


def _baseline() -> str:
    if not META_PATH.exists():
        return "baselines: no artefact built yet"
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    built = str(meta.get("built_at", "?"))[:19].replace("T", " ")
    return (
        f"baselines: {meta.get('cities_covered', 0):,} of {meta.get('n_cities', 0):,} "
        f"indexed cities, {meta.get('years')}-year window ending "
        f"{meta.get('window_end')}, packed {built}Z"
    )


def _recent(target_end) -> tuple[str, int]:
    """The running year's file, summarised by the one thing the board needs.

    Currency, not size: a city with months of history but a stale tail scores
    against an old day while looking exactly as fresh as the rest of the board.
    """
    if not RECENT_PATH.exists():
        return "recent readings: none cached yet", 0
    runs = parse_runs(RECENT_PATH.read_text(encoding="utf-8").splitlines())
    if not runs:
        return "recent readings: file present but empty", 0
    current = sum(1 for run in runs.values() if run.end >= target_end)
    newest = max(run.end for run in runs.values())
    return (
        f"recent readings: {len(runs):,} cities, {current:,} current through "
        f"{target_end}, newest day on file {newest}"
    ), current


def _last_run() -> list[str]:
    if not LOG_PATH.exists():
        return ["no run has been logged yet"]
    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("=== session fetch started")]
    if not starts:
        return ["no run has been logged yet"]
    block = [line.rstrip() for line in lines[starts[-1] :] if line.strip()]
    if len(block) <= LOG_TAIL_LINES + 1:
        return block
    return [
        block[0],
        f"    ... {len(block) - LOG_TAIL_LINES - 1} lines ...",
        *block[-LOG_TAIL_LINES:],
    ]


def main() -> None:
    # Yesterday, for the same reason the fetcher stops there: today's daily mean
    # is a partial day and cannot be compared against a baseline built from
    # whole ones.
    target_end = datetime.now(UTC).date() - timedelta(days=1)
    baseline = _baseline()
    recent, current = _recent(target_end)

    context = "\n".join(
        [
            "Climate cache at session start (read from disk, no network):",
            f"  {baseline}",
            f"  {recent}",
            "",
            "The SessionStart hook has ALREADY launched scripts/session_fetch_normals.py",
            "in the background -- repack, then the recent top-up, then the baseline",
            "backfill until Open-Meteo's daily quota is spent. This happens on every",
            "session without being asked, and no-ops when another session holds the lock.",
            "Do not start a fetch, and do not offer to; the user should never have to ask",
            f"for one. To check on it, read {LOG_PATH.name} rather than re-running anything.",
            "",
            "Last logged run:",
            *(f"  {line}" for line in _last_run()),
        ]
    )

    print(
        json.dumps(
            {
                "systemMessage": (
                    f"Climate cache: {current:,} cities current through {target_end}. "
                    "Today's fetch is already running from the session hook."
                ),
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                },
            }
        )
    )


if __name__ == "__main__":
    main()
