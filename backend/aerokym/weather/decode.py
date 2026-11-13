"""Turn decoded METAR/TAF fields into plain language.

aviationweather.gov already parses the raw report into fields (wind, clouds,
visibility...). This module does the last step: sentences a non-pilot can
read. Distances are shown in kilometres and feet, as used in India.
"""

from __future__ import annotations

from datetime import UTC, datetime

SM_TO_KM = 1.609344
COMPASS = ["north", "north-northeast", "northeast", "east-northeast", "east", "east-southeast",
           "southeast", "south-southeast", "south", "south-southwest", "southwest",
           "west-southwest", "west", "west-northwest", "northwest", "north-northwest"]
COVER = {"FEW": "a few clouds", "SCT": "scattered clouds", "BKN": "broken clouds",
         "OVC": "overcast", "OVX": "sky obscured"}
CLEAR = {"CLR", "SKC", "NSC", "NCD", "CAVOK"}
DESCRIPTOR = {"MI": "shallow", "BC": "patches of", "PR": "partial", "DR": "low drifting",
              "BL": "blowing", "SH": "showers of", "TS": "thunderstorm with", "FZ": "freezing"}
PHENOMENON = {"DZ": "drizzle", "RA": "rain", "SN": "snow", "SG": "snow grains",
              "IC": "ice crystals", "PL": "ice pellets", "GR": "hail", "GS": "small hail",
              "UP": "unknown precipitation", "BR": "mist", "FG": "fog", "FU": "smoke",
              "VA": "volcanic ash", "DU": "dust", "SA": "sand", "HZ": "haze", "PY": "spray",
              "PO": "dust whirls", "SQ": "squalls", "FC": "funnel cloud", "SS": "sandstorm",
              "DS": "duststorm"}
CEILING_COVERS = {"BKN", "OVC", "OVX"}


def compass(deg: float) -> str:
    return COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


def visibility_km(visib) -> tuple[float | None, bool]:
    """AWC gives statute miles, sometimes as "10+" or "6+". Returns (km, is_at_least)."""
    if visib is None or visib == "":
        return None, False
    plus = isinstance(visib, str) and visib.endswith("+")
    try:
        return float(str(visib).rstrip("+")) * SM_TO_KM, plus
    except ValueError:
        return None, False


def ceiling_ft(clouds: list[dict] | None) -> int | None:
    """Lowest broken or overcast layer: the height that limits visual flying."""
    bases = [c["base"] for c in clouds or []
             if c.get("cover") in CEILING_COVERS and c.get("base") is not None]
    return min(bases) if bases else None


def flight_category(visib, clouds: list[dict] | None) -> str | None:
    """VFR / MVFR / IFR / LIFR from ceiling and visibility (US definitions)."""
    km, _ = visibility_km(visib)
    ceil = ceiling_ft(clouds)
    if km is None and ceil is None:
        return None
    sm = km / SM_TO_KM if km is not None else 99
    c = ceil if ceil is not None else 99999
    if c < 500 or sm < 1:
        return "LIFR"
    if c < 1000 or sm < 3:
        return "IFR"
    if c <= 3000 or sm <= 5:
        return "MVFR"
    return "VFR"


def weather_words(wx: str | None) -> str:
    """"-TSRA BR" -> "light thunderstorm with rain, mist"."""
    out = []
    for token in (wx or "").split():
        words = []
        if token.startswith("-"):
            words.append("light")
        elif token.startswith("+"):
            words.append("heavy")
        code = token.lstrip("+-")
        if code.startswith("VC"):
            code = code[2:]
            suffix = " in the vicinity"
        else:
            suffix = ""
        pairs = [code[i:i + 2] for i in range(0, len(code), 2)]
        known = [DESCRIPTOR.get(p) or PHENOMENON.get(p) for p in pairs]
        if not pairs or any(k is None for k in known):
            out.append(token)  # unknown code: show it raw; never guess
            continue
        out.append(" ".join(words + known) + suffix)
    return ", ".join(out)


