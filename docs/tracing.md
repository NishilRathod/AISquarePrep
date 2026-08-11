# Tracing

The service exports OpenTelemetry spans over OTLP. Tracing is off unless
`OTEL_EXPORTER_OTLP_ENDPOINT` is set: with no endpoint no tracer provider is
installed, the no-op provider stands in, and every span in the code costs an
attribute lookup. That is the only switch.

```sh
docker compose up -d          # brings up jaeger alongside redis and the app
curl -X POST localhost:8000/anomalies/refresh
open http://localhost:16686   # service: weather-cache-service
```

FastAPI, httpx, and Redis are auto-instrumented; the sweep stages and the model
call are hand-written spans.

## Where the model call lives

Not on a request path. `GET /anomalies` only reads a stored board out of Redis —
it never calls a model and never touches upstream. The Anthropic call happens
inside `AnomalyBoardService.sweep()`, which runs on the background timer or via
`POST /anomalies/refresh`, and it is skipped whenever the briefing cache already
holds an entry for those exact rows. **A sweep trace with no `chat` span under
`anomaly.briefing` is the cache working**, which is what `briefing.cache_hit`
records.

## A cold sweep

420 cities scored, briefing cache miss. Captured 2026-08-11.

```
span                                        timeline                                    duration
------------------------------------------------------------------------------------------------
POST /anomalies/refresh                     #############################################  8.394s
  anomaly.sweep                             #############################################  8.378s
    anomaly.load_normals                    ###                                            0.567s
    open_meteo.fetch_current_bulk              ###############                             2.781s
      GET api.open-meteo.com                   ########                                    1.616s
      GET api.open-meteo.com                            ####                               0.874s
      GET api.open-meteo.com                                #                              0.261s
    anomaly.score                                             #                            0.013s
    anomaly.briefing                                          ###########################   5.002s
      GET  (redis, briefing cache)                            #                            0.001s
      chat claude-opus-5                                      ###########################   4.995s
      SET  (redis, briefing cache)                                                    #    0.005s
    SET  (redis, board)                                                               #    0.009s
```

> ⚠️ **The 4.995s `chat` span is synthetic.** There is no `ANTHROPIC_API_KEY` on
> this machine, so the capture ran with the SDK's `messages.parse` replaced by a
> stub that sleeps for a fixed 5s and reports fixed token counts. Its duration and
> its `gen_ai.usage.*` values are placeholders and mean nothing. Everything else
> in this trace is real: real Open-Meteo requests, real Redis, the real normals
> artefact, the real scoring code, the real instrumentation.

Selected attributes:

| Span | Attributes |
|---|---|
| `anomaly.sweep` | `cities.covered=420`, `cities.in_index=33957`, `cities.scored=420`, `board.temperature_rows=50`, `board.humidity_rows=50` |
| `open_meteo.fetch_current_bulk` | `coordinates.count=420`, `batch.size=200`, `readings.missing=0` |
| `anomaly.briefing` | `briefing.rows=30`, `briefing.cache_hit=false`, `briefing.served=true` |
| `chat claude-opus-5` | `gen_ai.operation.name=chat`, `gen_ai.provider.name=anthropic`, `gen_ai.request.model=claude-opus-5`, `gen_ai.request.max_tokens=4096`, `gen_ai.response.finish_reasons=["end_turn"]`, `anthropic.thinking=adaptive`, `anthropic.effort=low` |

A refusal sets the span's status to ERROR with
`gen_ai.response.finish_reasons=["refusal"]` and records no exception — a policy
decline is not an outage. A transport failure records the exception. Both still
return `None` to the sweep, which is the pre-existing contract.

## The same sweep, warm

Second refresh, seconds later. Same rows, so the briefing digest matches and the
cache answers.

```
POST /anomalies/refresh                     #############################################  1.934s
  anomaly.sweep                             #############################################  1.931s
    anomaly.load_normals                    #                                              0.000s
    open_meteo.fetch_current_bulk           #############################################  1.915s
      GET api.open-meteo.com                ##############################                 1.298s
      GET api.open-meteo.com                                              #########        0.388s
      GET api.open-meteo.com                                                       ####    0.202s
    anomaly.score                                                                     #    0.010s
    anomaly.briefing  (cache_hit=true)                                                #    0.002s
    SET  (redis, board)                                                               #    0.002s
```

## What surprised me

Three things, none of them the model. The first is that **the three Open-Meteo
batches run one after another** — 1.616s, then 0.874s, then 0.261s, summing to
almost exactly the 2.781s parent span. Nothing requires that ordering;
`fetch_current_bulk` just `await`s each batch inside a `for` loop, so the sweep
spends 2.8s doing what would take 1.6s concurrently. Reading the code, I had
assumed batching *was* the parallelism; the trace shows batching only reduced the
request count, and the requests are still serial. The second is the **ratio
between loading the baseline and using it**: `anomaly.load_normals` costs 567ms on
the first sweep while `anomaly.score` — the actual statistics, over all 420
cities — costs 13ms. The work the artefact exists to enable is 44× cheaper than
decompressing the artefact, and that cost sits in the first request's critical
path rather than at startup (it is memoized, so the warm sweep shows 0.000s). The
third is what the trace *stores*: httpx records the full query string, and because
the batching design puts 200 lat/lon pairs into a URL, two of those spans carry
4,208- and 4,200-character `http.url` attributes. Every sweep writes about 9KB of
coordinates into the trace backend — the same design decision that made a
420-city sweep feasible quietly made each of its traces bulky. On the model call
itself the honest answer is that this capture can't say anything: its duration is
my stub's. What the trace *does* establish is the budget it has to beat —
everything that is not the model call totals ~3.4s cold and ~1.9s warm.

## Reproducing the capture

The stub lives outside the repo (it was a scratchpad module that patches
`anthropic.AsyncAnthropic` before importing `app.main`, exactly as
`tests/test_anomaly_briefing_client.py` does). With a real key in `.env`, no stub
is needed — `docker compose up -d` and one `POST /anomalies/refresh` produces the
same trace with a real `chat` span.
