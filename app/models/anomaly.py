from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

Driver = Literal["temperature", "humidity"]
Direction = Literal["above", "below"]


class AnomalyRow(BaseModel):
    """One city on one board, with everything needed to audit its own ranking.

    Both z-scores are always present, so a row taken from the temperature board
    still shows what its humidity was doing. ``rank``, ``z_score``, ``driver``
    and ``direction`` describe the board this copy came from -- the same city can
    appear on both boards with a different headline on each.
    """

    rank: int
    city: str
    state: str
    country: str
    latitude: float
    longitude: float

    temperature_c: float
    humidity_pct: int

    normal_temperature_c: float
    normal_humidity_pct: float
    sd_temperature_c: float
    sd_humidity_pct: float

    z_temperature: float
    z_humidity: float
    # max(|z_temperature|, |z_humidity|), the value the board ranks on.
    z_score: float
    driver: Driver
    direction: Direction


class SynopticEvent(BaseModel):
    """Several rows that are one weather system rather than independent facts."""

    name: str
    cities: list[str]
    explanation: str


class CityNote(BaseModel):
    city: str
    significance: Literal["notable", "routine", "health_risk"]
    note: str


class AnomalyBriefing(BaseModel):
    """The interpretation layer. Always optional -- the board stands without it."""

    headline: str
    events: list[SynopticEvent]
    notes: list[CityNote]
    suspect_readings: list[str]


class AnomalyBoard(BaseModel):
    """Two rankings over the same sweep, one per variable.

    Split rather than combined because a single ranking on the larger of the two
    departures lets one variable sweep the entire visible top ten on any day it
    happens to be dramatic, hiding the other half of the weather entirely.
    """

    temperature: list[AnomalyRow]
    humidity: list[AnomalyRow]
    briefing: AnomalyBriefing | None
    swept_at: datetime | None
    # The local day the readings are from, which is not the day the sweep ran:
    # the board scores the last *complete* day, so this is normally yesterday.
    # Shown rather than implied, because "how anomalous is it" is meaningless
    # without knowing when.
    observed_date: date | None
    cities_scored: int
    source: Literal["fresh", "stale", "unavailable"]
