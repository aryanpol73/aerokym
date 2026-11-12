"""Roughness features for one level-cruise window.

Why not RMSVA: it needs about one sample per second, and the free data has one
every ~20 s (docs/m0-data-spike.md). At that spacing the acceleration is
averaged away, so instead we describe how unsteady the aircraft was over a few
minutes. Which feature actually tracks pilot reports is decided by calibration
(docs/m2-calibration.md), not assumed here.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from aerokym.detect.segments import Track, WindowConfig, level_windows

DENSE_DT_S = 10.0  # readsb stores extra points when vertical rate changes


@dataclass(frozen=True)
class WindowFeatures:
    icao24: str
    t_mid: float           # unix seconds, centre of the window
    lat: float
    lon: float
    alt_ft: float          # median altitude
    n: int                 # samples in the window
    median_dt_s: float     # typical sampling interval
    baro_rate_std: float   # ft/min; NaN if fewer than 4 values
    geom_rate_std: float   # ft/min; NaN if fewer than 4 values
    rate_range: float      # max - min of the better-populated rate, ft/min
    alt_std: float         # ft, after removing a straight-line trend
    dense_frac: float      # share of intervals shorter than DENSE_DT_S


def _std(x: np.ndarray) -> float:
    x = x[~np.isnan(x)]
    return float(np.std(x)) if len(x) >= 4 else float("nan")


def window_features(track: Track, i0: int, i1: int) -> WindowFeatures:
    t = track.t[i0:i1]
    alt = track.alt_ft[i0:i1]
    baro, geom = track.baro_rate[i0:i1], track.geom_rate[i0:i1]
    dt = np.diff(t)
    # Remove a linear trend so a slow drift of 100 ft over the window does not
    # count as roughness; what is left is the wobble around it.
    trend = np.polyval(np.polyfit(t - t[0], alt, 1), t - t[0])
    rate = baro if np.count_nonzero(~np.isnan(baro)) >= np.count_nonzero(~np.isnan(geom)) else geom
    rate = rate[~np.isnan(rate)]
    mid = (i1 - i0) // 2
    return WindowFeatures(
        icao24=track.icao24,
        t_mid=float((t[0] + t[-1]) / 2),
        lat=float(track.lat[i0 + mid]),
        lon=float(track.lon[i0 + mid]),
        alt_ft=float(np.median(alt)),
        n=int(i1 - i0),
        median_dt_s=float(np.median(dt)),
        baro_rate_std=_std(baro),
        geom_rate_std=_std(geom),
        rate_range=float(rate.max() - rate.min()) if len(rate) else float("nan"),
        alt_std=float(np.std(alt - trend)),
        dense_frac=float(np.mean(dt < DENSE_DT_S)),
    )


def track_features(track: Track, cfg: WindowConfig | None = None) -> Iterator[WindowFeatures]:
    """Features for every valid level-cruise window of one aircraft."""
    for i0, i1 in level_windows(track, cfg):
        yield window_features(track, i0, i1)


# Roughness index ------------------------------------------------------------
#
# Raw vertical-rate spread is not comparable between aircraft: some avionics
# report a rock-steady 0 ft/min in smooth air, others jitter by +-64 all the
# time. Calibration showed that dividing by the aircraft's own typical spread
# tracks pilot reports better than any raw feature (docs/m2-calibration.md).

FLEET_BASELINE_FPM = 27.0   # median spread over all aircraft, used when we know too little
BASELINE_FLOOR_FPM = 20.0   # stops a very quiet aircraft from turning tiny wobbles into alarms
MIN_WINDOWS_FOR_BASELINE = 10


def aircraft_baseline(rate_stds: np.ndarray) -> float:
    """The aircraft's own normal vertical-rate spread: its median over the flight."""
    valid = rate_stds[~np.isnan(rate_stds)]
    if len(valid) < MIN_WINDOWS_FOR_BASELINE:
        return FLEET_BASELINE_FPM
    return max(float(np.median(valid)), BASELINE_FLOOR_FPM)


def roughness_index(features: list[WindowFeatures]) -> np.ndarray:
    """Index per window for ONE aircraft: 1.0 = its normal, 2.0 = twice as unsteady.

    Known limit: if an aircraft is in rough air for most of its flight, its
    baseline rises and the index understates it.
    """
    stds = np.array([f.baro_rate_std for f in features], dtype=float)
    # Fall back to the geometric rate for aircraft that send no barometric rate.
    geom = np.array([f.geom_rate_std for f in features], dtype=float)
    stds = np.where(np.isnan(stds), geom, stds)
    return stds / aircraft_baseline(stds)
