"use client";
import { useEffect, useState } from "react";
import { api, mediaBlob } from "../lib/api";
import { SiteMap, boundsFor } from "./SiteMap";

export function MapEditor({
  site,
  onError,
  onSaved,
}: {
  site: any;
  onError: (s: string) => void;
  onSaved: () => void;
}) {
  const [bounds, setBounds] = useState(boundsFor(site.boundary));
  const [zones, setZones] = useState<any[]>([]);
  const [draft, setDraft] = useState<number[][]>([]);
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
  async function upload(file: File) {
    const form = new FormData();
    form.append("file", file);
    try {
      const result = await api(`/sites/${site.id}/plan`, {
        method: "POST",
        body: form,
      });
      setMediaId(result.media_id);
    } catch (e: any) {
      onError(e.message);
    }
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
        Загрузите схему, задайте координаты её углов. Точками на схеме нарисуйте
        зоны внутри границы объекта.
      </p>
      <div className="form-row">
        <label className="file-input">
          Загрузить схему
          <input
            type="file"
            accept="image/*"
            onChange={(e) => {
              if (e.target.files?.[0]) void upload(e.target.files[0]);
            }}
          />
        </label>
        {Object.entries(bounds).map(([key, value]) => (
          <label key={key} className="compact-field">
            {
              (
                {
                  west: "Запад",
                  east: "Восток",
                  south: "Юг",
                  north: "Север",
                } as any
              )[key]
            }
            <input
              type="number"
              step="0.000001"
              value={value}
              onChange={(e) =>
                setBounds({ ...bounds, [key]: Number(e.target.value) })
              }
            />
          </label>
        ))}
      </div>
      <SiteMap
        boundary={site.boundary}
        bounds={bounds}
        zones={zones}
        background={background}
        draft={draft}
        onPoint={(point) => setDraft([...draft, point])}
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
