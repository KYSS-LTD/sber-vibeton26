"use client";
import { useEffect, useState } from "react";
import { mediaBlob } from "../lib/api";
export function Photo({
  id,
  alt = "Фото сотрудника",
}: {
  id?: string | null;
  alt?: string;
}) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let value = "";
    let alive = true;
    if (id)
      mediaBlob(id)
        .then((blob) => {
          value = URL.createObjectURL(blob);
          if (alive) setUrl(value);
          else URL.revokeObjectURL(value);
        })
        .catch(() => setUrl(""));
    return () => {
      alive = false;
      if (value) URL.revokeObjectURL(value);
    };
  }, [id]);
  return url ? (
    <img src={url} alt={alt} className="employee-photo" />
  ) : (
    <span className="avatar-placeholder">Лицо</span>
  );
}
