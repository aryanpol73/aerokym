"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  BANDS,
  type Band,
  type CellProps,
  type Cells,
  type Events,
  SEVERITY,
  WINDOWS,
  type WindowMinutes,
  fetchCells,
  fetchEvents,
  minutesAgo,
} from "@/lib/api";
import { VIEWS } from "@/components/TurbulenceMap";

// MapLibre needs the browser, so the map is loaded on the client only.
const TurbulenceMap = dynamic(() => import("@/components/TurbulenceMap"), { ssr: false });

const REFRESH_MS = 60_000;

export default function Home() {
  const [minutes, setMinutes] = useState<WindowMinutes>(60);
  const [band, setBand] = useState<Band | null>(null);
  const [view, setView] = useState<keyof typeof VIEWS>("India");
  const [cells, setCells] = useState<Cells | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<CellProps | null>(null);
  const [events, setEvents] = useState<Events | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    const load = () =>
      fetchCells(minutes, band, ctrl.signal)
        .then((c) => {
          setCells(c);
          setError(null);
        })
        .catch((e) => {
          if (e.name !== "AbortError") setError("Cannot reach the Aerokym API.");
        });
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      ctrl.abort();
      clearInterval(timer);
    };
  }, [minutes, band]);

  useEffect(() => {
    setEvents(null);
    if (!selected) return;
    const ctrl = new AbortController();
    fetchEvents(selected.h3_cell, minutes, ctrl.signal).then(setEvents).catch(() => {});
    return () => ctrl.abort();
  }, [selected, minutes]);

  const onSelect = useCallback((c: CellProps | null) => setSelected(c), []);
  // Changing a filter can remove the selected cell from the map.
  useEffect(() => setSelected(null), [minutes, band]);

  const counts = [0, 1, 2].map(
    (s) => cells?.features.filter((f) => f.properties.severity === s).length ?? 0,
  );
  const latest = cells?.features.reduce<string | null>(
    (acc, f) => (!acc || f.properties.latest > acc ? f.properties.latest : acc), null);

  return (
    <main className="shell">
      <header className="topbar">
        <h1>Aerokym</h1>
        <span className="tagline">Rough air, inferred from signals planes already broadcast</span>
        <nav>
          <Link href="/about">How it works</Link>
        </nav>
      </header>

      <TurbulenceMap
        cells={cells}
        selected={selected ? `${selected.h3_cell}:${selected.alt_band}` : null}
        view={view}
        onSelect={onSelect}
      />

      <section className="panel controls" aria-label="Filters">
        <fieldset>
          <legend>Region</legend>
          <div className="seg">
            {(Object.keys(VIEWS) as (keyof typeof VIEWS)[]).map((v) => (
              <button key={v} aria-pressed={view === v} onClick={() => setView(v)}>{v}</button>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend>Time window</legend>
          <div className="seg">
            {WINDOWS.map((w) => (
              <button key={w} aria-pressed={minutes === w} onClick={() => setMinutes(w)}>
                {w} min
              </button>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend>Altitude</legend>
          <div className="seg">
            <button aria-pressed={band === null} onClick={() => setBand(null)}>All</button>
            {BANDS.map((b) => (
              <button key={b} aria-pressed={band === b} onClick={() => setBand(b)}>
                {b.replace("FL", "")}
              </button>
            ))}
          </div>
        </fieldset>

        <div className="legend" aria-label="Legend">
          {([0, 1, 2] as const).map((s) => (
            <div key={s} className="legend-row">
              <span className="swatch" style={{ background: SEVERITY[s].color }} />
              <span>{SEVERITY[s].label}</span>
              <span className="count">{counts[s]}</span>
            </div>
          ))}
          <p className="hint">Fainter cells rest on fewer aircraft.</p>
        </div>

        <p className="status" role="status">
          {error ? <span className="error">{error}</span>
            : !cells ? "Loading…"
            : cells.features.length === 0 ? "No aircraft sampled in this window."
            : `${cells.features.length} cells · newest data ${latest ? minutesAgo(latest) : "unknown"}`}
        </p>
      </section>

      {selected && (
        <section className="panel details" aria-label="Cell details">
          <button className="close" onClick={() => setSelected(null)} aria-label="Close">×</button>
          <h2>
            <span className="swatch" style={{ background: SEVERITY[selected.severity].color }} />
            {SEVERITY[selected.severity].label}
          </h2>
          <dl>
            <dt>Altitude band</dt><dd>{selected.alt_band}</dd>
            <dt>Aircraft sampled</dt><dd>{selected.n_aircraft}</dd>
            <dt>Confidence</dt><dd>{Math.round(selected.confidence * 100)}%</dd>
            <dt>Roughness index</dt>
            <dd>{selected.mean_index.toFixed(2)} mean, {selected.max_index.toFixed(2)} highest</dd>
            <dt>Newest sample</dt><dd>{minutesAgo(selected.latest)}</dd>
          </dl>
          {selected.n_aircraft < 3 && (
            <p className="hint">
              Based on {selected.n_aircraft === 1 ? "a single aircraft" : "two aircraft"}: treat as
              a hint, not a finding.
            </p>
          )}
          {events && events.features.length > 0 && (
            <>
              <h3>Samples in this cell</h3>
              <ul className="events">
                {events.features.slice(0, 8).map((e) => (
                  <li key={`${e.properties.icao24}${e.properties.time}`}>
                    <span className="swatch"
                          style={{ background: SEVERITY[e.properties.severity].color }} />
                    <code>{e.properties.icao24}</code>
                    <span>FL{Math.round(e.properties.alt_ft / 100)}</span>
                    <span>index {e.properties.index.toFixed(2)}</span>
                    <span className="muted">{minutesAgo(e.properties.time)}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      <footer className="disclaimer">
        Research project. <strong>Not for flight planning or any operational use.</strong>{" "}
        Estimates from sparse public ADS-B data; unvalidated outside the US.{" "}
        <Link href="/about">Details</Link>
      </footer>
    </main>
  );
}
