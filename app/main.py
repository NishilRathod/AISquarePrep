import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.api import anomalies, cities, health, weather
from app.clients.open_meteo import OpenMeteoClient
from app.clients.rate_limiter import AsyncTokenBucket
from app.config import Settings, get_settings
from app.exceptions import (
    CityNotFoundError,
    InvalidUpstreamCredentialsError,
    UpstreamBadResponseError,
    UpstreamConnectionError,
    UpstreamRateLimitedError,
)
from app.services.anomaly_board import AnomalyBoardService

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


def _build_briefer(settings: Settings):
    """The anomaly-briefing provider, or ``None`` when no key is configured.

    Import is local so the ``anthropic`` package is only required by deployments
    that actually want a briefing.
    """
    if not settings.anthropic_api_key:
        logger.info("No ANTHROPIC_API_KEY set; anomaly board will serve without a briefing")
        return None
    try:
        from app.clients.anthropic import AnomalyBriefingClient
    except ImportError:
        logger.warning("anthropic not installed; anomaly board will serve without a briefing")
        return None
    return AnomalyBriefingClient(settings)


async def _sweep_loop(app: FastAPI, settings: Settings) -> None:
    """Run the global sweep on a timer for the life of the process.

    Deliberately sleeps *before* the first sweep so that starting the app does
    not fire thousands of upstream requests -- use POST /anomalies/refresh to
    populate an empty board on demand.
    """
    while True:
        try:
            await asyncio.sleep(settings.anomaly_sweep_interval_seconds)
            service = AnomalyBoardService(
                app.state.redis,
                OpenMeteoClient(app.state.http_client, settings),
                settings.anomaly_board_size,
                app.state.briefer,
                settings.anomaly_briefing_cache_ttl_seconds,
            )
            await service.sweep()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failed sweep must not kill the loop
            logger.exception("Scheduled anomaly sweep failed; will retry next interval")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.http_client = httpx.AsyncClient()
    app.state.rate_limiter = AsyncTokenBucket(settings.openweather_max_calls_per_minute)
    app.state.briefer = _build_briefer(settings)

    sweep_task: asyncio.Task[None] | None = None
    if settings.anomaly_sweep_enabled:
        sweep_task = asyncio.create_task(_sweep_loop(app, settings))

    try:
        yield
    finally:
        if sweep_task is not None:
            sweep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweep_task
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
    app.include_router(anomalies.router)

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
