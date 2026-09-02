// Minimal service worker -- required by browsers for "Add to Home Screen"
// to offer full app-like install. No offline caching yet; this just needs
// to exist and register successfully.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", () => self.clients.claim());
self.addEventListener("fetch", () => {});