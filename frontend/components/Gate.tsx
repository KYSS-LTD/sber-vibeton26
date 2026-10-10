"use client";
import { useEffect, useRef, useState } from "react";
import { BrowserQRCodeReader, IScannerControls } from "@zxing/browser";
import { Camera, ScanLine, ShieldCheck } from "lucide-react";
import { api } from "../lib/api";

export function Gate({
  siteId,
  onError,
}: {
  siteId: string;
  onError: (text: string) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const scanner = useRef<IScannerControls | null>(null);
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
      scanner.current?.stop();
      stream.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  async function openCamera() {
    try {
      scanner.current?.stop();
      stream.current?.getTracks().forEach((t) => t.stop());
      stream.current = await navigator.mediaDevices.getUserMedia({
        video: deviceId
          ? {
              deviceId: { exact: deviceId },
              width: { ideal: 640 },
              height: { ideal: 480 },
            }
          : {
              facingMode: "user",
              width: { ideal: 640 },
              height: { ideal: 480 },
            },
        audio: false,
      });
      setDevices(
        (await navigator.mediaDevices.enumerateDevices()).filter(
          (d) => d.kind === "videoinput",
        ),
      );
      video.current!.srcObject = stream.current;
      await video.current!.play();
      setCamera(true);
      setStage("ready");
      setInstruction("Наведите камеру на QR сотрудника");
    } catch (e) {
      onError("Камера недоступна. Проверьте разрешение браузера и HTTPS.");
    }
  }
  async function capture(row: any) {
    setStage("capture");
    stageRef.current = "capture";
    scanner.current?.stop();
    const frames: Blob[] = [];
    const canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = 480;
    const context = canvas.getContext("2d")!;
    const start = performance.now();
    for (let i = 0; i < 24; i++) {
      if (!alive.current) return;
      const elapsed = performance.now() - start;
      const action =
        elapsed < 2000
          ? "Смотрите прямо"
          : elapsed < 4000
            ? row.actions[0] === "left"
              ? "Поверните голову влево"
              : "Поверните голову вправо"
            : elapsed < 6000
              ? row.actions[1] === "left"
                ? "Поверните голову влево"
                : "Поверните голову вправо"
              : "Снова смотрите прямо";
      setInstruction(action);
      context.drawImage(video.current!, 0, 0, 640, 480);
      const blob = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob(resolve, "image/jpeg", 0.8),
      );
      if (blob) frames.push(blob);
      await new Promise((resolve) => setTimeout(resolve, 340));
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
      onError(e.message);
      setStage("ready");
      stageRef.current = "ready";
    }
  }
  useEffect(() => {
    if (!camera || stage !== "ready" || !session || !video.current) return;
    const reader = new BrowserQRCodeReader();
    let stop = false;
    reader
      .decodeFromVideoElement(video.current, (result) => {
        if (result && !stop) void scanned(result.getText());
      })
      .then((controls) => {
        if (stop) controls.stop();
        else scanner.current = controls;
      })
      .catch((e) => onError(e.message));
    return () => {
      stop = true;
      scanner.current?.stop();
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
            disabled={stage === "capture" || stage === "verify"}
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
            onChange={(e) => setDeviceId(e.target.value)}
          >
            <option value="">Камера по умолчанию</option>
            {devices.map((d) => (
              <option key={d.deviceId} value={d.deviceId}>
                {d.label}
              </option>
            ))}
          </select>
          <button
            onClick={openCamera}
            disabled={stage === "capture" || stage === "verify"}
          >
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
          Телефон сотрудника должен показывать QR. После сканирования проверка
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
