"""Validation scoring, the accuracy endpoint and the validation-box budget split."""

from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aerokym.api import app as api
from aerokym.detect.classify import METHOD_VERSION, MODERATE_INDEX
from aerokym.ingest import adsblol, opensky
from aerokym.validate.pirep_match import Pirep
from aerokym.validate.run import DayScore, ratio, score_day

INDIA = (6.0, 68.0, 36.0, 98.0)
US = (30.0, -105.0, 45.0, -85.0)


def pirep(lat, intensity, t=1000.0):
    return Pirep(t, lat, -95.0, 35000.0, intensity, "")


def windows(rows):
    """rows: (lat, index). All at the same time, longitude and altitude."""
    a = np.array(rows, dtype=float)
    n = len(a)
    return np.full(n, 1000.0), a[:, 0], np.full(n, -95.0), np.full(n, 35000.0), a[:, 1]


def test_score_day_counts_each_outcome():
    t, lat, lon, alt, idx = windows([
        (30.0, 2.0), (30.1, 2.2),     # rough air near report A
        (32.0, 1.0),                  # smooth near B
        (34.0, 1.9),                  # rough near C
        (36.0, 1.0), (36.1, 1.1),     # smooth near D
    ])
    reports = [
        pirep(30.0, 2),   # A: we said rough, pilot said moderate  -> true positive
        pirep(32.0, 2),   # B: we said smooth, pilot said moderate -> miss
        pirep(34.0, 0),   # C: we said rough, pilot said smooth    -> false alarm
        pirep(36.0, 1),   # D: we said smooth, pilot said light    -> correct, not positive
        pirep(50.0, 3),   # E: nobody flew near it                 -> unscored
    ]
    s = score_day(reports, t, lat, lon, alt, idx)
    assert (s.n_pireps, s.n_matched) == (5, 4)
    assert (s.n_smooth, s.n_light, s.n_moderate_plus) == (1, 1, 2)
    assert (s.tp, s.fp, s.fn) == (1, 1, 1)
    assert s.precision == 0.5 and s.recall == 0.5
    assert s.auc is None            # far too few reports for an AUC


def test_score_day_uses_mean_of_matched_windows_and_threshold():
    t, lat, lon, alt, idx = windows([(30.0, MODERATE_INDEX + 1.0), (30.1, MODERATE_INDEX - 1.2)])
    s = score_day([pirep(30.0, 2)], t, lat, lon, alt, idx)
    assert (s.tp, s.fn) == (0, 1)   # mean is below the threshold although one window is above


def test_score_day_with_nothing_to_score():
    empty = np.array([])
    s = score_day([pirep(30.0, 2)], empty, empty, empty, empty, empty)
    assert s == DayScore(1, 0, 0, 0, 0, 0, 0, 0, None, None, None)
    assert score_day([], *windows([(30.0, 1.0)])).n_pireps == 0


def test_score_day_reports_auc_when_there_are_enough_reports():
    rng = np.random.default_rng(0)
    rows, reports = [], []
    for i in range(30):
        lat = 20.0 + i          # reports far apart, so each matches only its own window
        rough = i % 2 == 0
        rows.append((lat, 2.0 + rng.random() if rough else 1.0 + rng.random() * 0.3))
        reports.append(pirep(lat, 2 if rough else 0))
    s = score_day(reports, *windows(rows))
    assert s.auc == 1.0 and s.precision == 1.0 and s.recall == 1.0 and s.n_matched == 30


def test_ratio():
    assert ratio(1, 4) == 0.25 and ratio(0, 0) is None


# budget split between the map region and the validation box ---------------------------

def test_opensky_cycle_interval_accounts_for_validation_box():
    assert opensky.credit_cost(US) == 3
    assert opensky.cycle_interval_s(INDIA, None, 4, 3600) == pytest.approx(96.0)
    assert opensky.cycle_interval_s(INDIA, US, 4, 3600) == pytest.approx(114.0)   # 4 + 3/4 credits
    assert opensky.cycle_interval_s(INDIA, US, 1, 3600) == pytest.approx(168.0)
    # Stays inside the daily budget:
    cycles = 86400 / opensky.cycle_interval_s(INDIA, US, 4, 3600)
    assert cycles * 4 + cycles / 4 * 3 == pytest.approx(3600)


def test_adsblol_pick_boxes():
    assert adsblol.pick_boxes(1, INDIA, US, 3) == [INDIA, US]
    assert adsblol.pick_boxes(3, INDIA, US, 3) == [US, INDIA]     # every third aircraft
    assert adsblol.pick_boxes(3, INDIA, None, 3) == [INDIA]
    assert adsblol.pick_boxes(3, INDIA, US, 0) == [INDIA]
    share = sum(adsblol.pick_boxes(n, INDIA, US, 3)[0] == US for n in range(1, 301)) / 300
    assert share == pytest.approx(1 / 3)


# accuracy endpoint ----------------------------------------------------------------------

class FakeConn:
    def __init__(self, rows):
        self.rows, self.params = rows, None

    def execute(self, sql, params=None):
        self.params = params
        return self

    def fetchall(self):
        return self.rows


def run_row(day, tp, fp, fn, matched):
    return (date.fromisoformat(day), "us-central", 400, matched, 10, 12, tp + fn, tp, fp, fn,
            None, None, None, 40, 5000, "50 km / 1000 ft / 10 min",
            datetime(2026, 11, 20, tzinfo=UTC))


def client_with(rows):
    conn = FakeConn(rows)
    api.app.dependency_overrides[api.get_conn] = lambda: conn
    api._hits.clear()
    return TestClient(api.app), conn


def test_accuracy_endpoint_pools_counts_not_ratios():
    c, conn = client_with([run_row("2026-11-19", 3, 1, 1, 20), run_row("2026-11-18", 1, 3, 5, 30)])
    try:
        body = c.get("/api/accuracy", params={"days": 7}).json()
    finally:
        api.app.dependency_overrides.clear()
    p = body["pooled"]
    assert (p["tp"], p["fp"], p["fn"], p["n_matched"], p["days"]) == (4, 4, 6, 50, 2)
    assert p["precision"] == 0.5 and p["recall"] == 0.4      # from summed counts
    assert p["naive_precision"] == 0.2                        # 10 moderate+ of 50 matched
    assert len(body["runs"]) == 2 and body["runs"][0]["date"] == "2026-11-19"
    assert body["calibration"]["precision"] == 0.51 and "optimistic" in body["calibration"]["caveat"]
    assert any("unvalidated" in x for x in body["caveats"])
    assert body["meta"]["method_version"] == METHOD_VERSION and conn.params[0] == METHOD_VERSION


def test_accuracy_endpoint_with_no_runs_yet():
    c, _ = client_with([])
    try:
        body = c.get("/api/accuracy").json()
        assert c.get("/api/accuracy", params={"days": 0}).status_code == 422
    finally:
        api.app.dependency_overrides.clear()
    assert body["runs"] == [] and body["pooled"]["precision"] is None
    assert body["pooled"]["naive_precision"] is None and body["pooled"]["days"] == 0
