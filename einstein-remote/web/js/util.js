// Utilidades compartilhadas pelo painel (formatação, escape, toasts, confirmação).

export const $ = (s, root = document) => root.querySelector(s);
export const $$ = (s, root = document) => [...root.querySelectorAll(s)];

export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export function bytes(n, digits = 1) {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? digits : 0)} ${u[i]}`;
}
export const rate = (bps) => (bps == null ? "—" : `${bytes(bps)}/s`);
export function duration(sec) {
  if (sec == null) return "—";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d ? `${d}d ${h}h ${m}min` : h ? `${h}h ${m}min` : `${m}min`;
}
export const dateTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString("pt-BR") : "—");
export function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "agora";
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h`;
  return new Date(ts * 1000).toLocaleDateString("pt-BR");
}

export function toast(message, level = "info") {
  const el = document.createElement("div");
  el.className = `toast ${level}`;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

export function confirmDialog(title, text, okLabel = "Confirmar") {
  const dlg = $("#confirm-dialog");
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text;
  $("#confirm-ok").textContent = okLabel;
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true }));
}
