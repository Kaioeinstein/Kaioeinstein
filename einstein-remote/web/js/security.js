// Fase 8: verificação em duas etapas (TOTP) e notificações push neste aparelho.
import * as API from "./api.js";
import { $, esc, toast } from "./util.js";

// ------------------------------------------------------------------ 2FA (Minha conta)
export class TwoFactorCard {
  constructor(onChange) {
    this.onChange = onChange;
    $("#tf-start").addEventListener("click", () => this.start());
    $("#tf-enable-form").addEventListener("submit", (e) => { e.preventDefault(); this.enable(); });
    $("#tf-disable-form").addEventListener("submit", (e) => { e.preventDefault(); this.disable(); });
    $("#tf-download").addEventListener("click", () => this.downloadCodes());
    $("#tf-done").addEventListener("click", () => { this.codes = null; this.render(); this.onChange?.(); });
  }

  render() {
    const enabled = !!API.currentUser?.totp_enabled;
    const pill = $("#tf-status");
    pill.textContent = enabled ? "Ativa" : "Desativada";
    pill.className = `pill ${enabled ? "on" : "off"}`;
    const show = (id, on) => $(id).classList.toggle("hidden", !on);
    show("#tf-off", !enabled && !this.setup && !this.codes);
    show("#tf-setup", !!this.setup && !this.codes);
    show("#tf-codes", !!this.codes);
    show("#tf-on", enabled && !this.codes);
  }

  async start() {
    try {
      this.setup = await API.api("/api/auth/2fa/setup", { method: "POST" });
      $("#tf-qr").src = this.setup.qr;
      $("#tf-secret").textContent = this.setup.secret.replace(/(.{4})/g, "$1 ").trim();
      $("#tf-error").textContent = "";
      this.render();
      $("#tf-code").focus();
    } catch (err) { toast(err.message, "error"); }
  }

  async enable() {
    try {
      const r = await API.api("/api/auth/2fa/enable", { method: "POST", body: { code: $("#tf-code").value } });
      this.codes = r.recovery_codes;
      this.setup = null;
      await API.refresh(); // atualiza currentUser.totp_enabled
      $("#tf-code-list").innerHTML = this.codes.map((c) => `<li><code>${esc(c)}</code></li>`).join("");
      $("#tf-enable-form").reset();
      this.render();
      toast("Verificação em duas etapas ativada.");
    } catch (err) { $("#tf-error").textContent = err.message; }
  }

  downloadCodes() {
    const text = `EINSTEIN REMOTE — códigos de recuperação de ${API.currentUser.username}\n`
      + `Cada código funciona UMA vez, se você perder o celular. Guarde em local seguro.\n\n${this.codes.join("\n")}\n`;
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "einstein-remote-codigos-recuperacao.txt" });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async disable() {
    const f = $("#tf-disable-form");
    try {
      await API.api("/api/auth/2fa/disable", { method: "POST", body: { password: f.elements.password.value, code: f.elements.code.value } });
      f.reset();
      await API.refresh();
      this.render();
      toast("Verificação em duas etapas desativada.", "warning");
      this.onChange?.();
    } catch (err) { toast(err.message, "error"); }
  }
}

// ------------------------------------------------------------------ notificações push
function b64uToBytes(s) {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const raw = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

export class PushCard {
  constructor() {
    $("#push-on").addEventListener("click", () => this.subscribe());
    $("#push-off").addEventListener("click", () => this.unsubscribe());
    $("#push-test").addEventListener("click", () => this.test());
    $("#push-level").addEventListener("change", () => { if (this.sub) this.subscribe(); });
  }

  supportProblem() {
    if (!window.isSecureContext) return "Notificações push exigem HTTPS (ou acesso por localhost). Configure o certificado do servidor (scripts/gen-cert).";
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      return /iPhone|iPad/.test(navigator.userAgent)
        ? "No iPhone/iPad: toque em Compartilhar › Adicionar à Tela de Início e abra o EINSTEIN REMOTE por lá (iOS 16.4 ou mais novo)."
        : "Este navegador não suporta notificações push.";
    }
    if (Notification.permission === "denied") return "As notificações estão bloqueadas para este site nas configurações do navegador.";
    return null;
  }

  async registration() {
    return (await navigator.serviceWorker.getRegistration("/")) || navigator.serviceWorker.register("/sw.js", { scope: "/" });
  }

  async render() {
    const problem = this.supportProblem();
    $("#push-problem").textContent = problem || "";
    $("#push-problem").classList.toggle("hidden", !problem);
    this.sub = null;
    if (!problem) {
      try { this.sub = await (await this.registration()).pushManager.getSubscription(); } catch { /* sem registro */ }
    }
    $("#push-state").textContent = this.sub ? "Ativas neste aparelho" : "Desativadas neste aparelho";
    $("#push-state").className = `pill ${this.sub ? "on" : "off"}`;
    $("#push-on").classList.toggle("hidden", !!this.sub || !!problem);
    $("#push-off").classList.toggle("hidden", !this.sub);
    $("#push-test").classList.toggle("hidden", !this.sub);
  }

  async subscribe() {
    try {
      if ((await Notification.requestPermission()) !== "granted") throw new Error("Permissão de notificação negada.");
      const reg = await this.registration();
      await navigator.serviceWorker.ready;
      const { key } = await API.api("/api/push/key");
      const sub = (await reg.pushManager.getSubscription())
        || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64uToBytes(key) });
      const json = sub.toJSON();
      await API.api("/api/push/subscribe", { method: "POST", body: { endpoint: json.endpoint, keys: json.keys, min_level: $("#push-level").value } });
      toast("Notificações push ativadas neste aparelho.");
    } catch (err) { toast(err.message || "Não foi possível ativar as notificações push.", "error"); }
    this.render();
  }

  async unsubscribe() {
    try {
      const sub = await (await this.registration()).pushManager.getSubscription();
      if (sub) {
        await API.api("/api/push/unsubscribe", { method: "POST", body: { endpoint: sub.endpoint } }).catch(() => {});
        await sub.unsubscribe();
      }
      toast("Notificações push desativadas neste aparelho.");
    } catch (err) { toast(err.message, "error"); }
    this.render();
  }

  async test() {
    try {
      const r = await API.api("/api/push/test", { method: "POST" });
      toast(r.accepted ? `Teste enviado para ${r.accepted} aparelho(s).` : "O serviço de push recusou o envio.", r.accepted ? "info" : "error");
    } catch (err) { toast(err.message, "error"); }
  }
}
