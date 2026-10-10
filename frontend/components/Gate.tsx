"use client";
import { useEffect, useRef, useState } from "react";
import { BrowserQRCodeReader, IScannerControls } from "@zxing/browser";
import { Camera, ScanLine, ShieldCheck } from "lucide-react";
import { api } from "../lib/api";

const FRAME_COUNT = 16;
const FRAME_INTERVAL_MS = 250;
const MAX_FRAME_SIDE = 1280;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function Gate({
  siteId,
  onError,
}: {
  siteId: string;
  onError: (text: string) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const cameraRequest = useRef(0);
  const rejected = useRef({ token: "", until: 0 });
  const [session, setSession] = useState("");
  const [checkpoint, setCheckpoint] = useState("");
  const [checkpoints, setCheckpoints] = useState<any[]>([]);
  const [direction, setDirection] = useState("entry");
  const directionRef = useRef(direction);
  directionRef.current = direction;
  const [stage, setStage] = useState("idle");
  const stageRef = useRef(stage);
  stageRef.current = stage;
  const [attempt, setAttempt] = useState<any>(null);
  const [instruction, setInstruction] = useState("Разрешите доступ к камере");
  const [camera, setCamera] = useState(false);
  const [journal, setJournal] = useState<any[]>([]);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const alive = useRef(true);
  const busy = stage === "scan" || stage === "capture" || stage === "verify";
  const refresh = () =>
    api<any[]>(`/sites/${siteId}/journal`)
      .then(setJournal)
      .catch((e) => onError(e.message));
  useEffect(() => {
    api<any[]>(`/sites/${siteId}/checkpoints`)
      .then((rows) => {
        setCheckpoints(rows);
        setCheckpoint(rows[0]?.id || "");
      })
      .catch((e) => onError(e.message));
    refresh();
  }, [siteId]);
  useEffect(() => {
    if (checkpoint)
      api<{ id: string }>("/gates/sessions", {
        method: "POST",
        body: JSON.stringify({ checkpoint_id: checkpoint }),
      })
        .then((r) => setSession(r.id))
        .catch((e) => onError(e.message));
  }, [checkpoint]);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      stream.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  // choice: "" = фронтальная, "environment" = основная, иначе точный deviceId.
  async function openCamera(choice = deviceId) {
    const request = ++cameraRequest.current;
    // Выключаем камеру: эффект сканера QR останавливается и заново запускается
    // для нового потока, когда camera снова станет true.
    setCamera(false);
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    try {
      const next = await navigator.mediaDevices.getUserMedia({
        video: {
          ...(choice === "environment"
            ? { facingMode: { ideal: "environment" } }
            : choice
              ? { deviceId: { exact: choice } }
              : { facingMode: "user" }),
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: false,
      });
      if (request !== cameraRequest.current || !alive.current) {
        next.getTracks().forEach((t) => t.stop());
        return;
      }
      stream.current = next;
      setDevices(
        (await navigator.mediaDevices.enumerateDevices()).filter(
          (d) => d.kind === "videoinput",
        ),
      );
      video.current!.srcObject = next;
      await video.current!.play();
      if (request !== cameraRequest.current) return;
      stageRef.current = "ready";
      setCamera(true);
      setStage("ready");
      setInstruction("Наведите камеру на QR сотрудника");
    } catch (e) {
      if (request !== cameraRequest.current) return;
      stageRef.current = "idle";
      setStage("idle");
      setInstruction("Камера выключена");
      onError("Камера недоступна. Проверьте разрешение браузера и HTTPS.");
    }
  }
  async function capture(row: any) {
    setStage("capture");
    stageRef.current = "capture";
    setInstruction("Смотрите прямо в камеру");
    const frames: Blob[] = [];
    const element = video.current!;
    // Сохраняем пропорции камеры: растягивание кадра в 640x480 искажало лицо
    // (особенно у портретной камеры телефона) и размывало изображение.
    const scale = Math.min(
      1,
      MAX_FRAME_SIDE / Math.max(element.videoWidth, element.videoHeight),
    );
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(element.videoWidth * scale);
    canvas.height = Math.round(element.videoHeight * scale);
    const context = canvas.getContext("2d")!;
    for (let i = 0; i < FRAME_COUNT; i++) {
      if (!alive.current) return;
      context.drawImage(element, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob(resolve, "image/jpeg", 0.92),
      );
      if (blob) frames.push(blob);
      await sleep(FRAME_INTERVAL_MS);
    }
    setStage("verify");
    setInstruction("Сервер проверяет лицо и подтверждение телефона…");
    const form = new FormData();
    frames.forEach((blob, i) => form.append("frames", blob, `frame-${i}.jpg`));
    try {
      const result = await api(`/passages/${row.id}/verify`, {
        method: "POST",
        body: form,
      });
      setAttempt(result);
      setStage("result");
      setInstruction(
        result.state === "allowed"
          ? "Проход разрешён"
          : result.reason || "Проход отклонён",
      );
      refresh();
    } catch (e: any) {
      setStage("result");
      setInstruction("Проверка не завершена");
      onError(e.message);
    }
  }
  async function scanned(token: string) {
    if (stageRef.current !== "ready" || !session) return;
    // Тот же отклонённый QR остаётся перед камерой: не долбим сервер повторно.
    if (token === rejected.current.token && Date.now() < rejected.current.until)
      return;
    stageRef.current = "scan";
    setStage("scan");
    try {
      const row = await api("/passages", {
        method: "POST",
        body: JSON.stringify({
          session_id: session,
          token,
          direction: directionRef.current,
        }),
      });
      setAttempt(row);
      await capture(row);
    } catch (e: any) {
      rejected.current = { token, until: Date.now() + 4000 };
      onError(e.message);
      setStage("ready");
      stageRef.current = "ready";
    }
  }
  useEffect(() => {
    const element = video.current;
    if (!camera || stage !== "ready" || !session || !element) return;
    let stopped = false;
    let generation = 0;
    let controls: IScannerControls | undefined;
    // Сканер привязан к размеру кадра на момент запуска, поэтому перезапускаем
    // его и при смене потока (камеры), и при повороте телефона (событие resize).
    const start = () => {
      const current = ++generation;
      controls?.stop();
      controls = undefined;
      if (!element.videoWidth) return;
      new BrowserQRCodeReader(undefined, { delayBetweenScanAttempts: 200 })
        .decodeFromVideoElement(element, (result) => {
          if (result && !stopped && current === generation)
            void scanned(result.getText());
        })
        .then((next) => {
          if (stopped || current !== generation) next.stop();
          else controls = next;
        })
        .catch((e) => {
          if (!stopped && current === generation)
            onError(e?.message || "Не удалось запустить сканер QR");
        });
    };
    start();
    element.addEventListener("resize", start);
    return () => {
      stopped = true;
      element.removeEventListener("resize", start);
      controls?.stop();
    };
  }, [camera, stage, session]);
  return (
    <div className="gate-grid">
      <section className="panel camera-panel">
        <div className="section-heading">
          <div>
            <span className="eyebrow">КОНТРОЛЬ ПРОХОДА</span>
            <h2>Камера КПП</h2>
          </div>
          <Camera size={22} />
        </div>
        <div className="form-row">
          <select
            value={checkpoint}
            onChange={(e) => setCheckpoint(e.target.value)}
            disabled={busy}
          >
            {checkpoints.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <div className="segmented">
            <button
              className={direction === "entry" ? "selected" : ""}
              onClick={() => setDirection("entry")}
            >
              Вход
            </button>
            <button
              className={direction === "exit" ? "selected" : ""}
              onClick={() => setDirection("exit")}
            >
              Выход
            </button>
          </div>
        </div>
        <div
          className={`camera-view ${stage === "result" ? (attempt?.state === "allowed" ? "success" : "failure") : ""}`}
        >
          <video ref={video} muted playsInline />
          <div className="scan-frame" />
          <div className="camera-caption">
            <ScanLine size={18} />
            {instruction}
          </div>
        </div>
        <div className="form-row">
          <select
            value={deviceId}
            onChange={(e) => {
              setDeviceId(e.target.value);
              if (camera) void openCamera(e.target.value);
            }}
            disabled={busy}
          >
            <option value="">Фронтальная камера</option>
            <option value="environment">Основная камера</option>
            {devices.map((d, i) => (
              <option key={d.deviceId} value={d.deviceId}>
                {d.label || `Камера ${i + 1}`}
              </option>
            ))}
          </select>
          <button onClick={() => void openCamera()} disabled={busy}>
            {camera ? "Сменить камеру" : "Включить камеру"}
          </button>
        </div>
        {stage === "result" && (
          <button
            className="primary full"
            onClick={() => {
              setAttempt(null);
              setStage("ready");
              setInstruction("Наведите камеру на QR сотрудника");
            }}
          >
            Следующий сотрудник
          </button>
        )}
        <p className="muted small">
          Телефон сотрудника должен показывать QR. После сканирования сотрудник
          смотрит прямо в камеру, поворачивать голову не нужно; проверка
          занимает до 30 секунд.
        </p>
      </section>
      <section className="panel">
        <div className="section-heading">
          <div>
            <span className="eyebrow">ЭТОТ ОБЪЕКТ</span>
            <h2>Журнал проходов</h2>
          </div>
          <ShieldCheck size={22} />
        </div>
        <div className="journal-list">
          {journal.length === 0 ? (
            <p className="empty">Проходов пока нет</p>
          ) : (
            journal.slice(0, 20).map((row) => (
              <div className="journal-row" key={row.id}>
                <span
                  className={`status-dot ${row.state === "allowed" ? "ok" : row.state === "pending" ? "pending" : "bad"}`}
                />
                <div>
                  <strong>{row.full_name}</strong>
                  <small>
                    {row.direction === "entry" ? "Вход" : "Выход"} ·{" "}
                    {new Date(row.created_at).toLocaleTimeString("ru")}
                  </small>
                  {row.reason && (
                    <small className="danger-text">{row.reason}</small>
                  )}
                </div>
                <span className="badge">
                  {row.state === "allowed"
                    ? "Разрешён"
                    : row.state === "pending"
                      ? "Проверка"
                      : "Отказ"}
                </span>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
