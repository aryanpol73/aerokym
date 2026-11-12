"use client";

import maplibregl, { type GeoJSONSource, type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";

import { type CellProps, type Cells, SEVERITY } from "@/lib/api";

// Free vector basemap, no key. Override with NEXT_PUBLIC_MAP_STYLE.
const STYLE_URL =
  process.env.NEXT_PUBLIC_MAP_STYLE ?? "https://tiles.openfreemap.org/styles/positron";

// Used when the basemap cannot be reached (offline demo): the hexes still
// draw, on a plain background.
const BLANK_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#e8edf2" } }],
};

export const VIEWS = {
  India: { center: [79.5, 21.5] as [number, number], zoom: 3.9 },
  "United States": { center: [-96.5, 38.5] as [number, number], zoom: 3.4 },
};

// MapLibre only accepts numeric feature ids, so the string id ("h3:band") is
// copied into the properties and promoted from there.
function toSource(cells: Cells | null): GeoJSON.FeatureCollection {
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
  selected: string | null; // feature id "h3:band"
  view: keyof typeof VIEWS;
  onSelect: (cell: CellProps | null) => void;
}

export default function TurbulenceMap({ cells, selected, view, onSelect }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const readyRef = useRef(false);
  const cellsRef = useRef<Cells | null>(cells);
  const onSelectRef = useRef(onSelect);
  cellsRef.current = cells;
  onSelectRef.current = onSelect;

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
        m.addSource("cells", { type: "geojson", data: toSource(cellsRef.current), promoteId: "id" });
        m.addLayer({
          id: "cells-fill",
          type: "fill",
          source: "cells",
          paint: {
            "fill-color": ["match", ["get", "severity"],
              2, SEVERITY[2].color, 1, SEVERITY[1].color, SEVERITY[0].color],
            // Confidence drives opacity: a cell seen by one aircraft is faint.
            "fill-opacity": ["interpolate", ["linear"], ["get", "confidence"], 0, 0.18, 1, 0.75],
          },
        });
        m.addLayer({
          id: "cells-line",
          type: "line",
          source: "cells",
          paint: {
            "line-color": ["case", ["boolean", ["feature-state", "selected"], false],
              "#111827", "rgba(17,24,39,0.25)"],
            "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 2.5, 0.5],
          },
        });
        readyRef.current = true;
      });

      m.on("click", (e) => {
        if (!readyRef.current) return; // layers not added yet
        const hit = m.queryRenderedFeatures(e.point, { layers: ["cells-fill"] })[0];
        onSelectRef.current(hit ? (hit.properties as CellProps) : null);
      });
      m.on("mousemove", "cells-fill", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "cells-fill", () => (m.getCanvas().style.cursor = ""));
    });

    return () => {
      cancelled = true;
      readyRef.current = false;
      map?.remove();
      mapRef.current = null;
    };
  }, []);

  // Push new data into the existing source (no layer rebuild, no flicker).
  // If the map is not ready yet, its "load" handler reads cellsRef instead.
  useEffect(() => {
    if (!readyRef.current) return;
    (mapRef.current?.getSource("cells") as GeoJSONSource | undefined)?.setData(toSource(cells));
  }, [cells]);

  // Outline the selected cell.
  const previous = useRef<string | null>(null);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    if (previous.current)
      map.setFeatureState({ source: "cells", id: previous.current }, { selected: false });
    if (selected) map.setFeatureState({ source: "cells", id: selected }, { selected: true });
    previous.current = selected;
  }, [selected, cells]);

  useEffect(() => {
    mapRef.current?.flyTo({ ...VIEWS[view], duration: 800 });
  }, [view]);

  return <div ref={container} className="map" role="application" aria-label="Roughness map" />;
}
