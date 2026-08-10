import json
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.models.city import CitySuggestion

INDEX_PATH = Path(__file__).resolve().parent.parent / "data" / "cities15000.json"

MIN_QUERY_LENGTH = 2


@dataclass(frozen=True, slots=True)
class CityRecord:
    """One row of the vendored GeoNames extract.

    ``row_index`` is the city's position in the index and doubles as its offset
    into the climate-normals artefact, which is written in the same order.
    """

    row_index: int
    geonameid: int
    name: str
    state: str
    country: str
    population: int
    latitude: float
    longitude: float


def normalize(value: str) -> str:
    """Fold case and strip accents so 'Malmo' matches 'Malmö' and vice versa.

    Also used to compare a suggestion against the tracked list, so the two sides
    always agree on what counts as the same city.
    """
    decomposed = unicodedata.normalize("NFKD", value.strip().casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


@lru_cache(maxsize=1)
def city_records() -> list[CityRecord]:
    """Load the vendored GeoNames extract once.

    The file is already sorted population-descending, so a prefix scan yields
    the most significant cities first without re-sorting per keystroke.
    """
    with INDEX_PATH.open(encoding="utf-8") as handle:
        rows = json.load(handle)

    return [
        CityRecord(
            row_index=row_index,
            geonameid=geonameid,
            name=name,
            state=state,
            country=country,
            population=population,
            latitude=latitude,
            longitude=longitude,
        )
        for row_index, (
            geonameid,
            name,
            state,
            country,
            population,
            latitude,
            longitude,
        ) in enumerate(rows)
    ]


@lru_cache(maxsize=1)
def _index() -> list[tuple[str, CitySuggestion]]:
    """Search view over the index: each city paired with its normalized name."""
    return [
        (
            normalize(record.name),
            CitySuggestion(
                name=record.name,
                state=record.state,
                country=record.country,
                population=record.population,
            ),
        )
        for record in city_records()
    ]


def search_cities(query: str, limit: int = 8) -> list[CitySuggestion]:
    """Prefix-first city lookup, falling back to substring matches.

    Prefix hits are what someone typing expects to see, so they always outrank
    substring hits; within each group the index's population ordering stands.
    """
    needle = normalize(query)
    if len(needle) < MIN_QUERY_LENGTH:
        return []

    prefix: list[CitySuggestion] = []
    contains: list[CitySuggestion] = []

    for normalized_name, suggestion in _index():
        if normalized_name.startswith(needle):
            prefix.append(suggestion)
            if len(prefix) >= limit:
                return prefix
        elif len(contains) < limit and needle in normalized_name:
            contains.append(suggestion)

    return (prefix + contains)[:limit]
