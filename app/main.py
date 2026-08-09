import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.api import cities, health, weather
from app.clients.rate_limiter import AsyncTokenBucket
from app.config import get_settings
from app.exceptions import (
    CityNotFoundError,
    InvalidUpstreamCredentialsError,
    UpstreamBadResponseError,
    UpstreamConnectionError,
    UpstreamRateLimitedError,
)

logger = logging.getLogger(__name__)

_DEFAULT_CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def _cors_origins() -> list[str]:
    """Browser origins allowed to call this API.

    Read straight from the environment rather than through ``Settings``: this
    module builds ``app`` at import time, and ``Settings.openweather_api_key``
    has no default, so touching ``get_settings()`` here would make importing
    ``app.main`` fail anywhere without a .env present.
    """
    raw = os.getenv("CORS_ALLOW_ORIGINS")
    if not raw:
        return _DEFAULT_CORS_ORIGINS
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.http_client = httpx.AsyncClient()
    app.state.rate_limiter = AsyncTokenBucket(settings.openweather_max_calls_per_minute)
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await app.state.http_client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="Weather Cache Service", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(weather.router)
    app.include_router(cities.router)

    @app.exception_handler(CityNotFoundError)
    async def _city_not_found(request: Request, exc: CityNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(UpstreamRateLimitedError)
    async def _rate_limited(request: Request, exc: UpstreamRateLimitedError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(UpstreamConnectionError)
    async def _connection_error(request: Request, exc: UpstreamConnectionError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(UpstreamBadResponseError)
    async def _bad_response(request: Request, exc: UpstreamBadResponseError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(InvalidUpstreamCredentialsError)
    async def _invalid_credentials(
        request: Request, exc: InvalidUpstreamCredentialsError
    ) -> JSONResponse:
        logger.error("Invalid OpenWeather credentials: %s", exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
