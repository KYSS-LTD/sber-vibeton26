import type Keycloak from "keycloak-js";
export let auth: Keycloak | null = null;
export function setAuth(value: Keycloak) {
  auth = value;
}
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  if (!auth) throw new Error("Войдите через Keycloak");
  await auth.updateToken(30);
  const headers = new Headers(options.headers);
  headers.set("Authorization", `Bearer ${auth.token}`);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch("/api/v1" + path, {
    ...options,
    headers,
    cache: "no-store",
  });
  if (!response.ok) {
    const data = await response
      .json()
      .catch(() => ({ detail: `HTTP ${response.status}` }));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  }
  return response.json();
}
export async function mediaBlob(id: string): Promise<Blob> {
  if (!auth) throw new Error("Войдите");
  await auth.updateToken(30);
  const response = await fetch("/api/v1/media/" + id, {
    headers: { Authorization: `Bearer ${auth.token}` },
  });
  if (!response.ok) throw new Error("Файл недоступен");
  return response.blob();
}
export async function snapshots(
  onData: (value: any) => void,
  signal: AbortSignal,
) {
  while (!signal.aborted) {
    try {
      if (!auth) return;
      await auth.updateToken(30);
      const response = await fetch("/api/v1/me/events", {
        headers: { Authorization: `Bearer ${auth.token}` },
        signal,
      });
      if (!response.ok || !response.body) throw new Error("Нет соединения");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!signal.aborted) {
        const part = await reader.read();
        if (part.done) break;
        buffer += decoder.decode(part.value, { stream: true });
        let index;
        while ((index = buffer.indexOf("\n\n")) >= 0) {
          const message = buffer.slice(0, index);
          buffer = buffer.slice(index + 2);
          const data = message
            .split("\n")
            .find((line) => line.startsWith("data: "));
          if (data) onData(JSON.parse(data.slice(6)));
        }
      }
    } catch (error) {
      if (signal.aborted) return;
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
}
