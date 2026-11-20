// EINSTEIN REMOTE — Dashboard (web + mobile)
import * as API from "./api.js";
import { LineChart } from "./charts.js";

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

// ----------------------------------------------------------------- utilidades
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function bytes(n, digits = 1) {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? digits : 0)} ${u[i]}`;
}
const rate = (bps) => (bps == null ? "—" : `${bytes(bps)}/s`);
function duration(sec) {
  if (sec == null) return "—";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d ? `${d}d ${h}h ${m}min` : h ? `${h}h ${m}min` : `${m}min`;
}
const dateTime = (ts) => (ts ? new Date(ts * 1000).toLocaleString("pt-BR") : "—");
function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "agora";
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h`;
  return new Date(ts * 1000).toLocaleDateString("pt-BR");
}

function toast(message, level = "info") {
  const el = document.createElement("div");
  el.className = `toast ${level}`;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

function confirmDialog(title, text, okLabel = "Confirmar") {
  const dlg = $("#confirm-dialog");
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text;
  $("#confirm-ok").textContent = okLabel;
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true }));
}

// ----------------------------------------------------------------- estado
const state = {
  devices: new Map(),
  selected: localStorage.getItem("er.device") || null,
  notifications: [],
  live: null,
  view: "dashboard",
  histHours: 1,
};
let charts = {};

// ----------------------------------------------------------------- autenticação
let setupMode = false;

async function showAuth() {
  $("#app-view").classList.add("hidden");
  $("#auth-view").classList.remove("hidden");
  try {
    setupMode = (await API.authStatus()).setup_required;
  } catch {
    $("#auth-error").textContent = "Servidor indisponível.";
  }
  $("#auth-title").textContent = setupMode ? "Configuração inicial" : "Entrar";
  $("#auth-sub").textContent = setupMode ? "Crie a conta do proprietário deste servidor." : "Acesse o painel do seu computador.";
  $("#auth-submit").textContent = setupMode ? "Criar conta e entrar" : "Entrar";
  $("#auth-pass2-wrap").classList.toggle("hidden", !setupMode);
  $("#auth-hint").classList.toggle("hidden", !setupMode);
  $("#auth-pass").autocomplete = setupMode ? "new-password" : "current-password";
}

$("#auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const user = $("#auth-user").value.trim();
  const pass = $("#auth-pass").value;
  $("#auth-error").textContent = "";
  if (setupMode && pass !== $("#auth-pass2").value) {
    $("#auth-error").textContent = "As senhas não coincidem.";
    return;
  }
  $("#auth-submit").disabled = true;
  try {
    await (setupMode ? API.setup(user, pass) : API.login(user, pass));
    $("#auth-pass").value = $("#auth-pass2").value = "";
    startApp();
  } catch (err) {
    $("#auth-error").textContent = err.message;
  } finally {
    $("#auth-submit").disabled = false;
  }
});

API.onSessionLostHandler(() => {
  state.live?.close();
  toast("Sua sessão expirou. Entre novamente.", "warning");
  showAuth();
});

async function doLogout() {
  state.live?.close();
  await API.logout().catch(() => {});
  showAuth();
}
$("#logout-btn").addEventListener("click", doLogout);
$("#logout-btn-mobile").addEventListener("click", doLogout);

// ----------------------------------------------------------------- navegação
const TITLES = {
  dashboard: ["Painel", "Seu computador. Seu controle. Em qualquer lugar."],
  devices: ["Dispositivos", "Vincule, renomeie ou revogue computadores."],
  notifications: ["Alertas", "Eventos do sistema e de segurança."],
  security: ["Segurança", "Sessões ativas e registro de auditoria."],
};

function go(view) {
  state.view = view;
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$("[data-page]").forEach((p) => p.classList.toggle("hidden", p.dataset.page !== view));
  const [t, s] = TITLES[view];
  $("#page-title").textContent = t;
  $("#page-sub").textContent = s;
  if (view === "devices") renderDeviceList();
  if (view === "notifications") { renderNotifications(); markRead(); }
  if (view === "security") loadSecurity();
  if (view === "dashboard") Object.values(charts).forEach((c) => c.draw());
}
$$("#nav button").forEach((b) => b.addEventListener("click", () => go(b.dataset.view)));
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-goto]");
  if (t) go(t.dataset.goto);
});

