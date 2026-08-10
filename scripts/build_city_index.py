"""Build the static city index used by ``GET /cities/search`` and the anomaly sweep.

Downloads the GeoNames ``cities15000`` dump (every city with population > 15,000)
and trims it to the fields we actually need. The raw dump is ~12 MB, almost
entirely because of the ``alternatenames`` column; dropping it takes the vendored
artefact down to roughly 2 MB.

Four fields serve the autocomplete (name, state, country, population). Three more
serve the global anomaly board:

* ``geonameid`` is the stable join key between this index and the climate-normals
  artefact. City names repeat across countries and regions, so joining those two
  files on name would silently misattribute anomalies; an integer id cannot.
* ``latitude`` / ``longitude`` are what the bulk weather APIs are keyed by. Without
  them there is no way to ask for current conditions across the whole index.

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
COL_GEONAMEID = 0
COL_NAME = 1
COL_LATITUDE = 4
COL_LONGITUDE = 5
COL_COUNTRY = 8
COL_ADMIN1 = 10
COL_POPULATION = 14

# Index into the *output* row, which is not the same shape as the input line.
OUT_POPULATION = 4


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

        # A city with no usable coordinates cannot be swept for anomalies, and a
        # row without an id cannot be joined to the normals artefact. Both are
        # malformed rather than merely sparse, so drop them here instead of
        # carrying nulls through every downstream consumer.
        try:
            geonameid = int(fields[COL_GEONAMEID])
            latitude = round(float(fields[COL_LATITUDE]), 4)
            longitude = round(float(fields[COL_LONGITUDE]), 4)
        except ValueError:
            continue

        key = (name.casefold(), state.casefold(), country)
        existing = best.get(key)
        if existing is None or population > int(existing[OUT_POPULATION]):  # type: ignore[arg-type]
            best[key] = [geonameid, name, state, country, population, latitude, longitude]

    # Population-descending, so the search can rank by relevance without re-sorting
    # the whole index on every keystroke. The anomaly sweep also relies on this
    # ordering being stable: the normals artefact is written in index order.
    return sorted(
        best.values(),
        key=lambda row: (-int(row[OUT_POPULATION]), int(row[0])),  # type: ignore[arg-type]
    )


def main() -> None:
    cities = _load_cities(_load_admin1_names())

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as handle:
        json.dump(cities, handle, ensure_ascii=False, separators=(",", ":"))

    size_mb = OUTPUT_PATH.stat().st_size / 1_000_000
    print(f"Wrote {len(cities):,} cities to {OUTPUT_PATH} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
