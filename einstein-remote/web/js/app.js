// EINSTEIN REMOTE — Dashboard (web + mobile)
import * as API from "./api.js";
import { LineChart } from "./charts.js";
import { $, $$, ago, bytes, confirmDialog, dateTime, duration, esc, rate, toast } from "./util.js";
import { Monitor } from "./monitor.js";
import { RemoteViewer } from "./remote.js";
import { PowerCenter } from "./power.js";
import { FileBrowser } from "./files.js";
import { Optimizer } from "./optimize.js";
import { UpdateCard } from "./updates.js";
import { VolumeCard } from "./volume.js";
import { Assistant } from "./assistant.js";
import { PushCard, TwoFactorCard } from "./security.js";

// ----------------------------------------------------------------- utilidades
// Estado do computador: online, offline (queda), pausado ou encerrado pelo dono no próprio PC
const STATE_LABELS = {
  online: ["Online", "on"], paused: ["Pausado no PC", "pause"], stopped: ["Agente encerrado", "pause"], offline: ["Offline", "off"],
  power_off: ["Desligado pelo painel", "idle"], restarting: ["Reiniciando…", "pause"], sleeping: ["Suspenso", "idle"], hibernating: ["Hibernando", "idle"],
};
const stateOf = (d) => (d.online ? "online" : d.state && STATE_LABELS[d.state] ? d.state : "offline");
const statePill = (d) => { const [label, cls] = STATE_LABELS[stateOf(d)]; return `<span class="pill ${cls}">${label}</span>`; };

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

let pendingChallenge = null;
function showCodeStep(on) {
  if (!on) pendingChallenge = null;
  $("#auth-2fa").classList.toggle("hidden", !on);
  $("#auth-user").closest("label").classList.toggle("hidden", on);
  $("#auth-pass").closest("label").classList.toggle("hidden", on);
  $("#auth-pass").required = !on;
  $("#auth-title").textContent = on ? "Verificação em duas etapas" : "Entrar";
  $("#auth-sub").textContent = on ? "Digite o código do seu aplicativo autenticador." : "Acesse o painel do seu computador.";
  $("#auth-submit").textContent = on ? "Verificar" : "Entrar";
  if (on) $("#auth-code").focus();
}

$("#auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const user = $("#auth-user").value.trim();
  const pass = $("#auth-pass").value;
  $("#auth-error").textContent = "";
  if (pendingChallenge) {
    $("#auth-submit").disabled = true;
    try {
      await API.verify2fa(pendingChallenge, $("#auth-code").value.trim());
      $("#auth-code").value = "";
      $("#auth-pass").value = "";
      showCodeStep(false);
      startApp();
    } catch (err) {
      $("#auth-error").textContent = err.message;
      if (/expirou/.test(err.message)) showCodeStep(false);
    } finally {
      $("#auth-submit").disabled = false;
    }
    return;
  }
  if (setupMode && pass !== $("#auth-pass2").value) {
    $("#auth-error").textContent = "As senhas não coincidem.";
    return;
  }
  $("#auth-submit").disabled = true;
  try {
    if (setupMode) await API.setup(user, pass);
    else {
      const step = await API.login(user, pass);
      if (step?.challenge) { pendingChallenge = step.challenge; showCodeStep(true); return; }
    }
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
  remote?.teardown();
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
  settings: ["Ajustes", "Limites de alerta, sua conta, usuários e permissões."],
  monitor: ["System Monitor", "Processos, aplicativos, serviços, hardware e rede em tempo real."],
  files: ["Arquivos", "Somente as pastas que o dono liberou no computador."],
  optimize: ["Otimizar", "Modo jogo, modo de energia, limpeza de disco e inicialização do Windows."],
};
let monitor = null;
let remote = null;
let power = null;
let files = null;
let optimizer = null;
let updateCard = null;
let volume = null;
let assistant = null;
const ROLE_NAMES = { owner: "Proprietário", admin: "Administrador", viewer: "Observador" };

function go(view) {
  state.view = view;
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$("[data-page]").forEach((p) => p.classList.toggle("hidden", p.dataset.page !== view));
  const [t, s] = TITLES[view];
  $("#page-title").textContent = t;
  $("#page-sub").textContent = s;
  if (view === "devices") renderDeviceList();
  if (view === "notifications") { renderNotifications(); markRead(); pushCard?.render(); }
  if (view === "security") loadSecurity();
  if (view === "settings") loadSettingsPage();
  if (monitor) view === "monitor" ? monitor.show() : monitor.hide();
  if (view === "files") files?.show();
  if (view === "optimize") optimizer?.show();
  if (view === "dashboard") Object.values(charts).forEach((c) => c.draw());
}
$$("#nav button").forEach((b) => b.addEventListener("click", () => go(b.dataset.view)));
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-goto]");
  if (t) go(t.dataset.goto);
});

