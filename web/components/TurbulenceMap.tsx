"use client";

import maplibregl, { type GeoJSONSource, type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";

import {
  type AirportProps,
  type Airports,
  type CellProps,
  type Cells,
  FLIGHT_CAT,
  NO_REPORT_COLOR,
  type PirepProps,
  type Pireps,
  SEVERITY,
  type SigmetProps,
  type Sigmets,
} from "@/lib/api";

// Free vector basemap, no key. Override with NEXT_PUBLIC_MAP_STYLE.
const STYLE_URL =
  process.env.NEXT_PUBLIC_MAP_STYLE ?? "https://tiles.openfreemap.org/styles/positron";

// Used when the basemap cannot be reached (offline demo): the data still
// draws, on a plain background.
const BLANK_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#e8edf2" } }],
};

export const VIEWS = {
  India: { center: [79.5, 21.5] as [number, number], zoom: 3.9 },
  "United States": { center: [-96.5, 38.5] as [number, number], zoom: 3.4 },
};

export type LayerKey = "cells" | "airports" | "sigmets" | "pireps";
export type Layers = Record<LayerKey, boolean>;

// What the user clicked. Airports win over PIREPs, which win over cells,
// which win over the large SIGMET areas underneath.
export type Selection =
  | { kind: "cell"; props: CellProps }
  | { kind: "airport"; props: AirportProps }
  | { kind: "sigmet"; props: SigmetProps }
  | { kind: "pirep"; props: PirepProps };

const LAYER_IDS: Record<LayerKey, string[]> = {
  sigmets: ["sigmets-fill", "sigmets-line"],
  cells: ["cells-fill", "cells-line"],
  pireps: ["pireps-dot"],
  airports: ["airports-dot"],
};
const CLICK_ORDER: [string, Selection["kind"]][] = [
  ["airports-dot", "airport"], ["pireps-dot", "pirep"], ["cells-fill", "cell"],
  ["sigmets-fill", "sigmet"],
];

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

// MapLibre only accepts numeric feature ids, so the string id ("h3:band") is
// copied into the properties and promoted from there.
function cellSource(cells: Cells | null): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: (cells?.features ?? []).map((f) => ({
      ...f,
      properties: { ...f.properties, id: f.id },
    })),
  };
}

interface Props {
  cells: Cells | null;
  airports: Airports | null;
  sigmets: Sigmets | null;
  pireps: Pireps | null;
  layers: Layers;
  selectedCell: string | null; // feature id "h3:band"
  view: keyof typeof VIEWS;
  onSelect: (selection: Selection | null) => void;
}