// ----------------------------------------------------------------- inicialização
async function startApp() {
  $("#auth-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  const u = API.currentUser;
  $("#user-name").textContent = u.username;
  $("#user-role").textContent = { owner: "Proprietário", admin: "Administrador", viewer: "Observador" }[u.role] || u.role;
  $("#audit-card").classList.toggle("hidden", u.role === "viewer");
  $("#pair-form").closest("article").classList.toggle("hidden", u.role === "viewer");

  if (!charts.perf) {
    charts.perf = new LineChart($("#chart-perf"), { series: [{ key: "cpu", color: "purple" }, { key: "ram", color: "green" }] });
    charts.net = new LineChart($("#chart-net"), { series: [{ key: "net_down", color: "green" }, { key: "net_up", color: "purple" }], max: "auto", format: (v) => rate(v).replace("/s", "") });
    charts.hist = new LineChart($("#chart-hist"), { series: [{ key: "cpu", color: "purple" }, { key: "ram", color: "green" }] });
  }

  state.notifications = await API.api("/api/notifications").catch(() => []);
  updateBadge();

  state.live?.close();
  const live = new API.Live();
  state.live = live;
  live.addEventListener("status", (e) => setLive(e.detail.online));
  live.addEventListener("hello", (e) => {
    state.devices.clear();
    for (const d of e.detail.devices) state.devices.set(d.id, d);
    renderDeviceSelect();
    renderDashboard(true);
    if (state.view === "devices") renderDeviceList();
  });
  live.addEventListener("device", (e) => {
    const d = e.detail.data;
    const prev = state.devices.get(d.id);
    if (!d.online && prev) d.history = prev.history; // mantém o gráfico após desconexão
    state.devices.set(d.id, d);
    renderDeviceSelect();
    if (d.id === state.selected) renderDashboard(true);
    if (state.view === "devices") renderDeviceList();
  });
  live.addEventListener("device_removed", (e) => {
    state.devices.delete(e.detail.device_id);
    renderDeviceSelect();
    renderDashboard(true);
    if (state.view === "devices") renderDeviceList();
  });
  live.addEventListener("metrics", (e) => {
    const { device_id, data, summary } = e.detail;
    const d = state.devices.get(device_id);
    if (!d) return;
    d.metrics = data;
    d.history = [...(d.history || []), summary].slice(-300);
    if (device_id === state.selected) renderDashboard(false);
  });
  live.addEventListener("notification", (e) => {
    const n = e.detail.data;
    state.notifications.unshift(n);
    state.notifications = state.notifications.slice(0, 100);
    updateBadge();
    toast(n.message, n.level);
    browserNotify(n);
    if (state.view === "notifications") { renderNotifications(); markRead(); }
    if (state.view === "dashboard") renderActivity();
  });
  live.connect();
  go(state.view);
}

function setLive(online) {
  const el = $("#live-indicator");
  el.classList.toggle("off", !online);
  $("span", el).textContent = online ? "Tempo real" : "Reconectando…";
}

// ----------------------------------------------------------------- painel
function renderDeviceSelect() {
  const sel = $("#device-select");
  const list = [...state.devices.values()];
  if (!state.devices.has(state.selected)) state.selected = list.find((d) => d.online)?.id || list[0]?.id || null;
  sel.innerHTML = list.map((d) => `<option value="${esc(d.id)}">${d.online ? "● " : "○ "}${esc(d.name)}</option>`).join("");
  sel.value = state.selected || "";
  sel.classList.toggle("hidden", list.length === 0);
}
$("#device-select").addEventListener("change", (e) => {
  state.selected = e.target.value;
  localStorage.setItem("er.device", state.selected);
  renderDashboard(true);
});

function setGauge(id, value, sub, { warn = 75, crit = 90, unit = "%", max = 100 } = {}) {
  const g = $(id);
  const has = value != null;
  g.style.setProperty("--p", has ? Math.min(100, (value / max) * 100) : 0);
  g.classList.toggle("warn", has && value >= warn && value < crit);
  g.classList.toggle("crit", has && value >= crit);
  $("b", g).textContent = has ? `${Math.round(value)}${unit}` : "N/D";
  $(`${id}-sub`).textContent = sub;
}

function renderDashboard(full) {
  const d = state.devices.get(state.selected);
  $("#empty-devices").classList.toggle("hidden", state.devices.size > 0);
  $("#dash").classList.toggle("hidden", !d);
  if (!d) return;
  const info = d.info || {};
  const m = d.metrics;

  $("#d-name").textContent = d.name;
  const st = $("#d-status");
  st.textContent = d.online ? "Online" : "Offline";
  st.className = `pill ${d.online ? "on" : "off"}`;
  $("#d-os").textContent = info.os || d.os || "—";
  $("#d-host").textContent = info.hostname || d.hostname || "—";
  $("#d-cpu-model").textContent = info.cpu_model ? `${info.cpu_model} (${info.cpu_cores}C/${info.cpu_threads}T)` : "—";
  $("#d-ip").textContent = info.ip || "—";
  $("#d-uptime").textContent = d.online && m ? duration(m.uptime) : "—";
  $("#d-procs").textContent = d.online && m ? m.process_count : "—";
  $("#d-seen").textContent = d.online ? `Conectado desde ${dateTime(d.connected_at)}` : dateTime(d.last_seen);
  $("#ping-btn").disabled = !d.online;

  if (d.online && m) {
    setGauge("#g-cpu", m.cpu.percent, m.cpu.freq_mhz ? `${(m.cpu.freq_mhz / 1000).toFixed(2)} GHz` : "");
    setGauge("#g-ram", m.memory.percent, `${bytes(m.memory.used)} / ${bytes(m.memory.total)}`);
    const gpu = m.gpu?.[0];
    setGauge("#g-gpu", gpu?.util ?? null, gpu ? gpu.name : "Não suportado neste hardware");
    const t = m.temperatures?.max;
    setGauge("#g-temp", t ?? null, t != null ? `${m.temperatures.sensors.length} sensor(es)` : "Sensor indisponível", { warn: 70, crit: 85, unit: "°", max: 110 });
    $("#net-down").textContent = rate(m.network.down_bps);
    $("#net-up").textContent = rate(m.network.up_bps);
    renderDisks(m.disks);
    renderCores(m.cpu);
  } else {
    for (const id of ["#g-cpu", "#g-ram", "#g-gpu", "#g-temp"]) setGauge(id, null, "Computador offline");
    $("#net-down").textContent = $("#net-up").textContent = "—";
    $("#disks").innerHTML = `<p class="muted small">Dados disponíveis quando o computador estiver online.</p>`;
    $("#cores").innerHTML = "";
    $("#cpu-freq").textContent = "";
  }
  const hist = d.history || [];
  charts.perf.set(hist);
  charts.net.set(hist);
  if (full) { renderActivity(); loadHistory(); }
}

function renderDisks(disks) {
  $("#disks").innerHTML = disks.map((x) => {
    const cls = x.percent >= 90 ? "crit" : x.percent >= 75 ? "warn" : "";
    return `<div class="bar-item"><header><span>${esc(x.mount)} ${x.system ? '<small class="muted">sistema</small>' : ""}</span><span class="muted">${bytes(x.free)} livres de ${bytes(x.total)}</span></header><div class="bar ${cls}"><i data-w="${x.percent}"></i></div></div>`;
  }).join("") || `<p class="muted small">Nenhum disco encontrado.</p>`;
  $$("#disks .bar i").forEach((i) => requestAnimationFrame(() => (i.style.width = `${i.dataset.w}%`)));
}

function renderCores(cpu) {
  const box = $("#cores");
  const cores = cpu.per_core || [];
  if (box.children.length !== cores.length) box.innerHTML = cores.map(() => `<div class="core"><i></i></div>`).join("");
  cores.forEach((v, i) => { const bar = box.children[i].firstChild; bar.style.height = `${Math.max(2, v)}%`; box.children[i].title = `Núcleo ${i + 1}: ${v.toFixed(0)}%`; });
  $("#cpu-freq").textContent = `${cores.length} threads`;
}

function feedItem(level, text, ts, unread = false) {
  return `<li class="${unread ? "unread" : ""}"><span class="dot ${esc(level)}"></span><span>${esc(text)}</span><time>${ago(ts)}</time></li>`;
}

async function renderActivity() {
  const box = $("#activity");
  const sel = state.selected;
  if (API.currentUser?.role !== "viewer") {
    const rows = await API.api("/api/audit?limit=60").catch(() => []);
    const mine = rows.filter((r) => r.device_id === sel || !r.device_id).slice(0, 12);
    box.innerHTML = mine.map((r) => feedItem(r.success ? "ok" : "fail", describeAudit(r), r.ts)).join("") || `<li class="empty-li">Sem atividades registradas.</li>`;
  } else {
    const items = state.notifications.filter((n) => n.device_id === sel).slice(0, 12);
    box.innerHTML = items.map((n) => feedItem(n.level, n.message, n.ts)).join("") || `<li class="empty-li">Sem atividades registradas.</li>`;
  }
}

const AUDIT_LABELS = {
  "auth.setup": "Conta do proprietário criada", "auth.login": "Login realizado", "auth.logout": "Logout",
  "auth.login_failed": "Tentativa de login falhou", "auth.login_blocked": "Login bloqueado (força bruta)",
  "auth.session_revoked": "Sessão revogada", "device.pairing_code": "Código de pareamento gerado",
  "device.paired": "Computador vinculado", "device.pair_failed": "Pareamento recusado", "device.renamed": "Computador renomeado",
  "device.revoked": "Computador revogado", "agent.connected": "Agente conectado", "agent.disconnected": "Agente desconectado",
  "agent.auth_failed": "Agente com credencial inválida", "command.sent": "Comando enviado", "command.result": "Comando concluído",
  "command.rejected": "Comando recusado",
};
function describeAudit(r) {
  let label = AUDIT_LABELS[r.action] || r.action;
  try {
    const det = r.detail ? JSON.parse(r.detail) : null;
    if (det?.action) label += `: ${det.action}`;
  } catch { /* detalhe em texto */ }
  return r.username ? `${label} · ${r.username}` : label;
}

async function loadHistory() {
  if (!state.selected) return;
  const rows = await API.api(`/api/devices/${encodeURIComponent(state.selected)}/history?hours=${state.histHours}`).catch(() => []);
  charts.hist.set(rows);
  $("#hist-empty").classList.toggle("hidden", rows.length > 1);
}
$$("#hist-range button").forEach((b) => b.addEventListener("click", () => {
  $$("#hist-range button").forEach((x) => x.classList.toggle("active", x === b));
  state.histHours = Number(b.dataset.h);
  loadHistory();
}));

$("#ping-btn").addEventListener("click", async () => {
  const out = $("#ping-result");
  out.textContent = "Medindo…";
  const t0 = performance.now();
  try {
    await state.live.command(state.selected, "ping");
    out.textContent = `Ida e volta: ${Math.round(performance.now() - t0)} ms`;
  } catch (e) {
    out.textContent = e.message;
  }
});

// ----------------------------------------------------------------- dispositivos
$("#pair-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await API.api("/api/pairing-codes", { method: "POST", body: { device_name: $("#pair-name").value.trim() } });
    $("#pair-code").textContent = r.code;
    $("#pair-cmd").textContent = `einstein-agent pair --server ${location.origin} --code ${r.code}\neinstein-agent run`;
    $("#pair-result").classList.remove("hidden");
    $("#pair-name").value = "";
  } catch (err) {
    toast(err.message, "error");
  }
});

