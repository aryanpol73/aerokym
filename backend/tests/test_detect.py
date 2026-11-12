"""Detector tests on synthetic tracks and on the bundled real flights."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from aerokym.detect.classify import (
    LIGHT_INDEX,
    MODERATE_INDEX,
    Severity,
    detect,
    severity,
    window_confidence,
)
from aerokym.detect.roughness import (
    BASELINE_FLOOR_FPM,
    FLEET_BASELINE_FPM,
    aircraft_baseline,
    track_features,
)
from aerokym.detect.segments import Track, WindowConfig, level_windows, track_from_positions
from aerokym.ingest.trace import decode_trace, iter_trace_docs
from aerokym.validate.pirep_match import (
    MatchConfig,
    Pirep,
    auc,
    load_iem_csv,
    match_indices,
    parse_intensity,
    precision_recall,
)

DEMO = Path(__file__).resolve().parents[2] / "data" / "demo" / "traces.tar.gz"


def make_track(alt, rate, dt=20.0) -> Track:
    """A straight-flying aircraft sampled every `dt` seconds."""
    alt = np.asarray(alt, float)
    n = len(alt)
    return Track(
        icao24="abc123",
        t=1_794_400_000 + dt * np.arange(n),
        lat=np.full(n, 39.0),
        lon=-95.0 + 0.03 * np.arange(n),
        alt_ft=alt,
        baro_rate=np.asarray(rate, float),
        geom_rate=np.full(n, np.nan),
    )


def smooth(n, alt=36000.0, seed=0):
    rng = np.random.default_rng(seed)
    return np.full(n, alt), rng.choice([-64.0, 0.0, 0.0, 0.0, 64.0], n)


# segments ---------------------------------------------------------------------

def test_level_flight_gives_windows_of_nine_samples():
    track = make_track(*smooth(90))  # 30 minutes
    wins = list(level_windows(track))
    assert len(wins) > 20
    assert all(i1 - i0 == 9 for i0, i1 in wins)  # 180 s / 20 s


def test_climb_and_low_altitude_give_no_windows():
    climb = make_track(20000 + 500 * np.arange(60), np.full(60, 1500.0))
    low = make_track(np.full(60, 12000.0), np.zeros(60))
    assert list(level_windows(climb)) == [] and list(level_windows(low)) == []


def test_guard_band_rejects_the_minutes_around_a_level_change():
    alt = np.concatenate([np.full(40, 34000.0), 34000 + 200 * np.arange(1, 11), np.full(40, 36000.0)])
    track = make_track(alt, np.zeros(len(alt)))
    cfg = WindowConfig()
    for i0, i1 in level_windows(track, cfg):
        t0, t1 = track.t[i0] - cfg.guard_s, track.t[i1 - 1] + cfg.guard_s
        guarded = track.alt_ft[(track.t >= t0) & (track.t <= t1)]
        assert guarded.max() - guarded.min() <= cfg.level_tol_ft  # never touches the climb
    assert len(list(level_windows(track))) > 0


def test_data_gap_invalidates_windows_near_it():
    alt, rate = smooth(90)
    track = make_track(alt, rate)
    t = track.t.copy()
    t[45:] += 300  # five-minute hole
    holed = Track(track.icao24, t, track.lat, track.lon, track.alt_ft, track.baro_rate,
                  track.geom_rate)
    for i0, i1 in level_windows(holed):
        assert not (i0 < 45 < i1)
        assert np.max(np.diff(holed.t[i0:i1])) <= WindowConfig().max_gap_s
    assert 0 < len(list(level_windows(holed))) < len(list(level_windows(track)))


# roughness and classification --------------------------------------------------

def test_bumpy_stretch_stands_out_against_the_aircrafts_own_baseline():
    alt, rate = smooth(180)  # one hour
    rng = np.random.default_rng(1)
    rate[90:120] = rng.normal(0, 250, 30)        # ten rough minutes
    alt[90:120] += rng.normal(0, 40, 30)
    dets = detect(make_track(alt, rate))
    t_bump = (1_794_400_000 + 20 * 95, 1_794_400_000 + 20 * 115)
    inside = [d for d in dets if t_bump[0] <= d.features.t_mid <= t_bump[1]]
    outside = [d for d in dets if d.features.t_mid < t_bump[0] - 400
               or d.features.t_mid > t_bump[1] + 400]
    assert inside and all(d.severity == Severity.MODERATE_OR_GREATER for d in inside)
    assert np.mean([d.severity == Severity.NONE for d in outside]) > 0.8
    assert np.median([d.index for d in outside]) == pytest.approx(1.0, abs=0.3)


def test_features_values():
    alt, rate = smooth(60)
    rate[:] = 0
    rate[20:29] = [100, -100] * 4 + [100]
    f = [x for x in track_features(make_track(alt, rate)) if x.baro_rate_std > 90]
    assert f and f[0].n == 9 and f[0].median_dt_s == 20 and f[0].rate_range == 200
    assert f[0].alt_ft == 36000 and f[0].alt_std < 1e-6 and f[0].dense_frac == 0
    assert np.isnan(f[0].geom_rate_std)


def test_baseline_fallbacks():
    assert aircraft_baseline(np.array([30.0] * 5)) == FLEET_BASELINE_FPM       # too few windows
    assert aircraft_baseline(np.array([5.0] * 20)) == BASELINE_FLOOR_FPM       # very quiet aircraft
    assert aircraft_baseline(np.array([50.0] * 20 + [np.nan])) == 50.0


def test_severity_thresholds_and_confidence():
    assert severity(1.0) == Severity.NONE and severity(float("nan")) == Severity.NONE
    assert severity(LIGHT_INDEX) == Severity.LIGHT
    assert severity(MODERATE_INDEX) == Severity.MODERATE_OR_GREATER
    assert window_confidence(9, True) == 1.0
    assert window_confidence(6, True) == pytest.approx(0.67)
    assert window_confidence(9, False) == 0.6


@pytest.mark.skipif(not DEMO.exists(), reason="demo sample not present")
def test_detector_runs_on_real_flights():
    n_tracks, indices = 0, []
    for doc in iter_trace_docs([DEMO]):
        dets = detect(track_from_positions(decode_trace(doc)))
        if dets:
            n_tracks += 1
            indices += [d.index for d in dets]
            assert all(d.features.alt_ft >= 20000 and d.features.n >= 6 for d in dets)
    assert n_tracks >= 30 and len(indices) > 2000
    assert np.isfinite(indices).all()
    assert 0.8 < np.median(indices) < 1.2          # index is centred on "normal"
    assert 0.02 < np.mean(np.array(indices) >= MODERATE_INDEX) < 0.30


# PIREP matching -----------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("NEG", 0), ("SMOOTH", 0), ("LGT CHOP", 1), ("CONS LGT OCNL MOD CHOP", 2),
    ("LGT-MOD", 2), ("MOD-SEV", 3), ("OCNL SEV", 3), ("None", None), ("", None),
    (None, None), ("BUMPY IN CLOUDS", None),
])
def test_parse_intensity(text, expected):
    assert parse_intensity(text) == expected


def test_load_iem_csv(tmp_path):
    p = tmp_path / "p.csv"
    p.write_text(
        "VALID,URGENT,AIRCRAFT,REPORT,ICING,TURBULENCE,ATRCC,PRODUCT_ID,FL,LAT,LON\n"
        "202611110010,F,B738,X UA /OV X/TM 0010/FL370/TP B738/TB MOD,None,MOD,ZKC,id,37000,39.0,-95.0\n"
        "202611110020,F,A320,X UA /OV X/TM 0020/FL350/TP A320/TB NEG,None,NEG,ZKC,id,None,40.0,-96.0\n"
        "202611110030,F,C172,X UA /OV X/TM 0030/TP C172/SK CLR,None,None,ZKC,id,5000,40.0,-96.0\n",
        encoding="utf-8",
    )
    out = load_iem_csv(p)
    assert [(q.intensity, q.alt_ft) for q in out] == [(2, 37000.0), (0, 35000.0)]
    assert out[0].t == 1794355800.0  # 2026-11-11 00:10 UTC


def test_match_indices_applies_all_three_tolerances():
    pirep = Pirep(1000.0, 39.0, -95.0, 37000.0, 2, "")
    t = np.array([1000.0, 1000.0, 1000.0, 5000.0, 1000.0])
    lat = np.array([39.0, 39.3, 41.0, 39.0, 39.0])
    lon = np.array([-95.0, -95.0, -95.0, -95.0, -95.0])
    alt = np.array([37000.0, 36500.0, 37000.0, 37000.0, 31000.0])
    assert list(match_indices(pirep, t, lat, lon, alt, MatchConfig(50, 1000, 600))) == [0, 1]
    assert list(match_indices(pirep, t, lat, lon, alt, MatchConfig(300, 1000, 600))) == [0, 1, 2]


def test_auc_and_precision_recall():
    assert auc(np.array([1, 2]), np.array([3, 4])) == 1.0
    assert auc(np.array([1, 2]), np.array([1, 2])) == 0.5
    assert np.isnan(auc(np.array([]), np.array([1])))
    p, r = precision_recall(np.array([1, 2, 3, 4]), np.array([0, 0, 1, 1]), 2)
    assert p == pytest.approx(2 / 3) and r == 1.0