def wind_text(wdir, wspd, wgst=None) -> str:
    if wspd is None:
        return "Wind not reported"
    if wspd < 1:
        return "Wind calm"
    gust = f", gusting {wgst} kt" if wgst else ""
    if not isinstance(wdir, (int, float)):
        return f"Wind variable in direction at {wspd} kt{gust}"
    return f"Wind from the {compass(wdir)} ({int(wdir):03d}°) at {wspd} kt{gust}"


def visibility_text(visib) -> str | None:
    km, plus = visibility_km(visib)
    if km is None:
        return None
    if plus or km >= 9.9:
        return "Visibility 10 km or more"
    return f"Visibility {km:.1f} km" if km < 5 else f"Visibility {km:.0f} km"


def cloud_text(clouds: list[dict] | None, cover: str | None = None) -> str:
    layers = [f"{COVER[c['cover']]} at {int(c['base']):,} ft" for c in clouds or []
              if c.get("cover") in COVER and c.get("base") is not None]
    if layers:
        return layers[0].capitalize() + "".join(f", {x}" for x in layers[1:])
    covers = {c.get("cover") for c in clouds or []} | {cover}
    return "No significant cloud" if covers & CLEAR or not clouds else "Cloud not reported"


def decode_metar(m: dict) -> dict:
    """Plain-language lines plus the handful of numbers the UI needs."""
    lines = [wind_text(m.get("wdir"), m.get("wspd"), m.get("wgst"))]
    vis = visibility_text(m.get("visib"))
    wx = weather_words(m.get("wxString"))
    if vis:
        lines.append(f"{vis}{' in ' + wx if wx else ''}")
    elif wx:
        lines.append(wx.capitalize())
    lines.append(cloud_text(m.get("clouds"), m.get("cover")))
    if m.get("temp") is not None:
        dew = f", dew point {round(m['dewp'])}°C" if m.get("dewp") is not None else ""
        lines.append(f"Temperature {round(m['temp'])}°C{dew}")
    if m.get("altim") is not None:
        lines.append(f"Pressure {round(m['altim'])} hPa")
    cat = m.get("fltCat") or flight_category(m.get("visib"), m.get("clouds"))
    km, _ = visibility_km(m.get("visib"))
    return {
        "lines": lines,
        "flight_category": cat,
        "ceiling_ft": ceiling_ft(m.get("clouds")),
        "visibility_km": None if km is None else round(km, 1),
        "wind_dir": m.get("wdir"),
        "wind_kt": m.get("wspd"),
        "gust_kt": m.get("wgst"),
    }


def _hhmm(ts) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%d %b %H:%M")


def decode_taf_period(f: dict) -> dict:
    change = f.get("fcstChange")
    start, end = _hhmm(f["timeFrom"]), _hhmm(f["timeTo"])
    if change == "BECMG":
        head = f"Becoming, from {start} UTC"
    elif change == "TEMPO":
        head = f"Temporarily between {start} and {end} UTC"
    elif change == "PROB" or f.get("probability"):
        head = f"{f.get('probability') or ''}% chance between {start} and {end} UTC".lstrip("% ")
    else:
        head = f"From {start} to {end} UTC"
    parts = []
    if f.get("wspd") is not None:
        parts.append(wind_text(f.get("wdir"), f.get("wspd"), f.get("wgst")).lower())
    vis = visibility_text(f.get("visib"))
    if vis:
        parts.append(vis.lower())
    wx = weather_words(f.get("wxString"))
    if wx:
        parts.append(wx)
    if f.get("clouds"):
        parts.append(cloud_text(f.get("clouds")).lower())
    return {"from": f["timeFrom"], "to": f["timeTo"], "change": change,
            "text": f"{head}: {', '.join(parts) if parts else 'no change given'}"}


def decode_taf(t: dict) -> list[dict]:
    return [decode_taf_period(f) for f in t.get("fcsts") or []]