function renderDeviceList() {
  const box = $("#device-list");
  const canAdmin = API.currentUser?.role !== "viewer";
  const list = [...state.devices.values()];
  box.innerHTML = list.map((d) => `
    <div class="list-item" data-id="${esc(d.id)}">
      <div class="meta">
        <b>${esc(d.name)} <span class="pill ${d.online ? "on" : "off"}">${d.online ? "Online" : "Offline"}</span></b>
        <small>${esc(d.hostname || "—")} · ${esc(d.os || "—")}</small>
        <small>Vinculado em ${dateTime(d.created_at)} · visto ${d.online ? "agora" : dateTime(d.last_seen)}</small>
      </div>
      ${canAdmin ? `<div class="row"><button class="btn ghost sm" data-act="rename">Renomear</button><button class="btn danger sm" data-act="revoke">Revogar</button></div>` : ""}
    </div>`).join("") || `<p class="muted">Nenhum computador vinculado ainda.</p>`;
}

$("#device-list").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.closest("[data-id]").dataset.id;
  const d = state.devices.get(id);
  if (btn.dataset.act === "rename") {
    const meta = btn.closest(".list-item").querySelector(".meta b");
    meta.innerHTML = `<form class="row rename-form"><input class="input" maxlength="64" required value="${esc(d.name)}"><button class="btn primary sm">Salvar</button></form>`;
    const form = meta.querySelector("form");
    form.querySelector("input").focus();
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const name = form.querySelector("input").value.trim();
      try {
        await API.api(`/api/devices/${encodeURIComponent(id)}`, { method: "PATCH", body: { name } });
        d.name = name;
        renderDeviceList(); renderDeviceSelect(); renderDashboard(false);
        toast("Computador renomeado.");
      } catch (err) { toast(err.message, "error"); }
    });
  }
  if (btn.dataset.act === "revoke") {
    const ok = await confirmDialog("Revogar computador", `"${d.name}" será desconectado e suas credenciais deixarão de funcionar. Para usá-lo novamente será preciso vinculá-lo de novo.`, "Revogar");
    if (!ok) return;
    try {
      await API.api(`/api/devices/${encodeURIComponent(id)}`, { method: "DELETE" });
      toast("Computador revogado.", "warning");
    } catch (err) { toast(err.message, "error"); }
  }
});

