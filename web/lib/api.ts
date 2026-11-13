// Thin client for the Aerokym API. The types mirror docs/api.md.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const WINDOWS = [15, 30, 60] as const;
export type WindowMinutes = (typeof WINDOWS)[number];

export const BANDS = ["FL200-290", "FL300-390", "FL400+"] as const;
export type Band = (typeof BANDS)[number];

export type Severity = 0 | 1 | 2;

export interface CellProps {
  h3_cell: string;
  alt_band: Band;
  severity: Severity;
  severity_label: string;
  confidence: number;
  n_aircraft: number;
  n_windows: number;
  mean_index: number;
  max_index: number;
  latest: string;
}

export interface EventProps {
  icao24: string;
  time: string;
  alt_ft: number;
  index: number;
  severity: Severity;
  confidence: number;
  n_samples: number;
}

export interface Meta {
  generated_at: string;
  method_version: string;
  disclaimer: string;
  attribution: string;
  latest_event?: string | null;
  n_events?: number;
}

export type Cells = GeoJSON.FeatureCollection<GeoJSON.Polygon, CellProps> & { meta: Meta };
export type Events = GeoJSON.FeatureCollection<GeoJSON.Point, EventProps> & { meta: Meta };

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { signal });
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json() as Promise<T>;
}

export function fetchCells(minutes: WindowMinutes, band: Band | null, signal?: AbortSignal) {
  const q = new URLSearchParams({ minutes: String(minutes) });
  if (band) q.set("band", band);
  return get<Cells>(`/api/cells?${q}`, signal);
}

export function fetchEvents(cell: string, minutes: WindowMinutes, signal?: AbortSignal) {
  const q = new URLSearchParams({ cell, minutes: String(minutes), min_severity: "0", limit: "200" });
  return get<Events>(`/api/events?${q}`, signal);
}

export function fetchMeta(signal?: AbortSignal) {
  return get<Meta>("/api/meta", signal);
}

// One place for the colours, shared by the map layer and the legend.
// Blue / yellow / orange stay distinguishable for the common colour-vision
// deficiencies, and each level also has a text label.
export const SEVERITY: Record<Severity, { label: string; color: string }> = {
  0: { label: "Smooth", color: "#3b82c4" },
  1: { label: "Light", color: "#e9b824" },
  2: { label: "Moderate or greater", color: "#d9480f" },
};

export function minutesAgo(iso: string, now = Date.now()): string {
  const m = Math.max(0, Math.round((now - new Date(iso).getTime()) / 60000));
  if (m < 1) return "under a minute ago";
  if (m < 60) return `${m} min ago`;
  return `${Math.floor(m / 60)} h ${m % 60} min ago`;
}

// Weather and airports ---------------------------------------------------------

export type FlightCategory = "VFR" | "MVFR" | "IFR" | "LIFR";

export interface AirportProps {
  icao: string;
  name: string;
  city: string | null;
  iata: string | null;
  flight_category: FlightCategory | null;
  obs_time: string | null;
}

export interface SigmetProps {
  id: string;
  hazard: string | null;
  fir: string | null;
  qualifier: string | null;
  valid_from: string;
  valid_to: string;
  alt_lo_ft: number | null;
  alt_hi_ft: number | null;
  raw: string | null;
}

export interface PirepProps {
  id: string;
  obs_time: string;
  alt_ft: number | null;
  turbulence: string | null;
  aircraft_type: string | null;
  raw: string;
}

export type Airports = GeoJSON.FeatureCollection<GeoJSON.Point, AirportProps> & { meta: Meta };
export type Sigmets = GeoJSON.FeatureCollection<GeoJSON.Geometry, SigmetProps> & { meta: Meta };
export type Pireps = GeoJSON.FeatureCollection<GeoJSON.Point, PirepProps> & { meta: Meta };

export interface RunwayEnd {
  ident: string | null;
  heading_deg: number | null;
  status: "ok" | "calm" | "variable" | "unknown";
  headwind_kt?: number;
  crosswind_kt?: number;
  crosswind_from?: "left" | "right" | "";
  gust_crosswind_kt?: number;
  max_crosswind_kt?: number;
}

export interface AirportCard {
  meta: Meta & { weather_attribution: string };
  airport: { icao: string; name: string; city: string | null; iata: string | null;
             country: string | null; elevation_ft: number | null; lat: number; lon: number };
  metar: { obs_time: string; raw: string; lines: string[];
           flight_category: FlightCategory | null; wind_kt: number | null } | null;
  taf: { issue_time: string; valid_to: string; raw: string;
         periods: { text: string; change: string | null }[] } | null;
  runways: { length_ft: number | null; surface: string | null; ends: RunwayEnd[] }[];
  nearby_sigmets: { id: string; hazard: string | null; fir: string | null; valid_to: string;
                    alt_lo_ft: number | null; alt_hi_ft: number | null; distance_km: number }[];
  nearby_pireps: { obs_time: string; alt_ft: number | null; turbulence: string | null;
                   raw: string; distance_km: number }[];
}

export const fetchAirports = (signal?: AbortSignal) => get<Airports>("/api/airports", signal);
export const fetchSigmets = (signal?: AbortSignal) => get<Sigmets>("/api/sigmets", signal);
export const fetchPireps = (signal?: AbortSignal) => get<Pireps>("/api/pireps", signal);
export const fetchAirport = (icao: string, signal?: AbortSignal) =>
  get<AirportCard>(`/api/airports/${encodeURIComponent(icao)}`, signal);

// Conventional aviation colours for flight category. Airports are drawn as
// ringed dots, so they are not confused with the hexagons' colours.
export const FLIGHT_CAT: Record<FlightCategory, { label: string; color: string }> = {
  VFR: { label: "VFR: good visual conditions", color: "#2f9e44" },
  MVFR: { label: "MVFR: marginal", color: "#1c7ed6" },
  IFR: { label: "IFR: low cloud or visibility", color: "#e03131" },
  LIFR: { label: "LIFR: very low", color: "#c2255c" },
};
export const NO_REPORT_COLOR = "#868e96";

export const HAZARD: Record<string, string> = {
  TS: "Thunderstorms", TURB: "Turbulence", ICE: "Icing", VA: "Volcanic ash",
  MTW: "Mountain wave", TC: "Tropical cyclone", DS: "Duststorm", SS: "Sandstorm",
};
