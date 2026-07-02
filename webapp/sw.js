/* PolyGlotty Service Worker — offline app-shell caching.
 *
 * The whole course (A0–C2, lessons, tests, vocab) ships as inline JS constants
 * inside index.html, so a cached app shell == the full "Учёба"/"Прогресс"
 * experience offline. This SW therefore focuses on caching the shell + static
 * assets; progress write-back is handled in the page (IndexedDB sync queue).
 *
 * Strategy:
 *   • Navigation (HTML document): NETWORK-FIRST → cache fallback. Online users
 *     always receive the freshly deployed build (important: we push to live
 *     constantly); offline users get the last good shell.
 *   • Same-origin static assets (JS/CSS/icons): CACHE-FIRST + background
 *     revalidate — they change rarely and should load instantly.
 *   • Cross-origin (Google Fonts, telegram-web-app.js): cache-first, opaque,
 *     best-effort — never block the page if they fail.
 *   • /api/* and every non-GET request: NETWORK-ONLY, never cached
 *     (dynamic + per-user authenticated).
 *
 * Bump CACHE_VERSION to force a clean re-cache after a breaking shell change.
 */
const CACHE_VERSION = 'v2';
const CACHE = 'polyglotty-' + CACHE_VERSION;
const SHELL = ['/', '/data/growth_engine.js', '/icons/fire.png'];

self.addEventListener('install', (e) => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    // Cache best-effort, one by one: a single 404 must not abort the install
    // (which caches.addAll would do — it is atomic).
    await Promise.all(SHELL.map((u) => c.add(u).catch(() => {})));
    self.skipWaiting();
  })());
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(
      keys.filter((k) => k.startsWith('polyglotty-') && k !== CACHE)
          .map((k) => caches.delete(k))
    );
    await self.clients.claim();
  })());
});

function isApi(url) {
  return url.pathname.startsWith('/api/') || url.pathname === '/health';
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return; // mutations go straight to the network

  let url;
  try { url = new URL(req.url); } catch (_) { return; }

  // Never cache the API — it is dynamic and per-user authenticated.
  if (url.origin === self.location.origin && isApi(url)) return;

  const accept = req.headers.get('accept') || '';

  // App document → network-first, fall back to the cached shell when offline.
  if (req.mode === 'navigate' || accept.includes('text/html')) {
    e.respondWith((async () => {
      try {
        const fresh = await fetch(req);
        const c = await caches.open(CACHE);
        c.put('/', fresh.clone()).catch(() => {});
        return fresh;
      } catch (_) {
        const cached = (await caches.match('/')) || (await caches.match(req));
        return cached || new Response(
          'Offline', { status: 503, headers: { 'Content-Type': 'text/plain' } }
        );
      }
    })());
    return;
  }

  // Everything else (static assets, fonts, telegram js) → cache-first with a
  // background refresh so the next launch picks up any change.
  e.respondWith((async () => {
    const cached = await caches.match(req);
    if (cached) {
      fetch(req).then((res) => {
        if (res && (res.ok || res.type === 'opaque')) {
          caches.open(CACHE).then((c) => c.put(req, res.clone()).catch(() => {}));
        }
      }).catch(() => {});
      return cached;
    }
    try {
      const res = await fetch(req);
      if (res && (res.ok || res.type === 'opaque')) {
        const c = await caches.open(CACHE);
        c.put(req, res.clone()).catch(() => {});
      }
      return res;
    } catch (_) {
      return cached || Response.error();
    }
  })());
});

self.addEventListener('message', (e) => {
  if (e.data === 'skipWaiting') self.skipWaiting();
});
