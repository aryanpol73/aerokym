"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";

import {
  type AirportCard,
  FLIGHT_CAT,
  HAZARD,
  NO_REPORT_COLOR,
  type RunwayEnd,
  fetchAirport,
  minutesAgo,
} from "@/lib/api";

function windCell(end: RunwayEnd) {
  if (end.status === "calm") return "Calm";
  if (end.status === "variable")
    return `Variable wind: up to ${end.max_crosswind_kt} kt across`;
  if (end.status !== "ok" || end.headwind_kt === undefined) return "No wind report";
  const head = end.headwind_kt >= 0
    ? `${end.headwind_kt.toFixed(0)} kt headwind`
    : `${Math.abs(end.headwind_kt).toFixed(0)} kt tailwind`;
  const cross = end.crosswind_kt && end.crosswind_kt >= 0.5
    ? `${end.crosswind_kt.toFixed(0)} kt crosswind from the ${end.crosswind_from}`
    : "no crosswind";
  const gust = end.gust_crosswind_kt ? ` (gusts: ${end.gust_crosswind_kt.toFixed(0)} kt across)` : "";
  return `${head}, ${cross}${gust}`;
}

export default function AirportPage({ params }: { params: Promise<{ icao: string }> }) {
  const { icao } = use(params);
  const [card, setCard] = useState<AirportCard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    fetchAirport(icao, ctrl.signal)
      .then(setCard)
      .catch((e) => {
        if (e.name === "AbortError") return;
        setError(e.message === "API 404"
          ? `No airport ${icao.toUpperCase()} in Aerokym's list.`
          : "Cannot reach the Aerokym API.");
      });
    return () => ctrl.abort();
  }, [icao]);

  if (error)
    return (
      <article className="prose">
        <p><Link href="/">← Back to the map</Link></p>
        <h1>{icao.toUpperCase()}</h1>
        <p className="warn">{error}</p>
      </article>
    );
  if (!card) return <article className="prose"><p>Loading…</p></article>;

  const { airport, metar, taf, runways } = card;
  const cat = metar?.flight_category ?? null;
  // Runway ends with a headwind are the ones aircraft would normally use.
  const favoured = (e: RunwayEnd) => e.status === "ok" && (e.headwind_kt ?? -1) >= 0;

  return (
    <article className="prose">
      <p><Link href="/">← Back to the map</Link></p>
      <h1>{airport.name}</h1>
      <p className="muted">
        {airport.icao}{airport.iata ? ` / ${airport.iata}` : ""}
        {airport.city ? ` · ${airport.city}` : ""}
        {airport.elevation_ft !== null ? ` · elevation ${airport.elevation_ft.toLocaleString()} ft` : ""}
      </p>

      <h2>Current weather</h2>
      {metar ? (
        <>
          <p className="catline">
            <span className="swatch dot"
                  style={{ background: cat ? FLIGHT_CAT[cat].color : NO_REPORT_COLOR }} />
            <strong>{cat ? FLIGHT_CAT[cat].label : "Category unknown"}</strong>
            <span className="muted"> · observed {minutesAgo(metar.obs_time)}</span>
          </p>
          <ul>{metar.lines.map((l) => <li key={l}>{l}</li>)}</ul>
          <pre className="raw">{metar.raw}</pre>
        </>
      ) : (
        <p>No weather report from this airport in the last 3 hours.</p>
      )}

      <h2>Runway winds</h2>
      {runways.length === 0 ? <p>No runway data.</p> : (
        <table>
          <thead><tr><th>Runway</th><th>Heading</th><th>Wind right now</th></tr></thead>
          <tbody>
            {runways.flatMap((r) => r.ends).map((e) => (
              <tr key={e.ident} className={favoured(e) ? "favoured" : undefined}>
                <td><strong>{e.ident ?? "?"}</strong></td>
                <td>{e.heading_deg !== null ? `${String(e.heading_deg).padStart(3, "0")}°` : "unknown"}</td>
                <td>{windCell(e)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="hint">
        Computed from the reported wind and the runway&apos;s true heading. Highlighted rows have a
        headwind. Which runway is in use is decided by air traffic control, not shown here.
      </p>

      <h2>Forecast</h2>
      {taf ? (
        <>
          <ul>{taf.periods.map((p) => <li key={p.text}>{p.text}</li>)}</ul>
          <pre className="raw">{taf.raw}</pre>
        </>
      ) : (
        <p>No current forecast for this airport.</p>
      )}

      <h2>Nearby hazards</h2>
      {card.nearby_sigmets.length === 0 && card.nearby_pireps.length === 0 ? (
        <p>No SIGMET within 300 km and no pilot report within 200 km in the last 3 hours.
          Pilot reports are rare over India, so their absence says little.</p>
      ) : (
        <ul>
          {card.nearby_sigmets.map((s) => (
            <li key={s.id}>
              SIGMET, {HAZARD[s.hazard ?? ""] ?? s.hazard ?? "hazard"}
              {s.distance_km > 0 ? `, ${s.distance_km} km away` : ", covering the airport"}
              {s.fir ? ` (${s.fir})` : ""}
            </li>
          ))}
          {card.nearby_pireps.map((p) => (
            <li key={p.obs_time + p.raw}>
              Pilot report {minutesAgo(p.obs_time)}, {p.distance_km} km away: <code>{p.raw}</code>
            </li>
          ))}
        </ul>
      )}

      <p className="warn">
        <strong>Not for flight planning or any operational use.</strong> Flight categories use the
        US definitions. {card.meta.weather_attribution}
      </p>
    </article>
  );
}