// ----------------------------------------------------------------- notificações
function updateBadge() {
  const n = state.notifications.filter((x) => !x.read).length;
  const b = $("#notif-badge");
  b.textContent = n > 99 ? "99+" : n;
  b.classList.toggle("hidden", n === 0);
}
function renderNotifications() {
  $("#notif-list").innerHTML = state.notifications.map((n) => feedItem(n.level, n.message, n.ts, !n.read)).join("") || `<li class="empty-li">Nenhuma notificação.</li>`;
}
async function markRead() {
  if (!state.notifications.some((n) => !n.read)) return;
  await API.api("/api/notifications/read-all", { method: "POST" }).catch(() => {});
  setTimeout(() => { state.notifications.forEach((n) => (n.read = 1)); updateBadge(); if (state.view === "notifications") renderNotifications(); }, 1500);
}
$("#read-all-btn").addEventListener("click", markRead);

function browserNotify(n) {
  if (!("Notification" in window) || Notification.permission !== "granted" || !document.hidden) return;
  new Notification("EINSTEIN REMOTE", { body: n.message, icon: "/assets/icon.svg", tag: `er-${n.id}` });
}
function updatePushBtn() {
  const b = $("#push-btn");
  if (!("Notification" in window)) { b.textContent = "Navegador sem suporte a alertas"; b.disabled = true; return; }
  if (Notification.permission === "granted") { b.textContent = "Alertas do navegador ativos"; b.disabled = true; }
  if (Notification.permission === "denied") { b.textContent = "Alertas bloqueados no navegador"; b.disabled = true; }
}
$("#push-btn").addEventListener("click", async () => { await Notification.requestPermission(); updatePushBtn(); });
updatePushBtn();

