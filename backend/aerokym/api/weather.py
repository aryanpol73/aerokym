"""Airports and official weather: read-only routes over what awc.py stored."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from aerokym.api.deps import At, Conn, _at, _meta
from aerokym.weather.crosswind import runway_wind
from aerokym.weather.decode import decode_metar, decode_taf

router = APIRouter(prefix="/api", tags=["weather"])

WEATHER_ATTRIBUTION = (
    "Weather: aviationweather.gov (US government, public domain). "
    "Airports and runways: OurAirports (public domain)."
)
METAR_MAX_AGE_H = 3        # an older report is shown as "no current report"
NEARBY_PIREP_KM = 200
NEARBY_SIGMET_KM = 300


def _wmeta(at, **extra) -> dict:
    return {**_meta(at, **extra), "weather_attribution": WEATHER_ATTRIBUTION}


@router.get("/airports")
def airports(conn: Conn, at: At = None) -> dict:
    """Airports (GeoJSON points) with the flight category of their latest METAR.

    Flight category: VFR, MVFR, IFR or LIFR, from cloud ceiling and visibility.
    `null` means no report in the last 3 hours.
    """
    ref = _at(at)
    rows = conn.execute(
        "SELECT a.icao, a.name, a.city, a.iata, a.kind, ST_X(a.geom), ST_Y(a.geom), "
        "       m.flight_cat, m.obs_time "
        "FROM airports a LEFT JOIN LATERAL ("
        "  SELECT flight_cat, obs_time FROM metars WHERE station = a.icao "
        "  AND obs_time <= %(at)s AND obs_time > %(at)s - make_interval(hours => %(h)s) "
        "  ORDER BY obs_time DESC LIMIT 1) m ON true ORDER BY a.icao",
        {"at": ref, "h": METAR_MAX_AGE_H}).fetchall()
    return {"type": "FeatureCollection", "meta": _wmeta(ref, n_airports=len(rows)),
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(r[5], 4), round(r[6], 4)]},
                "properties": {"icao": r[0], "name": r[1], "city": r[2], "iata": r[3],
                               "kind": r[4], "flight_category": r[7],
                               "obs_time": r[8].isoformat() if r[8] else None},
            } for r in rows]}


@router.get("/airports/{icao}")
def airport(icao: str, conn: Conn, at: At = None) -> dict:
    """One airport: decoded METAR and TAF, wind components per runway, nearby hazards."""
    icao = icao.upper()
    if not (len(icao) == 4 and icao.isalnum()):
        raise HTTPException(422, "icao must be a 4-character airport code")
    ref = _at(at)
    a = conn.execute(
        "SELECT icao, name, city, iata, country, elevation_ft, ST_X(geom), ST_Y(geom) "
        "FROM airports WHERE icao = %s", (icao,)).fetchone()
    if a is None:
        raise HTTPException(404, f"unknown airport {icao}")

    m = conn.execute(
        "SELECT obs_time, raw, data FROM metars WHERE station = %s AND obs_time <= %s "
        "AND obs_time > %s - make_interval(hours => %s) ORDER BY obs_time DESC LIMIT 1",
        (icao, ref, ref, METAR_MAX_AGE_H)).fetchone()
    metar = None
    wind = {"dir": None, "kt": None, "gust": None}
    if m:
        decoded = decode_metar(m[2] or {})
        metar = {"obs_time": m[0].isoformat(), "raw": m[1], **decoded}
        wind = {"dir": decoded["wind_dir"], "kt": decoded["wind_kt"], "gust": decoded["gust_kt"]}

    t = conn.execute(
        "SELECT issue_time, valid_from, valid_to, raw, data FROM tafs WHERE station = %s "
        "AND valid_to > %s AND issue_time <= %s ORDER BY issue_time DESC LIMIT 1",
        (icao, ref, ref)).fetchone()
    taf = None
    if t:
        taf = {"issue_time": t[0].isoformat(), "valid_from": t[1].isoformat(),
               "valid_to": t[2].isoformat(), "raw": t[3], "periods": decode_taf(t[4] or {})}

    runways = []
    for le, he, le_h, he_h, length, surface in conn.execute(
            "SELECT le_ident, he_ident, le_heading_deg, he_heading_deg, length_ft, surface "
            "FROM runways WHERE airport_icao = %s ORDER BY length_ft DESC NULLS LAST", (icao,)):
        runways.append({
            "length_ft": length, "surface": surface,
            "ends": [runway_wind(ident, hdg, wind["dir"], wind["kt"], wind["gust"])
                     for ident, hdg in ((le, le_h), (he, he_h))],
        })

    here = "(SELECT geom::geography FROM airports WHERE icao = %(icao)s)"
    sigmets = [
        {"id": r[0], "hazard": r[1], "fir": r[2], "valid_to": r[3].isoformat(),
         "alt_lo_ft": r[4], "alt_hi_ft": r[5], "distance_km": round(r[6] / 1000)}
        for r in conn.execute(
            f"SELECT id, hazard, fir, valid_to, alt_lo_ft, alt_hi_ft, "
            f"ST_Distance(geom::geography, {here}) AS d FROM sigmets "
            f"WHERE valid_from <= %(at)s AND valid_to > %(at)s "
            f"AND ST_DWithin(geom::geography, {here}, %(m)s) ORDER BY d",
            {"icao": icao, "at": ref, "m": NEARBY_SIGMET_KM * 1000})]
    pireps = [
        {"obs_time": r[0].isoformat(), "alt_ft": r[1], "turbulence": r[2], "raw": r[3],
         "distance_km": round(r[4] / 1000)}
        for r in conn.execute(
            f"SELECT obs_time, alt_ft, turb_intensity, raw, "
            f"ST_Distance(geom::geography, {here}) AS d FROM pireps "
            f"WHERE obs_time <= %(at)s AND obs_time > %(at)s - interval '3 hours' "
            f"AND ST_DWithin(geom::geography, {here}, %(m)s) ORDER BY obs_time DESC LIMIT 20",
            {"icao": icao, "at": ref, "m": NEARBY_PIREP_KM * 1000})]

    return {
        "meta": _wmeta(ref),
        "airport": {"icao": a[0], "name": a[1], "city": a[2], "iata": a[3], "country": a[4],
                    "elevation_ft": a[5], "lon": round(a[6], 4), "lat": round(a[7], 4)},
        "metar": metar,
        "taf": taf,
        "runways": runways,
        "nearby_sigmets": sigmets,
        "nearby_pireps": pireps,
    }


@router.get("/sigmets")
def sigmets(conn: Conn, at: At = None) -> dict:
    """SIGMET hazard areas valid at the reference time (GeoJSON polygons, worldwide)."""
    ref = _at(at)
    rows = conn.execute(
        "SELECT id, hazard, fir, qualifier, valid_from, valid_to, alt_lo_ft, alt_hi_ft, raw, "
        "ST_AsGeoJSON(geom, 4) FROM sigmets WHERE valid_from <= %s AND valid_to > %s",
        (ref, ref)).fetchall()
    return {"type": "FeatureCollection", "meta": _wmeta(ref, n_sigmets=len(rows)),
            "features": [{
                "type": "Feature", "geometry": json.loads(r[9]),
                "properties": {"id": r[0], "hazard": r[1], "fir": r[2], "qualifier": r[3],
                               "valid_from": r[4].isoformat(), "valid_to": r[5].isoformat(),
                               "alt_lo_ft": r[6], "alt_hi_ft": r[7], "raw": r[8]},
            } for r in rows]}


@router.get("/pireps")
def pireps(conn: Conn, hours: Annotated[int, Query(ge=1, le=12)] = 3, at: At = None) -> dict:
    """Pilot reports from the last few hours (GeoJSON points). Sparse outside the US."""
    ref = _at(at)
    rows = conn.execute(
        "SELECT id, obs_time, ST_X(geom), ST_Y(geom), alt_ft, turb_intensity, aircraft_type, raw "
        "FROM pireps WHERE obs_time <= %s AND obs_time > %s - make_interval(hours => %s) "
        "ORDER BY obs_time DESC LIMIT 1000", (ref, ref, hours)).fetchall()
    return {"type": "FeatureCollection", "meta": _wmeta(ref, n_pireps=len(rows)),
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(r[2], 4), round(r[3], 4)]},
                "properties": {"id": r[0], "obs_time": r[1].isoformat(), "alt_ft": r[4],
                               "turbulence": r[5], "aircraft_type": r[6], "raw": r[7]},
            } for r in rows]}
