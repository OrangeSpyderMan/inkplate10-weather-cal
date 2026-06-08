const CACHE_NAME = "weather-calendar-pwa-v3";
const APP_SHELL = [
  "/app",
  "/app.css",
  "/app.js",
  "/manifest.webmanifest",
  "/favicon.ico",
  "/icons/weathercal-icon-48.png",
  "/icons/weathercal-icon-192.png",
  "/icons/weathercal-icon-512.png",
  "/icons/weathercal-icon.svg"
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL))
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(names => Promise.all(
      names
        .filter(name => name !== CACHE_NAME)
        .map(name => caches.delete(name))
    ))
  );
});

self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);

  if (url.pathname === "/outputs/inkplate10-portrait/calendar.png") {
    event.respondWith(fetch(event.request));
    return;
  }

  event.respondWith(
    caches.match(event.request).then(cached => cached || fetch(event.request))
  );
});