// ----------------------------------------------------------------- inicialização
let tfCard = null;
let pushCard = null;
let enrollOnly = false;

async function startApp() {
  $("#auth-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  applyRole(API.currentUser);
  tfCard ??= new TwoFactorCard(() => { if (enrollOnly && API.currentUser?.totp_enabled) startApp(); else loadSettingsPage(); });
  pushCard ??= new PushCard();
  const me = await API.api("/api/auth/me").catch(() => ({}));
  enrollOnly = !!me.must_enroll_2fa;
  document.body.classList.toggle("enroll-only", enrollOnly);
  $("#enroll-banner").classList.toggle("hidden", !enrollOnly);
  if (enrollOnly) { state.live?.close(); go("settings"); return; }
  files ??= new FileBrowser({ device: () => state.devices.get(state.selected), devices: () => [...state.devices.values()], live: () => state.live });
  optimizer ??= new Optimizer({ device: () => state.devices.get(state.selected), live: () => state.live });
  assistant ??= new Assistant({
    device: () => state.devices.get(state.selected), live: () => state.live, go, user: () => API.currentUser,
    openScreen: () => remote?.start(state.selected),
  });
  volume ??= new VolumeCard({ device: () => state.devices.get(state.selected), live: () => state.live });
  power ??= new PowerCenter({ device: () => state.devices.get(state.selected), live: () => state.live });
  remote ??= new RemoteViewer({ live: () => state.live, device: (id) => state.devices.get(id) });
  monitor ??= new Monitor({ device: () => state.devices.get(state.selected), live: () => state.live, role: () => API.currentUser?.role });

  if (!charts.perf) {
    charts.spark = new LineChart($("#hero-spark"), { series: [{ key: "cpu", color: "purple" }, { key: "ram", color: "green" }], bare: true });
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
    applyRole(e.detail.user);
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
    if (d.id === state.selected) {
      renderDashboard(true);
      if (state.view === "monitor" && d.online !== prev?.online) monitor.deviceChanged();
    }
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
    if (state.view === "notifications") { renderNotifications(); markRead(); }
    if (state.view === "dashboard") renderActivity();
  });
  live.addEventListener("enroll_required", () => startApp());
  live.connect();
  go(state.view);
}

