"""Turn one aircraft's positions into level-cruise analysis windows.

Turbulence detection only makes sense while an aircraft is trying to hold an
altitude. In a climb, descent or level change the vertical rate moves for
reasons that have nothing to do with rough air, so those parts are rejected
here, before any feature is computed.

Pure numpy, no database: easy to test and to reuse in notebooks.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import numpy as np

from aerokym.models import Position


@dataclass(frozen=True)
class Track:
    """One aircraft's samples as parallel arrays, sorted by time."""

    icao24: str
    t: np.ndarray          # unix seconds
    lat: np.ndarray
    lon: np.ndarray
    alt_ft: np.ndarray     # barometric altitude; NaN when unknown
    baro_rate: np.ndarray  # ft/min; NaN when unknown
    geom_rate: np.ndarray  # ft/min; NaN when unknown

    def __len__(self) -> int:
        return len(self.t)


def _arr(values: Iterable[float | int | None]) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in values], dtype=float)


def track_from_positions(positions: Iterable[Position]) -> Track:
    ps = sorted((p for p in positions if not p.on_ground), key=lambda p: p.time)
    return Track(
        icao24=ps[0].icao24 if ps else "",
        t=np.array([p.time.timestamp() for p in ps], dtype=float),
        lat=np.array([p.lat for p in ps], dtype=float),
        lon=np.array([p.lon for p in ps], dtype=float),
        alt_ft=_arr(p.baro_alt_ft for p in ps),
        baro_rate=_arr(p.baro_rate_fpm for p in ps),
        geom_rate=_arr(p.geom_rate_fpm for p in ps),
    )


@dataclass(frozen=True)
class WindowConfig:
    window_s: float = 180.0    # length of one analysis window
    step_s: float = 60.0       # windows overlap: a new one starts every minute
    guard_s: float = 60.0      # the aircraft must also be level this long before and after
    min_alt_ft: float = 20000.0
    level_tol_ft: float = 300.0  # every altitude within this of the window median
    max_gap_s: float = 45.0    # a longer hole in the data invalidates the window
    min_samples: int = 6


def level_windows(track: Track, cfg: WindowConfig | None = None) -> Iterator[tuple[int, int]]:
    """Yield (start, stop) index pairs of valid level-cruise windows.

    A window is kept only if, over the window plus a guard band on each side:
      * the data has no hole longer than `max_gap_s`,
      * the aircraft stays at or above `min_alt_ft`,
      * every altitude is within `level_tol_ft` of the window's median.

    The guard band is what rejects level-offs: the minute after a climb ends
    still has a settling vertical rate, and would look like turbulence.
    """
    cfg = cfg or WindowConfig()
    t, alt = track.t, track.alt_ft
    if len(t) < cfg.min_samples:
        return
    start = t[0] + cfg.guard_s
    last_start = t[-1] - cfg.guard_s - cfg.window_s
    while start <= last_start:
        end = start + cfg.window_s
        i0, i1 = np.searchsorted(t, [start, end])
        g0, g1 = np.searchsorted(t, [start - cfg.guard_s, end + cfg.guard_s])
        start += cfg.step_s
        if i1 - i0 < cfg.min_samples:
            continue
        tg, ag = t[g0:g1], alt[g0:g1]
        # Coverage: the samples must reach both ends of the guarded span...
        if tg[0] > end - cfg.window_s - cfg.guard_s + cfg.max_gap_s:
            continue
        if tg[-1] < end + cfg.guard_s - cfg.max_gap_s:
            continue
        # ...with no hole in between.
        if np.max(np.diff(tg)) > cfg.max_gap_s:
            continue
        if np.isnan(ag).any() or ag.min() < cfg.min_alt_ft:
            continue
        if np.max(np.abs(ag - np.median(alt[i0:i1]))) > cfg.level_tol_ft:
            continue
        yield int(i0), int(i1)
