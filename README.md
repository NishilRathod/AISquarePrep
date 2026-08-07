# Weather Cache Service

A small FastAPI service that fetches current weather data from the
[OpenWeather API](https://openweathermap.org/api), reshapes it into a simpler
schema, and serves it through a Redis cache-aside layer. Calls to OpenWeather
are throttled with an async token bucket and retried with backoff on
`429`/`5xx` responses; our own endpoints support pagination.

## Endpoints

| Method | Path             | Description                                                         |
|--------|------------------|-----------------------------------------------------------------------|
| GET    | `/health`        | Redis connectivity check (never calls OpenWeather).                  |
| GET    | `/weather/{city}`| Reshaped current weather for one city (cache-aside).                 |
| GET    | `/weather`       | Paginated weather for multiple cities. `cities` (comma-separated, optional — defaults to `TRACKED_CITIES`), `page`, `page_size`. |

## Prerequisites

- A free OpenWeather API key: https://home.openweathermap.org/users/sign_up
- Either:
  - Docker + Docker Compose, **or**
  - Python 3.12 and a local Redis instance for non-Docker development.

## Configuration

Copy `.env.example` to `.env` and fill in your API key:

```bash
cp .env.example .env
```

| Variable                            | Default                                          | Description                                   |
|--------------------------------------|---------------------------------------------------|------------------------------------------------|
| `OPENWEATHER_API_KEY`                | *(required)*                                      | Your OpenWeather API key.                      |
| `OPENWEATHER_BASE_URL`               | `https://api.openweathermap.org/data/2.5`         | OpenWeather base URL.                          |
| `OPENWEATHER_MAX_CALLS_PER_MINUTE`   | `60`                                               | Outbound rate limit toward OpenWeather.        |
| `OPENWEATHER_TIMEOUT_SECONDS`        | `5.0`                                              | Per-request timeout.                           |
| `OPENWEATHER_MAX_RETRIES`            | `2`                                                | Retries after the first attempt (429/5xx).     |
| `OPENWEATHER_BACKOFF_BASE_SECONDS`   | `1.0`                                              | Backoff base when OpenWeather omits `Retry-After`. |
| `REDIS_URL`                          | `redis://localhost:6379/0`                        | Overridden to `redis://redis:6379/0` in Compose. |
| `CACHE_TTL_SECONDS`                  | `600`                                              | How long a city's weather stays cached.        |
| `TRACKED_CITIES`                     | `London,Paris,New York,Tokyo,Sydney`               | Default city list for `GET /weather`.          |
| `DEFAULT_PAGE_SIZE` / `MAX_PAGE_SIZE`| `10` / `50`                                        | Pagination defaults/limits.                    |

## Run with Docker Compose

```bash
docker compose up --build
```

Then, in another terminal:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/weather/London
curl "http://localhost:8000/weather?cities=London,Paris&page=1&page_size=10"
```

## Run locally without Docker

```bash
python -m venv .venv
.venv/Scripts/activate   # on Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -e ".[dev]"

# start a local Redis (or point REDIS_URL at one you already have running)
docker run --rm -p 6379:6379 redis:7-alpine

uvicorn app.main:app --reload
```

## Run tests

```bash
pytest
```

The suite runs fully offline — OpenWeather calls are mocked with `respx` and
Redis is faked with `fakeredis`, so no real network access, Redis server, or
API key is required.

## Lint

```bash
ruff check .
```

## Error responses

| Situation                                   | HTTP status |
|-----------------------------------------------|-------------|
| Unknown city                                   | 404         |
| OpenWeather rate-limited us after retries      | 503         |
| OpenWeather unreachable (network/timeout)      | 503         |
| OpenWeather returned 5xx / malformed response  | 502         |
| Our OpenWeather API key was rejected (401)     | 500 (detail logged server-side only) |

For `GET /weather` with multiple cities, an individual unknown city is
skipped (excluded from `items`, request still returns 200); a systemic
upstream failure aborts the whole request with the status above.

## Project structure

```
app/
  main.py             FastAPI app factory, lifespan, exception handlers
  config.py           Settings (pydantic-settings)
  exceptions.py        Exception hierarchy
  models/              Pydantic models (raw OpenWeather + reshaped response)
  clients/              Rate limiter + OpenWeather HTTP client
  services/             Redis cache service + cache-aside weather service
  api/                   Routers (health, weather) + dependency providers
tests/                  pytest suite (offline: respx + fakeredis)
```
