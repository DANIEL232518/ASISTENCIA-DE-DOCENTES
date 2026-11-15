/* Service Worker · PWA Control de Asistencia Docente
   Estrategia:
   - Navegación  : network-first con respaldo en caché (funciona sin señal).
   - Estáticos   : stale-while-revalidate (responde al instante y se actualiza
                   en segundo plano, así un despliegue nuevo nunca se queda
                   pegado en la caché del navegador).
   - API y datos : network-only (los datos deben ser siempre reales).
*/
const VERSION = 'asistencia-v1.3.0';
const SHELL = [
  '/',
  '/login',
  '/static/manifest.json',
  '/static/img/icon-192.png',
  '/static/img/icon-512.png',
  '/static/img/favicon.png',
];

self.addEventListener('install', (evento) => {
  evento.waitUntil(
    caches
      .open(VERSION)
      .then((cache) => Promise.allSettled(SHELL.map((url) => cache.add(url))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (evento) => {
  evento.waitUntil(
    caches
      .keys()
      .then((claves) =>
        Promise.all(claves.filter((c) => c !== VERSION).map((c) => caches.delete(c)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (evento) => {
  const req = evento.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // API, archivos subidos, credenciales y el propio sw: siempre red
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/static/uploads/') ||
    url.pathname.startsWith('/static/credenciales/') ||
    url.pathname === '/sw.js'
  ) {
    return;
  }

  // Estáticos: stale-while-revalidate
  if (url.pathname.startsWith('/static/')) {
    evento.respondWith(
      caches.open(VERSION).then((cache) =>
        cache.match(req).then((cacheado) => {
          const red = fetch(req)
            .then((resp) => {
              if (resp && resp.status === 200 && resp.type === 'basic') {
                cache.put(req, resp.clone());
              }
              return resp;
            })
            .catch(() => cacheado);
          return cacheado || red;
        })
      )
    );
    return;
  }

  // Navegación: network-first con respaldo
  if (req.mode === 'navigate') {
    evento.respondWith(
      fetch(req)
        .then((resp) => {
          const copia = resp.clone();
          caches.open(VERSION).then((c) => c.put(req, copia));
          return resp;
        })
        .catch(() =>
          caches.match(req).then((cacheado) => cacheado || caches.match('/'))
        )
    );
  }
});
