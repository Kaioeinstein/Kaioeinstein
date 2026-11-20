// Central de energia: desligar, reiniciar, suspender, hibernar, bloquear, cancelar e ligar (Wake-on-LAN).
import * as API from "./api.js";
import { $, $$, toast } from "./util.js";

const ACTIONS = {
  shutdown: { title: "Desligar", verb: "desligar", noun: "Desligamento", destructive: true,
    delays: [[0, "Agora"], [30, "Em 30 segundos"], [60, "Em 1 minuto"], [300, "Em 5 minutos"], [900, "Em 15 minutos"], [1800, "Em 30 minutos"], [3600, "Em 1 hora"]], def: 60,
    warn: "Programas abertos serão fechados e trabalho não salvo será perdido. O Windows exibirá um aviso no PC." },
  restart: { title: "Reiniciar", verb: "reiniciar", noun: "Reinício", destructive: true,
    delays: [[0, "Agora"], [30, "Em 30 segundos"], [60, "Em 1 minuto"], [300, "Em 5 minutos"], [900, "Em 15 minutos"], [1800, "Em 30 minutos"], [3600, "Em 1 hora"]], def: 60,
    warn: "Programas abertos serão fechados e trabalho não salvo será perdido. O agente volta sozinho se o início automático estiver ativado." },
  sleep: { title: "Suspender", verb: "suspender", noun: "Suspensão", destructive: false,
    delays: [[0, "Agora"], [60, "Em 1 minuto"], [300, "Em 5 minutos"], [900, "Em 15 minutos"]], def: 0,
    warn: "O PC entra em modo de economia e o painel fica offline até ele acordar (teclado, mouse ou Wake-on-LAN)." },
  hibernate: { title: "Hibernar", verb: "hibernar", noun: "Hibernação", destructive: false,
    delays: [[0, "Agora"], [60, "Em 1 minuto"], [300, "Em 5 minutos"], [900, "Em 15 minutos"]], def: 0,
    warn: "O conteúdo da memória é salvo em disco e o PC desliga. Ao ligar, tudo volta como estava." },
};

function fmt(sec) {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}h ${String(m).padStart(2, "0")}min` : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export class PowerCenter {
  /** @param {{device: () => any, live: () => any}} ctx */
  constructor(ctx) {
    this.ctx = ctx;
    this.caps = {};
    this.timer = null;
    $("#power-grid").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-power]");
      if (b && !b.disabled) this.ask(b.dataset.power);
    });
    $("#pw-cancel").addEventListener("click", () => this.cancel());
    $("#power-form").addEventListener("submit", (e) => { e.preventDefault(); this.confirm(); });
    $("#power-form").addEventListener("change", () => this.syncConfirm());
    $("#power-cancel-btn").addEventListener("click", () => $("#power-dialog").close());
  }

  // ------------------------------------------------------------ renderização
  async render(d, refreshCaps = false) {
    this.device = d;
    const card = $("#power-card");
    card.classList.toggle("hidden", !d);
    if (!d) return;
    if (d.online && (refreshCaps || !this.caps[d.id])) {
      try { this.caps[d.id] = await this.ctx.live().command(d.id, "power.status"); } catch { /* agente antigo ou offline */ }
    }
    const info = d.online ? this.caps[d.id] : null;
    const caps = info?.capabilities || {};
    const reasons = info?.reasons || {};
    $$("#power-grid button[data-power]").forEach((b) => {
      const a = b.dataset.power;
      if (a === "wake") {
        b.classList.toggle("hidden", d.online);
        b.disabled = !d.mac;
        b.title = d.mac ? `Envia o pacote Wake-on-LAN para ${d.mac}` : "MAC ainda desconhecido: conecte o agente desta versão ao menos uma vez.";
        return;
      }
      b.classList.toggle("hidden", !d.online);
      b.disabled = !d.online || caps[a] === false || !!d.power;
      b.title = caps[a] === false ? (reasons[a] || "Não suportado neste computador") : "";
    });
    $("#pw-note").textContent = d.online ? (caps.dry_run ? "modo de simulação no agente" : "") : "computador offline";
    this.renderPending(d);
  }

  renderPending(d) {
    clearInterval(this.timer);
    const box = $("#pw-pending");
    const p = d?.online ? d.power : null;
    box.classList.toggle("hidden", !p);
    if (!p) return;
    const a = ACTIONS[p.action] || { noun: "Ação" };
    const tick = () => {
      const left = p.at - Date.now() / 1000;
      $("#pw-pending-text").textContent = left > 1 ? `${a.noun} em ${fmt(left)}` : `${a.noun} em andamento…`;
      $("#pw-pending-sub").textContent = `Pedido por ${p.by}. Pode ser cancelado aqui ou no ícone do agente no PC.`;
    };
    tick();
    this.timer = setInterval(tick, 1000);
  }

  // ------------------------------------------------------------ ações
  ask(action) {
    const d = this.device;
    if (action === "lock") return this.run("lock", {});
    if (action === "wake") return this.wake();
    const a = ACTIONS[action];
    this.pendingAction = action;
    $("#power-title").textContent = `${a.title} “${d.name}”?`;
    $("#power-warn").textContent = a.warn;
    $("#power-delay").innerHTML = a.delays.map(([v, l]) => `<option value="${v}" ${v === a.def ? "selected" : ""}>${l}</option>`).join("");
    $("#power-ack-wrap").classList.toggle("hidden", !a.destructive);
    $("#power-ack").checked = false;
    $("#power-ok").textContent = a.title;
    this.syncConfirm();
    $("#power-dialog").showModal();
  }

  syncConfirm() {
    const a = ACTIONS[this.pendingAction];
    $("#power-ok").disabled = a?.destructive && !$("#power-ack").checked;
  }

  async confirm() {
    const action = this.pendingAction;
    const delay = Number($("#power-delay").value);
    $("#power-dialog").close();
    await this.run(action, { delay }, true);
  }

  async run(action, params, confirm = false) {
    const d = this.device;
    try {
      await this.ctx.live().command(d.id, `power.${action}`, params, { confirm });
      if (action === "lock") toast(`Tela de ${d.name} bloqueada.`);
      else toast(`${ACTIONS[action].noun} de ${d.name} agendado(a).`, "warning");
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async cancel() {
    const d = this.device;
    try {
      const r = await this.ctx.live().command(d.id, "power.cancel");
      toast(r.canceled ? "Ação de energia cancelada." : "Não havia ação de energia pendente.");
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async wake() {
    const d = this.device;
    try {
      await API.api(`/api/devices/${encodeURIComponent(d.id)}/wake`, { method: "POST" });
      toast(`Pacote Wake-on-LAN enviado para ${d.name}. Se o PC estiver configurado, ele liga em alguns segundos.`);
    } catch (err) {
      toast(err.message, "error");
    }
  }
}
