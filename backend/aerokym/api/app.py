"""Public read-only API.

    uvicorn aerokym.api.app:app --port 8000        docs at /docs

Design rules:
  * Never calls an outside data source: it only reads what the workers stored.
  * Every response carries the disclaimer and the data licence.
  * Responses are cacheable for 60 s, so a CDN can absorb a traffic spike.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from aerokym import __version__
from aerokym.aggregate.h3cells import (
    BANDS,
    MAP_H3_RES,
    WINDOWS_MIN,
    cell_feature,
    fetch_cells,
)
from aerokym.api.accuracy import router as accuracy_router
from aerokym.api.deps import ATTRIBUTION, DISCLAIMER, At, Conn, _at, _meta, get_conn  # noqa: F401
from aerokym.api.weather import router as weather_router
from aerokym.detect.classify import LIGHT_INDEX, METHOD_VERSION, MODERATE_INDEX

CACHE_SECONDS = 60
RATE_LIMIT = 60          # requests
RATE_WINDOW_S = 60.0     # per this many seconds, per client address

app = FastAPI(
    title="Aerokym API",
    version=__version__,
    description=f"Open turbulence-roughness map from public ADS-B data.\n\n**{DISCLAIMER}**\n\n"
                f"{ATTRIBUTION}",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"])
app.include_router(weather_router)
app.include_router(accuracy_router)

_hits: dict[str, deque[float]] = defaultdict(deque)


@app.middleware("http")
async def rate_limit_and_cache(request: Request, call_next):
    """In-memory limiter: fine for one process. Behind a proxy, limit there too,
    because every request then appears to come from the proxy's address."""
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    q = _hits[client]
    while q and now - q[0] > RATE_WINDOW_S:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        retry = int(RATE_WINDOW_S - (now - q[0])) + 1
        return JSONResponse({"detail": "rate limit exceeded"}, status_code=429,
                            headers={"Retry-After": str(retry)})
    q.append(now)
    response = await call_next(request)
    if request.method == "GET" and response.status_code == 200:
        response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    return response


@app.get("/health")
def health(conn: Conn) -> dict:
    conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/meta")
def meta(conn: Conn) -> dict:
    """What the numbers mean, and how fresh the data is."""
    n, latest = conn.execute(
        "SELECT count(*), max(time) FROM turb_events WHERE method_version = %s",
        (METHOD_VERSION,)).fetchone()
    return _meta(
        datetime.now(UTC),
        latest_event=latest.isoformat() if latest else None,
        n_events=n,
        alt_bands=list(BANDS),
        windows_minutes=list(WINDOWS_MIN),
        h3_resolution=MAP_H3_RES,
        severity={"0": "none", "1": f"light (index >= {LIGHT_INDEX})",
                  "2": f"moderate or greater (index >= {MODERATE_INDEX})"},
        validation="Calibrated on US pilot reports for one day. Unvalidated outside the US.",
    )


@app.get("/api/cells")
def cells(
    conn: Conn,
    minutes: Annotated[int, Query(description="Time window: 15, 30 or 60")] = 30,
    band: Annotated[Literal["FL200-290", "FL300-390", "FL400+"] | None,
                    Query(description="Altitude band. Default: all")] = None,
    at: At = None,
) -> dict:
    """Hex cells (GeoJSON) with roughness severity and confidence.

    Cells with severity 0 are included: they are places where aircraft flew
    and felt nothing, which is information too.
    """
    if minutes not in WINDOWS_MIN:
        raise HTTPException(422, "minutes must be 15, 30 or 60")
    ref = _at(at)
    found = fetch_cells(conn, ref, minutes, band)
    return {"type": "FeatureCollection",
            "meta": _meta(ref, minutes=minutes, band=band, n_cells=len(found)),
            "features": [cell_feature(c) for c in found]}


@app.get("/api/events")
def events(
    conn: Conn,
    cell: Annotated[str | None, Query(description="H3 map cell id from /api/cells")] = None,
    minutes: Annotated[int, Query(ge=1, le=180)] = 60,
    min_severity: Annotated[int, Query(ge=0, le=2)] = 1,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    at: At = None,
) -> dict:
    """Individual detector windows (GeoJSON points), newest first."""
    ref = _at(at)
    if cell is not None and not (len(cell) == 15 and cell.isalnum()):
        raise HTTPException(422, "cell must be a 15-character H3 index")
    rows = conn.execute(
        "SELECT icao24, time, ST_X(geom), ST_Y(geom), alt_ft, roughness_index, severity, "
        "confidence, n_samples FROM turb_events "
        "WHERE method_version = %(m)s AND severity >= %(sev)s "
        "AND time > %(at)s - make_interval(mins => %(min)s) AND time <= %(at)s "
        "AND (%(cell)s::text IS NULL OR h3_cell_to_parent(h3_cell, %(res)s) = %(cell)s::h3index) "
        "ORDER BY time DESC LIMIT %(limit)s",
        {"m": METHOD_VERSION, "sev": min_severity, "at": ref, "min": minutes, "cell": cell,
         "res": MAP_H3_RES, "limit": limit}).fetchall()
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(r[2], 4), round(r[3], 4)]},
        "properties": {"icao24": r[0], "time": r[1].isoformat(), "alt_ft": r[4],
                       "index": round(r[5], 2), "severity": r[6], "confidence": r[7],
                       "n_samples": r[8]},
    } for r in rows]
    return {"type": "FeatureCollection", "meta": _meta(ref, minutes=minutes, n_events=len(rows)),
            "features": features}
