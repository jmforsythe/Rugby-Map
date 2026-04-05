// Service Worker for caching external resources (especially RFU images)
const CACHE_NAME = "rugby-maps-v1";
const IMAGE_CACHE = "rugby-images-v1";

// Install event - cache static resources
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(["shared/boundaries.json"]);
    }),
  );
  self.skipWaiting();
});

// Activate event - clean up old caches
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME && cacheName !== IMAGE_CACHE) {
            return caches.delete(cacheName);
          }
        }),
      );
    }),
  );
  self.clients.claim();
});

// Fetch event - implement caching strategies
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Cache RFU images aggressively (cache-first strategy)
  if (
    url.hostname === "rfu.widen.net" ||
    event.request.destination === "image" ||
    url.hostname === "images.englandrugby.com"
  ) {
    console.log("🖼️ Image request detected:", event.request.url);
    event.respondWith(
      caches.open(IMAGE_CACHE).then((cache) => {
        return cache.match(event.request).then((response) => {
          if (response) {
            console.log("✅ Loaded from cache:", event.request.url);
            return response; // Return cached image
          }

          // Fetch and cache new images
          console.log("📥 Fetching from network:", event.request.url);
          return fetch(event.request)
            .then((networkResponse) => {
              // Only cache successful responses
              if (networkResponse && networkResponse.status === 200) {
                console.log("💾 Caching image:", event.request.url);
                cache.put(event.request, networkResponse.clone());
              }
              return networkResponse;
            })
            .catch((error) => {
              console.log("⚠️ Failed to load image:", event.request.url, error);
              // Return fallback logo if both cache and network fail
              return caches.match(
                "https://rfu.widen.net/content/klppexqa5i/svg/Fallback-logo.svg",
              );
            });
        });
      }),
    );
    return;
  }

  // Cache boundaries.json with stale-while-revalidate
  if (url.pathname.includes("boundaries.json")) {
    event.respondWith(
      caches.open(CACHE_NAME).then((cache) => {
        return cache.match(event.request).then((response) => {
          const fetchPromise = fetch(event.request).then((networkResponse) => {
            cache.put(event.request, networkResponse.clone());
            return networkResponse;
          });
          return response || fetchPromise;
        });
      }),
    );
    return;
  }

  // Default: network-first for everything else
  event.respondWith(fetch(event.request));
});
