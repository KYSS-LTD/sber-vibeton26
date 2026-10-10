"use client";
import { useEffect, useRef, useState } from "react";
import QRCode from "qrcode";
import { MapPin, Clock, Smartphone, Bell } from "lucide-react";
import { api, snapshots } from "../lib/api";
import { deviceKey, signChallenge } from "../lib/device";
import { enablePush } from "../lib/push";

export function Worker({
  me,
  siteId,
  onError,
}: {
  me: any;
  siteId: string;
  onError: (text: string) => void;
}) {
  const [qr, setQr] = useState<any>(null);
  const qrRef = useRef<any>(null);
  qrRef.current = qr;
  const [image, setImage] = useState("");
  const [shift, setShift] = useState<any>(null);
  const [history, setHistory] = useState<any[]>([]);
  const [schedules, setSchedules] = useState<any[]>([]);
  const [notifications, setNotifications] = useState<any[]>([]);
  const [geoStatus, setGeoStatus] = useState("Геолокация выключена");
  const [ready, setReady] = useState(false);
  const keyRef = useRef<CryptoKeyPair | null>(null);
  const [clock, setClock] = useState(Date.now());
  const signed = useRef(new Set<string>());
  async function loadQR() {
    try {
      const value = await api(`/me/qr?site_id=${siteId}`);
      setQr(value);
      setImage(
        await QRCode.toDataURL(value.token, {
          width: 320,
          margin: 2,
          errorCorrectionLevel: "M",
        }),
      );
    } catch (e: any) {
      setQr(null);
      setImage("");
      onError(e.message);
    }
  }
  useEffect(() => {
    let active = true;
    deviceKey(me.employee.id)
      .then(async (pair) => {
        keyRef.current = pair;
        const public_jwk = await crypto.subtle.exportKey("jwk", pair.publicKey);
        await api("/me/device", {
          method: "POST",
          body: JSON.stringify({ public_jwk }),
        });
        if (active) setReady(true);
      })
      .catch((e) => onError(e.message));
    return () => {
      active = false;
    };
  }, [me.employee.id]);
  useEffect(() => {
    if (ready && siteId) void loadQR();
  }, [ready, siteId]);
  useEffect(() => {
    const interval = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(interval);
  }, []);
  useEffect(() => {
    if (qr && clock >= new Date(qr.expires_at).getTime()) {
      setQr(null);
      setImage("");
      void loadQR();
    }
  }, [clock, qr]);
  useEffect(() => {
    const controller = new AbortController();
    snapshots(async (value) => {
      setShift(value.shift);
      setNotifications(value.notifications);
      for (const challenge of value.challenges) {
        const current = qrRef.current;
        if (
          !current ||
          current.credential_id !== challenge.qr_id ||
          !keyRef.current ||
          signed.current.has(challenge.id) ||
          document.visibilityState !== "visible"
        )
          continue;
        signed.current.add(challenge.id);
        try {
          const signature = await signChallenge(
            keyRef.current,
            challenge.challenge,
          );
          await api(`/passages/${challenge.id}/confirm`, {
            method: "POST",
            body: JSON.stringify({ signature }),
          });
        } catch (e: any) {
          signed.current.delete(challenge.id);
          onError(e.message);
        }
      }
    }, controller.signal);
    api<any[]>("/me/shifts")
      .then(setHistory)
      .catch((e) => onError(e.message));
    api<any[]>("/schedules")
      .then(setSchedules)
      .catch((e) => onError(e.message));
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!shift) {
      setGeoStatus("Геолокация выключена");
      return;
    }
    let active = true;
    const controller = new AbortController();
    function sample() {
      if (!active) return;
      setGeoStatus("Получаем координаты…");
      navigator.geolocation.getCurrentPosition(
        async (position) => {
          if (!active || controller.signal.aborted) return;
          try {
            const result = await api("/me/location", {
              method: "POST",
              signal: controller.signal,
              body: JSON.stringify({
                shift_id: shift.id,
                sample_id: crypto.randomUUID(),
                measured_at: new Date().toISOString(),
                latitude: position.coords.latitude,
                longitude: position.coords.longitude,
                accuracy_m: position.coords.accuracy,
              }),
            });
            if (active)
              setGeoStatus(
                result.inside
                  ? "На объекте · следующая проверка через 10 минут"
                  : "Вы за границей объекта · руководитель уведомлён",
              );
          } catch (e: any) {
            if (active) setGeoStatus(e.message);
          }
        },
        (error) => {
          if (active) setGeoStatus("Координаты недоступны: " + error.message);
        },
        { enableHighAccuracy: false, maximumAge: 0, timeout: 20000 },
      );
    }
    sample();
    const interval = setInterval(sample, 600000);
    return () => {
      active = false;
      clearInterval(interval);
      controller.abort();
      setGeoStatus("Геолокация выключена");
    };
  }, [shift?.id]);
  async function push() {
    try {
      await enablePush();
    } catch (e: any) {
      onError(e.message);
    }
  }
  const hours = shift
    ? ((clock - new Date(shift.opened_at).getTime()) / 3600000).toFixed(1)
    : "—";
  return (
    <div className="worker-grid">
      <section className="panel qr-panel">
        <span className="eyebrow">ЛИЧНЫЙ ПРОПУСК</span>
        <h2>{me.employee.full_name}</h2>
        <p className="muted">
          {me.sites.find((s: any) => s.id === siteId)?.name}
        </p>
        <div className="qr-image">
          {image ? (
            <img src={image} alt="Личный суточный QR-код" />
          ) : (
            <div className="empty">
              <Smartphone size={38} />
              <p>Пропуск готовится</p>
              <small>
                Нужны назначение, три эталона и зарегистрированный браузер.
              </small>
            </div>
          )}
        </div>
        <span className="badge">Обновление ежедневно</span>
        {qr && (
          <small className="muted">
            Действует до {new Date(qr.expires_at).toLocaleString("ru")}
          </small>
        )}
        <p className="small">
          Покажите этот экран на КПП и выполните повороты головы перед камерой.
        </p>
        <button onClick={loadQR} disabled={!ready}>
          Обновить пропуск
        </button>
      </section>
      <div className="stack">
        <section className="panel">
          <span className="eyebrow">МОЯ СМЕНА</span>
          <div className="shift-state">
            <span className={`status-dot ${shift ? "ok" : ""}`} />
            <h2>{shift ? "Смена открыта" : "Смена закрыта"}</h2>
          </div>
          <div className="worker-metrics">
            <div>
              <Clock />
              <strong>{hours}</strong>
              <span>часов на смене</span>
            </div>
            <div>
              <MapPin />
              <strong>{shift ? "Включена" : "Выключена"}</strong>
              <span>геопроверка</span>
            </div>
          </div>
          <p className="geo-status">{geoStatus}</p>
          <p className="small muted">
            Координаты отправляются только во время открытой смены. При закрытом
            браузере позиция может стать неизвестной.
          </p>
        </section>
        <section className="panel">
          <div className="section-heading">
            <h2>Расписание</h2>
            <Bell size={20} />
          </div>
          {schedules.length ? (
            schedules.slice(0, 4).map((s) => (
              <div className="list-row" key={s.id}>
                <strong>{s.site_name}</strong>
                <span>
                  {new Date(s.starts_at).toLocaleString("ru")} —{" "}
                  {new Date(s.ends_at).toLocaleTimeString("ru")}
                </span>
              </div>
            ))
          ) : (
            <p className="empty">Запланированных смен нет</p>
          )}
          <button onClick={push}>Разрешить уведомления</button>
        </section>
        <section className="panel">
          <h2>Уведомления</h2>
          {notifications.slice(0, 5).map((n) => (
            <div className="list-row" key={n.id}>
              <span>
                {n.payload.detail ||
                  (
                    {
                      shift_opened: "Смена открыта",
                      shift_closed: "Смена закрыта",
                    } as any
                  )[n.kind] ||
                  n.kind}
              </span>
              <small>{new Date(n.created_at).toLocaleTimeString("ru")}</small>
            </div>
          ))}
          {!notifications.length && (
            <p className="empty">Новых уведомлений нет</p>
          )}
        </section>
        <section className="panel">
          <h2>Последние смены</h2>
          {history.slice(0, 5).map((s) => (
            <div className="list-row" key={s.id}>
              <strong>{s.site_name}</strong>
              <span>
                {new Date(s.opened_at).toLocaleString("ru")} ·{" "}
                {s.closed_at ? "Закрыта" : "Открыта"}
              </span>
            </div>
          ))}
        </section>
      </div>
    </div>
  );
}
