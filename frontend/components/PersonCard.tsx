"use client";
import { Photo } from "./Photo";

export function PersonCard({
  person,
  onClose,
}: {
  person: any;
  onClose: () => void;
}) {
  return (
    <div className="map-person-card">
      <button className="close" onClick={onClose}>
        ×
      </button>
      <Photo id={person.photo_id} />
      <strong>{person.full_name}</strong>
      <span>{Number(person.hours).toFixed(1)} ч на смене</span>
      <small>
        {person.suspicious
          ? "Координаты требуют проверки"
          : person.location_unknown
            ? "Геопозиция устарела"
            : `Обновлено ${new Date(person.location_at).toLocaleTimeString("ru")}`}
      </small>
    </div>
  );
}
