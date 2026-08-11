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
| GET    | `/weather`       | Paginated weather for multiple cities. `cities` (comma-separated, optional — defaults to the tracked list), `page`, `page_size`. |
| GET    | `/cities`        | Tracked cities, oldest-added first, plus the env-configured `defaults`. |
| POST   | `/cities`        | Track a new city. `201` when added, `200` when already tracked.       |
| GET    | `/cities/search` | Autocomplete over a vendored GeoNames index. `q` (min 2 chars), `limit`. |
| GET    | `/anomalies`     | The most anomalous cities on Earth right now, ranked. `limit` (1–50, default 10). |
| POST   | `/anomalies/refresh` | Force a sweep now instead of waiting for the timer.              |

Cities added through `POST /cities` persist in Redis (`tracked:cities`) and are
appended after the `TRACKED_CITIES` defaults, so `GET /weather` picks them up
without a restart.

## Global anomaly board

`GET /anomalies` ranks cities by **standardized anomaly** — how many standard
deviations today's local daily mean sits from that city's own normal for this
calendar month:

```
z = (observed - mean) / stddev
```

Dividing by each city's own sigma is what makes the ranking meaningful across
climates. Eight degrees above normal is the same departure in Reykjavík and
Delhi but nowhere near the same event; the measured normals bear that out —
Moscow's January sigma is 8.1 °C against Lagos's 0.6 °C. A row is ranked on
`max(|z_temperature|, |z_humidity|)` rather than their average, because
averaging would let a perfectly normal humidity halve a genuine temperature
extreme.

Every row carries the observation, the normal, and the standard deviation, so
the ranking can be recomputed from the response rather than taken on trust.

Three things are worth knowing about how it runs:

- **It is scheduled, not per-request.** A background sweep scores every covered
  city every `ANOMALY_SWEEP_INTERVAL_SECONDS` and stores the board in Redis;
  reads never touch an upstream API. Before the first sweep, `GET /anomalies`
  returns `200` with `rows: []` and `source: "unavailable"` rather than an
  error — use `POST /anomalies/refresh` to populate it immediately.
- **Coverage is whatever the normals artefact holds.** Cities without a
  baseline are absent from the board rather than wrong on it, so the artefact
  can be widened toward the full 33,957-city index without a code change.
- **The briefing is optional.** `briefing` is `null` whenever no Anthropic key
  is configured or the call fails. The rows are unaffected.

### The LLM step

`briefing` is the one place a model is involved, and it never touches the
ranking. Detection is arithmetic, ranking is a sort, and the top-N is a slice —
a model doing any of that would be slower, costlier, and occasionally wrong.

It is asked for what arithmetic cannot supply: that five Pearl River Delta
cities in the top ten are one dry intrusion rather than five independent facts;
that a large departure in a mild absolute range is not a health story while a
smaller one in a dense humid city is; and that a reading which passes the
plausibility filter may still be a broken station. It sees the top 15 rows once
per sweep, returns structured output, and is cached against a digest of the rows
themselves.

## Web UI

A React + TypeScript dashboard lives in [`frontend/`](frontend/). It lists the
tracked cities, badges each reading `CACHED` or `LIVE` depending on whether it
came from Redis or OpenWeather, and lets you search for and track new cities.

Below the grid it shows the global anomaly board. That section is independent of
the tracked cities and of each other's failures: if `/anomalies` errors or has
never swept, the section is simply absent and the dashboard is unaffected. The
briefing above the rankings appears only when one is available.

```bash
cd frontend
npm install
npm run dev     # http://localhost:5173
```

It expects the API on `http://localhost:8000`; override with
`VITE_API_BASE_URL`. The API must allow the dev server's origin — see
`CORS_ALLOW_ORIGINS`.

City suggestions come from a vendored extract of the
[GeoNames](https://www.geonames.org/) `cities15000` dataset, licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Regenerate it with
`python scripts/build_city_index.py`.

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
| `TRACKED_CITIES`                     | `London,Paris,New York,Tokyo,Sydney,Davangere`     | Seed city list; runtime additions are appended. |
| `DEFAULT_PAGE_SIZE` / `MAX_PAGE_SIZE`| `10` / `50`                                        | Pagination defaults/limits.                    |
| `OPEN_METEO_BASE_URL`                | `https://api.open-meteo.com/v1`                    | Bulk provider for the anomaly sweep. No API key — the free tier is keyless. |
| `OPEN_METEO_TIMEOUT_SECONDS`         | `60.0`                                             | Per-batch timeout (a batch covers many cities). |
| `OPEN_METEO_MAX_RETRIES`             | `2`                                                | Retries per batch on 429/5xx.                  |
| `OPEN_METEO_BACKOFF_BASE_SECONDS`    | `2.0`                                              | Backoff base when `Retry-After` is absent.     |
| `ANOMALY_SWEEP_ENABLED`              | `true`                                             | Set `false` to disable the background sweep entirely. |
| `ANOMALY_SWEEP_INTERVAL_SECONDS`     | `10800`                                            | Three-hourly. The first sweep runs after one interval, not at startup. |
| `ANOMALY_SWEEP_BATCH_SIZE`           | `200`                                              | Coordinates per upstream request.              |
| `ANOMALY_BOARD_SIZE` / `ANOMALY_DEFAULT_LIMIT` | `50` / `10`                              | Rows stored per sweep / rows served by default. |
| `ANTHROPIC_API_KEY`                  | *(optional)*                                       | Enables the anomaly briefing. Without it the board still ranks; `briefing` is `null`. |
| `ANTHROPIC_MODEL`                    | `claude-opus-5`                                    | Model used for the briefing.                   |
| `ANOMALY_BRIEFING_CACHE_TTL_SECONDS` | `21600`                                            | How long a briefing is reused for an unchanged board. |

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
