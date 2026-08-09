"""Build the static city index used by ``GET /cities/search``.

Downloads the GeoNames ``cities15000`` dump (every city with population > 15,000)
and trims it to the four fields the autocomplete actually needs. The raw dump is
~12 MB, almost entirely because of the ``alternatenames`` column; dropping it
takes the vendored artefact down to roughly 1 MB.

Run once and commit the output:

    python scripts/build_city_index.py

Data is licensed CC BY 4.0 by GeoNames -- see app/data/ATTRIBUTION.md.
"""

from __future__ import annotations

import io
import json
import urllib.request
import zipfile
from pathlib import Path

CITIES_URL = "https://download.geonames.org/export/dump/cities15000.zip"
ADMIN1_URL = "https://download.geonames.org/export/dump/admin1CodesASCII.txt"

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "app" / "data" / "cities15000.json"

# Column offsets in the GeoNames cities dump (tab-separated, no header row).
COL_NAME = 1
COL_COUNTRY = 8
COL_ADMIN1 = 10
COL_POPULATION = 14


def _download(url: str) -> bytes:
    print(f"Downloading {url} ...")
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - fixed https URL
        payload: bytes = response.read()
    print(f"  {len(payload):,} bytes")
    return payload


def _load_admin1_names() -> dict[str, str]:
    """Map ``"IN.19"`` -> ``"Karnataka"`` so suggestions can show a real region name."""
    raw = _download(ADMIN1_URL).decode("utf-8")
    names: dict[str, str] = {}
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2:
            names[fields[0]] = fields[1]
    return names


def _load_cities(admin1_names: dict[str, str]) -> list[list[object]]:
    archive = zipfile.ZipFile(io.BytesIO(_download(CITIES_URL)))
    raw = archive.read("cities15000.txt").decode("utf-8")

    # Collapse duplicates (GeoNames carries several entries for some names within
    # one region); keep whichever has the larger population.
    best: dict[tuple[str, str, str], list[object]] = {}
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) <= COL_POPULATION:
            continue

        name = fields[COL_NAME].strip()
        country = fields[COL_COUNTRY].strip()
        if not name or not country:
            continue

        admin1 = fields[COL_ADMIN1].strip()
        state = admin1_names.get(f"{country}.{admin1}", "")
        try:
            population = int(fields[COL_POPULATION] or 0)
        except ValueError:
            population = 0

        key = (name.casefold(), state.casefold(), country)
        existing = best.get(key)
        if existing is None or population > int(existing[3]):  # type: ignore[arg-type]
            best[key] = [name, state, country, population]

    # Population-descending, so the search can rank by relevance without re-sorting
    # the whole index on every keystroke.
    return sorted(best.values(), key=lambda row: int(row[3]), reverse=True)  # type: ignore[arg-type]


def main() -> None:
    cities = _load_cities(_load_admin1_names())

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as handle:
        json.dump(cities, handle, ensure_ascii=False, separators=(",", ":"))

    size_mb = OUTPUT_PATH.stat().st_size / 1_000_000
    print(f"Wrote {len(cities):,} cities to {OUTPUT_PATH} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
