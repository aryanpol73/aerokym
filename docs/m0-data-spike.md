# M0 — Data spike

**Question:** do free public ADS-B sources update each aircraft often enough
for RMSVA-style turbulence detection?

**Short answer:** no. The unkeyed live REST APIs are limited by request quota,
and the free historical archive is thinned to one point every ~20 s in level
cruise. True RMSVA needs about 1 Hz, which only our own receiver gives. What
the free data can support is a coarser "roughness index", to be proven or
dropped against PIREPs in M2 (sections 5 and 6).

Measured 12 Nov 2026, ~06:40–07:10 UTC (midday India, midnight US Central).
Reproduce with `backend/spikes/m0_sampling.py`. One short capture per region:
treat the numbers as indicative, not as statistics.

## 1. What the sources allow

| Source | Quota observed | What that means |
|---|---|---|
| adsb.lol REST, no key | Response header `x-ratelimit-limit: 500;w=86400` → **500 requests/day**, plus a burst limit (HTTP 429 after 13 polls at 5 s; 10 s polling was accepted) | One 250 nm circle polled all day = 1 request every ~3 min. India needs roughly 8–10 circles. Live coverage is impossible without a key. |
| adsb.lol map host (`/data/traces/...`) | HTTP 403 for scripts: "Automated clients belong on the keyed API" | Per-aircraft trace files are off limits to bots. Not worked around. |
| OpenSky REST, anonymous | `x-rate-limit-remaining` started near 400 credits/day; the India box costs 4 credits per call | ~100 whole-India snapshots/day anonymous. A registered account (4,000 credits) gives ~1,000/day = **one snapshot every ~90 s**. |

## 2. What the data looks like when you can get it

Cruise aircraft only (at or above FL200), adsb.lol polled every 10 s for 190 s:

| Region (250 nm circle) | Cruise aircraft | Position age: median / p90 | Stale > 15 s | Followed continuously* | Vertical rate present (baro / geom) |
|---|---|---|---|---|---|
| India west (Mumbai–Pune) | 28 | 1.1 s / 28.2 s | 17% | 36% | 97% / 90% |
| India north (Delhi) | 30 | 0.8 s / 27.2 s | 17% | 37% | 96% / 87% |
| India south (Bengaluru) | 33 | 0.3 s / 3.9 s | 5% | 58% | 99% / 92% |
| US central (Kansas) | 24 | 0.3 s / 0.5 s | 0% | 79% | 100% / 51% |

\* fresh position in at least 80% of polls. Aircraft entering or leaving the
circle during the capture also count as "not continuous", so this understates
coverage somewhat.

- Where receivers exist, the feed is fresh to about a second: the upstream data
  is dense. The median update interval we saw simply equals our polling
  interval (5.1 s at 5 s polling, 10.0 s at 10 s).
- India has real coverage holes: about 1 in 6 cruise positions near Mumbai and
  Delhi was more than 15 s old, against none over Kansas.
- Vertical rate is almost always present. Use `baro_rate` first and fall back
  to `geom_rate`.
- OpenSky saw 222 cruise aircraft over the whole India box in one call, with
  vertical rate on 100% of them, at a fixed ~10 s time resolution.

## 3. Is RMSVA viable at these rates?

`backend/spikes/m0_rate_sim.py` simulates band-limited turbulence, quantises
vertical rate to ADS-B's 64 ft/min steps and subsamples it.

| Sampling interval | Samples per 60 s | RMSVA recovered (true = 100%) |
|---|---|---|
| 1 s | 61 | 71% |
| 5 s | 13 | 24% |
| 10 s | 7 | 10% |
| 30 s | 3 | 3% |
| 60 s | 2 | 1% |

- Differencing vertical rate over more than a couple of seconds averages the
  bumps away, so the RMSVA value shrinks fast. Thresholds are therefore only
  valid for the sampling interval they were calibrated at.
