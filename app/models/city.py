from pydantic import BaseModel, Field, field_validator


class CitySuggestion(BaseModel):
    """A single autocomplete hit from the static GeoNames index."""

    name: str
    state: str
    country: str
    population: int


class TrackedCitiesResponse(BaseModel):
    """The full tracked list, oldest-added first.

    ``defaults`` are the env-configured cities; they always lead ``cities`` and
    cannot be removed at runtime.
    """

    cities: list[str]
    defaults: list[str]


class AddCityRequest(BaseModel):
    city: str = Field(min_length=1, max_length=100)

    @field_validator("city")
    @classmethod
    def _strip(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("city must not be blank")
        return stripped


class AddCityResponse(BaseModel):
    city: str
    added: bool
    cities: list[str]
