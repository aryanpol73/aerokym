# How Aerokym detects rough air

Method version: `roughness-0.1` (provisional, calibrated on one day).

## Why not RMSVA

The research method is RMSVA: the root-mean-square of vertical acceleration,
taken from how fast an aircraft's vertical rate changes. It needs about one
sample per second. The free historical data has one sample every ~20 s in
level cruise ([M0 findings](m0-data-spike.md)), which averages the
acceleration away. So Aerokym measures something coarser and names it
honestly: a **roughness index**, not RMSVA and not EDR.

## The method

Code: `backend/aerokym/detect/`.

1. **Keep level cruise only** (`segments.py`). Slide a 3-minute window along
   each aircraft's track, one step per minute. Keep a window only if, across
   the window plus one minute on each side:
   - the aircraft is at or above FL200,
   - every altitude is within 300 ft of the window's median,
   - there is no data hole longer than 45 s, and at least 6 samples.

   The one-minute guard on each side rejects level-offs, where vertical rate
   is still settling after a climb or descent.

2. **Measure unsteadiness** (`roughness.py`). For each window, the standard
   deviation of the reported vertical rate, in ft/min.

3. **Compare the aircraft with itself.** Some avionics report a steady
   0 ft/min in smooth air, others flicker by 64 ft/min all the time. So:

   ```
   index = window spread / aircraft's median spread over its whole flight
   ```

   The baseline is floored at 20 ft/min, and aircraft with fewer than 10
   windows use the fleet median (27 ft/min). 1.0 means "normal for this
   aircraft"; 2.0 means twice as unsteady.

4. **Classify** (`classify.py`):

   | Index | Label |
   |---|---|
   | below 1.22 | none |
   | 1.22 to 1.60 | light |
   | 1.60 and above | moderate or greater |

   There is no "severe" class: the calibration day had 3 severe reports, too
   few to place a threshold.

5. **Confidence per window**: `min(1, samples / 9)`, multiplied by 0.6 when
   the aircraft had no baseline of its own. Agreement between several
   aircraft in one map cell is added at aggregation time (M3).

## Calibration against pilot reports

Reproduce with `backend/spikes/m2_calibrate.py`.

- **Aircraft data:** the first 523 MB of adsb.lol's 11 Nov 2026 archive, a
  random sample of roughly 14% of the day's aircraft. Over the US that gave
  337,541 windows from 2,515 aircraft.
- **Ground truth:** 1,614 cruise-level PIREPs with a turbulence remark over
  the US that day, from the Iowa State IEM archive: 323 smooth, 617 light,
  671 moderate, 3 severe.
- **Matching:** a report is compared with the mean index of every window from
  any aircraft that passed close to it. The reporting aircraft itself is
  usually not in the sample, so this tests the map's actual claim: what
  nearby aircraft felt.

AUC is the chance that a moderate-or-worse report scores higher than a smooth
one (0.5 = coin flip, 1.0 = perfect):

| Match tolerance | Reports matched (smooth / light / moderate+) | Index | Raw spread | 95% interval for the index |
|---|---|---|---|---|
| 100 km, 2000 ft, 20 min | 217 / 476 / 483 | 0.70 | 0.64 | 0.66–0.74 |
| 50 km, 1000 ft, 10 min | 93 / 190 / 146 | 0.79 | 0.74 | 0.74–0.85 |
| 30 km, 1000 ft, 10 min | 64 / 131 / 93 | 0.83 | 0.79 | 0.76–0.88 |

The closer the aircraft was to the report, the better the agreement, which is
what a real signal should do. Other candidate features were weaker at 50 km:
altitude wobble 0.64, share of extra trace points 0.63.

Thresholds were set on a random half of the 50 km matches (193 reports) so
that they fire on 25% and 10% of smooth reports, then scored on the other 236:

| Label | Precision | Recall | Naive "always yes" precision |
|---|---|---|---|
| light or worse (index ≥ 1.22) | 0.89 | 0.59 | 0.78 |
| moderate or worse (index ≥ 1.55) | 0.51 | 0.51 | 0.33 |

Held-out AUC for moderate-or-worse against smooth: 0.79 (79 vs 52 reports).
The shipped thresholds (1.22 and 1.60) are refitted on all smooth reports.

## What this does and does not show

- The index carries real information about turbulence that pilots report, and
  beats the naive baseline. It is far from a precise instrument: at the
  moderate threshold, half the alarms are wrong and half the events are
  missed.
- **One day only.** The feature (per-aircraft ratio) and the thresholds were
  chosen on the same day's data, so these numbers are optimistic. They need
  repeating on other days before any accuracy claim is published.
- **US only.** There are almost no PIREPs over India, so results there are
  unvalidated.
- PIREPs are subjective and skewed: pilots report rough air more readily than
  smooth air, so precision against PIREPs is not precision against the sky.
- An aircraft that spends most of its flight in rough air raises its own
  baseline and is under-called.
- Convective weather, mountain wave and wake encounters are not told apart.
- Nothing here is operational or safety guidance.
