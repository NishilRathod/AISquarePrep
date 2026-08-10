"""Read access to the climate-normals artefact built by scripts/build_climate_normals.py.

The artefact is a flat float32 array laid out as ``[city][month][stat]``, joined
back to the city index *by position*. Positional joins are fast and compact but
fail silently when the two sides drift, so the meta file carries a digest of the
geonameids it was built against and :func:`load_normals` refuses to serve a
mismatch.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from array import array
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.services.city_index import city_records

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
NORMALS_PATH = DATA_DIR / "climate_normals.bin.gz"
META_PATH = DATA_DIR / "climate_normals.meta.json"

MONTHS = 12
STATS = 4


class NormalsUnavailableError(RuntimeError):
    """The artefact is missing, malformed, or does not match the city index."""


@dataclass(frozen=True, slots=True)
class Normals:
    """Baseline for one city in one calendar month."""

    mean_temperature_c: float
    sd_temperature_c: float
    mean_humidity_pct: float
    sd_humidity_pct: float


@dataclass(frozen=True, slots=True)
class NormalsStore:
    values: array
    n_cities: int
    window_start: str
    window_end: str
    cities_covered: int

    def get(self, row_index: int, month: int) -> Normals | None:
        """Baseline for a city index position and a 1-based calendar month.

        ``None`` means this city-month has no usable baseline -- either it was
        never fetched, or it had too few samples for a trustworthy sigma. The
        caller must skip it rather than substitute a default: a fabricated
        baseline produces a confident, entirely fictitious anomaly.
        """
        if not 0 <= row_index < self.n_cities or not 1 <= month <= MONTHS:
            return None

        offset = (row_index * MONTHS + (month - 1)) * STATS
        mean_t, sd_t, mean_h, sd_h = self.values[offset : offset + STATS]
        if math.isnan(mean_t) or math.isnan(sd_t):
            return None
        return Normals(
            mean_temperature_c=mean_t,
            sd_temperature_c=sd_t,
            mean_humidity_pct=mean_h,
            sd_humidity_pct=sd_h,
        )


def geonameid_digest(geonameids: list[int]) -> str:
    joined = ",".join(str(value) for value in geonameids)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def load_normals() -> NormalsStore:
    if not NORMALS_PATH.exists() or not META_PATH.exists():
        raise NormalsUnavailableError(
            f"climate normals artefact missing at {NORMALS_PATH}; "
            "run scripts/build_climate_normals.py"
        )

    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    n_cities = int(meta["n_cities"])

    records = city_records()
    if len(records) < n_cities:
        raise NormalsUnavailableError(
            f"normals cover {n_cities:,} cities but the index only has {len(records):,}"
        )

    expected = geonameid_digest([record.geonameid for record in records[:n_cities]])
    if expected != meta.get("geonameid_sha256"):
        raise NormalsUnavailableError(
            "climate normals do not match the current city index -- the index was "
            "rebuilt without rebuilding the normals. Every anomaly would be "
            "attributed to the wrong city. Re-run scripts/build_climate_normals.py."
        )

    values = array("f")
    with gzip.open(NORMALS_PATH, "rb") as handle:
        values.frombytes(handle.read())

    expected_len = n_cities * MONTHS * STATS
    if len(values) != expected_len:
        raise NormalsUnavailableError(
            f"normals artefact holds {len(values):,} floats, expected {expected_len:,}"
        )

    return NormalsStore(
        values=values,
        n_cities=n_cities,
        window_start=str(meta.get("window_start", "")),
        window_end=str(meta.get("window_end", "")),
        cities_covered=int(meta.get("cities_covered", 0)),
    )
