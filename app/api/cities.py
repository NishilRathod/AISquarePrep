from fastapi import APIRouter, Query, Response

from app.api.deps import TrackedCitiesServiceDep
from app.models.city import (
    AddCityRequest,
    AddCityResponse,
    CitySuggestion,
    TrackedCitiesResponse,
)
from app.services.city_index import search_cities

router = APIRouter()


@router.get("/cities/search", response_model=list[CitySuggestion])
def search(
    q: str = Query(default="", description="Partial city name; under 2 characters returns []"),
    limit: int = Query(default=8, ge=1, le=25),
) -> list[CitySuggestion]:
    """Autocomplete over the vendored GeoNames index.

    Declared ``def`` rather than ``async def`` on purpose: the lookup is a
    synchronous scan over ~34k rows, so FastAPI running it in a threadpool keeps
    it off the event loop.
    """
    return search_cities(q, limit=limit)


@router.get("/cities", response_model=TrackedCitiesResponse)
async def list_tracked(tracked: TrackedCitiesServiceDep) -> TrackedCitiesResponse:
    return TrackedCitiesResponse(
        cities=await tracked.list_cities(),
        defaults=list(tracked.defaults),
    )


@router.post("/cities", response_model=AddCityResponse)
async def add_tracked(
    payload: AddCityRequest,
    tracked: TrackedCitiesServiceDep,
    response: Response,
) -> AddCityResponse:
    """Track a new city.

    Adding one that is already tracked is a no-op rather than an error -- the UI
    disables those suggestions, but a duplicate arriving anyway does not deserve
    a 4xx. 201 signals a real addition, 200 signals it was already there.
    """
    added = await tracked.add(payload.city)
    response.status_code = 201 if added else 200
    return AddCityResponse(
        city=payload.city,
        added=added,
        cities=await tracked.list_cities(),
    )
