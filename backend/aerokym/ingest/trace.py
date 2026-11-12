"""Decode readsb "trace_full" files (the format of adsb.lol's globe_history).

Pure functions, no database or network, so they are easy to unit test.

One file = one aircraft for one UTC day:

    {"icao": "ab0998", "r": "N810CK", "t": "GLEX", "timestamp": 1794355200.0,
     "trace": [[seconds_after_timestamp, lat, lon, alt_baro_ft | "ground",
                ground_speed_kt, track_deg, flags, baro_rate_fpm, details | null,
                source_type, alt_geom_ft, geom_rate_fpm, ias_kt, roll_deg], ...]}

flags is a bit field: 1 = position is stale, 2 = start of a new leg.
"""

from __future__ import annotations

import gzip
import json
import tarfile
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any

from aerokym.models import Position

SOURCE = "adsblol_trace"
FLAG_STALE = 1


def _int(v: Any) -> int | None:
    return round(v) if isinstance(v, (int, float)) else None


def _float(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def parse_trace_bytes(raw: bytes) -> dict:
    """Trace files are gzip-compressed JSON, even though they are named .json."""
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def decode_trace(doc: dict, shift: timedelta = timedelta(0)) -> Iterator[Position]:
    """Yield one Position per usable trace point.

    `shift` moves every timestamp (used by demo mode to make an old day look
    like the last 24 hours). Points flagged stale or without coordinates are
    dropped: a repeated old position would look like a frozen aircraft.
    """
    icao = str(doc["icao"]).lower().lstrip("~")
    base = datetime.fromtimestamp(float(doc["timestamp"]), tz=UTC) + shift
    for p in doc.get("trace", []):
        if len(p) < 8 or p[1] is None or p[2] is None:
            continue
        flags = p[6] if isinstance(p[6], int) else 0
        if flags & FLAG_STALE:
            continue
        on_ground = p[3] == "ground"
        yield Position(
            time=base + timedelta(seconds=float(p[0])),
            icao24=icao,
            lat=float(p[1]),
            lon=float(p[2]),
            baro_alt_ft=0 if on_ground else _int(p[3]),
            geom_alt_ft=_int(p[10]) if len(p) > 10 else None,
            baro_rate_fpm=_int(p[7]),
            geom_rate_fpm=_int(p[11]) if len(p) > 11 else None,
            gs_kt=_float(p[4]),
            track_deg=_float(p[5]),
            on_ground=on_ground,
            source=SOURCE,
        )


class ChainedReader:
    """Read several files as one continuous stream.

    adsb.lol splits a day's tar into 2 GB parts (.tar.aa, .tar.ab). Reading the
    parts back to back avoids writing a joined copy to disk.
    """

    def __init__(self, paths: Iterable[Path]):
        self._paths = list(paths)
        self._fh: IO[bytes] | None = None

    def read(self, n: int = -1) -> bytes:
        out = b""
        while n < 0 or len(out) < n:
            if self._fh is None:
                if not self._paths:
                    break
                self._fh = open(self._paths.pop(0), "rb")  # noqa: SIM115 (closed in close())
            chunk = self._fh.read(-1 if n < 0 else n - len(out))
            if not chunk:
                self._fh.close()
                self._fh = None
                continue
            out += chunk
        return out

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def _is_trace(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    return base.startswith("trace_full_") and base.endswith(".json")


def iter_trace_docs(sources: list[Path]) -> Iterator[dict]:
    """Yield decoded trace documents from a directory or from tar file(s).

    Tar input is streamed and never extracted, so a 3.6 GB day needs no extra
    disk. Several paths are treated as the ordered parts of one split tar.
    """
    if len(sources) == 1 and sources[0].is_dir():
        for path in sorted(sources[0].rglob("trace_full_*.json")):
            yield parse_trace_bytes(path.read_bytes())
        return
    reader = ChainedReader(sources)
    try:
        # "r|*" = streaming mode with automatic compression detection.
        with tarfile.open(fileobj=reader, mode="r|*") as tar:  # type: ignore[call-overload]
            for member in tar:
                if member.isfile() and _is_trace(member.name):
                    fh = tar.extractfile(member)
                    if fh is not None:
                        yield parse_trace_bytes(fh.read())
    finally:
        reader.close()
