// Volume do PC: controle deslizante, − / + e silenciar (o PC pode bloquear na bandeja).
import { $, toast } from "./util.js";

export class VolumeCard {
  /** @param {{device: () => any, live: () => any}} ctx */
  constructor(ctx) {
    this.ctx = ctx;
    this.state = null;
    this.timer = null;
    this.busy = false;
    $("#vol-range").addEventListener("input", (e) => {
      $("#vol-value").textContent = `${e.target.value}%`;
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.send({ volume: Number(e.target.value) }), 250);  // envia ao parar de arrastar
    });
    $("#vol-down").addEventListener("click", () => this.send({ step: -10 }));
    $("#vol-up").addEventListener("click", () => this.send({ step: 10 }));
    $("#vol-mute").addEventListener("click", () => this.send({ muted: !this.state?.muted }));
  }

  async render(d, refresh = false) {
    const card = $("#vol-card");
    card.classList.toggle("hidden", !d?.online);
    if (!d?.online) { this.loadedFor = null; return; }
    if (!refresh && this.loadedFor === d.id) return;
    this.loadedFor = d.id;
    try {
      this.show(await this.ctx.live().command(d.id, "audio.status"));
    } catch (e) {
      card.classList.add("disabled");
      $("#vol-note").textContent = /não suportada/.test(e.message) ? "atualize o agente para usar" : e.message;
    }
  }

  show(s) {
    this.state = s;
    $("#vol-card").classList.remove("disabled");
    $("#vol-note").textContent = s.muted ? "sem som" : "";
    if (document.activeElement !== $("#vol-range")) $("#vol-range").value = s.volume;
    $("#vol-value").textContent = `${s.volume}%`;
    $("#vol-icon").textContent = s.muted ? "🔇" : s.volume === 0 ? "🔈" : s.volume < 50 ? "🔉" : "🔊";
    $("#vol-mute").classList.toggle("muted", !!s.muted);
    $("#vol-mute").setAttribute("aria-label", s.muted ? "Tirar do mudo" : "Silenciar");
  }

  async send(params) {
    const d = this.ctx.device();
    if (!d?.online) return;
    if (this.busy) { this.queued = params; return; }  // o último valor pedido é enviado em seguida
    this.busy = true;
    try {
      this.show(await this.ctx.live().command(d.id, "audio.set", params));
    } catch (e) {
      toast(e.message, "error");
    } finally {
      this.busy = false;
      if (this.queued) { const q = this.queued; this.queued = null; this.send(q); }
    }
  }
}
