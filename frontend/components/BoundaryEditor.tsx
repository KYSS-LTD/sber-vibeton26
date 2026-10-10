"use client";
import { useState } from "react";
import { Layers } from "lucide-react";
import { api } from "../lib/api";
import {
  type LngLat,
  areaM2,
  openRing,
  polygonFromRing,
  selfIntersects,
} from "../lib/geometry";
import { GeoMap } from "./GeoMap";

export function BoundaryEditor({
  sites,
  busy,
  run,
  onError,
}: {
  sites: any[];
  busy: boolean;
  run: (action: () => Promise<any>) => Promise<void>;
  onError: (s: string) => void;
}) {
  const [editingSite, setEditingSite] = useState("");
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState("Europe/Moscow");
  const [ring, setRing] = useState<LngLat[]>([]);
  const area = areaM2(ring);

  function select(id: string) {
    setEditingSite(id);
    const selected = sites.find((s) => s.id === id);
    setName(selected?.name ?? "");
    setTimezone(selected?.timezone ?? "Europe/Moscow");
    setRing(selected ? openRing(selected.boundary) : []);
  }
  function save() {
    if (ring.length < 3) {
      onError("Поставьте на карте минимум три вершины границы");
      return;
    }
    if (selfIntersects(ring)) {
      onError("Граница объекта не должна пересекать сама себя");
      return;
    }
    void run(async () => {
      const result = await api(
        editingSite ? `/sites/${editingSite}` : "/sites",
        {
          method: editingSite ? "PUT" : "POST",
          body: JSON.stringify({
            name,
            timezone,
            boundary: polygonFromRing(ring),
          }),
        },
      );
      if (!editingSite) select("");
      else if (result?.id) setEditingSite(result.id);
    });
  }
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <span className="eyebrow">ГРАНИЦЫ ПЛОЩАДКИ</span>
          <h2>Участок на карте</h2>
        </div>
        <Layers size={22} />
      </div>
      <div className="form-row">
        <label className="compact-field">
          Объект
          <select value={editingSite} onChange={(e) => select(e.target.value)}>
            <option value="">Создать новый объект</option>
            {sites.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label className="compact-field">
          Название
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="compact-field">
          Часовой пояс
          <input
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
          />
        </label>
      </div>
      <p className="muted small">
        Кликайте по карте, чтобы обвести участок по вершинам; вершины можно
        перетаскивать. Контуры других объектов показаны для ориентира.
      </p>
      <GeoMap
        zones={sites
          .filter((s) => s.id !== editingSite)
          .map((s) => ({ id: s.id, name: s.name, polygon: s.boundary }))}
        draft={ring}
        fitKey={editingSite || "new"}
        snap
        onPoint={(point) => setRing([...ring, point])}
        onMovePoint={(index, point) =>
          setRing(ring.map((p, i) => (i === index ? point : p)))
        }
      />
      <div className="form-row">
        <button onClick={() => setRing(ring.slice(0, -1))}>
          Отменить точку
        </button>
        <button onClick={() => setRing([])}>Очистить контур</button>
        <span className="muted small">
          Вершин: {ring.length}
          {ring.length >= 3 &&
            ` · площадь ≈ ${Math.round(area).toLocaleString("ru")} м²`}
        </span>
        <button className="primary" disabled={busy} onClick={save}>
          {editingSite ? "Сохранить границы" : "Создать объект и хранилище"}
        </button>
      </div>
      <p className="small muted">
        Изменение границы отзовёт суточные QR объекта. Зоны внутри объекта
        рисуются отдельно в разделе «Схема и зоны».
      </p>
    </section>
  );
}