export default function TurbulenceMap(props: Props) {
  const { cells, airports, sigmets, pireps, layers, selectedCell, view, onSelect } = props;
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const readyRef = useRef(false);
  const latest = useRef(props);
  latest.current = props;

  // Create the map once.
  useEffect(() => {
    let cancelled = false;
    let map: maplibregl.Map | null = null;

    // Check the basemap first, so an offline demo falls back cleanly instead
    // of leaving MapLibre half-initialised.
    const pickStyle = fetch(STYLE_URL, { signal: AbortSignal.timeout(4000) })
      .then((r) => (r.ok ? STYLE_URL : BLANK_STYLE))
      .catch(() => BLANK_STYLE);

    pickStyle.then((style) => {
      if (cancelled || !container.current) return;
      map = new maplibregl.Map({
        container: container.current,
        style,
        ...VIEWS.India,
        attributionControl: { compact: true },
      });
      const m = map;
      mapRef.current = m;
      // Development only: lets you inspect the map from the browser console.
      if (process.env.NODE_ENV !== "production") {
        (window as unknown as { __aerokymMap?: maplibregl.Map }).__aerokymMap = m;
      }
      m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

      m.on("load", () => {
        const p = latest.current;
        // Drawing order, bottom to top: SIGMET areas, hex cells, PIREPs, airports.
        m.addSource("sigmets", { type: "geojson", data: p.sigmets ?? EMPTY });
        m.addLayer({ id: "sigmets-fill", type: "fill", source: "sigmets",
          paint: { "fill-color": "#7048e8", "fill-opacity": 0.1 } });
        m.addLayer({ id: "sigmets-line", type: "line", source: "sigmets",
          paint: { "line-color": "#7048e8", "line-width": 1.5, "line-dasharray": [3, 2] } });

        m.addSource("cells", { type: "geojson", data: cellSource(p.cells), promoteId: "id" });
        m.addLayer({
          id: "cells-fill", type: "fill", source: "cells",
          paint: {
            "fill-color": ["match", ["get", "severity"],
              2, SEVERITY[2].color, 1, SEVERITY[1].color, SEVERITY[0].color],
            // Confidence drives opacity: a cell seen by one aircraft is faint.
            "fill-opacity": ["interpolate", ["linear"], ["get", "confidence"], 0, 0.18, 1, 0.75],
          },
        });
        m.addLayer({
          id: "cells-line", type: "line", source: "cells",
          paint: {
            "line-color": ["case", ["boolean", ["feature-state", "selected"], false],
              "#111827", "rgba(17,24,39,0.25)"],
            "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 2.5, 0.5],
          },
        });

        m.addSource("pireps", { type: "geojson", data: p.pireps ?? EMPTY });
        m.addLayer({ id: "pireps-dot", type: "circle", source: "pireps",
          paint: { "circle-radius": 5, "circle-color": "#f8f9fa", "circle-stroke-color": "#111827",
                   "circle-stroke-width": 2 } });

        m.addSource("airports", { type: "geojson", data: p.airports ?? EMPTY });
        m.addLayer({
          id: "airports-dot", type: "circle", source: "airports",
          paint: {
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 3.5, 7, 7],
            "circle-color": ["match", ["get", "flight_category"],
              "VFR", FLIGHT_CAT.VFR.color, "MVFR", FLIGHT_CAT.MVFR.color,
              "IFR", FLIGHT_CAT.IFR.color, "LIFR", FLIGHT_CAT.LIFR.color, NO_REPORT_COLOR],
            "circle-stroke-color": "#ffffff",
            "circle-stroke-width": 1.5,
            // Airports without a current report stay in the background.
            "circle-opacity": ["case", ["==", ["get", "flight_category"], null], 0.55, 1],
          },
        });

        for (const key of Object.keys(LAYER_IDS) as LayerKey[])
          for (const id of LAYER_IDS[key])
            m.setLayoutProperty(id, "visibility", p.layers[key] ? "visible" : "none");
        readyRef.current = true;
      });

      m.on("click", (e) => {
        if (!readyRef.current) return; // layers not added yet
        for (const [layer, kind] of CLICK_ORDER) {
          const hit = m.queryRenderedFeatures(e.point, { layers: [layer] })[0];
          if (hit) {
            latest.current.onSelect({ kind, props: hit.properties } as Selection);
            return;
          }
        }
        latest.current.onSelect(null);
      });
      for (const [layer] of CLICK_ORDER) {
        m.on("mousemove", layer, () => (m.getCanvas().style.cursor = "pointer"));
        m.on("mouseleave", layer, () => (m.getCanvas().style.cursor = ""));
      }
    });

    return () => {
      cancelled = true;
      readyRef.current = false;
      map?.remove();
      mapRef.current = null;
    };
  }, []);

  // Push new data into the existing sources (no layer rebuild, no flicker).
  // If the map is not ready yet, its "load" handler reads the latest props.
  const setData = (id: string, data: GeoJSON.FeatureCollection) => {
    if (readyRef.current) (mapRef.current?.getSource(id) as GeoJSONSource | undefined)?.setData(data);
  };
  useEffect(() => setData("cells", cellSource(cells)), [cells]);
  useEffect(() => setData("airports", airports ?? EMPTY), [airports]);
  useEffect(() => setData("sigmets", sigmets ?? EMPTY), [sigmets]);
  useEffect(() => setData("pireps", pireps ?? EMPTY), [pireps]);

  useEffect(() => {
    const m = mapRef.current;
    if (!m || !readyRef.current) return;
    for (const key of Object.keys(LAYER_IDS) as LayerKey[])
      for (const id of LAYER_IDS[key])
        m.setLayoutProperty(id, "visibility", layers[key] ? "visible" : "none");
  }, [layers]);

  // Outline the selected cell.
  const previous = useRef<string | null>(null);
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !readyRef.current) return;
    if (previous.current)
      m.setFeatureState({ source: "cells", id: previous.current }, { selected: false });
    if (selectedCell) m.setFeatureState({ source: "cells", id: selectedCell }, { selected: true });
    previous.current = selectedCell;
  }, [selectedCell, cells]);

  useEffect(() => {
    mapRef.current?.flyTo({ ...VIEWS[view], duration: 800 });
  }, [view]);

  return <div ref={container} className="map" role="application" aria-label="Roughness map" />;
}
