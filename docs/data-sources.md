# Data sources

Limits marked "measured" were observed on 12 Nov 2026 (see
[m0-data-spike.md](m0-data-spike.md)). Everything else is from the providers'
documentation and must be re-checked before launch.

| Source | Used for | Limits | Licence / terms |
|---|---|---|---|
| adsb.lol REST API | Dense per-aircraft traces in live mode (`/1/trace/full` and `/1/trace/recent`) | Published tiers: 500 requests/day without a key, 1,000 with a free adhoc key, 7,200 for feeders. Measured: a burst limit as well (HTTP 429 after about 10 quick requests). Key goes in the `x-api-key` header. | ODbL v1.0: attribute adsb.lol; derived databases are share-alike |
| adsb.lol `globe_history` | Detector calibration, validation, demo replay | Daily archives on GitHub releases, several GB per day | ODbL v1.0 |
| OpenSky REST API | Backup live feed, cross-check | Measured: about 400 credits/day anonymous, 4 credits per India-sized call. 4,000 credits/day when registered. | Research and non-commercial use; attribution required |
| aviationweather.gov Data API | METAR, TAF, PIREP, SIGMET | Public, no key | US government, public domain |
| Iowa State IEM PIREP archive | Historical PIREPs for calibration (aviationweather.gov only serves recent ones) | CSV by date range, no key | Public archive of US government reports; credit IEM |
| OurAirports | Airports and runway headings | CSV download | Public domain |

Not used: adsb.fi open data (personal, non-commercial only) and any paid API.

Rules Aerokym follows:

- No user request ever triggers a call to an outside source. Only background
  jobs fetch, on a fixed budget.
- A refusal (HTTP 429 or 403) stops that job. No retries around a block.
- Raw ADS-B captures are never committed to the repository.

## adsb.lol trace endpoints (measured 12 Nov 2026)

| Endpoint | Covers | Freshness | Spacing in level cruise |
|---|---|---|---|
| `/1/trace/full/{hex}` | the whole UTC day | 17 to 100 minutes behind | about 20 s |
| `/1/trace/recent/{hex}` | roughly the last 15 minutes | a few seconds | about 20 s |

Measured on four aircraft, so treat the figures as indicative. The live
sampler fetches both for each aircraft: `full` for the history its baseline
needs, `recent` for the present.

The basemap comes from OpenFreeMap (no key). If it cannot be reached, the map
falls back to a plain background and still draws the cells.
