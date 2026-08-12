# A reusable daily cache for the climate normals

**Date:** 2026-08-12
**Status:** approved, not yet implemented

## Why

Two problems surfaced on the same day, and one change answers both.

**Changing the window throws away every city already fetched.** The checkpoint
stores *finalised* statistics -- per city-month, a mean and a standard deviation
reduced over the whole window. A three-year normal cannot be derived from a
five-year mean and sigma, so `--years 3` starts an empty checkpoint and refetches
from zero. With Open-Meteo's free daily quota binding at roughly a day per
thousand cities, that is not a small cost.

**The baseline has no defence against extreme events, and a shorter window makes
that worse.** `_accumulate` sums every daily value in the window and `_finalize`
turns those sums into a mean and a sigma. Nothing is trimmed or down-weighted. A
heat dome inside a city's June baseline shifts that city's June mean *and*
inflates its June sigma -- and the inflated sigma is the greater harm, because it
widens the band a future event is measured against and pushes genuine anomalies
down or off the board. Going from five years to three drops each city-month from
~150 daily samples to ~90, giving any single event roughly 1.7x more leverage.

The scoring layer does not help here: `MAX_PLAUSIBLE_Z = 8.0` is a fault filter,
not a disaster filter, and would discard a truly catastrophic reading as a likely
broken station. Nor does the briefing: the model is told the z-scores are
established fact and instructed not to second-guess them, so a contaminated
baseline reaches it as an authoritative number it has no way to question.

Deciding between plain and robust statistics therefore needs to stay open, and
that decision needs the underlying distribution, which finalised sums have
already discarded.

## What changes

Cache the **raw daily series** instead of statistics computed from it. Every
downstream choice -- window length, mean/sd versus median/MAD, even what counts
as a month -- becomes a local recomputation over data already held, and no city
is ever fetched twice for any reason.

### Cache layout

A single file, `app/data/.climate_normals.v2.daily.jsonl`, gitignored as the
current checkpoint is.

**The window is deliberately absent from the filename.** v1 puts it there because
resume is keyed on "have we already done this city", so a checkpoint from another
date range would silently blend baselines computed over different periods.
Labelling each record with its year dissolves that hazard instead of guarding
against it, and the window stops being a property of the cache at all -- it
becomes an argument to composition.

One line per city-year:

```json
{"id": 1796236, "y": 2024, "d": "<base64 int16 array>"}
```

The blob is that calendar year's daily temperatures followed by its daily
humidities, each stored as `round(value * 10)` in `int16`.

- **Resolution.** 0.1 units, two orders of magnitude below the ~2.4 C median
  sigma the board ranks on. Quantisation is invisible in any output.
- **Range.** Temperature reaches +-3276.7 C and humidity is bounded at 100, so
  neither can overflow.
- **Missing days.** `-32768` is the sentinel, unreachable by any real reading.
  Excluded from every statistic rather than counted as zero -- the same reasoning
  behind v1's `NaN`: a missing value must never be able to masquerade as a real
  one.

Size is ~2 KB per line: **37 MB** for 6,226 cities x 3 years, and **202 MB** if
the scope later widens to the full 33,957-city index.

### Fetch and storage granularity differ

Requests still cover the whole window in one call; storage is per year. Fewer
requests, finer cache. The response is split into calendar years on arrival.

### Composition

`compose(city, years, statistic)` decodes the requested years, buckets each day
into its calendar month using that year's own boundaries -- so 2024's leap day
lands in February rather than shifting the rest of the year -- and reduces to the
existing 48-float per-city layout.

Two reducers over identical input:

- `mean-sd`: today's behaviour exactly. The default.
- `median-mad`: median, with sigma estimated as `1.4826 * MAD`.

The cache supports either; the artefact commits to one. A `--statistic` flag
selects it, meta records which produced the artefact, and switching is a
`--write-only` repack that costs no quota. MAD's symmetry assumption is weakest
for humidity, which is bounded at 100% and skews near saturation -- a reason the
default stays `mean-sd` until the alternative is measured rather than assumed.

### Resume and widening

Resume keys on the set of `(geonameid, year)` pairs already present. Pending
cities are grouped by which years they lack, and each group requests the span
covering its gaps. A fresh run is one group; a widening run is also one group,
since every city lacks the same years. A city interrupted mid-window keeps the
years it already received.

Widening 3 to 5 years later fetches only 2021 and 2022 and recomposes.

### Batch sizing

Unchanged from the fix already shipped in `6e62c56`: chunks respect both the
8 KB URI cap and the 200,000 location-day budget. At a 3-year window
(1,096 days) that allows ~182 cities per request, up from ~109 at five years.

## What does not change

`app/services/normals.py`, the artefact format, the scoring engine, the API, and
the frontend. `_write_artifact` composes from the cache rather than reading
finalised values, and meta gains `statistic` and `cache_version`. The board
serves whatever coverage exists throughout, as it already does -- cities without
a baseline are absent from it rather than wrong on it.

## Migration

v1 records cannot be converted; their raw values were discarded at write time.
The 520 cities already fetched are refetched once under the new format, roughly a
day of quota, and never again. The v1 checkpoint is left on disk untouched.

## Scope

`--min-population 100000 --years 3`: 6,226 cities, window 2023-01-01 to
2025-12-31. Widening to the full index later costs only the cities added.

## Error handling

- A response whose length does not match the request is rejected rather than
  zipped, matching the existing positional-join guard -- a shifted response would
  attribute every city's climate to its neighbour, undetectably.
- A decoded year whose day count does not match that calendar year is discarded
  and refetched rather than composed.
- A city-month below `MIN_SAMPLES` stays `NaN`, as now.
- Fetch failures still drop the batch rather than abort the run.

## Testing

- Encode/decode round-trip preserves values within quantisation tolerance, and
  preserves the missing sentinel distinctly from a real reading.
- Composition matches statistics computed directly from the same daily values.
- Composability: three years drawn from a five-year cache equals a three-year
  cache built alone.
- Leap-year month boundaries: 2024 daily values land in the right months.
- Length mismatch is rejected, not silently misaligned.
- Partial-city resume requests only the missing years.
- Both reducers, including MAD on a skewed distribution.
- `--write-only` composes with no network access.

## Also in scope

The startup ETA models only the pacing rate and is blind to the daily cap; it
predicted 46 hours for work that is quota-bound at weeks. It should either
account for the daily limit or stop claiming a completion time it cannot know.
