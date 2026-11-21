// Otimizar: modo jogo, modo de energia, limpeza de disco e programas que iniciam com o Windows.
import { $, $$, bytes, confirmDialog, esc, toast } from "./util.js";

export class Optimizer {
  /** @param {{device: () => any, live: () => any}} ctx */
  constructor(ctx) {
    this.ctx = ctx;
    this.scan = null;
    this.status = null;
    $("#op-scan").addEventListener("click", () => this.runScan());
    $("#op-clean").addEventListener("click", () => this.clean());
    $("#op-cats").addEventListener("change", () => this.syncClean());
    $("#op-game-auto").addEventListener("change", (e) => this.setGame({ auto: e.target.checked }));
    $("#op-game-save").addEventListener("click", () => {
      const names = $("#op-game-list").value.split(/[,;\n]/).map((s) => s.trim()).filter(Boolean);
      this.setGame({ processes: names });
    });
    $("#op-modes").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-mode]");
      if (b) this.setMode(b.dataset.mode);
    });
    $("#op-plan").addEventListener("change", (e) => this.setPlan(e.target.value));
    $("#op-startup").addEventListener("change", (e) => {
      const input = e.target.closest("input[data-id]");
      if (input) this.setStartup(input.dataset.id, input.checked, input);
    });
    $("#op-startup-load").addEventListener("click", () => this.loadStartup());
  }

  device() { return this.ctx.device(); }
  async cmd(action, params = {}, opts = {}) {
    const d = this.device();
    if (!d?.online) throw new Error("Computador offline.");
    return this.ctx.live().command(d.id, action, params, opts);
  }

  async show() {
    const d = this.device();
    $("#op-offline").classList.toggle("hidden", !!d?.online);
    $("#op-body").classList.toggle("hidden", !d?.online);
    if (!d?.online) return;
    await this.loadStatus();
    if (!this.scan || this.scan.device !== d.id) this.runScan();
  }

  // ---------------------------------------------------------------- modo jogo e energia
  async loadStatus() {
    try {
      this.status = await this.cmd("perf.status");
    } catch (e) {
      $("#op-game-state").textContent = `Indisponível: ${e.message}`;
      return;
    }
    this.renderStatus();
  }

  renderStatus() {
    const s = this.status;
    const g = s.game || {};
    $("#op-game-auto").checked = !!g.auto;
    $("#op-game-list").value = (g.processes || []).join(", ");
    const st = $("#op-game-state");
    st.className = `pill ${g.active ? "on" : g.auto ? "warn" : "off"}`;
    st.textContent = g.active ? `Jogando: ${g.game}` : g.auto ? "Aguardando um jogo abrir" : "Desligado";
    $("#op-game-error").textContent = g.error || "";
    $("#op-modes").innerHTML = (s.modes || []).map((m) =>
      `<button type="button" data-mode="${m.id}" class="${m.id === s.mode ? "active" : ""}">${esc(m.label)}</button>`).join("");
    $("#op-mode-note").textContent = s.mode ? "" : (s.windows ? "O Windows não informou o modo atual (use o plano Equilibrado para liberar este controle)." : "Disponível apenas no Windows.");
    const plans = s.plans || [];
    $("#op-plan-row").classList.toggle("hidden", !plans.length);
    $("#op-plan").innerHTML = plans.map((p) => `<option value="${esc(p.guid)}" ${p.active ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  }

  async setGame(params) {
    try {
      const r = await this.cmd("perf.game", params);
      this.status.game = r.state;
      this.renderStatus();
      if ("auto" in params) toast(params.auto ? "Modo jogo automático ligado: ao abrir um jogo, o PC vai para Melhor desempenho." : "Modo jogo automático desligado.", "success");
      else toast("Lista de jogos salva.", "success");
    } catch (e) { toast(e.message, "error"); this.loadStatus(); }
  }

  async setMode(mode) {
    try {
      await this.cmd("perf.mode", { mode });
      this.status.mode = mode;
      this.renderStatus();
      toast("Modo de energia alterado.", "success");
    } catch (e) { toast(e.message, "error"); }
  }

  async setPlan(guid) {
    try {
      await this.cmd("perf.plan", { guid });
      toast("Plano de energia alterado.", "success");
      this.loadStatus();
    } catch (e) { toast(e.message, "error"); this.loadStatus(); }
  }

  // ---------------------------------------------------------------- limpeza
  async runScan() {
    const btn = $("#op-scan");
    btn.disabled = true;
    btn.textContent = "Analisando…";
    $("#op-cats").innerHTML = `<p class="muted small">Calculando quanto espaço pode ser liberado…</p>`;
    try {
      const r = await this.cmd("maint.scan", {}, { timeout: 120000 });
      this.scan = { ...r, device: this.device().id };
      this.renderScan();
    } catch (e) {
      $("#op-cats").innerHTML = `<p class="muted small">Falha na análise: ${esc(e.message)}</p>`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Analisar de novo";
    }
  }

  renderScan() {
    const s = this.scan;
    $("#op-disks").innerHTML = (s.disks || []).map((d) => `
      <div class="op-disk"><div class="row between"><b>${esc(d.mount)}</b><small class="muted">${bytes(d.free)} livres de ${bytes(d.total)}</small></div>
      <div class="bar ${d.percent > 90 ? "danger" : ""}"><i data-w="${Number(d.percent) || 0}"></i></div></div>`).join("");
    // largura via CSSOM (atributo style em HTML é bloqueado pela política de segurança do painel)
    $$("#op-disks .bar i").forEach((i) => { i.style.width = `${i.dataset.w}%`; });
    $("#op-cats").innerHTML = s.categories.map((c) => `
      <label class="op-cat">
        <input type="checkbox" value="${esc(c.id)}" ${c.bytes > 0 ? "checked" : "disabled"}>
        <span><b>${esc(c.label)}</b><small class="muted">${esc(c.desc)}</small></span>
        <em>${c.bytes > 0 ? bytes(c.bytes) : "limpo"}</em>
      </label>`).join("");
    this.syncClean();
  }

  selected() { return $$("#op-cats input:checked").map((i) => i.value); }

  syncClean() {
    const ids = this.selected();
    const total = (this.scan?.categories || []).filter((c) => ids.includes(c.id)).reduce((a, c) => a + c.bytes, 0);
    const btn = $("#op-clean");
    btn.disabled = !ids.length || !total;
    btn.textContent = total ? `Limpar ${bytes(total)}` : "Nada para limpar";
    this.total = total;
  }

  async clean() {
    const ids = this.selected();
    const labels = this.scan.categories.filter((c) => ids.includes(c.id)).map((c) => c.label).join(", ");
    const ok = await confirmDialog("Limpar o disco?",
      `Serão apagados até ${bytes(this.total)} em: ${labels}. Arquivos em uso ou modificados na última hora ficam. Isso não pode ser desfeito.`,
      "Limpar agora");
    if (!ok) return;
    const btn = $("#op-clean");
    btn.disabled = true;
    btn.textContent = "Limpando…";
    try {
      const r = await this.cmd("maint.clean", { categories: ids }, { confirm: true, timeout: 300000 });
      toast(`Pronto: ${bytes(r.freed)} liberados (${r.files} arquivos${r.skipped ? `; ${r.skipped} em uso ficaram` : ""}).`, "success");
    } catch (e) { toast(e.message, "error"); }
    this.runScan();
  }

  // ---------------------------------------------------------------- inicialização
  async loadStartup() {
    const box = $("#op-startup");
    box.innerHTML = `<p class="muted small">Carregando…</p>`;
    try {
      const r = await this.cmd("maint.startup");
      if (!r.windows) { box.innerHTML = `<p class="muted small">Disponível apenas no Windows.</p>`; return; }
      box.innerHTML = r.items.length ? r.items.map((i) => `
        <div class="op-start">
          <span><b>${esc(i.name)}</b><small class="muted" title="${esc(i.command)}">${esc(i.command)}</small></span>
          ${i.editable
            ? `<label class="switch" title="${i.enabled ? "Desativar" : "Ativar"}"><input type="checkbox" data-id="${esc(i.id)}" ${i.enabled ? "checked" : ""}><i></i></label>`
            : `<small class="pill off" title="Instalado para todos os usuários: só pode ser alterado como administrador no PC">${i.enabled ? "ativo · sistema" : "desativado · sistema"}</small>`}
        </div>`).join("") : `<p class="muted small">Nenhum programa inicia com o Windows.</p>`;
    } catch (e) { box.innerHTML = `<p class="muted small">${esc(e.message)}</p>`; }
  }

  async setStartup(id, enabled, input) {
    try {
      await this.cmd("maint.startup.set", { id, enabled });
      toast(enabled ? "Vai iniciar com o Windows." : "Não vai mais iniciar com o Windows (o programa continua instalado).", "success");
    } catch (e) { toast(e.message, "error"); input.checked = !enabled; }
  }
}
