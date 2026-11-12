"""Aggregation and API tests. The database is replaced by a small fake."""

from __future__ import annotations

from datetime import UTC, datetime

import h3
import pytest
from fastapi.testclient import TestClient

from aerokym.aggregate.h3cells import Cell, cell_feature, hexagon
from aerokym.api import app as api
from aerokym.detect.classify import METHOD_VERSION, Detection, Severity
from aerokym.detect.roughness import WindowFeatures
from aerokym.detect.run import EVENT_H3_RES, event_row

T = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
CELL = h3.latlng_to_cell(39.0, -95.0, 4)


def make_cell(n_aircraft=3, mean_index=1.8, conf=0.9) -> Cell:
    return Cell(CELL, "FL300-390", n_aircraft, 12, mean_index, 2.4, conf, T)


# aggregation ----------------------------------------------------------------------

def test_cell_severity_follows_the_mean_index():
    assert make_cell(mean_index=1.0).severity == Severity.NONE
    assert make_cell(mean_index=1.3).severity == Severity.LIGHT
    assert make_cell(mean_index=1.8).severity == Severity.MODERATE_OR_GREATER


def test_cell_confidence_grows_with_aircraft_count():
    assert make_cell(n_aircraft=1).confidence == 0.3
    assert make_cell(n_aircraft=2).confidence == 0.6
    assert make_cell(n_aircraft=3).confidence == 0.9
    assert make_cell(n_aircraft=10).confidence == 0.9  # capped by window quality


def test_hexagon_is_a_closed_lonlat_ring_around_the_cell_centre():
    ring = hexagon(CELL)[0]
    assert len(ring) == 7 and ring[0] == ring[-1]
    lons, lats = [p[0] for p in ring], [p[1] for p in ring]
    assert min(lons) < -95.0 < max(lons) and min(lats) < 39.0 < max(lats)


def test_cell_feature_is_valid_geojson():
    f = cell_feature(make_cell())
    assert f["type"] == "Feature" and f["geometry"]["type"] == "Polygon"
    p = f["properties"]
    assert p["severity"] == 2 and p["severity_label"] == "moderate_or_greater"
    assert p["alt_band"] == "FL300-390" and p["n_aircraft"] == 3 and p["latest"] == T.isoformat()


def test_event_row_matches_the_insert_statement():
    f = WindowFeatures("abc123", T.timestamp(), 39.0, -95.0, 36012.4, 9, 20.0, 80.0,
                       float("nan"), 200.0, 5.0, 0.0)
    row = event_row(Detection(f, 1.9, Severity.MODERATE_OR_GREATER, 1.0))
    assert len(row) == 12
    assert row[:5] == ("abc123", T, -95.0, 39.0, 36012)        # lon before lat for PostGIS
    assert h3.get_resolution(row[5]) == EVENT_H3_RES
    assert row[6:] == (1.9, 2, 1.0, 20.0, 9, METHOD_VERSION)


# API ------------------------------------------------------------------------------

class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0]


class FakeConn:
    """Answers the three queries the API makes, and records their parameters."""

    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "per_aircraft" in sql:
            return FakeResult([(CELL, "FL300-390", 3, 12, 1.8, 2.4, 0.9, T)])
        if "ST_X" in sql:
            return FakeResult([("abc123", T, -95.0, 39.0, 36000, 1.93, 2, 1.0, 9)])
        if "max(time)" in sql:
            return FakeResult([(42, T)])
        return FakeResult([(1,)])


@pytest.fixture
def client():
    conn = FakeConn()
    api.app.dependency_overrides[api.get_conn] = lambda: conn
    api._hits.clear()
    c = TestClient(api.app)
    c.conn = conn
    yield c
    api.app.dependency_overrides.clear()


def test_cells_endpoint_returns_geojson_with_disclaimer_and_cache_header(client):
    r = client.get("/api/cells", params={"minutes": 60, "band": "FL300-390",
                                         "at": "2026-11-11T12:00:00Z"})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=60"
    body = r.json()
    assert body["type"] == "FeatureCollection" and len(body["features"]) == 1
    assert "Not for operational use" in body["meta"]["disclaimer"]
    assert "ODbL" in body["meta"]["attribution"]
    assert body["meta"]["method_version"] == METHOD_VERSION
    params = client.conn.calls[-1][1]
    assert params["minutes"] == 60 and params["band"] == "FL300-390" and params["at"] == T


def test_cells_endpoint_rejects_unknown_window_and_band(client):
    assert client.get("/api/cells", params={"minutes": 45}).status_code == 422
    assert client.get("/api/cells", params={"band": "FL100"}).status_code == 422


def test_events_endpoint(client):
    r = client.get("/api/events", params={"cell": CELL, "min_severity": 2})
    assert r.status_code == 200
    f = r.json()["features"][0]
    assert f["geometry"] == {"type": "Point", "coordinates": [-95.0, 39.0]}
    assert f["properties"]["index"] == 1.93 and f["properties"]["severity"] == 2
    assert client.get("/api/events", params={"cell": "x'; drop"}).status_code == 422
    assert client.get("/api/events", params={"limit": 999999}).status_code == 422


def test_meta_and_health(client):
    m = client.get("/api/meta").json()
    assert m["n_events"] == 42 and m["latest_event"] == T.isoformat()
    assert m["alt_bands"] == ["FL200-290", "FL300-390", "FL400+"]
    assert "Unvalidated outside the US" in m["validation"]
    assert client.get("/health").json() == {"status": "ok"}


def test_openapi_docs_are_served(client):
    spec = client.get("/openapi.json").json()
    assert {"/api/cells", "/api/events", "/api/meta"} <= set(spec["paths"])
    assert "Not for operational use" in spec["info"]["description"]


def test_rate_limit_returns_429_with_retry_after(client):
    for _ in range(api.RATE_LIMIT):
        assert client.get("/health").status_code == 200
    r = client.get("/health")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
