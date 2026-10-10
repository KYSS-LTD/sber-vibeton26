"use client";
import { Download } from "lucide-react";
import { useInstall } from "../lib/pwa";

export function InstallApp() {
  const { installed, canInstall, showIosHint, install } = useInstall();
  if (installed || (!canInstall && !showIosHint)) return null;
  return (
    <section className="panel">
      <div className="section-heading">
        <h2>Приложение на телефон</h2>
        <Download size={20} />
      </div>
      <p className="small muted">
        Пропуск открывается в один тап с главного экрана, без адресной строки.
      </p>
      {canInstall ? (
        <button className="primary" onClick={() => void install()}>
          Установить приложение
        </button>
      ) : (
        <p className="small">
          В Safari нажмите «Поделиться» и выберите «На экран «Домой»».
        </p>
      )}
    </section>
  );
}
