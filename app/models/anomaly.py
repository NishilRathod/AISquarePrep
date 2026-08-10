from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Driver = Literal["temperature", "humidity"]
Direction = Literal["above", "below"]


class AnomalyRow(BaseModel):
    """One city on the board, with everything needed to audit its own ranking."""

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
    rows: list[AnomalyRow]
    briefing: AnomalyBriefing | None
    swept_at: datetime | None
    cities_scored: int
    source: Literal["fresh", "stale", "unavailable"]
