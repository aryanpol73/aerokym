"""Unit tests for decoding and polling. No database or network needed."""

from __future__ import annotations

import gzip
import io
import json
import tarfile
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from aerokym.ingest import opensky
from aerokym.ingest.replay import day_shift, keep, positions_from_docs
from aerokym.ingest.trace import ChainedReader, decode_trace, iter_trace_docs, parse_trace_bytes

BASE = 1794355200.0  # 2026-11-11 00:00:00 UTC
DOC = {
    "icao": "AB0998",
    "r": "N810CK",
    "t": "GLEX",
    "timestamp": BASE,
    "trace": [
        # real rows from the 2026-11-11 archive, trimmed
        [84661.42, 37.622726, -86.289952, 22925, 470.3, 59.5, 0, -72, None,
         "adsb_icao", 23025, -1152, None, None],
        [84665.18, 37.62735, -86.281362, 22850, 466.4, 54.8, 0, -72, None,
         "adsb_icao", 22950, -1472, None, None],
        [84668.0, 37.63, -86.27, 22800, 460.0, 50.0, 1, -72, None,
         "adsb_icao", 22900, -1500, None, None],                       # stale -> dropped
        [86000.0, 36.37, -94.10, "ground", 12.0, 18.9, 0, None, None,
         "adsb_icao", None, None, None, None],                          # on the ground
        [86001.0, None, None, 100, 12.0, 18.9, 0, 0, None, "adsb_icao", None, None, None, None],
    ],
}


def test_decode_trace_fields_and_filters():
    rows = list(decode_trace(DOC))
    assert len(rows) == 3  # stale and position-less points dropped
    p = rows[0]
    assert p.icao24 == "ab0998"
    assert p.time == datetime.fromtimestamp(BASE + 84661.42, tz=UTC)
    assert (p.lat, p.lon) == (37.622726, -86.289952)
    assert (p.baro_alt_ft, p.geom_alt_ft) == (22925, 23025)
    assert (p.baro_rate_fpm, p.geom_rate_fpm) == (-72, -1152)
    assert p.on_ground is False and p.source == "adsblol_trace"
    ground = rows[2]
    assert ground.on_ground is True and ground.baro_alt_ft == 0 and ground.baro_rate_fpm is None


def test_decode_trace_shift_moves_all_times():
    a = list(decode_trace(DOC))
    b = list(decode_trace(DOC, timedelta(hours=5)))
    assert [y.time - x.time for x, y in zip(a, b)] == [timedelta(hours=5)] * len(a)


def test_parse_trace_bytes_handles_gzip_and_plain():
    raw = json.dumps(DOC).encode()
    assert parse_trace_bytes(raw)["icao"] == "AB0998"
    assert parse_trace_bytes(gzip.compress(raw))["icao"] == "AB0998"


