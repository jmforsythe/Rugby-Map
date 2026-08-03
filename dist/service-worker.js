// Service Worker for caching external resources (especially RFU images)
// and the self-hosted vendor JS/CSS bundles introduced to cut CDN round-trips.
const CACHE_NAME = "rugby-maps-v2";
const IMAGE_CACHE = "rugby-images-v2";
const VENDOR_CACHE = "rugby-vendor-v2";
const CURRENT_CACHES = [CACHE_NAME, IMAGE_CACHE, VENDOR_CACHE];

// Self-hosted copies of pinned CDN libraries (see core/asset_utils.py). These
// are versioned filenames that never change in place, so they're safe to
// precache and serve cache-first indefinitely.
const VENDOR_ASSETS = [
  "shared/vendor/leaflet-1.9.3.js",
  "shared/vendor/leaflet-1.9.3.css",
  "shared/vendor/leaflet-1.9.4.js",
  "shared/vendor/leaflet-1.9.4.css",
  "shared/vendor/bootstrap.bundle.min.js",
  "shared/vendor/bootstrap.min.css",
  "shared/vendor/bootstrap-glyphicons.css",
  "shared/vendor/jquery.min.js",
  "shared/vendor/fontawesome.min.css",
  "shared/vendor/leaflet.awesome-markers.js",
  "shared/vendor/leaflet.awesome-markers.css",
  "shared/vendor/leaflet.awesome.rotate.min.css",
  "shared/vendor/leaflet.markercluster-1.1.0.js",
  "shared/vendor/MarkerCluster-1.1.0.css",
  "shared/vendor/MarkerCluster.Default-1.1.0.css",
  "shared/vendor/leaflet.markercluster-1.5.3.js",
  "shared/vendor/MarkerCluster-1.5.3.css",
  "shared/vendor/MarkerCluster.Default-1.5.3.css",
  "shared/vendor/leaflet.featuregroup.subgroup.js",
  "shared/vendor/turf.min.js",
];

// Install event - cache static resources. Each asset is cached individually
// (rather than via a single cache.addAll) so that a single missing file
// (e.g. a vendor asset not fetched in this build) doesn't abort the whole
// install step.
self.addEventListener("install", (event) => {
  event.waitUntil(
    Promise.all([
      caches.open(CACHE_NAME).then((cache) => {
        return cache.add("shared/boundaries.json").catch(() => {});
      }),
      caches.open(VENDOR_CACHE).then((cache) => {
        return Promise.all(
          VENDOR_ASSETS.map((asset) => cache.add(asset).catch(() => {})),
        );
      }),
    ]),
  );
  self.skipWaiting();
});

// Activate event - clean up old caches from previous service worker versions.
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (!CURRENT_CACHES.includes(cacheName)) {
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
    event.respondWith(
      caches.open(IMAGE_CACHE).then((cache) => {
        return cache.match(event.request).then((response) => {
          if (response) {
            return response; // Return cached image
          }

          // Fetch and cache new images
          return fetch(event.request)
            .then((networkResponse) => {
              // Only cache successful responses
              if (networkResponse && networkResponse.status === 200) {
                cache.put(event.request, networkResponse.clone());
              }
              return networkResponse;
            })
            .catch(() => {
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

  // Self-hosted vendor JS/CSS bundles: filenames are pinned per-version, so
  // once cached they never need revalidation (cache-first, network fallback).
  if (url.pathname.includes("/shared/vendor/")) {
    event.respondWith(
      caches.open(VENDOR_CACHE).then((cache) => {
        return cache.match(event.request).then((response) => {
          if (response) {
            return response;
          }
          return fetch(event.request).then((networkResponse) => {
            if (networkResponse && networkResponse.status === 200) {
              cache.put(event.request, networkResponse.clone());
            }
            return networkResponse;
          });
        });
      }),
    );
    return;
  }

  // Data sidecars (territories.json, boundaries*.json, per-date match-day
  // payloads, teams.json, etc.) with stale-while-revalidate: serve the cached
  // copy instantly if present while refreshing it in the background.
  if (url.pathname.endsWith(".json")) {
    event.respondWith(
      caches.open(CACHE_NAME).then((cache) => {
        return cache.match(event.request).then((response) => {
          const fetchPromise = fetch(event.request)
            .then((networkResponse) => {
              cache.put(event.request, networkResponse.clone());
              return networkResponse;
            })
            .catch(() => response);
          return response || fetchPromise;
        });
      }),
    );
    return;
  }

  // Default: network-first for everything else
  event.respondWith(fetch(event.request));
});
