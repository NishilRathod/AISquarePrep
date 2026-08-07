import asyncio
import logging

import httpx
from pydantic import ValidationError

from app.clients.rate_limiter import AsyncTokenBucket
from app.config import Settings
from app.exceptions import (
    CityNotFoundError,
    InvalidUpstreamCredentialsError,
    UpstreamBadResponseError,
    UpstreamConnectionError,
    UpstreamRateLimitedError,
)
from app.models.openweather import OpenWeatherCurrentResponse

logger = logging.getLogger(__name__)


class OpenWeatherClient:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        settings: Settings,
        rate_limiter: AsyncTokenBucket,
    ):
        self._http_client = http_client
        self._settings = settings
        self._rate_limiter = rate_limiter

    async def get_current_weather(self, city: str) -> OpenWeatherCurrentResponse:
        max_attempts = self._settings.openweather_max_retries + 1

        for attempt in range(max_attempts):
            await self._rate_limiter.acquire()

            try:
                response = await self._http_client.get(
                    f"{self._settings.openweather_base_url}/weather",
                    params={
                        "q": city,
                        "appid": self._settings.openweather_api_key,
                        "units": "metric",
                    },
                    timeout=self._settings.openweather_timeout_seconds,
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise UpstreamConnectionError(
                    f"Could not reach OpenWeather: {exc}"
                ) from exc

            is_last_attempt = attempt == max_attempts - 1

            if response.status_code == 200:
                try:
                    return OpenWeatherCurrentResponse.model_validate(response.json())
                except (ValidationError, ValueError) as exc:
                    raise UpstreamBadResponseError(
                        f"Malformed response from OpenWeather: {exc}"
                    ) from exc

            if response.status_code == 404:
                raise CityNotFoundError(city)

            if response.status_code == 401:
                logger.error("OpenWeather rejected our API key (401)")
                raise InvalidUpstreamCredentialsError("OpenWeather API key was rejected")

            if response.status_code == 429:
                if is_last_attempt:
                    raise UpstreamRateLimitedError(
                        "OpenWeather rate limit exceeded after retries"
                    )
                await asyncio.sleep(self._retry_delay(response, attempt))
                continue

            if response.status_code >= 500:
                if is_last_attempt:
                    raise UpstreamBadResponseError(
                        f"OpenWeather returned {response.status_code} after retries"
                    )
                await asyncio.sleep(self._retry_delay(response, attempt))
                continue

            raise UpstreamBadResponseError(
                f"Unexpected OpenWeather response status: {response.status_code}"
            )

        raise UpstreamBadResponseError("Exhausted retries without a definitive response")

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return float(retry_after)
            except ValueError:
                pass
        return self._settings.openweather_backoff_base_seconds * (2**attempt)
