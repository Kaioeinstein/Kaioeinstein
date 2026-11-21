// Atualizações: versão atual × publicada, botão Atualizar (proprietário) e acompanhamento até o servidor voltar.
import * as API from "./api.js";
import { $, confirmDialog, esc, toast } from "./util.js";

const PHASES = {
  downloading: "Baixando a versão nova…",
  installing: "Instalando…",
  building_agent: "Montando o agente para os seus PCs (alguns minutos)…",
  restarting: "Religando o servidor…",
  done: "Atualização concluída.",
  failed: "A atualização falhou — nada foi perdido.",
  rolled_back: "A versão nova não respondeu; a anterior foi restaurada.",
};

export class UpdateCard {
  constructor() {
    this.polling = null;
    $("#upd-check").addEventListener("click", () => this.render(true));
    $("#upd-start").addEventListener("click", () => this.start());
    $("#upd-agents").addEventListener("change", async (e) => {
      try {
        await API.api("/api/settings", { method: "PUT", body: { auto_update_agents: e.target.checked } });
        toast(e.target.checked ? "Os PCs vão se atualizar sozinhos." : "Atualização automática dos PCs desligada.", "success");
      } catch (err) { toast(err.message, "error"); e.target.checked = !e.target.checked; }
    });
  }

  async render(refresh = false) {
    const box = $("#upd-card");
    const role = API.currentUser?.role;
    box.classList.toggle("hidden", role === "viewer");
    if (role === "viewer") return;
    $("#upd-text").textContent = refresh ? "Verificando no GitHub…" : $("#upd-text").textContent;
    let s;
    try {
      s = await API.api(`/api/update/status${refresh ? "?refresh=1" : ""}`);
    } catch (err) {
      $("#upd-text").textContent = err.message;
      return;
    }
    this.status = s;
    API.api("/api/settings").then((st) => ($("#upd-agents").checked = st.auto_update_agents !== false)).catch(() => {});
    $("#upd-current").textContent = `v${s.current}`;
    $("#upd-latest").textContent = s.latest ? `v${s.latest}` : "—";
    const pill = $("#upd-pill");
    pill.className = `pill ${s.available ? "pause" : "on"}`;
    pill.textContent = s.available ? "Nova versão disponível" : s.latest ? "Atualizado" : "—";
    const phase = s.state?.phase;
    let text = s.error || (s.available ? `A versão ${s.latest} está disponível.` : "Você está na versão mais recente.");
    if (phase && PHASES[phase] && (s.state.ts || 0) > Date.now() / 1000 - 86400) {
      text = `${PHASES[phase]}${s.state.message && ["failed", "rolled_back"].includes(phase) ? ` (${s.state.message})` : ""}`;
    }
    if (!s.supported && s.available) text += " Neste servidor (Docker/manual), atualize com: git pull && docker compose up -d --build.";
    $("#upd-text").textContent = text;
    const btn = $("#upd-start");
    btn.classList.toggle("hidden", !(s.available && s.supported && role === "owner"));
    btn.disabled = ["downloading", "installing", "building_agent", "restarting"].includes(phase);
    $("#upd-agent-pkg").textContent = s.agent_package ? `Pacote do agente pronto: v${esc(s.agent_package)}` : "";
  }

  async start() {
    const s = this.status;
    const ok = await confirmDialog(`Atualizar para a versão ${s.latest}?`,
      "O servidor baixa a versão nova, guarda uma cópia da atual e reinicia (o painel fica fora do ar por alguns minutos). " +
      "Se a nova não funcionar, a anterior volta sozinha. Depois, os seus PCs se atualizam sozinhos ao conectar.",
      "Atualizar agora");
    if (!ok) return;
    try {
      await API.api("/api/update/start", { method: "POST", body: { confirm: true } });
    } catch (err) { toast(err.message, "error"); return; }
    const target = s.latest;
    $("#upd-start").disabled = true;
    $("#upd-text").textContent = "Atualizando… o painel volta sozinho quando terminar (não feche esta página).";
    toast("Atualização iniciada.", "warning");
    clearInterval(this.polling);
    const started = Date.now();
    this.polling = setInterval(async () => {
      try {
        const h = await (await fetch("/api/health", { cache: "no-store" })).json();
        if (h.version === target) {
          clearInterval(this.polling);
          toast(`Servidor atualizado para a versão ${target}.`, "success");
          setTimeout(() => location.reload(), 1500);  // carrega o painel novo
        } else if (Date.now() - started > 60 * 1000) {
          // servidor no ar com a versão antiga: ainda montando/atualizando, falhou ou restaurou a anterior
          await this.render();
          const phase = this.status?.state?.phase;
          if (["failed", "rolled_back", "done"].includes(phase) || Date.now() - started > 20 * 60 * 1000) clearInterval(this.polling);
        }
      } catch { $("#upd-text").textContent = "Servidor reiniciando para atualizar…"; }
    }, 5000);
  }
}
