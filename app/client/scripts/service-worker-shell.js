// Keep complete offline coverage, but do not open hundreds of requests together.
async function cacheAssets(assets, concurrency = 16) {
  const cache = await caches.open(VERSION);
  let next = 0;
  await Promise.all(Array.from({length: Math.min(concurrency, assets.length)}, async () => {
    while (next < assets.length) {
      const asset = assets[next++];
      if (await cache.match(asset)) continue;
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 20000);
      try {
        // Hashed assets are immutable. Reuse the document's fresh HTTP cache
        // instead of downloading its entry JS/CSS again during installation.
        const response = await fetch(new Request(asset, {cache: 'default', signal: controller.signal}));
        if (!response.ok) throw new Error('Offline shell asset unavailable');
        await cache.put(asset, response);
      } finally {clearTimeout(timer);}
    }
  }));
}
// Own authentication after caching the entry shell. Other views must not delay
// login. Retain the previous offline cache until the full new cache is complete.
self.addEventListener('install', event => event.waitUntil(cacheAssets(CORE_ASSETS).then(() => self.skipWaiting())));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
let warming;
function warmShell() {
  if (!warming) warming = cacheAssets(ASSETS, 4).then(() => caches.keys())
    .then(keys => {
      // An older worker can finish a download after its replacement activates.
      // Only the active worker may retire prior versions of the static cache.
      if (self.registration.active?.scriptURL !== self.location.href) return;
      return Promise.all(keys.filter(k => k.startsWith('reader-shell-') && k !== VERSION).map(k => caches.delete(k)));
    })
    .catch(error => {warming = undefined; throw error;});
  return warming;
}
self.addEventListener('message', event => {
  if (event.data?.kind !== 'reader-shell-warm') return;
  event.waitUntil(warmShell().then(
    () => event.ports[0]?.postMessage({kind: 'reader-shell-warm-complete', complete: true}),
    () => event.ports[0]?.postMessage({kind: 'reader-shell-warm-complete', complete: false})
  ));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.origin !== location.origin || event.request.method !== 'GET' || url.pathname.startsWith('/api/') || url.pathname.startsWith('/health/')) return;
  if (event.request.mode === 'navigate') event.respondWith(fetch(event.request).catch(() => caches.open(VERSION).then(cache => cache.match('/index.html'))));
  else if (ASSETS.includes(url.pathname)) event.respondWith(caches.open(VERSION).then(async cache => {
    const cached = await cache.match(url.pathname);
    if (cached) return cached;
    const response = await fetch(event.request);
    if (response.ok) await cache.put(url.pathname, response.clone());
    return response;
  }));
  else if (OPTIONAL_ASSETS.includes(url.pathname)) event.respondWith(caches.open(VERSION).then(async cache => {
    const cached = await cache.match(url.pathname);
    if (cached) return cached;
    const response = await fetch(event.request);
    if (response.ok) await cache.put(url.pathname, response.clone());
    return response;
  }));
});
