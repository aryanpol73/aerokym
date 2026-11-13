"""Weather decoding, crosswind maths and weather ingest row builders."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from aerokym.ingest import awc, ourairports
from aerokym.weather.crosswind import (
    WindComponents,
    components,
    heading_from_ident,
    runway_wind,
)
from aerokym.weather.decode import (
    ceiling_ft,
    cloud_text,
    compass,
    decode_metar,
    decode_taf,
    flight_category,
    visibility_km,
    weather_words,
    wind_text,
)

# Real reports captured 12 Nov 2026.
METAR = {"icaoId": "VABB", "obsTime": 1794493800, "temp": 31, "dewp": 16, "wdir": 20, "wspd": 7,
         "visib": 2.17, "altim": 1013, "wxString": "FU", "cover": "CLR", "clouds": [],
         "fltCat": "IFR", "rawOb": "METAR VABB 121430Z 02007KT 3500 FU NSC 31/16 Q1013 NOSIG"}
TAF = {"icaoId": "VABB", "issueTime": "2026-11-12T14:00:00.000Z", "validTimeFrom": 1794495600,
       "validTimeTo": 1794528000, "rawTAF": "TAF VABB 121400Z ...", "fcsts": [
           {"timeFrom": 1794495600, "timeTo": 1794517200, "timeBec": None, "fcstChange": None,
            "wdir": 40, "wspd": 5, "wgst": None, "visib": 1.86, "wxString": "HZ FU",
            "clouds": [{"cover": "NSC", "base": None}]},
           {"timeFrom": 1794517200, "timeTo": 1794528000, "timeBec": 1794524400,
            "fcstChange": "BECMG", "wdir": "VRB", "wspd": 3, "wgst": None, "visib": 1.55,
            "wxString": "HZ FU", "clouds": [{"cover": "NSC", "base": None}]}]}


# crosswind -------------------------------------------------------------------------

def test_components_head_cross_and_tail():
    assert components(90, 20, 90) == WindComponents(20.0, 0.0, "")              # straight down the runway
    c = components(180, 20, 90)                                    # from the right, 90 degrees
    assert (c.headwind_kt, c.crosswind_kt, c.crosswind_from) == (pytest.approx(0, abs=0.01), 20.0, "right")
    c = components(60, 20, 90)                                     # 30 degrees off, from the left
    assert (c.headwind_kt, c.crosswind_kt, c.crosswind_from) == (17.3, 10.0, "left")
    assert components(270, 10, 90).headwind_kt == -10.0            # tailwind
    assert components(350, 10, 10).crosswind_from == "left"        # wraps through north


def test_heading_from_ident():
    assert heading_from_ident("09L") == 90 and heading_from_ident("27") == 270
    assert heading_from_ident("36") == 0 and heading_from_ident("H1") is None
    assert heading_from_ident(None) is None


def test_runway_wind_cases():
    ok = runway_wind("09", 89.0, 20, 7, 15)
    assert ok["status"] == "ok" and ok["crosswind_from"] == "left"
    assert ok["headwind_kt"] == 2.5 and ok["crosswind_kt"] == 6.5 and ok["gust_crosswind_kt"] == 14.0
    assert runway_wind("09", None, 180, 10)["heading_deg"] == 90     # falls back to the name
    assert runway_wind("09", 89.0, "VRB", 3) == {"ident": "09", "heading_deg": 89,
                                                  "status": "variable", "max_crosswind_kt": 3.0}
    assert runway_wind("09", 89.0, 0, 0)["status"] == "calm"
    assert runway_wind("09", 89.0, None, None)["status"] == "unknown"
    assert runway_wind("H1", None, 90, 10)["status"] == "unknown"


# decode ----------------------------------------------------------------------------

def test_small_helpers():
    assert compass(20) == "north-northeast" and compass(359) == "north" and compass(225) == "southwest"
    assert visibility_km(2.17)[0] == pytest.approx(3.49, abs=0.01)
    assert visibility_km("6+") == (pytest.approx(9.66, abs=0.01), True)
    assert visibility_km(None) == (None, False)
    assert ceiling_ft([{"cover": "SCT", "base": 800}, {"cover": "BKN", "base": 2500},
                       {"cover": "OVC", "base": 9000}]) == 2500
    assert ceiling_ft([{"cover": "FEW", "base": 800}]) is None
    assert weather_words("-TSRA BR") == "light thunderstorm with rain, mist"
    assert weather_words("+SHRA VCFG") == "heavy showers of rain, fog in the vicinity"
    assert weather_words("XYZQ") == "XYZQ"            # unknown code is shown raw, not guessed
    assert wind_text("VRB", 3) == "Wind variable in direction at 3 kt"
    assert wind_text(0, 0) == "Wind calm"
    assert cloud_text([{"cover": "BKN", "base": 2500}, {"cover": "OVC", "base": 9000}]) == \
        "Broken clouds at 2,500 ft, overcast at 9,000 ft"


@pytest.mark.parametrize("vis,clouds,expected", [
    (10, [], "VFR"), (4, [], "MVFR"), (2, [], "IFR"), (0.5, [], "LIFR"),
    (10, [{"cover": "BKN", "base": 2500}], "MVFR"), (10, [{"cover": "OVC", "base": 800}], "IFR"),
    (10, [{"cover": "OVC", "base": 300}], "LIFR"), (None, None, None),
])
def test_flight_category(vis, clouds, expected):
    assert flight_category(vis, clouds) == expected


def test_decode_metar_real_report():
    d = decode_metar(METAR)
    assert d["lines"] == [
        "Wind from the north-northeast (020°) at 7 kt",
        "Visibility 3.5 km in smoke",
        "No significant cloud",
        "Temperature 31°C, dew point 16°C",
        "Pressure 1013 hPa",
    ]
    assert d["flight_category"] == "IFR" and d["visibility_km"] == 3.5
    assert (d["wind_dir"], d["wind_kt"], d["gust_kt"], d["ceiling_ft"]) == (20, 7, None, None)
    assert decode_metar({})["lines"] == ["Wind not reported", "No significant cloud"]


def test_decode_taf_real_report():
    periods = decode_taf(TAF)
    assert periods[0]["text"] == ("From 12 Nov 15:00 to 12 Nov 21:00 UTC: wind from the "
                                  "northeast (040°) at 5 kt, visibility 3.0 km, haze, smoke, "
                                  "no significant cloud")
    assert periods[1]["text"].startswith("Becoming, from 12 Nov 21:00 UTC: wind variable")
    assert periods[1]["change"] == "BECMG" and decode_taf({}) == []


# ingest ----------------------------------------------------------------------------

def test_metar_and_taf_rows():
    row = awc.metar_row(METAR)
    assert row[:3] == ("VABB", datetime.fromtimestamp(1794493800, tz=UTC), METAR["rawOb"])
    assert row[3:9] == (20, 7, None, 2.17, None, "IFR")
    assert awc.metar_row({**METAR, "wdir": "VRB"})[3] is None
    assert awc.metar_row({"icaoId": "VABB"}) is None
    t = awc.taf_row(TAF)
    assert t[0] == "VABB" and t[1] == datetime(2026, 11, 12, 14, 0, tzinfo=UTC)


def test_sigmet_and_pirep_rows():
    s = {"icaoId": "VABB", "seriesId": "A2", "validTimeFrom": 100, "validTimeTo": 200,
         "hazard": "TS", "base": None, "top": 45000, "firName": "MUMBAI", "rawSigmet": "raw",
         "coords": [{"lon": 72, "lat": 18}, {"lon": 74, "lat": 18}, {"lon": 73, "lat": 20}]}
    row = awc.sigmet_row(s)
    assert row[0] == "VABB-A2-100" and row[3] == "TS" and row[6] == 45000
    assert row[4] == "POLYGON((72 18, 74 18, 73 20, 72 18))"          # ring is closed
    assert awc.sigmet_row({**s, "coords": s["coords"][:2]}) is None    # not an area
    two = awc.sigmet_row({**s, "coords": [s["coords"], s["coords"]]})   # several separate areas
    assert two[4].startswith("MULTIPOLYGON(((72 18,") and two[4].count("((") == 2
    assert awc.sigmet_row({**s, "coords": [s["coords"]]})[4] == row[4]
    assert awc.polygon_wkt([]) is None and awc.polygon_wkt([[{"lon": 1, "lat": 1}]]) is None
    p = awc.pirep_row({"obsTime": 1794495180, "lat": 43.8, "lon": -91.2, "fltLvl": 350,
                       "tbInt1": "MOD", "tbType1": "CAT", "acType": "B38M", "rawOb": "UA /OV X"})
    assert p[2:6] == (-91.2, 43.8, 35000, "MOD") and len(p[0]) == 16
    assert awc.pirep_row({"obsTime": 1, "lat": 1, "lon": 1, "fltLvl": 0, "rawOb": ""})[4] is None


def test_shift_snapshot_moves_every_timestamp():
    snap = {"fetched_at": 1000.0, "metars": [{"obsTime": 900}],
            "tafs": [{"validTimeFrom": 1000, "validTimeTo": 5000,
                      "issueTime": "1970-01-01T00:15:00Z",
                      "fcsts": [{"timeFrom": 1000, "timeTo": 5000, "timeBec": None}]}],
            "sigmets": [{"validTimeFrom": 800, "validTimeTo": 4000}], "pireps": [{"obsTime": 950}]}
    out = awc.shift_snapshot(snap, now=11000.0)
    assert out["metars"][0]["obsTime"] == 10900 and out["pireps"][0]["obsTime"] == 10950
    assert out["tafs"][0]["fcsts"][0] == {"timeFrom": 11000, "timeTo": 15000, "timeBec": None}
    assert out["tafs"][0]["issueEpoch"] == 10900 and out["sigmets"][0]["validTimeTo"] == 14000
    assert awc.taf_row({"icaoId": "X", **out["tafs"][0]})[1] == datetime.fromtimestamp(10900, tz=UTC)


def test_fetch_snapshot_handles_empty_body():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.params["format"] == "json"
        if req.url.path.endswith("/pirep"):
            return httpx.Response(200, content=b"")          # "no data" is an empty body
        if req.url.path.endswith("/metar"):
            assert req.url.params["bbox"] == "6.0,68.0,36.0,98.0"
            return httpx.Response(200, json=[METAR])
        return httpx.Response(200, json=[])

    snap = awc.fetch_snapshot((6.0, 68.0, 36.0, 98.0),
                              httpx.Client(transport=httpx.MockTransport(handler)))
    assert len(snap["metars"]) == 1 and snap["pireps"] == [] and snap["fetched_at"] > 0


def test_bundled_airport_reference_data():
    airports, runways = ourairports.read_reference()
    by_icao = {a[0]: a for a in airports}
    assert len(airports) > 150 and {"VABB", "VIDP", "VAPO", "VOBL"} <= set(by_icao)
    assert by_icao["VAPO"][1] == "Pune International Airport"
    assert 72 < by_icao["VABB"][2] < 73 and 19 < by_icao["VABB"][3] < 20   # lon, lat
    mumbai = [r for r in runways if r[1] == "VABB"]
    assert len(mumbai) == 2 and {r[2] for r in mumbai} == {"09", "14"}
    assert all(r[1] in by_icao for r in runways)