// Ajusta a interface ao papel do usuário (o servidor sempre revalida tudo).
function applyRole(u) {
  if (!u) return;
  document.body.classList.toggle("viewer", u.role === "viewer");
  document.body.classList.toggle("not-owner", u.role !== "owner");
  $("#user-name").textContent = u.username;
  $("#user-role").textContent = ROLE_NAMES[u.role] || u.role;
  $("#me-role").textContent = ROLE_NAMES[u.role] || u.role;
  $("#pw-username").value = u.username;
  $("#audit-card").classList.toggle("hidden", u.role === "viewer");
  $("#pair-form").closest("article").classList.toggle("hidden", u.role === "viewer");
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
  monitor?.deviceChanged();
  if (state.view === "files") files?.show();
  if (state.view === "optimize") optimizer?.show();
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
  renderHero(d);
  $("#empty-devices").classList.toggle("hidden", state.devices.size > 0);
  const isAdmin = API.currentUser?.role !== "viewer";
  $("#empty-title").textContent = isAdmin ? "Nenhum computador vinculado" : "Nenhum computador liberado";
  $("#empty-text").textContent = isAdmin
    ? "Gere um código de pareamento e execute o agente no seu PC Windows."
    : "Sua conta ainda não tem acesso a nenhum computador. Peça ao proprietário para liberar em Ajustes › Usuários.";
  $("#dash").classList.toggle("hidden", !d);
  if (!d) return;
  const info = d.info || {};
  const m = d.metrics;

  $("#d-name").textContent = d.name;
  const st = $("#d-status");
  const [stLabel, stCls] = STATE_LABELS[stateOf(d)];
  st.textContent = stLabel;
  st.className = `pill ${stCls}`;
  $("#d-agent").textContent = d.agent_version ? `v${d.agent_version}` : "—";
  power?.render(d, full);
  volume?.render(d, full);
  $("#d-os").textContent = info.os || d.os || "—";
  $("#d-host").textContent = info.hostname || d.hostname || "—";
  $("#d-cpu-model").textContent = info.cpu_model ? `${info.cpu_model} (${info.cpu_cores}C/${info.cpu_threads}T)` : "—";
  $("#d-ip").textContent = info.ip || "—";
  $("#d-uptime").textContent = d.online && m ? duration(m.uptime) : "—";
  $("#d-procs").textContent = d.online && m ? m.process_count : "—";
  $("#d-seen").textContent = d.online ? `Conectado desde ${dateTime(d.connected_at)}` : dateTime(d.last_seen);

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
    const why = { paused: "Pausado no próprio PC", stopped: "Agente encerrado no PC", power_off: "Desligado pelo painel",
    restarting: "Reiniciando…", sleeping: "Suspenso", hibernating: "Hibernando" }[stateOf(d)] || "Computador offline";
    for (const id of ["#g-cpu", "#g-ram", "#g-gpu", "#g-temp"]) setGauge(id, null, why);
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
  "command.rejected": "Comando recusado", "auth.password_changed": "Senha alterada",
  "auth.password_change_failed": "Troca de senha recusada", "user.created": "Usuário criado",
  "user.updated": "Usuário alterado", "user.deleted": "Usuário removido", "settings.updated": "Limites de alerta alterados",
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

// ----------------------------------------------------------------- tela inicial (hero + atalhos)
function setMini(id, pct) { $(id).style.width = `${Math.max(0, Math.min(100, pct ?? 0))}%`; }

function renderHero(d) {
  const mon = $("#hero-monitor");
  const status = $("#hero-status");
  const online = [...state.devices.values()].filter((x) => x.online).length;
  $("#sc-ping").disabled = !d?.online;
  $("#sc-screen").disabled = !d?.online;
  $("#d-screen").disabled = !d?.online;
  if (!d) {
    mon.classList.add("off");
    $("#hv-name").textContent = "Nenhum computador";
    $("#hv-status").textContent = "aguardando vínculo";
    status.textContent = API.currentUser?.role === "viewer"
      ? "Nenhum computador liberado para sua conta. Peça acesso ao proprietário."
      : "Nenhum computador vinculado ainda — use o atalho “Vincular PC”.";
    for (const k of ["cpu", "ram", "disk", "net"]) { $(`#hv-${k}`).textContent = "—"; setMini(`#hb-${k}`, 0); }
    charts.spark?.set([]);
    return;
  }
  const m = d.online ? d.metrics : null;
  mon.classList.toggle("off", !d.online);
  $("#hv-name").textContent = d.name;
  $("#hv-status").textContent = STATE_LABELS[stateOf(d)][0].toLowerCase();
  status.innerHTML = statePill(d)
    + `<span>${esc(d.name)}${m ? ` · ligado há ${esc(duration(m.uptime))}` : d.last_seen ? ` · visto em ${esc(dateTime(d.last_seen))}` : ""}</span>`
    + `<span class="muted">· ${online} de ${state.devices.size} computador(es) online</span>`;
  if (m) {
    const sys = m.disks?.find((x) => x.system) || m.disks?.[0];
    $("#hv-cpu").textContent = `${Math.round(m.cpu.percent)}%`; setMini("#hb-cpu", m.cpu.percent);
    $("#hv-ram").textContent = `${Math.round(m.memory.percent)}%`; setMini("#hb-ram", m.memory.percent);
    $("#hv-disk").textContent = sys ? `${Math.round(sys.percent)}%` : "—"; setMini("#hb-disk", sys?.percent);
    $("#hv-net").textContent = rate(m.network.down_bps);
    // escala logarítmica: 1 KB/s ≈ 0 %, 100 MB/s ≈ 100 %
    setMini("#hb-net", m.network.down_bps > 1024 ? (Math.log10(m.network.down_bps / 1024) / 5) * 100 : 0);
  } else {
    for (const k of ["cpu", "ram", "disk", "net"]) { $(`#hv-${k}`).textContent = "—"; setMini(`#hb-${k}`, 0); }
  }
  charts.spark?.set((d.history || []).slice(-90));
}

$("#sc-ping").addEventListener("click", async () => {
  const out = $("#ping-result");
  out.textContent = "medindo…";
  const t0 = performance.now();
  try {
    await state.live.command(state.selected, "ping");
    out.textContent = `${Math.round(performance.now() - t0)} ms`;
  } catch (e) {
    out.textContent = e.message;
  }
});
$("#sc-screen").addEventListener("click", () => remote?.start(state.selected));
$("#d-screen").addEventListener("click", () => remote?.start(state.selected));
$("#sc-history").addEventListener("click", () => {
  if ($("#dash").classList.contains("hidden")) return toast("Vincule um computador para ver o histórico.", "warning");
  $("#hist-card").scrollIntoView({ behavior: "smooth", block: "start" });
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
        <b>${esc(d.name)} ${statePill(d)}</b>
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
  $("#sc-alerts").textContent = n ? `${n} não lida(s)` : "tudo em dia";
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

// ----------------------------------------------------------------- ajustes
const ALERT_FIELDS = ["cpu_alert_percent", "ram_alert_percent", "temp_alert_celsius", "alert_sustain_seconds", "alert_cooldown_minutes"];
const UNITS = { cpu_alert_percent: "%", ram_alert_percent: "%", temp_alert_celsius: " °C" };

function syncRange(input) {
  const pct = ((input.value - input.min) / (input.max - input.min)) * 100;
  input.style.setProperty("--fill", `${pct}%`);
  const out = $(`output[data-for="${input.name}"]`);
  if (out) out.textContent = `${input.value}${UNITS[input.name] || ""}`;
}
$$("#alerts-form input[type=range]").forEach((r) => r.addEventListener("input", () => syncRange(r)));

async function loadSettingsPage() {
  const form = $("#alerts-form");
  const canEdit = API.currentUser.role !== "viewer";
  try {
    const s = await API.api("/api/settings");
    for (const [k, v] of Object.entries(s)) {
      const el = form.elements[k];
      if (!el) continue;
      if (el.type === "checkbox") el.checked = !!v; else el.value = v;
      if (el.type === "range") syncRange(el);
    }
  } catch (err) { toast(err.message, "error"); }
  $$("input, button", form).forEach((el) => (el.disabled = !canEdit));
  $("#alerts-ro").classList.toggle("hidden", canEdit);
  tfCard?.render();
  if (enrollOnly) return;
  updateCard ??= new UpdateCard();
  updateCard.render();
  if (API.currentUser.role === "owner") {
    loadUsers();
    API.api("/api/settings").then((s) => ($("#req-2fa").checked = !!s.require_2fa)).catch(() => {});
  }
}

$("#alerts-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const body = {};
  for (const k of ALERT_FIELDS) body[k] = Number(form.elements[k].value);
  $$("input[type=checkbox]", form).forEach((c) => (body[c.name] = c.checked));
  try {
    await API.api("/api/settings", { method: "PUT", body });
    toast("Limites de alerta salvos.");
  } catch (err) { toast(err.message, "error"); }
});

$("#password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const err = $("#pw-error");
  err.textContent = "";
  if (f.elements.new_password.value !== f.elements.confirm.value) { err.textContent = "As senhas não coincidem."; return; }
  try {
    const r = await API.api("/api/auth/password", { method: "POST", body: { current_password: f.elements.current_password.value, new_password: f.elements.new_password.value } });
    f.reset();
    toast(`Senha alterada. ${r.sessions_revoked} outra(s) sessão(ões) encerrada(s).`);
  } catch (ex) { err.textContent = ex.message; }
});

// ----------------------------------------------------------------- usuários e permissões (proprietário)
let users = [];
let editingUser = null;

function accessLabel(u) {
  if (u.role === "owner" || u.all_devices) return "Todos os computadores";
  const names = u.device_ids.map((id) => state.devices.get(id)?.name).filter(Boolean);
  return names.length ? names.join(", ") : "Nenhum computador";
}

async function loadUsers() {
  try { users = await API.api("/api/users"); } catch (err) { toast(err.message, "error"); return; }
  $("#user-list").innerHTML = users.map((u) => `
    <div class="list-item" data-uid="${u.id}">
      <div class="meta">
        <b>${esc(u.username)}<span class="role-tag ${esc(u.role)}">${esc(ROLE_NAMES[u.role])}</span>${u.totp_enabled ? `<span class="role-tag tf">2FA</span>` : ""}</b>
        <small>Acesso: ${esc(accessLabel(u))}</small>
        <small>Criado em ${dateTime(u.created_at)} · último login ${u.last_login ? dateTime(u.last_login) : "nunca"}</small>
      </div>
      ${u.role === "owner" ? "" : `<div class="row">${u.totp_enabled ? `<button class="btn ghost sm" data-act="reset2fa">Redefinir 2FA</button>` : ""}<button class="btn ghost sm" data-act="edit">Editar</button><button class="btn danger sm" data-act="delete">Remover</button></div>`}
    </div>`).join("");
}

function syncAccessUI() {
  const some = $("#user-form").elements.access.value === "some";
  $("#user-devices").classList.toggle("disabled", !some);
}

function openUserDialog(u = null) {
  editingUser = u;
  const f = $("#user-form");
  f.reset();
  $("#user-error").textContent = "";
  $("#user-dialog-title").textContent = u ? `Editar ${u.username}` : "Novo usuário";
  $("#user-pass-label").textContent = u ? "Redefinir senha (opcional — encerra as sessões do usuário)" : "Senha inicial";
  f.elements.username.value = u?.username || "";
  f.elements.username.disabled = !!u;
  f.elements.password.required = !u;
  f.elements.role.value = u?.role || "viewer";
  f.elements.access.value = !u || u.all_devices ? "all" : "some";
  const allowed = new Set(u?.device_ids || []);
  const list = [...state.devices.values()];
  $("#user-devices").innerHTML = list.map((d) => `<label><input type="checkbox" value="${esc(d.id)}" ${allowed.has(d.id) ? "checked" : ""}> ${esc(d.name)}</label>`).join("")
    || `<p class="hint">Nenhum computador vinculado ainda.</p>`;
  syncAccessUI();
  $("#user-dialog").showModal();
}

$("#user-form").addEventListener("change", (e) => { if (e.target.name === "access") syncAccessUI(); });
$("#user-new").addEventListener("click", () => openUserDialog());
$("#user-cancel").addEventListener("click", () => $("#user-dialog").close());

$("#user-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const all = f.elements.access.value === "all";
  const device_ids = $$("#user-devices input:checked").map((c) => c.value);
  const body = { role: f.elements.role.value, all_devices: all, device_ids };
  try {
    if (editingUser) {
      if (f.elements.password.value) body.new_password = f.elements.password.value;
      await API.api(`/api/users/${editingUser.id}`, { method: "PATCH", body });
      toast("Usuário atualizado. As conexões dele foram renovadas com as novas permissões.");
    } else {
      await API.api("/api/users", { method: "POST", body: { ...body, username: f.elements.username.value.trim(), password: f.elements.password.value } });
      toast("Usuário criado.");
    }
    $("#user-dialog").close();
    loadUsers();
  } catch (err) { $("#user-error").textContent = err.message; }
});

