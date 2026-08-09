import json
import unicodedata
from functools import lru_cache
from pathlib import Path

from app.models.city import CitySuggestion

INDEX_PATH = Path(__file__).resolve().parent.parent / "data" / "cities15000.json"

MIN_QUERY_LENGTH = 2


def normalize(value: str) -> str:
    """Fold case and strip accents so 'Malmo' matches 'Malmö' and vice versa.

    Also used to compare a suggestion against the tracked list, so the two sides
    always agree on what counts as the same city.
    """
    decomposed = unicodedata.normalize("NFKD", value.strip().casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


@lru_cache(maxsize=1)
def _index() -> list[tuple[str, CitySuggestion]]:
    """Load the vendored GeoNames extract once, paired with a normalized name.

    The file is already sorted population-descending, so a prefix scan yields
    the most significant cities first without re-sorting per keystroke.
    """
    with INDEX_PATH.open(encoding="utf-8") as handle:
        rows = json.load(handle)

    return [
        (
            normalize(name),
            CitySuggestion(name=name, state=state, country=country, population=population),
        )
        for name, state, country, population in rows
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
