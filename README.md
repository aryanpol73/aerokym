# Aerokym

**A free, open turbulence map built from signals planes already broadcast.**

Every airliner broadcasts its position, altitude and vertical rate over ADS-B.
Aerokym listens to the public feeds, looks for the short, sharp vertical-rate
changes that turbulence causes, and draws them on a live map by altitude band —
next to official aviation weather (SIGMETs, PIREPs, METAR/TAF).

> **Not for operational use.** Aerokym is a research and education project. It
> is not a flight-planning tool and gives no safety guidance. Detections are
> statistical guesses from sparse public data. Always use official sources.

<!-- GIF placeholder: demo replay of a notable day -->

## Status

Pre-alpha. Ingest, storage and a first detector work; there is no API or map yet.

| Milestone | What | State |
|---|---|---|
| M0 | Data spike: is the public data dense enough? | done: [findings](docs/m0-data-spike.md) |
| M1 | Ingest + storage + replay mode | done |
| M2 | Roughness detector, calibrated against PIREPs | done: [method and results](docs/method.md) |
| M3 | H3 aggregation + API | next |
| M4 | Live map | |
| M5 | Weather + airport cards | |
| M6 | Accuracy page + launch | |

## Quickstart

You need Docker. The first run pulls a database image of about 4 GB.

```bash
docker compose --profile demo up --build
```

This starts Postgres (TimescaleDB + PostGIS + h3), creates the schema, and
replays a bundled sample of 50 real flights from 11 Nov 2026 (25 over India,
25 over the US), re-timed so they end "now". No internet or API keys needed
once the images exist. Look at the data:

```bash
docker compose exec db psql -U aerokym -d aerokym -c "select count(*), count(distinct icao24), min(time), max(time) from positions"
```

Live mode polls OpenSky for all of India about every 96 seconds. Copy
`.env.example` to `.env`, add your OpenSky API client id and secret, then:

```bash
docker compose --profile live up --build
```

Run the tests:

```bash
cd backend && pip install -e ".[dev]" && pytest -q
```

## How detection works

The textbook method (RMSVA) needs about one sample per second. Free public
data has one every ~20 seconds in cruise, so Aerokym uses a coarser
**roughness index** instead:

1. Keep only level cruise: above FL200, within 300 ft of one altitude, with a
   one-minute guard that rejects climbs, descents and level-offs.
2. In each 3-minute window, measure how much the reported vertical rate
   varies.
3. Divide by the same aircraft's normal variation over its flight. 1.0 is
   normal for that aircraft; 1.6 or more is labelled "moderate or greater".

Full description, formulas and limits: [docs/method.md](docs/method.md).

## Accuracy

First calibration, one day only (11 Nov 2026, US, 429 pilot reports matched
within 50 km, 1000 ft and 10 minutes):

| | Aerokym | Naive "always yes" |
|---|---|---|
| Moderate or worse: precision | 0.51 | 0.33 |
| Moderate or worse: recall | 0.51 | 1.00 |
| Chance a rough report outscores a smooth one (AUC) | 0.79 | 0.50 |

These numbers are optimistic: the method was tuned on the same day it was
scored on. Results over India are **unvalidated**, because pilot reports
there are too sparse to check against. A public accuracy page with daily
numbers comes in M6.

## Data and attribution

- ADS-B data: [adsb.lol](https://adsb.lol), licensed under the
  [ODbL v1.0](https://opendatacommons.org/licenses/odbl/1-0/). Derived
  databases published by Aerokym are share-alike under the same licence.
- Weather: [aviationweather.gov](https://aviationweather.gov/data/api/) Data
  API (US government, public domain).
- Airports and runways: [OurAirports](https://ourairports.com/data/) (public
  domain).

See [docs/data-sources.md](docs/data-sources.md) for limits and terms.

## Licence

Code: MIT. Data: see above.