$("#user-list").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const u = users.find((x) => x.id === Number(btn.closest("[data-uid]").dataset.uid));
  if (btn.dataset.act === "edit") return openUserDialog(u);
  if (btn.dataset.act === "reset2fa") {
    if (!(await confirmDialog("Redefinir verificação em duas etapas", `Use quando "${u.username}" perdeu o celular e os códigos de recuperação. As sessões dele serão encerradas e ele precisará ativar o 2FA de novo.`, "Redefinir"))) return;
    try { await API.api(`/api/users/${u.id}/reset-2fa`, { method: "POST" }); toast("2FA redefinido.", "warning"); loadUsers(); }
    catch (err) { toast(err.message, "error"); }
    return;
  }
  if (!(await confirmDialog("Remover usuário", `"${u.username}" perderá o acesso imediatamente e todas as sessões dele serão encerradas.`, "Remover"))) return;
  try {
    await API.api(`/api/users/${u.id}`, { method: "DELETE" });
    toast("Usuário removido.", "warning");
    loadUsers();
  } catch (err) { toast(err.message, "error"); }
});

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

// ----------------------------------------------------------------- Fase 8: política 2FA e sessões
$("#req-2fa").addEventListener("change", async (e) => {
  const on = e.target.checked;
  if (on && !API.currentUser.totp_enabled) {
    e.target.checked = false;
    return toast("Ative primeiro a verificação em duas etapas na sua própria conta.", "warning");
  }
  try {
    await API.api("/api/settings", { method: "PUT", body: { require_2fa: on } });
    toast(on ? "2FA agora é obrigatório para todos os usuários." : "2FA deixou de ser obrigatório.", on ? "info" : "warning");
  } catch (err) { e.target.checked = !on; toast(err.message, "error"); }
});

$("#sess-revoke-all").addEventListener("click", async () => {
  if (!(await confirmDialog("Encerrar outras sessões", "Todos os outros navegadores e celulares conectados a esta conta serão desconectados.", "Encerrar"))) return;
  try { const r = await API.api("/api/sessions", { method: "DELETE" }); toast(`${r.revoked} sessão(ões) encerrada(s).`); loadSecurity(); }
  catch (err) { toast(err.message, "error"); }
});
