"""From roughness index to a severity label and a confidence.

The thresholds come from calibration against US pilot reports on one day
(11 Nov 2026, see docs/method.md). They are provisional: every stored event
carries METHOD_VERSION so that results from different calibrations are never
mixed on the accuracy page.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np

from aerokym.detect.roughness import (
    MIN_WINDOWS_FOR_BASELINE,
    WindowFeatures,
    roughness_index,
    track_features,
)
from aerokym.detect.segments import Track, WindowConfig

METHOD_VERSION = "roughness-0.1"

# Index values that fire on 25% and 10% of pilot reports of SMOOTH air.
LIGHT_INDEX = 1.22
MODERATE_INDEX = 1.60


class Severity(IntEnum):
    NONE = 0
    LIGHT = 1
    # Only 3 severe reports existed in the calibration day, so "severe" cannot
    # be told apart from "moderate" yet. We do not pretend otherwise.
    MODERATE_OR_GREATER = 2


def severity(index: float) -> Severity:
    if not np.isfinite(index) or index < LIGHT_INDEX:
        return Severity.NONE
    return Severity.LIGHT if index < MODERATE_INDEX else Severity.MODERATE_OR_GREATER


def window_confidence(n_samples: int, has_own_baseline: bool) -> float:
    """0..1: how much one window's index can be trusted.

    More samples in the window is better (9 = one every 20 s for 3 minutes),
    and an index measured against the fleet average instead of the aircraft's
    own baseline is worth less. Confidence for a map cell also depends on how
    many aircraft agree; that is added at aggregation time.
    """
    return round(min(1.0, n_samples / 9) * (1.0 if has_own_baseline else 0.6), 2)


@dataclass(frozen=True)
class Detection:
    features: WindowFeatures
    index: float
    severity: Severity
    confidence: float
    method_version: str = METHOD_VERSION


def detect(track: Track, cfg: WindowConfig | None = None) -> list[Detection]:
    """Run the whole detector on one aircraft's track."""
    feats = list(track_features(track, cfg))
    if not feats:
        return []
    own = len(feats) >= MIN_WINDOWS_FOR_BASELINE
    return [
        Detection(f, float(i), severity(float(i)), window_confidence(f.n, own))
        for f, i in zip(feats, roughness_index(feats), strict=True)
    ]
