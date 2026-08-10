"""Scoring and ranking for the global anomaly board.

Everything here is arithmetic and deterministic. The board is only credible if a
reader can recompute any row from the numbers shown in it, so no judgement,
weighting, or model output enters at this layer -- interpretation happens later,
on top of a ranking that already stands on its own.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.models.anomaly import AnomalyRow
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
    """Score one city, or ``None`` if it should not appear on the board."""
    z_temperature = z_score(
        observation.temperature_c, normals.mean_temperature_c, normals.sd_temperature_c
    )
    z_humidity = z_score(
        observation.humidity_pct, normals.mean_humidity_pct, normals.sd_humidity_pct
    )

    # The board ranks on the strongest single departure, not the average of the
    # two. Averaging would let a perfectly normal humidity halve a genuine
    # temperature extreme, hiding exactly the events this exists to surface.
    if abs(z_temperature) >= abs(z_humidity):
        headline, driver = z_temperature, "temperature"
    else:
        headline, driver = z_humidity, "humidity"

    if headline == 0.0 or abs(headline) > MAX_PLAUSIBLE_Z:
        return None

    return AnomalyRow(
        rank=0,  # assigned by rank_rows once the field is sorted
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
        z_score=round(abs(headline), 2),
        driver=driver,
        direction="above" if headline > 0 else "below",
    )


def rank_rows(rows: list[tuple[AnomalyRow, CityRecord]], limit: int) -> list[AnomalyRow]:
    """Order by anomaly magnitude and assign ranks.

    Ties break on population then geonameid so that two sweeps over identical
    data always produce an identical board -- otherwise the leaderboard would
    reshuffle between refreshes for no visible reason.
    """
    ordered = sorted(
        rows,
        key=lambda pair: (-pair[0].z_score, -pair[1].population, pair[1].geonameid),
    )
    return [
        row.model_copy(update={"rank": index + 1})
        for index, (row, _) in enumerate(ordered[:limit])
    ]
