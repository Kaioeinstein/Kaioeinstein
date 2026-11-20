// SYSTEM MONITOR — processos, aplicativos, serviços, hardware e rede do computador selecionado.
import { $, $$, bytes, confirmDialog, dateTime, esc, rate, toast } from "./util.js";

const TABS = {
  processes: { title: "Processos", action: "system.processes", every: 3000, search: true },
  apps: { title: "Aplicativos abertos", action: "system.apps", every: 5000, search: true },
  services: { title: "Serviços", action: "system.services", every: 15000, search: true },
  hardware: { title: "Hardware", action: "system.hardware", every: 0, search: false },
  network: { title: "Rede", action: "system.network", every: 5000, search: false },
};
const MAX_ROWS = 300;
const SERVICE_STATUS = { running: ["Em execução", "on"], stopped: ["Parado", "off"], active: ["Ativo", "on"], inactive: ["Inativo", "off"], failed: ["Falhou", "off"] };

export class Monitor {
  /**
   * @param {{device: () => any, live: () => any, role: () => string}} ctx
   */
  constructor(ctx) {
    this.ctx = ctx;
    this.tab = "processes";
    this.visible = false;
    this.data = {};
    this.updated = {};
    this.inflight = false;
    this.timer = null;
    this.sort = { key: "cpu", dir: -1 };
    this.serviceFilter = "all";
    this.bind();
  }

  bind() {
    $$("#mon-tabs button").forEach((b) => b.addEventListener("click", () => this.setTab(b.dataset.tab)));
    $("#mon-search").addEventListener("input", () => this.render());
    $("#mon-refresh").addEventListener("click", () => this.load(true));
    $("#mon-auto").addEventListener("change", () => this.schedule());
    document.addEventListener("visibilitychange", () => this.schedule());
    $("#mon-body").addEventListener("click", (e) => this.onClick(e));
    $("#mon-body").addEventListener("change", (e) => {
      if (e.target.id === "svc-filter") { this.serviceFilter = e.target.value; this.render(); }
    });
  }

  // ------------------------------------------------------------ ciclo de vida
  show() { this.visible = true; this.data = {}; this.load(true); }
  hide() { this.visible = false; clearTimeout(this.timer); }
  deviceChanged() { this.data = {}; this.updated = {}; if (this.visible) this.load(true); }

