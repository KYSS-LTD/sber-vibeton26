"use client";
import { useState } from "react";
import { Photo } from "./Photo";
type Bounds = { west: number; east: number; south: number; north: number };
type Props = {
  boundary: any;
  zones?: any[];
  people?: any[];
  routes?: any[];
  bounds?: Bounds;
  background?: string;
  onPoint?: (point: number[]) => void;
  draft?: number[][];
};
export function boundsFor(boundary: any): Bounds {
  const points = boundary.coordinates[0] as number[][];
  return {
    west: Math.min(...points.map((p) => p[0])),
    east: Math.max(...points.map((p) => p[0])),
    south: Math.min(...points.map((p) => p[1])),
    north: Math.max(...points.map((p) => p[1])),
  };
}
export function SiteMap({
  boundary,
  zones = [],
  people = [],
  routes = [],
  bounds,
  background,
  onPoint,
  draft = [],
}: Props) {
  const b = bounds || boundsFor(boundary);
  const [selected, setSelected] = useState<any>(null);
  const point = (p: number[]) => [
    ((p[0] - b.west) / (b.east - b.west)) * 1000,
    700 - ((p[1] - b.south) / (b.north - b.south)) * 700,
  ];
  const points = (ps: number[][]) =>
    ps.map((p) => point(p).join(",")).join(" ");
  const max = Math.max(1, ...zones.map((z) => z.person_minutes || 0));
  const routeGroups = Object.groupBy(routes, (r) => r.shift_id);
  return (
    <div className="map-shell">
      <svg
        viewBox="0 0 1000 700"
        className={onPoint ? "site-map drawing" : "site-map"}
        role="img"
        aria-label="Схема объекта и зоны"
        onClick={(event) => {
          if (!onPoint) return;
          const svg = event.currentTarget;
          const p = svg.createSVGPoint();
          p.x = event.clientX;
          p.y = event.clientY;
          const transformed = p.matrixTransform(svg.getScreenCTM()!.inverse());
          onPoint([
            b.west + (transformed.x / 1000) * (b.east - b.west),
            b.south + ((700 - transformed.y) / 700) * (b.north - b.south),
          ]);
        }}
      >
        <defs>
          <pattern
            id="grid"
            width="50"
            height="50"
            patternUnits="userSpaceOnUse"
          >
            <path
              d="M 50 0 L 0 0 0 50"
              fill="none"
              stroke="#dfe5df"
              strokeWidth="1"
            />
          </pattern>
        </defs>
        <rect width="1000" height="700" fill="#edf1ec" />
        <rect width="1000" height="700" fill="url(#grid)" />
        {background && (
          <image
            href={background}
            width="1000"
            height="700"
            preserveAspectRatio="none"
          />
        )}
        <polygon
          points={points(boundary.coordinates[0])}
          fill="none"
          stroke="#486051"
          strokeWidth="4"
        />
        {zones.map((z, i) => {
          const p = z.polygon.coordinates[0];
          const center = p
            .slice(0, -1)
            .reduce(
              (a: number[], v: number[]) => [
                a[0] + v[0] / (p.length - 1),
                a[1] + v[1] / (p.length - 1),
              ],
              [0, 0],
            );
          const xy = point(center);
          return (
            <g key={z.id || i}>
              <polygon
                points={points(p)}
                fill={
                  z.samples === 0
                    ? "#c8ceca"
                    : `rgba(209, 125, 47, ${0.15 + (0.65 * (z.person_minutes || 0)) / max})`
                }
                stroke="#65745f"
                strokeWidth="2"
              />
              <text
                x={xy[0]}
                y={xy[1]}
                textAnchor="middle"
                className="map-label"
              >
                {z.name}
              </text>
              {z.person_minutes !== undefined && (
                <text
                  x={xy[0]}
                  y={xy[1] + 22}
                  textAnchor="middle"
                  className="map-value"
                >
                  {z.samples === 0
                    ? "нет данных"
                    : `${z.person_minutes.toFixed(1)} чел·мин`}
                </text>
              )}
            </g>
          );
        })}
        {Object.entries(routeGroups).map(([id, items]) => (
          <polyline
            key={id}
            points={points((items || []).map((r) => [r.longitude, r.latitude]))}
            fill="none"
            stroke="#26735b"
            strokeWidth="3"
            strokeDasharray="8 5"
          />
        ))}
        {people
          .filter((p) => p.longitude !== null && p.latitude !== null)
          .map((p) => {
            const xy = point([p.longitude, p.latitude]);
            return (
              <g
                key={p.employee_id}
                onClick={(e) => {
                  e.stopPropagation();
                  setSelected(p);
                }}
                className="map-person"
              >
                <circle
                  cx={xy[0]}
                  cy={xy[1]}
                  r="13"
                  fill={
                    p.suspicious
                      ? "#b37a29"
                      : p.location_unknown
                        ? "#989e98"
                        : "#1d7058"
                  }
                  stroke="white"
                  strokeWidth="4"
                />
                <text x={xy[0] + 20} y={xy[1] + 5}>
                  {p.full_name.split(" ")[0]}
                </text>
              </g>
            );
          })}
        {draft.length > 0 && (
          <polyline
            points={points(draft)}
            fill="rgba(85,144,99,.15)"
            stroke="#255b3a"
            strokeWidth="4"
          />
        )}
        {draft.map((p, i) => {
          const xy = point(p);
          return <circle key={i} cx={xy[0]} cy={xy[1]} r="6" fill="#255b3a" />;
        })}
      </svg>
      {selected && (
        <div className="map-person-card">
          <button className="close" onClick={() => setSelected(null)}>
            ×
          </button>
          <Photo id={selected.photo_id} />
          <strong>{selected.full_name}</strong>
          <span>{Number(selected.hours).toFixed(1)} ч на смене</span>
          <small>
            {selected.suspicious
              ? "Координаты требуют проверки"
              : selected.location_unknown
                ? "Геопозиция устарела"
                : `Обновлено ${new Date(selected.location_at).toLocaleTimeString("ru")}`}
          </small>
        </div>
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
