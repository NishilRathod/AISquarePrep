class WeatherServiceError(Exception):
    """Base class for all errors raised by the weather service layer."""


class CityNotFoundError(WeatherServiceError):
    def __init__(self, city: str):
        self.city = city
        super().__init__(f"City '{city}' not found")


class UpstreamRateLimitedError(WeatherServiceError):
    """Raised when OpenWeather keeps returning 429 after all retries are exhausted."""


class UpstreamBadResponseError(WeatherServiceError):
    """Raised on upstream 5xx responses or malformed/unparseable response bodies."""


class UpstreamConnectionError(WeatherServiceError):
    """Raised when OpenWeather cannot be reached (network error or timeout)."""


class InvalidUpstreamCredentialsError(WeatherServiceError):
    """Raised on upstream 401 — indicates a misconfigured/revoked API key."""