- In the simulation, bumpy and smooth air still separate at 5–10 s, and the
  plain spread of vertical rate (standard deviation over 5 minutes) separates
  them at every interval. That result is optimistic: the simulation has no
  autopilot oscillation, step climbs or bad data, which are the real sources
  of false positives. Only calibration against PIREPs can settle it.
- At one sample every 90 s or more (what the free quotas give) there is no
  time series to work with at all.

## 4. Recommendation

1. **Build and calibrate the detector offline on adsb.lol `globe_history`
   traces** (US region, matched to PIREPs). This removes the quota problem from
   M2 entirely and gives the demo replay day for free. The spacing inside
   those files was measured afterwards and is coarser than hoped: see
   section 5, which changes what "the detector" can be.
2. **Request an adsb.lol API key now** (their refusal message points to
   `https://www.adsb.lol/request`). With a key and 5–10 s polling, use two
   metrics side by side: RMSVA and vertical-rate spread, both calibrated at
   that interval.
3. **Register an OpenSky account** as the fallback live feed: whole India every
   ~90 s. Good enough for "where are the planes and what is their vertical
   rate right now", too sparse for per-aircraft detection. Their terms are
   research / non-commercial, which fits this project but needs attribution.
4. **Buy the RTL-SDR receiver** (about Rs 2,500–4,000) when budget allows. It
   is the only route to true 1 Hz data, it covers the Mumbai–Pune hole, and
   feeding adsb.lol is the documented way to earn API access.

Changes this forces on the reference spec:

- "Poll adsb.lol for India, no key" (feature 1) does not work as written.
- The spec's success criterion "live data less than 2 minutes old over India"
  is reachable only with a key, a receiver, or OpenSky snapshots.
- Until one of those exists, the honest live product is the weather layer plus
  a replayed/near-live map labelled as such.

## 5. Historical trace spacing (measured)

Measured with `backend/spikes/m0_trace_spacing.py` on the first 230 MB of the
`v2026.11.11-planes-readsb-prod-0` archive: 6,173 trace files. Files are
bucketed by the last two hex digits of the ICAO address, so this is a random
sample of about 6% of the day's aircraft, not a regional slice.

Interval between consecutive points at or above FL200, gaps over 120 s
excluded:

| Region | Aircraft | Cruise points | Level cruise: median / p90 | Level points 10 s apart or less | Vertical rate present |
|---|---|---|---|---|---|
| India | 141 | 58,192 | 19.4 s / 19.9 s | 27% | 100% |
| US | 1,153 | 911,189 | 19.7 s / 19.9 s | 12% | 100% |
| Elsewhere | 1,636 | 1,156,489 | 19.5 s / 19.9 s | 30% | 100% |

- The archive is thinned to **one point every ~20 s in level cruise**. It only
  stores extra points when track, altitude or vertical rate change noticeably.
- That is twice as coarse as the 10 s bar set above, so the gate **does not
  pass for RMSVA**. At 20 s the simulation recovers about 5% of the true
  RMSVA: the acceleration signal is gone.
- Two things survive at 20 s and are worth testing in M2: the spread of
  vertical rate over 3–5 minutes (9–15 samples), and the extra points
  themselves, since the archive samples faster exactly when vertical rate is
  changing.

## 6. Gate decision

- **RMSVA proper needs about 1 Hz data.** None of the free sources delivers
  it: not the unkeyed live API, not OpenSky, not the historical archive. Only
  our own receiver does, and possibly keyed adsb.lol polling at 1–5 s.
- **M1 (ingest and storage) is unaffected** and goes ahead: the schema and
  loaders are the same whichever detector wins.
- **M2 changes.** Build a coarse detector for 20 s data (vertical-rate spread,
  altitude deviation, extra-point density), calibrate it against US PIREPs,
  and keep it only if it beats the naive baseline. Call it a "roughness
  index", not RMSVA, and store the sampling interval with every event.
- If the coarse detector fails against PIREPs, the project needs the RTL-SDR
  receiver before it can make any turbulence claim.
