"""Scoring and ranking for the global anomaly board.

Everything here is arithmetic and deterministic. The board is only credible if a
reader can recompute any row from the numbers shown in it, so no judgement,
weighting, or model output enters at this layer -- interpretation happens later,
on top of a ranking that already stands on its own.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.models.anomaly import AnomalyRow, Driver
from app.services.city_index import CityRecord
from app.services.normals import Normals

# Below this, a standard deviation is small enough that dividing by it turns
# ordinary measurement noise into a headline anomaly. Guard rather than divide.
MIN_SD = 0.1

# A departure this large is almost never weather. Real records sit near 4-5
# sigma; beyond this the likeliest explanation is a station or grid-cell fault,
# so the row is dropped before it can take the top of the board.
MAX_PLAUSIBLE_Z = 8.0


@dataclass(frozen=True, slots=True)
class Observation:
    """A current reading for one city, as returned by the bulk sweep."""

    row_index: int
    temperature_c: float
    humidity_pct: float
    month: int


def z_score(observed: float, mean: float, sd: float) -> float:
    """Standardized anomaly: how many standard deviations from normal.

    This is what makes anomalies comparable between cities. Eight degrees above
    normal in Reykjavik and eight degrees above normal in Delhi are the same
    departure but not the same event -- dividing by each city's own sigma is
    what encodes that.
    """
    if sd < MIN_SD or math.isnan(sd) or math.isnan(mean) or math.isnan(observed):
        return 0.0
    return (observed - mean) / sd


def score_city(city: CityRecord, observation: Observation, normals: Normals) -> AnomalyRow | None:
    """Score one city on both variables, or ``None`` if neither is usable.

    Both z-scores are carried on every row so either board can be audited from
    the row alone. Which one is *headline* -- the ``driver`` / ``z_score`` /
    ``direction`` fields -- is decided later by :func:`rank_by`, because the same
    city can appear on the temperature board and the humidity board with a
    different headline on each.
    """
    z_temperature = z_score(
        observation.temperature_c, normals.mean_temperature_c, normals.sd_temperature_c
    )
    z_humidity = z_score(
        observation.humidity_pct, normals.mean_humidity_pct, normals.sd_humidity_pct
    )

    # A city needs at least one usable variable to be worth carrying. Each board
    # applies its own filter afterwards, so a broken humidity sensor cannot
    # suppress a genuine temperature anomaly at the same city.
    if not (_usable(z_temperature) or _usable(z_humidity)):
        return None

    return AnomalyRow(
        rank=0,  # assigned per board by rank_by
        city=city.name,
        state=city.state,
        country=city.country,
        latitude=city.latitude,
        longitude=city.longitude,
        temperature_c=round(observation.temperature_c, 1),
        humidity_pct=round(observation.humidity_pct),
        normal_temperature_c=round(normals.mean_temperature_c, 1),
        normal_humidity_pct=round(normals.mean_humidity_pct, 1),
        sd_temperature_c=round(normals.sd_temperature_c, 2),
        sd_humidity_pct=round(normals.sd_humidity_pct, 2),
        z_temperature=round(z_temperature, 2),
        z_humidity=round(z_humidity, 2),
        # Placeholders; rank_by rewrites these for the board it builds.
        z_score=0.0,
        driver="temperature",
        direction="above",
    )


def _usable(z: float) -> bool:
    """A departure worth ranking: non-zero, and not so large it is likely a fault.

    Real records sit near 4-5 sigma. Beyond ``MAX_PLAUSIBLE_Z`` the likeliest
    explanation is a station or grid-cell fault, and letting it through would put
    a broken sensor at the top of the board.
    """
    return z != 0.0 and abs(z) <= MAX_PLAUSIBLE_Z


def rank_by(
    rows: list[tuple[AnomalyRow, CityRecord]], driver: Driver, limit: int
) -> list[AnomalyRow]:
    """Build one board, ranked on ``driver`` alone.

    Ranking each variable separately rather than on the larger of the two is what
    keeps both boards populated. A single combined ranking lets whichever
    variable happens to be having a big day sweep the whole visible top ten --
    which is not a bug in the maths, but does hide the other half of the weather.

    Ties break on population then geonameid so that two sweeps over identical
    data produce an identical board, instead of reshuffling between refreshes.
    """
    scored: list[tuple[float, AnomalyRow, CityRecord]] = []
    for row, city in rows:
        z = row.z_temperature if driver == "temperature" else row.z_humidity
        if _usable(z):
            scored.append((z, row, city))

    scored.sort(key=lambda item: (-abs(item[0]), -item[2].population, item[2].geonameid))

    return [
        row.model_copy(
            update={
                "rank": index + 1,
                "z_score": round(abs(z), 2),
                "driver": driver,
                "direction": "above" if z > 0 else "below",
            }
        )
        for index, (z, row, _) in enumerate(scored[:limit])
    ]
