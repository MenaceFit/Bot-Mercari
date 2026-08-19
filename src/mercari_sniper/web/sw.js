/* Service worker — met en cache la coquille de l'application.

   Ce qui est mis en cache : le HTML, le CSS, le JS, les icônes. Rien d'autre.
   Les appels /api/ et le WebSocket ne sont JAMAIS servis depuis le cache :
   afficher des annonces périmées comme si elles étaient fraîches serait pire
   que d'afficher une erreur de connexion. */
'use strict';

const CACHE = 'sniper-shell-v3';
const SHELL = [
  '/',
  '/static/app.css',
  '/static/app.js',
  '/static/icon-192.png',
  '/manifest.webmanifest',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      // addAll échoue en bloc si une seule ressource manque : on tolère.
      .then((cache) => Promise.allSettled(SHELL.map((url) => cache.add(url))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Données vivantes : toujours le réseau, jamais le cache.
  if (url.pathname.startsWith('/api/') || url.pathname === '/ws') return;

  // Coquille : réseau d'abord (pour récupérer les mises à jour), cache en
  // repli quand le PC est éteint ou hors de portée.
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request).then((hit) => hit || caches.match('/')))
  );
});
