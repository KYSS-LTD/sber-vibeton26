import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const source = fs.readFileSync(
  path.join(__dirname, "..", "public", "sw.js"),
  "utf8",
);
const ORIGIN = "https://app.test";

// Запускает public/sw.js в песочнице с минимальными моками Service Worker API.
function loadWorker(script = "/sw.js") {
  const handlers: Record<string, (event: any) => void> = {};
  const store = new Map<string, Map<string, any>>();
  const net = { online: true, requests: [] as string[] };
  const href = (request: any) =>
    typeof request === "string" ? new URL(request, ORIGIN).href : request.url;
  const bucket = (name: string) => {
    if (!store.has(name)) store.set(name, new Map());
    return store.get(name)!;
  };
  const caches = {
    async open(name: string) {
      const entries = bucket(name);
      return {
        match: async (request: any) => entries.get(href(request)),
        put: async (request: any, response: any) =>
          void entries.set(href(request), response),
        addAll: async (urls: string[]) => {
          for (const url of urls)
            entries.set(href(url), { ok: true, from: "precache", url });
        },
      };
    },
    async match(request: any) {
      for (const entries of store.values()) {
        const hit = entries.get(href(request));
        if (hit) return hit;
      }
    },
    keys: async () => [...store.keys()],
    delete: async (name: string) => store.delete(name),
  };
  const self = {
    location: new URL(ORIGIN + script),
    addEventListener: (type: string, fn: (event: any) => void) =>
      void (handlers[type] = fn),
    skipWaiting: async () => {},
    clients: { claim: async () => {}, matchAll: async () => [] },
    registration: { showNotification: async () => {} },
  };
  const fetch = async (request: any) => {
    net.requests.push(href(request));
    if (!net.online) throw new TypeError("offline");
    const url = href(request);
    // Клон уходит в кэш, поэтому помечаем его: так видно, откуда пришёл ответ.
    const clone = () => ({ ok: true, from: "cache", url, clone });
    return { ok: true, from: "network", url, clone };
  };
  vm.runInNewContext(source, { self, caches, fetch, URL });
  async function lifecycle(type: "install" | "activate") {
    const waits: Promise<unknown>[] = [];
    handlers[type]({ waitUntil: (p: Promise<unknown>) => waits.push(p) });
    await Promise.all(waits);
  }
  // Возвращает ответ воркера или null, если запрос он не перехватывает.
  async function request(path: string, init: any = {}): Promise<any> {
    let responded = null as Promise<any> | null;
    handlers.fetch({
      request: {
        method: "GET",
        mode: "cors",
        url: new URL(path, ORIGIN).href,
        ...init,
      },
      respondWith: (p: Promise<any>) => (responded = p),
    });
    return responded ? await responded : null;
  }
  return { lifecycle, request, net, store, bucket };
}

test("service worker never intercepts API, auth, non-GET or cross-origin requests", async () => {
  const worker = loadWorker();
  await worker.lifecycle("install");
  expect(await worker.request("/api/v1/me/qr?site_id=1")).toBeNull();
  expect(await worker.request("/auth/realms/prohod/token")).toBeNull();
  expect(
    await worker.request("/api/v1/me/location", { method: "POST" }),
  ).toBeNull();
  expect(
    await worker.request("https://tile.openstreetmap.org/1/1/1.png"),
  ).toBeNull();
  expect(worker.net.requests).toEqual([]);
});

test("service worker serves the offline page for navigations without network", async () => {
  const worker = loadWorker();
  await worker.lifecycle("install");
  expect((await worker.request("/", { mode: "navigate" })).from).toBe(
    "network",
  );
  worker.net.online = false;
  const offline = await worker.request("/", { mode: "navigate" });
  expect(offline.url).toBe("/offline.html");
});

test("service worker caches hashed static assets once", async () => {
  const worker = loadWorker();
  await worker.lifecycle("install");
  await worker.request("/_next/static/chunks/app-1a2b.js");
  worker.net.online = false;
  const again = await worker.request("/_next/static/chunks/app-1a2b.js");
  expect(again.from).toBe("cache");
  expect(worker.net.requests).toHaveLength(1);
});

test("service worker removes caches of previous versions on activate", async () => {
  const worker = loadWorker();
  worker.bucket("prohod-static-v0");
  await worker.lifecycle("install");
  await worker.lifecycle("activate");
  expect([...worker.store.keys()]).toEqual(["prohod-shell-v1"]);
});

test("service worker registered for development does not cache anything", async () => {
  const worker = loadWorker("/sw.js?dev");
  await worker.lifecycle("install");
  expect(await worker.request("/_next/static/chunks/app.js")).toBeNull();
  expect(await worker.request("/", { mode: "navigate" })).toBeNull();
});