  setTab(tab) {
    this.tab = tab;
    $$("#mon-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    $("#mon-title").textContent = TABS[tab].title;
    $("#mon-search").classList.toggle("hidden", !TABS[tab].search);
    $("#mon-search").value = "";
    this.render();
    this.load(!this.data[tab]);
  }

  schedule() {
    clearTimeout(this.timer);
    const every = TABS[this.tab].every;
    if (!this.visible || !every || !$("#mon-auto").checked || document.hidden) return;
    this.timer = setTimeout(() => this.load(false), every);
  }

  async load(showSpinner) {
    const d = this.ctx.device();
    if (!this.visible) return;
    if (!d || !d.online) { this.render(); return; }
    if (this.inflight) return;
    const tab = this.tab;
    this.inflight = true;
    if (showSpinner && !this.data[tab]) $("#mon-body").innerHTML = `<p class="muted">Consultando o computador…</p>`;
    try {
      this.data[tab] = await this.ctx.live().command(d.id, TABS[tab].action);
      this.updated[tab] = Date.now() / 1000;
      this.error = null;
    } catch (err) {
      this.error = err.message;
    } finally {
      this.inflight = false;
    }
    if (tab === this.tab) this.render();
    this.schedule();
  }

  // ------------------------------------------------------------ renderização
  render() {
    const d = this.ctx.device();
    const body = $("#mon-body");
    const meta = $("#mon-meta");
    if (!d) { body.innerHTML = `<p class="muted">Nenhum computador selecionado.</p>`; meta.textContent = ""; return; }
    if (!d.online) {
      body.innerHTML = `<div class="empty-mon"><b>${esc(d.name)} não está online.</b><p class="muted">O SYSTEM MONITOR consulta o computador em tempo real; ele volta assim que o agente reconectar.</p></div>`;
      meta.textContent = "";
      return;
    }
    const data = this.data[this.tab];
    if (!data) { if (this.error) body.innerHTML = `<p class="error">${esc(this.error)}</p>`; return; }
    const when = this.updated[this.tab] ? `atualizado ${new Date(this.updated[this.tab] * 1000).toLocaleTimeString("pt-BR")}` : "";
    const errorNote = this.error ? ` · <span class="error">${esc(this.error)}</span>` : "";
    const q = $("#mon-search").value.trim().toLowerCase();
    const r = { processes: () => this.renderProcesses(data, q), apps: () => this.renderApps(data, q), services: () => this.renderServices(data, q),
      hardware: () => this.renderHardware(data), network: () => this.renderNetwork(data, d) }[this.tab]();
    body.innerHTML = r.html;
    meta.innerHTML = [r.meta, when].filter(Boolean).map(esc).join(" · ") + errorNote;
  }

  canKill(p) { return this.ctx.role() !== "viewer" && !p.protected && p.create_time != null; }

  killButton(p) {
    if (!this.canKill(p)) return p.protected ? `<span class="muted small" title="Processo essencial ou o próprio agente">protegido</span>` : "";
    return `<button class="btn danger sm" data-kill="${p.pid}" data-name="${esc(p.name)}" data-ct="${p.create_time}">Encerrar</button>`;
  }

  renderProcesses(data, q) {
    let rows = data.processes;
    if (q) rows = rows.filter((p) => p.name.toLowerCase().includes(q) || String(p.pid) === q || (p.user || "").toLowerCase().includes(q));
    const { key, dir } = this.sort;
    rows = [...rows].sort((a, b) => {
      const va = a[key] ?? "", vb = b[key] ?? "";
      return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * dir;
    });
    const totalCpu = data.processes.reduce((s, p) => s + (p.cpu || 0), 0);
    const th = (k, label, cls = "") => {
      const arrow = key === k ? (dir > 0 ? " ▲" : " ▼") : "";
      return `<th class="${cls} sortable" data-sort="${k}">${label}${arrow}</th>`;
    };
    const shown = rows.slice(0, MAX_ROWS);
    const html = `<div class="table-wrap"><table class="table mon-table">
      <thead><tr>${th("name", "Nome")}${th("pid", "PID", "num hide-sm")}${th("cpu", "CPU", "num")}${th("memory", "Memória", "num")}${th("user", "Usuário", "hide-sm")}${th("threads", "Threads", "num hide-sm")}<th></th></tr></thead>
      <tbody>${shown.map((p) => `<tr>
        <td><b class="pname">${esc(p.name)}</b><small class="show-sm muted">PID ${p.pid}</small></td>
        <td class="num hide-sm">${p.pid}</td>
        <td class="num"><span class="cpu-cell ${p.cpu >= 50 ? "hot" : p.cpu >= 15 ? "warm" : ""}">${p.cpu.toFixed(1)}%</span></td>
        <td class="num">${bytes(p.memory)}</td>
        <td class="hide-sm">${esc(p.user || "—")}</td>
        <td class="num hide-sm">${p.threads}</td>
        <td class="act">${this.killButton(p)}</td></tr>`).join("")}</tbody></table></div>
      ${rows.length > MAX_ROWS ? `<p class="muted small">Mostrando ${MAX_ROWS} de ${rows.length}. Use a busca para filtrar.</p>` : ""}`;
    return { html, meta: `${data.count} processos · CPU total ${Math.min(100, totalCpu).toFixed(0)}% · ${data.cpu_count} threads lógicas` };
  }

  renderApps(data, q) {
    if (!data.supported) return { html: `<p class="muted">${esc(data.reason || "Não suportado neste sistema.")}</p>`, meta: "" };
    let apps = data.apps;
    if (q) apps = apps.filter((a) => (a.name || "").toLowerCase().includes(q) || a.windows.some((w) => w.toLowerCase().includes(q)));
    const html = apps.length ? `<div class="apps">${apps.map((a) => `
      <div class="app-item">
        <div class="app-icon">${esc((a.name || "?").slice(0, 1).toUpperCase())}</div>
        <div class="meta">
          <b>${esc(a.name || `PID ${a.pid}`)}</b>
          ${a.windows.slice(0, 4).map((w) => `<small>${esc(w)}</small>`).join("")}
          ${a.windows.length > 4 ? `<small class="muted">+${a.windows.length - 4} janela(s)</small>` : ""}
        </div>
        <div class="app-stats"><span>${a.cpu != null ? `${a.cpu.toFixed(1)}% CPU` : ""}</span><span>${bytes(a.memory)}</span></div>
        <div class="act">${this.killButton(a)}</div>
      </div>`).join("")}</div>` : `<p class="muted">Nenhuma janela aberta${q ? " com esse filtro" : ""}.</p>`;
    return { html, meta: `${data.apps.length} aplicativo(s) com janela visível` };
  }

  renderServices(data, q) {
    if (!data.supported) return { html: `<p class="muted">${esc(data.reason || "Não suportado neste sistema.")}</p>`, meta: "" };
    let rows = data.services;
    if (this.serviceFilter !== "all") rows = rows.filter((s) => (this.serviceFilter === "running" ? ["running", "active"] : ["stopped", "inactive", "failed"]).includes(s.status));
    if (q) rows = rows.filter((s) => s.name.toLowerCase().includes(q) || (s.display_name || "").toLowerCase().includes(q));
    const running = data.services.filter((s) => ["running", "active"].includes(s.status)).length;
    const startLabel = { auto: "Automático", manual: "Manual", disabled: "Desativado" };
    const html = `<div class="row svc-filter"><select id="svc-filter" class="select">
        <option value="all" ${this.serviceFilter === "all" ? "selected" : ""}>Todos</option>
        <option value="running" ${this.serviceFilter === "running" ? "selected" : ""}>Em execução</option>
        <option value="stopped" ${this.serviceFilter === "stopped" ? "selected" : ""}>Parados</option></select></div>
      <div class="table-wrap"><table class="table mon-table"><thead><tr><th>Serviço</th><th>Status</th><th class="hide-sm">Inicialização</th><th class="num hide-sm">PID</th></tr></thead>
      <tbody>${rows.slice(0, MAX_ROWS).map((s) => {
        const [label, cls] = SERVICE_STATUS[s.status] || [s.status, "pause"];
        return `<tr><td><b>${esc(s.display_name || s.name)}</b><small class="muted block">${esc(s.name)}</small></td>
          <td><span class="pill ${cls}">${esc(label)}</span></td><td class="hide-sm">${esc(startLabel[s.start_type] || s.start_type || "—")}</td>
          <td class="num hide-sm">${s.pid || "—"}</td></tr>`;
      }).join("")}</tbody></table></div>`;
    return { html, meta: `${data.services.length} serviços · ${running} em execução` };
  }

  renderHardware(h) {
    const kv = (pairs) => `<dl class="kv">${pairs.filter(([, v]) => v != null && v !== "").map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("") || "<dt>—</dt><dd>Indisponível</dd>"}</dl>`;
    const card = (title, inner) => `<div class="hw-card"><h4>${esc(title)}</h4>${inner}</div>`;
    const html = `<div class="hw-grid">
      ${card("Sistema", kv([["Fabricante", h.system.manufacturer], ["Modelo", h.system.model], ["Placa-mãe", h.system.board], ["BIOS", h.system.bios], ["Data da BIOS", h.system.bios_date], ["Sistema operacional", h.os.name], ["Arquitetura", h.os.arch], ["Ligado desde", dateTime(h.os.boot_time)]]))}
      ${card("Processador", kv([["Modelo", h.cpu.model], ["Núcleos físicos", h.cpu.cores], ["Threads", h.cpu.threads], ["Frequência máxima", h.cpu.max_mhz ? `${(h.cpu.max_mhz / 1000).toFixed(2)} GHz` : null]]))}
      ${card("Memória", kv([["RAM instalada", bytes(h.memory.total)], ["Arquivo de paginação / swap", bytes(h.memory.swap_total)]]))}
      ${card("Placa de vídeo", h.gpus.length ? h.gpus.map((g) => kv([["Modelo", g.name], ["Memória", g.memory ? bytes(g.memory) : null], ["Driver", g.driver]])).join("") : `<p class="muted small">Não identificada neste sistema.</p>`)}
      ${card("Armazenamento", h.disks.map((dk) => kv([["Unidade", `${dk.mount} (${dk.fstype || "?"})`], ["Capacidade", bytes(dk.total)], ["Livre", bytes(dk.free)]])).join(""))}
      ${card("Bateria", h.battery ? kv([["Carga", `${Math.round(h.battery.percent)}%`], ["Na tomada", h.battery.plugged ? "Sim" : "Não"], ["Autonomia", h.battery.secs_left ? `${Math.round(h.battery.secs_left / 60)} min` : null]]) : `<p class="muted small">Sem bateria (computador de mesa).</p>`)}
    </div>`;
    return { html, meta: [h.system.manufacturer, h.system.model].filter(Boolean).join(" ") };
  }

  renderNetwork(n, d) {
    const net = d.metrics?.network;
    const ifaces = n.interfaces.filter((i) => !i.loopback);
    const conn = n.connections;
    const connLabel = { ESTABLISHED: "Estabelecidas", LISTEN: "Escutando", TIME_WAIT: "Aguardando fechamento", CLOSE_WAIT: "Fechando" };
    const html = `<div class="net-top">
        <div class="net-stat"><small>Download agora</small><b class="green-text">${rate(net?.down_bps)}</b></div>
        <div class="net-stat"><small>Upload agora</small><b class="purple-text">${rate(net?.up_bps)}</b></div>
        <div class="net-stat"><small>Recebido desde o boot</small><b>${bytes(net?.total_recv)}</b></div>
        <div class="net-stat"><small>Enviado desde o boot</small><b>${bytes(net?.total_sent)}</b></div>
      </div>
      ${conn ? `<div class="conn-row">${Object.entries(conn).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([k, v]) => `<span class="conn"><b>${v}</b> ${esc(connLabel[k] || k.toLowerCase().replace("_", " "))}</span>`).join("")}</div>` : ""}
      <div class="hw-grid">${ifaces.map((i) => `<div class="hw-card"><h4>${esc(i.name)} <span class="pill ${i.up ? "on" : "off"}">${i.up ? "Conectada" : "Desconectada"}</span></h4>
        <dl class="kv">${[["IPv4", i.ipv4.join(", ")], ["IPv6", i.ipv6.slice(0, 2).join(", ")], ["MAC", i.mac], ["Velocidade", i.speed_mbps ? `${i.speed_mbps} Mbps` : null], ["Recebido", bytes(i.bytes_recv)], ["Enviado", bytes(i.bytes_sent)]]
          .filter(([, v]) => v).map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl></div>`).join("")}</div>`;
    return { html, meta: `${ifaces.filter((i) => i.up).length} interface(s) ativa(s)` };
  }

  // ------------------------------------------------------------ ações
  async onClick(e) {
    const th = e.target.closest("th[data-sort]");
    if (th) {
      const k = th.dataset.sort;
      this.sort = { key: k, dir: this.sort.key === k ? -this.sort.dir : (k === "name" || k === "user" ? 1 : -1) };
      this.render();
      return;
    }
    const btn = e.target.closest("button[data-kill]");
    if (!btn) return;
    const d = this.ctx.device();
    const pid = Number(btn.dataset.kill);
    const name = btn.dataset.name;
    const ok = await confirmDialog(
      "Encerrar processo",
      `Encerrar "${name}" (PID ${pid}) em ${d.name}? Dados não salvos nesse programa serão perdidos. A ação fica registrada na auditoria.`,
      "Encerrar",
    );
    if (!ok) return;
    btn.disabled = true;
    try {
      await this.ctx.live().command(d.id, "process.kill", { pid, name, create_time: Number(btn.dataset.ct) }, { confirm: true });
      toast(`${name} (PID ${pid}) encerrado.`);
    } catch (err) {
      toast(err.message, "error");
      btn.disabled = false;
    }
    this.load(false);
  }
}
