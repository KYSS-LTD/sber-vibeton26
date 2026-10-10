"use client";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef, useState } from "react";
import type { ImageOverlay, LayerGroup, Map as LeafletMap } from "leaflet";
import {
  type Bounds,
  type LngLat,
  boundsOf,
  centroid,
  polygonFromRing,
  snapPoint,
} from "../lib/geometry";
import { PersonCard } from "./PersonCard";

export type MapProps = {
  boundary?: any;
  zones?: any[];
  people?: any[];
  routes?: any[];
  heat?: boolean;
  draft?: LngLat[];
  onPoint?: (point: LngLat) => void;
  onMovePoint?: (index: number, point: LngLat) => void;
  // Притягивать новые и перетаскиваемые вершины к вершинам границы, зон и контура.
  snap?: boolean;
  fitKey?: string;
  // Ранее загруженная схема объекта: показывается под зонами по координатам углов.
  bounds?: Bounds;
  background?: string;
};

type Leaflet = typeof import("leaflet");

const MOSCOW: LngLat = [37.6176, 55.7558];
const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a>';

// GeoJSON хранит [долгота, широта], Leaflet ждёт [широта, долгота].
const latLng = (p: number[]): [number, number] => [p[1], p[0]];
const latLngs = (ring: number[][]) => ring.map(latLng);

