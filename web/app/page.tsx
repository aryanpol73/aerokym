"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  type Airports,
  BANDS,
  type Band,
  type Cells,
  type Events,
  FLIGHT_CAT,
  HAZARD,
  NO_REPORT_COLOR,
  type Pireps,
  SEVERITY,
  type Sigmets,
  WINDOWS,
  type WindowMinutes,
  fetchAirports,
  fetchCells,
  fetchEvents,
  fetchPireps,
  fetchSigmets,
  minutesAgo,
} from "@/lib/api";
import { type LayerKey, type Layers, type Selection, VIEWS } from "@/components/TurbulenceMap";

// MapLibre needs the browser, so the map is loaded on the client only.
const TurbulenceMap = dynamic(() => import("@/components/TurbulenceMap"), { ssr: false });

const REFRESH_MS = 60_000;
const WEATHER_REFRESH_MS = 5 * 60_000;
const LAYER_LABELS: Record<LayerKey, string> = {
  cells: "Roughness", airports: "Airports", sigmets: "SIGMETs", pireps: "Pilot reports",
};

const levels = (lo: number | null, hi: number | null) =>
  `${lo ? `FL${Math.round(lo / 100)}` : "surface"} to ${hi ? `FL${Math.round(hi / 100)}` : "unknown"}`;

