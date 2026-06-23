const CACHE_NAME = 'civikindia-v3';
const CORE_ASSETS = [
  '/',
  '/static/manifest.json',
  '/static/brand/favicon.ico',
  '/static/brand/logo-mark.png',
  '/static/brand/logo-mark.webp',
  '/static/brand/pwa-192.png',
  '/static/brand/pwa-512.png',
  '/static/css/mibsp-tokens.css',
  '/static/css/style-core.css',
  '/static/vendor/bootstrap-icons/bootstrap-icons.css',
  '/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2',
  '/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff',
  '/static/vendor/bootstrap/bootstrap.min.css',
  '/static/vendor/bootstrap/bootstrap.bundle.min.js',
  '/static/js/main.js'
];

function isApiRequest(request) {
  const url = new URL(request.url);
  return url.pathname.startsWith('/api/');
}

function isNavigationRequest(request) {
  return request.mode === 'navigate';
}

function isSensitivePath(pathname) {
  return pathname.startsWith('/admin')
    || pathname.startsWith('/officer')
    || pathname.startsWith('/auth')
    || pathname.startsWith('/complaint/')
    || pathname.startsWith('/confirmation/')
    || pathname === '/track';
}

function isCacheableAsset(request) {
  return request.destination === 'style'
    || request.destination === 'script'
    || request.destination === 'image'
    || request.destination === 'font'
    || request.destination === 'manifest'
    || request.url.endsWith('.css')
    || request.url.endsWith('.js')
    || request.url.endsWith('.png')
    || request.url.endsWith('.jpg')
    || request.url.endsWith('.jpeg')
    || request.url.endsWith('.webp')
    || request.url.endsWith('.svg')
    || request.url.endsWith('.ico');
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      Promise.allSettled(
        CORE_ASSETS.map((url) =>
          cache.add(new Request(url, { cache: 'reload' })).catch(() => null)
        )
      )
    )
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') {
    return;
  }

  const request = event.request;
  const url = new URL(request.url);

  if (url.origin === self.location.origin && isSensitivePath(url.pathname)) {
    event.respondWith(fetch(request));
    return;
  }

  if (isApiRequest(request)) {
    event.respondWith((async function () {
      try {
        return await fetch(request);
      } catch (_) {
        if (
          request.headers.get('Accept')
          && request.headers.get('Accept').includes('application/json')
        ) {
          return new Response(
            JSON.stringify({ error: 'Network is unavailable. Please try again.' }),
            {
              status: 503,
              headers: {
                'Content-Type': 'application/json',
              },
            }
          );
        }
        return new Response('Network unavailable.', { status: 503 });
      }
    })());
    return;
  }

  // Keep HTML pages fresh after deployment while still allowing offline fallback.
  if (isNavigationRequest(request)) {
    event.respondWith((async function () {
      try {
        const response = await fetch(request);
        if (response && response.status === 200 && url.origin === self.location.origin) {
          const cache = await caches.open(CACHE_NAME);
          cache.put(request, response.clone());
        }
        return response;
      } catch (_) {
        const cached = await caches.match(request);
        if (cached) {
          return cached;
        }
        const fallback = await caches.match('/');
        return fallback || new Response('Offline', { status: 503 });
      }
    })());
    return;
  }

  // Static assets: cache-first.
  event.respondWith((async function () {
    const cached = await caches.match(request);
    if (cached) {
      return cached;
    }

    try {
      const response = await fetch(request);
      if (
        response
        && response.status === 200
        && isCacheableAsset(request)
        && url.origin === self.location.origin
      ) {
        const cache = await caches.open(CACHE_NAME);
        cache.put(request, response.clone());
      }
      return response;
    } catch (_) {
      const fallback = await caches.match('/');
      return fallback || new Response('Offline', { status: 503 });
    }
  })());
});
