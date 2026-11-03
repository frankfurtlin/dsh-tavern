// Coordinate cookie rotations across documents. Auth bodies and responses live
// only until the pending operation settles; they never enter CacheStorage.
const authQueues = new Map(), authBarriers = new Map(), pendingRefreshes = new Map();
function authOperation(request) {
  const origin = new URL(request.url).origin;
  const refresh = request.method === 'POST' && new URL(request.url).pathname === '/api/reader/v1/auth/refresh';
  if (!refresh) authBarriers.set(origin, (authBarriers.get(origin) || 0) + 1);
  const key = origin + ':' + (authBarriers.get(origin) || 0);
  if (refresh && pendingRefreshes.has(key)) return pendingRefreshes.get(key);
  // Copy while the initiating document is alive, then detach its abort signal.
  const body = request.clone().arrayBuffer();
  body.catch(() => {});
  const previous = authQueues.get(origin) || Promise.resolve();
  const operation = previous.catch(() => {}).then(async () => {
    const bytes = await body;
    if (bytes.byteLength > 65536) throw new TypeError('Authentication request exceeds keepalive limit');
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 15000);
    try {
    const response = await fetch(new Request(request.url, {
      method: request.method, headers: request.headers, credentials: request.credentials,
      mode: request.mode, redirect: request.redirect, referrer: request.referrer,
      referrerPolicy: request.referrerPolicy, cache: 'no-store', keepalive: true,
      body: bytes, signal: controller.signal
    }));
    // Finish the small JSON body before releasing the queue and worker lifetime.
    const content = await response.arrayBuffer();
    return new Response([204,205,304].includes(response.status) ? null : content, {
      status: response.status, statusText: response.statusText, headers: response.headers
    });
    } finally {clearTimeout(timer);}
  });
  const tail = operation.then(() => undefined, () => undefined);
  authQueues.set(origin, tail);
  if (refresh) pendingRefreshes.set(key, operation);
  const release = () => {
    if (authQueues.get(origin) === tail) authQueues.delete(origin);
    if (pendingRefreshes.get(key) === operation) pendingRefreshes.delete(key);
  };
  operation.then(release, release);
  return operation;
}
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url), path = url.pathname;
  const configuredOrigin = typeof AUTH_BACKEND_ORIGIN === 'string' ? AUTH_BACKEND_ORIGIN : '';
  if (url.origin !== self.location.origin && url.origin !== configuredOrigin) return;
  if (!['POST','PUT','DELETE'].includes(event.request.method) || !path.startsWith('/api/reader/v1/auth/')) return;
  const pending = authOperation(event.request);
  event.respondWith(pending.then(response => response.clone()));
  // The old document may disappear while Set-Cookie is being committed.
  event.waitUntil(pending.then(() => {}, () => {}));
});
self.addEventListener('message', event => {
  if (event.data?.kind === 'reader-auth-ready') event.ports[0]?.postMessage({kind:'reader-auth-ready',version:1});
});