function element(className: string, text?: string) {
  const el = document.createElement("div");
  el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

function personElement(p: any) {
  const el = element(
    "geo-person" +
      (p.suspicious ? " warn" : p.location_unknown ? " stale" : ""),
  );
  el.append(
    element("geo-dot"),
    element("geo-label", p.full_name.split(" ")[0]),
  );
  return el;
}

function vertices(p: MapProps, skip = -1): number[][] {
  return [
    ...(p.boundary ? p.boundary.coordinates[0] : []),
    ...(p.zones ?? []).flatMap((z) => z.polygon.coordinates[0]),
    ...(p.draft ?? []).filter((_, i) => i !== skip),
  ];
}

function zoneFill(z: any, heat: boolean, max: number) {
  if (!heat) return { fillColor: "#559063", fillOpacity: 0.18 };
  return z.samples === 0
    ? { fillColor: "#c8ceca", fillOpacity: 0.7 }
    : {
        fillColor: "#d17d2f",
        fillOpacity: 0.15 + (0.65 * (z.person_minutes || 0)) / max,
      };
}

export function GeoMap(props: MapProps) {
  const host = useRef<HTMLDivElement>(null);
  const latest = useRef(props);
  latest.current = props;
  const lib = useRef<Leaflet | null>(null);
  const overlay = useRef<LayerGroup | null>(null);
  const plan = useRef<ImageOverlay | null>(null);
  const fitted = useRef(false);
  const [map, setMap] = useState<LeafletMap | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let cancelled = false;
    let instance: LeafletMap | null = null;
    let observer: ResizeObserver | null = null;
    // Leaflet обращается к window при импорте, поэтому грузим его только в браузере.
    import("leaflet").then((module) => {
      if (cancelled || !host.current) return;
      const L: Leaflet = (module as any).default ?? module;
      lib.current = L;
      instance = L.map(host.current, {
        center: latLng(MOSCOW),
        zoom: 10,
        maxZoom: 19,
      });
      L.tileLayer(TILES, {
        maxZoom: 19,
        attribution: ATTRIBUTION,
        // Политика тайлов OSM требует Referer; на чужие домены уходит только origin.
        referrerPolicy: "strict-origin-when-cross-origin",
      }).addTo(instance);
      // Схема объекта лежит между тайлами (200) и векторными слоями (400).
      instance.createPane("plan").style.zIndex = "250";
      overlay.current = L.layerGroup().addTo(instance);
      instance.on("click", (event) => {
        const p = latest.current;
        const point: LngLat = [event.latlng.lng, event.latlng.lat];
        p.onPoint?.(
          p.snap ? snapPoint(point, vertices(p), instance!.getZoom()) : point,
        );
      });
      observer = new ResizeObserver(() => instance?.invalidateSize());
      observer.observe(host.current);
      setMap(instance);
    });
    return () => {
      cancelled = true;
      observer?.disconnect();
      overlay.current = null;
      plan.current = null;
      fitted.current = false;
      instance?.remove();
      setMap(null);
    };
  }, []);

  // Камера: один раз на fitKey показываем объект (или рисуемый контур).
  useEffect(() => {
    if (!map) return;
    const { boundary, zones = [], draft = [] } = latest.current;
    const points: number[][] = boundary
      ? boundary.coordinates[0]
      : draft.length >= 3
        ? draft
        : zones.flatMap((z) => z.polygon.coordinates[0]);
    if (!points.length) return;
    const b = boundsOf(points);
    map.fitBounds(
      [
        [b.south, b.west],
        [b.north, b.east],
      ],
      { padding: [24, 24], maxZoom: 19, animate: fitted.current },
    );
    fitted.current = true;
  }, [map, props.fitKey]);

  // Слои пересобираются при любом изменении данных (максимум десятки объектов).
  useEffect(() => {
    const L = lib.current;
    const group = overlay.current;
    if (!map || !L || !group) return;
    group.clearLayers();
    const { boundary, zones = [], people = [], routes = [] } = props;
    const draft = props.draft ?? [];
    const heat = !!props.heat;
    if (boundary)
      L.polygon(latLngs(boundary.coordinates[0]), {
        color: "#486051",
        weight: 4,
        fillColor: "#486051",
        fillOpacity: 0.05,
        interactive: false,
      }).addTo(group);
    const max = Math.max(1, ...zones.map((z) => z.person_minutes || 0));
    zones.forEach((z) => {
      L.polygon(latLngs(z.polygon.coordinates[0]), {
        color: "#65745f",
        weight: 2,
        ...zoneFill(z, heat, max),
        interactive: false,
      }).addTo(group);
      const label = element("geo-zone-label");
      label.append(element("geo-zone-name", z.name));
      if (z.person_minutes !== undefined)
        label.append(
          element(
            "geo-zone-value",
            z.samples === 0
              ? "нет данных"
              : `${z.person_minutes.toFixed(1)} чел·мин`,
          ),
        );
      L.marker(latLng(centroid(z.polygon.coordinates[0].slice(0, -1))), {
        icon: L.divIcon({
          className: "geo-icon",
          html: label,
          iconSize: [0, 0],
        }),
        interactive: false,
        keyboard: false,
      }).addTo(group);
    });
    const byShift = Object.groupBy(routes, (r) => r.shift_id);
    Object.values(byShift).forEach((items) => {
      if (!items || items.length < 2) return;
      L.polyline(
        items.map((r) => latLng([r.longitude, r.latitude])),
        { color: "#26735b", weight: 3, dashArray: "8 5", interactive: false },
      ).addTo(group);
    });
    people
      .filter((p) => p.longitude !== null && p.latitude !== null)
      .forEach((p) =>
        L.marker(latLng([p.longitude, p.latitude]), {
          icon: L.divIcon({
            className: "geo-icon",
            html: personElement(p),
            iconSize: [0, 0],
          }),
          title: p.full_name,
          zIndexOffset: 500,
        })
          .on("click", () => setSelectedId(p.employee_id))
          .addTo(group),
      );
    if (draft.length >= 3)
      L.polygon(latLngs(polygonFromRing(draft).coordinates[0]), {
        color: "#255b3a",
        weight: 3,
        fillColor: "#559063",
        fillOpacity: 0.2,
        interactive: false,
      }).addTo(group);
    else if (draft.length === 2)
      L.polyline(latLngs(draft), {
        color: "#255b3a",
        weight: 3,
        interactive: false,
      }).addTo(group);
    draft.forEach((point, index) =>
      L.marker(latLng(point), {
        icon: L.divIcon({
          className: "geo-icon",
          html: element("geo-vertex"),
          iconSize: [0, 0],
        }),
        draggable: !!props.onMovePoint,
        keyboard: false,
        zIndexOffset: 1000,
      })
        .on("dragend", (event) => {
          const p = latest.current;
          const { lat, lng } = (event.target as any).getLatLng();
          const moved: LngLat = [lng, lat];
          p.onMovePoint?.(
            index,
            p.snap
              ? snapPoint(moved, vertices(p, index), map.getZoom())
              : moved,
          );
        })
        .addTo(group),
    );
  }, [
    map,
    props.boundary,
    props.zones,
    props.people,
    props.routes,
    props.heat,
    props.draft,
  ]);

  // Ранее загруженная схема объекта: отдельный слой, чтобы опрос людей его не перерисовывал.
  const { bounds, background } = props;
  useEffect(() => {
    const L = lib.current;
    if (!map || !L) return;
    plan.current?.remove();
    plan.current = null;
    if (bounds && background)
      plan.current = L.imageOverlay(
        background,
        [
          [bounds.south, bounds.west],
          [bounds.north, bounds.east],
        ],
        { pane: "plan", opacity: 0.85, interactive: false },
      ).addTo(map);
  }, [
    map,
    background,
    bounds?.west,
    bounds?.east,
    bounds?.south,
    bounds?.north,
  ]);

  const selected = props.people?.find((p) => p.employee_id === selectedId);

  function locate() {
    if (!navigator.geolocation) return setNotice("Геолокация недоступна");
    setNotice("");
    navigator.geolocation.getCurrentPosition(
      (position) =>
        map?.setView(
          [position.coords.latitude, position.coords.longitude],
          17,
          { animate: true },
        ),
      () => setNotice("Не удалось определить ваше местоположение"),
      { enableHighAccuracy: true, timeout: 10000 },
    );
  }

  return (
    <div className="map-shell">
      <div
        ref={host}
        className={props.onPoint ? "geo-host drawing" : "geo-host"}
        role="application"
        aria-label="Карта объекта"
      />
      <div className="geo-controls">
        <button type="button" onClick={locate}>
          Где я
        </button>
      </div>
      {notice && <div className="geo-notice">{notice}</div>}
      {selected && (
        <PersonCard person={selected} onClose={() => setSelectedId(null)} />
      )}
      <div className="map-footer">
        <span>
          <i className="dot green" />
          Последняя известная позиция
        </span>
        <span>Пунктир — соединение измерений</span>
      </div>
    </div>
  );
}