export default function Home() {
  const [minutes, setMinutes] = useState<WindowMinutes>(60);
  const [band, setBand] = useState<Band | null>(null);
  const [view, setView] = useState<keyof typeof VIEWS>("India");
  const [layers, setLayers] = useState<Layers>(
    { cells: true, airports: true, sigmets: true, pireps: true });
  const [cells, setCells] = useState<Cells | null>(null);
  const [airports, setAirports] = useState<Airports | null>(null);
  const [sigmets, setSigmets] = useState<Sigmets | null>(null);
  const [pireps, setPireps] = useState<Pireps | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [events, setEvents] = useState<Events | null>(null);
  // The legend starts collapsed on phones, where the panel would cover the map.
  const [legendOpen, setLegendOpen] = useState(true);
  useEffect(() => setLegendOpen(window.innerWidth > 720), []);

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

  // Official weather changes slowly; a failed fetch just leaves the layer empty.
  useEffect(() => {
    const ctrl = new AbortController();
    const load = () => {
      fetchAirports(ctrl.signal).then(setAirports).catch(() => {});
      fetchSigmets(ctrl.signal).then(setSigmets).catch(() => {});
      fetchPireps(ctrl.signal).then(setPireps).catch(() => {});
    };
    load();
    const timer = setInterval(load, WEATHER_REFRESH_MS);
    return () => {
      ctrl.abort();
      clearInterval(timer);
    };
  }, []);

  const cell = selected?.kind === "cell" ? selected.props : null;
  useEffect(() => {
    setEvents(null);
    if (!cell) return;
    const ctrl = new AbortController();
    fetchEvents(cell.h3_cell, minutes, ctrl.signal).then(setEvents).catch(() => {});
    return () => ctrl.abort();
  }, [cell, minutes]);

  const onSelect = useCallback((s: Selection | null) => setSelected(s), []);
  // Changing a filter can remove the selected cell from the map.
  useEffect(() => setSelected((s) => (s?.kind === "cell" ? null : s)), [minutes, band]);

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
        airports={airports}
        sigmets={sigmets}
        pireps={pireps}
        layers={layers}
        selectedCell={cell ? `${cell.h3_cell}:${cell.alt_band}` : null}
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
        <fieldset>
          <legend>Layers</legend>
          <div className="toggles">
            {(Object.keys(LAYER_LABELS) as LayerKey[]).map((k) => (
              <label key={k}>
                <input type="checkbox" checked={layers[k]}
                       onChange={(e) => setLayers({ ...layers, [k]: e.target.checked })} />
                {LAYER_LABELS[k]}
              </label>
            ))}
          </div>
        </fieldset>

        <details className="legend" open={legendOpen}
                 onToggle={(e) => setLegendOpen(e.currentTarget.open)}>
          <summary>Legend</summary>
          {([0, 1, 2] as const).map((s) => (
            <div key={s} className="legend-row">
              <span className="swatch" style={{ background: SEVERITY[s].color }} />
              <span>{SEVERITY[s].label}</span>
              <span className="count">{counts[s]}</span>
            </div>
          ))}
          <p className="hint">Hexagons: roughness. Fainter cells rest on fewer aircraft.</p>
          {(Object.keys(FLIGHT_CAT) as (keyof typeof FLIGHT_CAT)[]).map((c) => (
            <div key={c} className="legend-row">
              <span className="swatch dot" style={{ background: FLIGHT_CAT[c].color }} />
              <span>{FLIGHT_CAT[c].label}</span>
            </div>
          ))}
          <div className="legend-row">
            <span className="swatch dot" style={{ background: NO_REPORT_COLOR }} />
            <span>No current report</span>
          </div>
          <div className="legend-row">
            <span className="swatch area" />
            <span>SIGMET hazard area</span>
          </div>
          <p className="hint">Dots: airports, by official weather.</p>
        </details>

        <p className="status" role="status">
          {error ? <span className="error">{error}</span>
            : !cells ? "Loading…"
            : cells.features.length === 0 ? "No aircraft sampled in this window."
            : `${cells.features.length} cells · newest data ${latest ? minutesAgo(latest) : "unknown"}`}
        </p>
      </section>

      {selected && (
        <section className="panel details" aria-label="Details">
          <button className="close" onClick={() => setSelected(null)} aria-label="Close">×</button>

          {selected.kind === "cell" && (
            <>
              <h2>
                <span className="swatch"
                      style={{ background: SEVERITY[selected.props.severity].color }} />
                {SEVERITY[selected.props.severity].label}
              </h2>
              <dl>
                <dt>Altitude band</dt><dd>{selected.props.alt_band}</dd>
                <dt>Aircraft sampled</dt><dd>{selected.props.n_aircraft}</dd>
                <dt>Confidence</dt><dd>{Math.round(selected.props.confidence * 100)}%</dd>
                <dt>Roughness index</dt>
                <dd>{selected.props.mean_index.toFixed(2)} mean,{" "}
                  {selected.props.max_index.toFixed(2)} highest</dd>
                <dt>Newest sample</dt><dd>{minutesAgo(selected.props.latest)}</dd>
              </dl>
              {selected.props.n_aircraft < 3 && (
                <p className="hint">
                  Based on {selected.props.n_aircraft === 1 ? "a single aircraft" : "two aircraft"}:
                  treat as a hint, not a finding.
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
            </>
          )}

          {selected.kind === "airport" && (
            <>
              <h2>
                <span className="swatch dot" style={{
                  background: selected.props.flight_category
                    ? FLIGHT_CAT[selected.props.flight_category].color : NO_REPORT_COLOR }} />
                {selected.props.icao}
              </h2>
              <p className="lead">{selected.props.name}</p>
              <dl>
                <dt>Conditions</dt>
                <dd>{selected.props.flight_category
                  ? FLIGHT_CAT[selected.props.flight_category].label : "No current report"}</dd>
                {selected.props.obs_time && (
                  <><dt>Observed</dt><dd>{minutesAgo(selected.props.obs_time)}</dd></>
                )}
              </dl>
              <p><Link className="button" href={`/airport/${selected.props.icao}`}>
                Weather and runway winds →</Link></p>
            </>
          )}

          {selected.kind === "sigmet" && (
            <>
              <h2><span className="swatch area" />
                SIGMET: {HAZARD[selected.props.hazard ?? ""] ?? selected.props.hazard ?? "hazard"}
              </h2>
              <dl>
                <dt>Region</dt><dd>{selected.props.fir ?? "unknown"}</dd>
                <dt>Levels</dt>
                <dd>{levels(selected.props.alt_lo_ft, selected.props.alt_hi_ft)}</dd>
                <dt>Valid until</dt>
                <dd>{new Date(selected.props.valid_to).toUTCString().slice(5, 22)} UTC</dd>
              </dl>
              {selected.props.raw && <pre className="raw">{selected.props.raw}</pre>}
              <p className="hint">Official hazard notice from the region&apos;s weather office.</p>
            </>
          )}

          {selected.kind === "pirep" && (
            <>
              <h2>Pilot report</h2>
              <dl>
                <dt>Turbulence</dt><dd>{selected.props.turbulence ?? "not mentioned"}</dd>
                <dt>Altitude</dt>
                <dd>{selected.props.alt_ft ? `FL${Math.round(selected.props.alt_ft / 100)}` : "unknown"}</dd>
                <dt>Aircraft</dt><dd>{selected.props.aircraft_type ?? "unknown"}</dd>
                <dt>Reported</dt><dd>{minutesAgo(selected.props.obs_time)}</dd>
              </dl>
              <pre className="raw">{selected.props.raw}</pre>
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
