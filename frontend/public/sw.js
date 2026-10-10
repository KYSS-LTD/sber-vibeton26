// Service worker PWA «Проход»: оболочка приложения и push-уведомления.
// Данные пользователя (/api/, /auth/) никогда не кэшируются: суточный QR и токены
// не должны оседать на диске устройства. Фоновый сбор GPS service worker не делает.
const VERSION = 'v1';
const STATIC_CACHE = `prohod-static-${VERSION}`;
const SHELL_CACHE = `prohod-shell-${VERSION}`;
const OFFLINE_URL = '/offline.html';
const SHELL = [OFFLINE_URL, '/manifest.json', '/icon.svg', '/icons/icon-192.png'];
// В режиме разработки (регистрация с ?dev) файлы меняются без смены имени: ничего не кэшируем.
const DEV = new URL(self.location.href).searchParams.has('dev');

self.addEventListener('install', event => {
  event.waitUntil(caches.open(SHELL_CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const key of await caches.keys())
      if (key !== STATIC_CACHE && key !== SHELL_CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok) cache.put(request, response.clone());
  return response;
}

self.addEventListener('fetch', event => {
  const { request } = event;
  if (DEV || request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/auth/')) return;
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  // Имена файлов в /_next/static/ содержат хэш содержимого, поэтому их можно кэшировать навсегда.
  if (url.pathname.startsWith('/_next/static/'))
    event.respondWith(cacheFirst(request, STATIC_CACHE));
  else if (url.pathname.startsWith('/icons/') || url.pathname === '/icon.svg' || url.pathname === '/manifest.json')
    event.respondWith(caches.match(request).then(hit => hit || fetch(request)));
});

self.addEventListener('push', event => {
  const data = event.data ? event.data.json() : {};
  event.waitUntil(self.registration.showNotification(data.title || 'Проход', {
    body: data.body || 'Новое уведомление',
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    if (windows.length) return windows[0].focus();
    return self.clients.openWindow('/');
  })());
});
