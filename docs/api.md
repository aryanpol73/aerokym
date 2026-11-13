# API

Read-only, no key. Interactive docs are served at `/docs` (OpenAPI at
`/openapi.json`). Locally the base URL is `http://localhost:8000`.

Every response includes `meta.disclaimer`, `meta.attribution` and
`meta.method_version`. Successful responses are cacheable for 60 seconds.
The limit is 60 requests per minute per address; beyond that you get HTTP 429
with a `Retry-After` header.

**Not for operational use.** The data is a statistical estimate from sparse
public ADS-B data and gives no safety guidance.

| Endpoint | Returns |
|---|---|
| `GET /api/cells` | GeoJSON hexagons (H3 resolution 4, about 45 km across) with roughness severity and confidence |
| `GET /api/events` | GeoJSON points: individual 3-minute detector windows |
| `GET /api/airports` | GeoJSON points: airports with the flight category of their latest METAR |
| `GET /api/airports/{icao}` | One airport: decoded METAR and TAF, wind per runway, nearby SIGMETs and pilot reports |
| `GET /api/sigmets` | GeoJSON polygons: SIGMET hazard areas valid now, worldwide |
| `GET /api/pireps` | GeoJSON points: pilot reports from the last `hours` (1 to 12, default 3) |
| `GET /api/accuracy` | Daily validation against pilot reports, and the counts pooled over `days` (1 to 365, default 30) |
| `GET /api/meta` | Method version, data freshness, what the severity numbers mean |
| `GET /health` | Liveness check |

## `/api/cells`

| Parameter | Values | Default |
|---|---|---|
| `minutes` | 15, 30, 60 | 30 |
| `band` | `FL200-290`, `FL300-390`, `FL400+` | all bands |
| `at` | ISO 8601 time to look back from | now |

Cell properties:

| Property | Meaning |
|---|---|
| `severity` | 0 none, 1 light, 2 moderate or greater |
| `confidence` | 0 to 1. Reaches full value only when 3 or more aircraft sampled the cell |
| `n_aircraft`, `n_windows` | How much evidence the cell rests on |
| `mean_index` | Mean roughness index; each aircraft counts once |
| `max_index` | Highest single-aircraft mean in the cell |
| `latest` | Time of the newest window in the cell |

Cells with severity 0 are included on purpose: they show where aircraft flew
and reported nothing.

## `/api/events`

| Parameter | Values | Default |
|---|---|---|
| `cell` | An `h3_cell` id from `/api/cells` | all cells |
| `minutes` | 1 to 180 | 60 |
| `min_severity` | 0, 1, 2 | 1 |
| `limit` | 1 to 2000 | 500 |
| `at` | ISO 8601 time | now |

## `/api/airports/{icao}`

`icao` is the 4-letter airport code, for example `VABB`. Unknown codes return
404. All weather routes accept `at` like the others.

| Field | Meaning |
|---|---|
| `metar.lines` | The report in plain language, one statement per line |
| `metar.flight_category` | `VFR`, `MVFR`, `IFR` or `LIFR` (US definitions), from ceiling and visibility |
| `metar.raw`, `taf.raw` | The original coded reports |
| `taf.periods[].text` | Each forecast period in plain language |
| `runways[].ends[]` | Per runway end: `headwind_kt` (negative is a tailwind), `crosswind_kt`, `crosswind_from` (`left` or `right`) |
| `runways[].ends[].status` | `ok`, `calm`, `variable` (then only `max_crosswind_kt` is given) or `unknown` |
| `nearby_sigmets` | SIGMETs valid now within 300 km |
| `nearby_pireps` | Pilot reports within 200 km in the last 3 hours |

`metar` is `null` when the airport has no report from the last 3 hours, and
`flight_category` in `/api/airports` is `null` in the same case.

Weather responses also carry `meta.weather_attribution`.

## `/api/accuracy`

"Positive" means moderate or greater.

| Field | Meaning |
|---|---|
| `runs[]` | One row per day and region: `n_pireps` (cruise turbulence reports), `n_matched` (those with a sampled aircraft within 50 km, 1000 ft and 10 minutes), `tp`, `fp`, `fn` |
| `pooled.precision`, `pooled.recall` | Computed from the summed counts, not by averaging days. `null` when there is nothing to score |
| `pooled.naive_precision` | What calling every matched report "moderate or greater" would score |
| `calibration` | The one-day calibration result, kept separate because it is not independent |
| `threshold_index` | The roughness index at which a report counts as "moderate or greater" |

## Example

```bash
curl "http://localhost:8000/api/cells?minutes=60&band=FL300-390"
```

## Licence of the data

Cells and events are derived from adsb.lol data and are published under the
ODbL v1.0: attribute adsb.lol and Aerokym, and share derived databases alike.

Weather comes from aviationweather.gov and airports from OurAirports; both are
public domain.
