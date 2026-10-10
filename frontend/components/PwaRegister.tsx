"use client";
import { useEffect } from "react";
import { registerWorker } from "../lib/pwa";

// Регистрирует service worker в продакшене: без него браузер не предложит установку и не покажет офлайн-страницу.
export function PwaRegister() {
  useEffect(() => {
    if (process.env.NODE_ENV === "production" && "serviceWorker" in navigator)
      registerWorker().catch(() => {});
  }, []);
  return null;
}
