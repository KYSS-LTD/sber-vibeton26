"use client";
import { useEffect, useState } from "react";
import { api, mediaBlob } from "../lib/api";
import {
  type LngLat,
  boundsOf,
  openRing,
  selfIntersects,
} from "../lib/geometry";
import { GeoMap } from "./GeoMap";

export function MapEditor({
  site,
  onError,
  onSaved,
}: {
  site: any;
  onError: (s: string) => void;
  onSaved: () => void;
}) {
  const [bounds, setBounds] = useState(boundsOf(site.boundary.coordinates[0]));
  const [zones, setZones] = useState<any[]>([]);
  const [draft, setDraft] = useState<LngLat[]>([]);
  const [name, setName] = useState("");
  const [mediaId, setMediaId] = useState<string | null>(null);
  const [background, setBackground] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    api(`/sites/${site.id}/map`)
      .then((value) => {
        if (value.version) {
          setBounds(value.version.transform);
          setMediaId(value.version.media_id);
        }
        setZones(
          value.zones.map((z: any) => ({ name: z.name, polygon: z.polygon })),
        );
      })
      .catch((e) => onError(e.message));
  }, [site.id]);
  useEffect(() => {
    let url = "";
    if (mediaId)
      mediaBlob(mediaId)
        .then((b) => {
          url = URL.createObjectURL(b);
          setBackground(url);
        })
        .catch((e) => onError(e.message));
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [mediaId]);
  function complete() {
    if (draft.length < 3 || !name.trim()) {
      onError("Укажите название и минимум три точки зоны");
      return;
    }
    if (selfIntersects(draft)) {
      onError("Контур зоны не должен пересекать сам себя");
      return;
    }
    if (zones.some((z) => z.name === name.trim())) {
      onError("Зона с таким названием уже есть");
      return;
    }
    setZones([
      ...zones,
      {
        name: name.trim(),
        polygon: { type: "Polygon", coordinates: [[...draft, draft[0]]] },
      },
    ]);
    setDraft([]);
    setName("");
  }
  async function save() {
    setSaving(true);
    try {
      await api(`/sites/${site.id}/map`, {
        method: "POST",
        body: JSON.stringify({ transform: bounds, zones, media_id: mediaId }),
      });
      onSaved();
    } catch (e: any) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <span className="eyebrow">СХЕМА ПЛОЩАДКИ</span>
          <h2>Редактор зон</h2>
        </div>
        <button className="primary" onClick={save} disabled={saving}>
          {saving ? "Сохраняем…" : "Опубликовать версию"}
        </button>
      </div>
      <p className="muted small">
        Кликайте по карте, чтобы поставить вершины зоны внутри границы объекта;
        вершины можно перетаскивать. Рядом с существующими вершинами точка
        притягивается к ним — так соседние зоны совпадают по границе.
      </p>
      <GeoMap
        boundary={site.boundary}
        bounds={bounds}
        zones={zones}
        background={background}
        draft={draft}
        fitKey={site.id}
        snap
        onPoint={(point) => setDraft([...draft, point])}
        onMovePoint={(index, point) =>
          setDraft(draft.map((p, i) => (i === index ? point : p)))
        }
      />
      <div className="form-row">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Название зоны"
        />
        <button onClick={complete}>Замкнуть зону ({draft.length})</button>
        <button onClick={() => setDraft(draft.slice(0, -1))}>
          Отменить точку
        </button>
        <button onClick={() => setDraft([])}>Очистить контур</button>
      </div>
      <div className="zone-chips">
        {zones.map((z, i) => (
          <span key={i} className="badge">
            {z.name}
            <button
              onClick={() => {
                setDraft(openRing(z.polygon));
                setName(z.name);
                setZones(zones.filter((_, n) => n !== i));
              }}
              aria-label={"Изменить " + z.name}
            >
              ✎
            </button>
            <button
              onClick={() => setZones(zones.filter((_, n) => n !== i))}
              aria-label={"Удалить " + z.name}
            >
              ×
            </button>
          </span>
        ))}
      </div>
    </section>
  );
}
