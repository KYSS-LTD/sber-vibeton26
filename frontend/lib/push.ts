import { api } from "./api";

export async function enablePush() {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("Уведомления не разрешены");
  const worker = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;
  const value = await api("/push/key");
  const raw = Uint8Array.from(
    atob(value.public_key.replaceAll("-", "+").replaceAll("_", "/")),
    (c) => c.charCodeAt(0),
  );
  const subscription = await worker.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: raw,
  });
  await api("/push/subscriptions", {
    method: "POST",
    body: JSON.stringify({ subscription: subscription.toJSON() }),
  });
}