def _make_tar(docs: list[dict]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for d in docs:
            data = gzip.compress(json.dumps(d).encode())
            info = tarfile.TarInfo(f"./traces/98/trace_full_{d['icao'].lower()}.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        junk = tarfile.TarInfo("./acas/acas.csv")
        junk.size = 3
        tar.addfile(junk, io.BytesIO(b"x,y"))
    return buf.getvalue()


def test_iter_trace_docs_reads_split_tar_and_directory(tmp_path):
    blob = _make_tar([DOC, {**DOC, "icao": "ab0999"}])
    cut = len(blob) // 2 + 7  # split mid-file, like adsb.lol's .tar.aa/.tar.ab
    (tmp_path / "d.tar.aa").write_bytes(blob[:cut])
    (tmp_path / "d.tar.ab").write_bytes(blob[cut:])
    docs = list(iter_trace_docs([tmp_path / "d.tar.aa", tmp_path / "d.tar.ab"]))
    assert [d["icao"] for d in docs] == ["AB0998", "ab0999"]

    folder = tmp_path / "traces" / "98"
    folder.mkdir(parents=True)
    (folder / "trace_full_ab0998.json").write_bytes(gzip.compress(json.dumps(DOC).encode()))
    assert len(list(iter_trace_docs([tmp_path / "traces"]))) == 1


def test_chained_reader_concatenates(tmp_path):
    (tmp_path / "a").write_bytes(b"hello ")
    (tmp_path / "b").write_bytes(b"world")
    r = ChainedReader([tmp_path / "a", tmp_path / "b"])
    assert r.read(4) == b"hell" and r.read(100) == b"o world" and r.read(5) == b""


def test_replay_filters_and_shift():
    rows = list(decode_trace(DOC))
    assert keep(rows[0], None, 15000) and not keep(rows[2], None, 15000)
    assert keep(rows[0], (30, -105, 45, -85), 0)
    assert not keep(rows[0], (6, 68, 36, 98), 0)

    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    assert day_shift(DOC, now) == timedelta(days=8, hours=12)

    out = list(positions_from_docs([DOC], (30, -105, 45, -85), 15000, shift_to_now=True))
    assert len(out) == 1 and len(out[0][1]) == 2
    # Shifted: the day now ends "now", so its points lie in the last 24 hours.
    age = datetime.now(UTC) - out[0][1][0].time
    assert timedelta(0) < age < timedelta(days=1)


# OpenSky --------------------------------------------------------------------

STATE = ["800abc", "AIC101  ", "India", 1794400000, 1794400001, 77.1, 28.5, 10668.0, False,
         230.0, 91.5, -0.65, None, 10972.8, "1234", False, 0]


def test_credit_cost_and_interval():
    assert opensky.credit_cost((0, 0, 5, 5)) == 1
    assert opensky.credit_cost((0, 0, 10, 10)) == 2
    assert opensky.credit_cost((0, 0, 20, 20)) == 3
    assert opensky.credit_cost((6, 68, 36, 98)) == 4
    assert opensky.poll_interval_s((6, 68, 36, 98), 3600) == pytest.approx(96.0)
    assert opensky.poll_interval_s((0, 0, 5, 5), 4000) == pytest.approx(21.6)
    assert opensky.poll_interval_s((0, 0, 5, 5), 10**9) == 10.0  # floor


def test_parse_states_converts_units():
    no_fix = list(STATE)
    no_fix[5] = None
    rows = opensky.parse_states({"time": 1794400002, "states": [STATE, no_fix]})
    assert len(rows) == 1
    p = rows[0]
    assert p.icao24 == "800abc" and (p.lat, p.lon) == (28.5, 77.1)
    assert p.baro_alt_ft == 35000 and p.geom_alt_ft == 36000
    assert p.baro_rate_fpm == -128 and p.gs_kt == pytest.approx(447.1, abs=0.1)
    assert p.time == datetime.fromtimestamp(1794400000, tz=UTC) and p.source == "opensky"
    assert opensky.parse_states({"time": 1, "states": None}) == []


def _client(handler) -> opensky.OpenSkyClient:
    return opensky.OpenSkyClient("id", "secret", httpx.Client(transport=httpx.MockTransport(handler)))


def test_client_fetches_token_once_and_sends_bearer():
    calls = {"token": 0, "states": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "auth.opensky-network.org":
            calls["token"] += 1
            assert b"grant_type=client_credentials" in req.content
            return httpx.Response(200, json={"access_token": "T1", "expires_in": 1800})
        calls["states"] += 1
        assert req.headers["Authorization"] == "Bearer T1"
        assert req.url.params["lamin"] == "6.0"
        return httpx.Response(200, json={"time": 1, "states": [STATE]},
                              headers={"X-Rate-Limit-Remaining": "3996"})

    c = _client(handler)
    assert len(c.fetch_states((6.0, 68.0, 36.0, 98.0))) == 1
    c.fetch_states((6.0, 68.0, 36.0, 98.0))
    assert calls == {"token": 1, "states": 2} and c.credits_remaining == 3996


def test_client_refreshes_token_on_401_and_raises_on_429():
    tokens = iter(["OLD", "NEW"])
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "auth.opensky-network.org":
            return httpx.Response(200, json={"access_token": next(tokens), "expires_in": 1800})
        seen.append(req.headers["Authorization"])
        if req.headers["Authorization"] == "Bearer OLD":
            return httpx.Response(401)
        return httpx.Response(429, headers={"X-Rate-Limit-Retry-After-Seconds": "1234"})

    with pytest.raises(opensky.RateLimited) as e:
        _client(handler).fetch_states((6, 68, 36, 98))
    assert seen == ["Bearer OLD", "Bearer NEW"] and e.value.retry_after_s == 1234


def test_client_requires_credentials():
    with pytest.raises(ValueError):
        opensky.OpenSkyClient("", "")
