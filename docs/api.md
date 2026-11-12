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

## Example

```bash
curl "http://localhost:8000/api/cells?minutes=60&band=FL300-390"
```

## Licence of the data

Cells and events are derived from adsb.lol data and are published under the
ODbL v1.0: attribute adsb.lol and Aerokym, and share derived databases alike.
