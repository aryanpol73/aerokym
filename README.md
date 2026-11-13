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

Pre-alpha. Every milestone of the MVP is built and works on replayed data. Live mode
has not been run end to end, nothing is deployed, and there are no independent
validation days yet.

| Milestone | What | State |
|---|---|---|
| M0 | Data spike: is the public data dense enough? | done: [findings](docs/m0-data-spike.md) |
| M1 | Ingest + storage + replay mode | done |
| M2 | Roughness detector, calibrated against PIREPs | done: [method and results](docs/method.md) |
| M3 | H3 aggregation + API | done: [API reference](docs/api.md) |
| M4 | Live map | done (demo data); live feed untested |
| M5 | Weather + airport cards | done (demo data); live feed untested |
| M6 | Accuracy page + launch | validation job and page done; deploy and launch not done: [guide](docs/deploy.md) |

## Quickstart

You need Docker. The first run pulls a database image of about 4 GB.

```bash
docker compose --profile demo up --build
```

This starts Postgres (TimescaleDB + PostGIS + h3), creates the schema,
replays a bundled sample of 50 real flights from 11 Nov 2026 (25 over India,
25 over the US) re-timed so they end "now", runs the detector over them, and
serves the result. No internet or API keys needed once the images exist.

- Map: http://localhost:3000
- An airport card: http://localhost:3000/airport/VABB (Mumbai)
- Accuracy page: http://localhost:3000/accuracy (empty in the demo: there is nothing
  independent to score)
- API docs: http://localhost:8000/docs
- Hex cells for the last hour:

```bash
curl "http://localhost:8000/api/cells?minutes=60"
```

The demo also loads a stored weather snapshot (METAR, TAF and SIGMETs from
12 Nov 2026), re-timed to now, and 213 airports in and around India.

With only 50 aircraft, most demo cells rest on a single aircraft and carry
low confidence. That is the sample size, not the method.

Live mode uses two sources together. OpenSky says which aircraft are in cruise
over India (one request covers the country, about every 96 seconds). adsb.lol
then supplies a dense trace for one of those aircraft at a time, two requests
each. Official weather is refreshed from aviationweather.gov every 10 minutes.

A share of that budget (every third aircraft by default) goes to a box over the
central US, where pilot reports are dense enough to check the detector against.
A validation job re-scores today and yesterday every hour. On a free adsb.lol key (1,000 requests a day) that is about 19 aircraft
an hour, so the live map is a thin sample of the traffic.

Copy `.env.example` to `.env`, add your OpenSky client id and secret and,
if you have one, your adsb.lol key, then:

```bash
docker compose --profile live up --build
```

Run the tests:

```bash
cd backend && pip install -e ".[dev]" && pytest -q
```

Work on the map with hot reload (needs the API running):

```bash
cd web && npm install && npm run dev
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

## Airport weather

Click an airport dot on the map for its card: the latest METAR and TAF in
plain language, and the headwind and crosswind on each runway computed from
the reported wind and the runway's true heading. Dots are coloured by flight
category (VFR, MVFR, IFR, LIFR, using the US definitions); grey means no
report in the last 3 hours. SIGMET hazard areas and pilot reports are separate
map layers. Pilot reports are rare over India, so that layer is usually empty
there.

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
there are too sparse to check against.

Independent checking is built in. Every hour a validation job compares the
detector with new pilot reports over the US and stores the counts, and the
[accuracy page](web/app/accuracy/page.tsx) (`/accuracy`, data at
`/api/accuracy`) pools them over 30 days. It shows a warning instead of
percentages until at least 100 reports have been scored. As of this commit it
has no independent days.

You can score any past day yourself from an Iowa State IEM PIREP archive CSV:

```bash
python -m aerokym.validate.run --date 2026-11-11 --bbox 24,-126,50,-66 --pireps-csv pireps.csv
```

## Data and attribution

- ADS-B data: [adsb.lol](https://adsb.lol), licensed under the
  [ODbL v1.0](https://opendatacommons.org/licenses/odbl/1-0/). Derived
  databases published by Aerokym are share-alike under the same licence.
- Weather: [aviationweather.gov](https://aviationweather.gov/data/api/) Data
  API (US government, public domain).
- Aircraft presence in live mode: [OpenSky Network](https://opensky-network.org).
- Basemap: © OpenStreetMap contributors, served by [OpenFreeMap](https://openfreemap.org).
- Airports and runways: [OurAirports](https://ourairports.com/data/) (public
  domain).

See [docs/data-sources.md](docs/data-sources.md) for limits and terms.

## Licence

Code: MIT. Data: see above.