// ----------------------------------------------------------------- segurança
async function loadSecurity() {
  const sessions = await API.api("/api/sessions").catch(() => []);
  $("#session-list").innerHTML = sessions.map((s) => `
    <div class="list-item" data-sid="${esc(s.id)}">
      <div class="meta">
        <b>${esc(parseUA(s.user_agent))} ${s.current ? '<span class="pill on">Esta sessão</span>' : ""}</b>
        <small>IP ${esc(s.ip)} · iniciada ${dateTime(s.created_at)} · última atividade ${dateTime(s.last_seen)}</small>
      </div>
      ${s.current ? "" : `<button class="btn danger sm" data-act="revoke-session">Revogar</button>`}
    </div>`).join("") || `<p class="muted">Nenhuma sessão.</p>`;
  if (API.currentUser.role !== "viewer") loadAudit();
}

function parseUA(ua = "") {
  const os = /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Mac OS/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "Dispositivo";
  const br = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "Navegador";
  return `${br} em ${os}`;
}

$("#session-list").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act=revoke-session]");
  if (!btn) return;
  if (!(await confirmDialog("Revogar sessão", "O dispositivo que usa esta sessão será desconectado imediatamente.", "Revogar"))) return;
  await API.api(`/api/sessions/${encodeURIComponent(btn.closest("[data-sid]").dataset.sid)}`, { method: "DELETE" }).catch((err) => toast(err.message, "error"));
  loadSecurity();
});

async function loadAudit() {
  const rows = await API.api("/api/audit?limit=200").catch(() => []);
  $("#audit-body").innerHTML = rows.map((r) => `
    <tr><td>${dateTime(r.ts)}</td><td>${esc(r.username || "—")}</td><td>${esc(AUDIT_LABELS[r.action] || r.action)}</td>
    <td class="detail">${esc(r.detail || "")}</td><td>${esc(r.ip || "")}</td><td class="${r.success ? "ok" : "fail"}">${r.success ? "✔" : "✖"}</td></tr>`).join("");
}
$("#audit-refresh").addEventListener("click", loadAudit);

// atualiza "tempo ligado" e relativos sem esperar novos dados
setInterval(() => { if (state.view === "dashboard" && state.selected) { const d = state.devices.get(state.selected); if (d?.online && d.metrics) $("#d-uptime").textContent = duration(d.metrics.uptime + (Date.now() / 1000 - d.metrics.ts)); } }, 30000);

// ----------------------------------------------------------------- boot
(async function boot() {
  try {
    await API.refresh();
    startApp();
  } catch {
    showAuth();
  }
})();
