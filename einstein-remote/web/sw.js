// EINSTEIN REMOTE — service worker: mostra notificações push mesmo com o painel fechado.
// Não guarda páginas em cache (o painel sempre carrega a versão atual do servidor).

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data && event.data.text() }; }
  const options = {
    body: data.body || "Nova notificação",
    icon: "/assets/icon.svg",
    badge: "/assets/icon.svg",
    tag: data.tag || undefined,
    renotify: !!data.tag,
    requireInteraction: data.level === "critical",
    data: { url: data.url || "/" },
  };
  event.waitUntil(self.registration.showNotification(data.title || "EINSTEIN REMOTE", options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) if ("focus" in c) return c.focus();
    return self.clients.openWindow(url);
  }));
});
