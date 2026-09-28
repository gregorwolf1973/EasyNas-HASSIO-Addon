// Simple NAS - Service Worker der Freigabe-Seite.
// Macht die Seite als App installierbar. Er speichert nichts zwischen:
// Dateien, Listen und Anmeldungen kommen immer frisch vom Server.
// Nur Seitenaufrufe laufen hier durch, damit ohne Verbindung eine
// verstaendliche Meldung statt der Browser-Fehlerseite erscheint.
// Uploads, Downloads, ZIP und Vorschau fasst er nicht an.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

const PASS_THROUGH = /\/s\/[^/]+\/(d|zip|v|e|upload|upload-form|delete)(\/|$)/;

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || req.mode !== "navigate") return;
  if (PASS_THROUGH.test(new URL(req.url).pathname)) return;
  e.respondWith(fetch(req).catch(() => new Response(
    "<!DOCTYPE html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>" +
    "<title>Simple NAS</title><body style='font:15px sans-serif;text-align:center;padding:48px 16px'>" +
    "<h2>Keine Verbindung / No connection</h2><p>Simple NAS ist gerade nicht erreichbar.<br>" +
    "Simple NAS cannot be reached right now.</p><button onclick='location.reload()'>Erneut versuchen / Retry</button>",
    { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } })));
});
