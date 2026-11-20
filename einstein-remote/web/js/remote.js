// Visualizador de tela remota com controle de mouse e teclado (desktop e celular).
import { $, $$, bytes, toast } from "./util.js";

const PRESETS = {
  low: { max_width: 960, quality: 45, fps: 8 },
  mid: { max_width: 1280, quality: 60, fps: 12 },
  high: { max_width: 1920, quality: 75, fps: 15 },
  max: { max_width: 0, quality: 85, fps: 20 },
};
// Atalhos que o navegador/sistema local não deixa capturar pelo teclado físico
const SHORTCUTS = [
  ["Esc", ["Escape"]], ["Tab", ["Tab"]], ["Enter", ["Enter"]], ["⌫", ["Backspace"]], ["Del", ["Delete"]],
  ["Win", ["MetaLeft"]], ["Alt+Tab", ["AltLeft", "Tab"]], ["Win+D", ["MetaLeft", "KeyD"]], ["Win+E", ["MetaLeft", "KeyE"]],
  ["Ctrl+C", ["ControlLeft", "KeyC"]], ["Ctrl+V", ["ControlLeft", "KeyV"]], ["Ctrl+Z", ["ControlLeft", "KeyZ"]],
  ["Ctrl+A", ["ControlLeft", "KeyA"]], ["Ctrl+Shift+Esc", ["ControlLeft", "ShiftLeft", "Escape"]], ["Alt+F4", ["AltLeft", "F4"]],
  ["F5", ["F5"]], ["PrtSc", ["PrintScreen"]], ["←", ["ArrowLeft"]], ["↑", ["ArrowUp"]], ["↓", ["ArrowDown"]], ["→", ["ArrowRight"]],
];
const SUPPORTED_CODES = new Set([
  ..."ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("").map((c) => `Key${c}`), ...[...Array(10).keys()].map((d) => `Digit${d}`),
  ...[...Array(12).keys()].map((i) => `F${i + 1}`), ...[...Array(10).keys()].map((d) => `Numpad${d}`),
  "Enter", "NumpadEnter", "Escape", "Backspace", "Tab", "Space", "ShiftLeft", "ShiftRight", "ControlLeft", "ControlRight",
  "AltLeft", "AltRight", "MetaLeft", "MetaRight", "ContextMenu", "CapsLock", "NumLock", "ScrollLock", "ArrowLeft", "ArrowUp",
  "ArrowRight", "ArrowDown", "Home", "End", "PageUp", "PageDown", "Insert", "Delete", "PrintScreen", "Pause", "Minus", "Equal",
  "BracketLeft", "BracketRight", "Backslash", "Semicolon", "Quote", "Backquote", "Comma", "Period", "Slash", "IntlBackslash",
  "IntlRo", "NumpadAdd", "NumpadSubtract", "NumpadMultiply", "NumpadDivide", "NumpadDecimal",
]);
const REASONS = {
  owner: "O dono do computador encerrou o compartilhamento.",
  disconnected: "O computador desconectou.",
  viewers_left: "Sessão encerrada.",
  agent: "O agente encerrou a sessão.",
};

export class RemoteViewer {
  /** @param {{live: () => any, device: (id: string) => any}} ctx */
  constructor(ctx) {
    this.ctx = ctx;
    this.deviceId = null;
    this.open = false;
    this.control = false;
    this.canvas = $("#rv-canvas");
    this.g = this.canvas.getContext("2d");
    this.queue = [];
    this.pending = null;
    this.stats = { frames: 0, bytes: 0, t0: performance.now(), fps: 0, kbps: 0 };
    this.size = { w: 0, h: 0, sw: 0, sh: 0 };
    this.touch = null;
    this.bindUI();
  }

  // ------------------------------------------------------------ abrir / fechar
  async start(deviceId) {
    const d = this.ctx.device(deviceId);
    if (!d?.online) return toast("O computador precisa estar online.", "warning");
    this.deviceId = deviceId;
    this.open = true;
    this.wantControl = true;
    $("#rv-name").textContent = d.name;
    $("#rv").classList.remove("hidden");
    document.body.classList.add("rv-open");
    this.message("Conectando à tela…");
    this.attachLive();
    await this.request();
    this.canvas.focus();
  }

  attachLive() {
    const live = this.ctx.live();
    if (this.live === live) return;
    this.live = live;
    live.addEventListener("frame", (e) => this.onFrame(e.detail));
    live.addEventListener("screen_stopped", (e) => {
      if (this.open && e.detail.device_id === this.deviceId) this.ended(REASONS[e.detail.reason] || "Sessão encerrada.");
    });
    live.addEventListener("device", (e) => {
      if (this.open && e.detail.data.id === this.deviceId && !e.detail.data.online) this.ended("O computador ficou offline.");
    });
    live.addEventListener("status", (e) => {
      if (this.open && !e.detail.online) this.message("Conexão com o servidor perdida… reconectando.");
      if (this.open && e.detail.online && this.lostServer) this.request();
      this.lostServer = !e.detail.online;
    });
  }

  async request() {
    const preset = PRESETS[$("#rv-quality").value] || PRESETS.mid;
    const params = { ...preset, monitor: Number($("#rv-monitor").value) || 1, control: this.wantControl };
    try {
      const r = await this.ctx.live().command(this.deviceId, "screen.start", params);
      this.setControl(r.control);
      if (r.control_blocked) toast("O dono do computador bloqueou o controle de mouse e teclado; modo somente visualização.", "warning");
      const sel = $("#rv-monitor");
      sel.innerHTML = r.monitors.map((m) => `<option value="${m.index}">Monitor ${m.index} · ${m.width}×${m.height}${m.primary ? " (principal)" : ""}</option>`).join("");
      sel.value = String(r.monitor);
      sel.classList.toggle("hidden", r.monitors.length < 2);
      if (!this.size.w) this.message("Aguardando a primeira imagem…");
    } catch (err) {
      this.message(err.message);
      toast(err.message, "error");
    }
  }

  async close() {
    if (!this.open) return;
    const id = this.deviceId;
    this.teardown();
    try { await this.ctx.live().command(id, "screen.stop"); } catch { /* conexão pode ter caído */ }
  }

  ended(text) {
    this.teardown();
    toast(text, "warning");
  }

  teardown() {
    this.open = false;
    this.releaseAll();
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    navigator.keyboard?.unlock?.();
    $("#rv").classList.add("hidden");
    document.body.classList.remove("rv-open");
    $("#rv-keypanel").classList.add("hidden");
    this.size = { w: 0, h: 0, sw: 0, sh: 0 };
    this.g.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }

  message(text) {
    const m = $("#rv-msg");
    m.textContent = text || "";
    m.classList.toggle("hidden", !text);
  }

  setControl(on) {
    this.control = !!on;
    const b = $("#rv-control");
    b.setAttribute("aria-pressed", String(this.control));
    b.classList.toggle("on", this.control);
    b.textContent = this.control ? "Controle: ligado" : "Controle: desligado";
    $("#rv").classList.toggle("controlling", this.control);
  }

  // ------------------------------------------------------------ quadros
  onFrame(buf) {
    if (!this.open) return;
    const u8 = new Uint8Array(buf);
    if (u8[0] !== 0x53) return; // 'S'
    const dev = new TextDecoder().decode(u8.subarray(1, 33));
    if (dev !== this.deviceId || u8[33] !== 0x01) return;
    const hlen = (u8[34] << 8) | u8[35];
    let header;
    try { header = JSON.parse(new TextDecoder().decode(u8.subarray(36, 36 + hlen))); } catch { return; }
    const jpeg = u8.subarray(36 + hlen);
    this.stats.frames += 1;
    this.stats.bytes += buf.byteLength;
    // decodifica fora da thread principal; se outro quadro chegar no meio, fica só o mais novo
    if (this.decoding) { this.next = { header, jpeg }; return; }
    this.decode(header, jpeg);
  }

  async decode(header, jpeg) {
    this.decoding = true;
    try {
      const bmp = await createImageBitmap(new Blob([jpeg], { type: "image/jpeg" }));
      if (this.canvas.width !== bmp.width || this.canvas.height !== bmp.height) {
        this.canvas.width = bmp.width;
        this.canvas.height = bmp.height;
      }
      this.g.drawImage(bmp, 0, 0);
      bmp.close?.();
      this.size = { w: header.w, h: header.h, sw: header.sw, sh: header.sh };
      this.message("");
    } catch { /* quadro corrompido: ignora */ }
    this.live.post({ type: "screen_ack", device_id: this.deviceId, seq: header.seq });
    this.updateStats();
    this.decoding = false;
    if (this.next) { const n = this.next; this.next = null; this.decode(n.header, n.jpeg); }
  }

  updateStats() {
    const now = performance.now();
    const dt = (now - this.stats.t0) / 1000;
    if (dt >= 1) {
      this.stats.fps = this.stats.frames / dt;
      this.stats.kbps = (this.stats.bytes * 8) / 1000 / dt;
      this.stats.frames = 0; this.stats.bytes = 0; this.stats.t0 = now;
      $("#rv-stats").textContent = `${this.size.sw}×${this.size.sh} → ${this.size.w}×${this.size.h} · ${this.stats.fps.toFixed(0)} fps · ${bytes(this.stats.kbps * 125)}/s`;
    }
  }

  // ------------------------------------------------------------ entrada
  send(events) {
    if (!this.control || !this.open || !events.length) return;
    this.live.post({ type: "input", device_id: this.deviceId, events });
  }

  pos(e) {
    const r = this.canvas.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  }

  queueMove(p) {
    this.pendingMove = p;
    if (this.rafMove) return;
    this.rafMove = requestAnimationFrame(() => {
      this.rafMove = null;
      if (this.pendingMove) this.send([{ t: "move", ...this.pendingMove }]);
      this.pendingMove = null;
    });
  }

  keys(codes) {
    if (!this.control) return toast("Ligue o controle para enviar teclas.", "warning");
    const down = codes.map((code) => ({ t: "key", code, down: true }));
    const up = [...codes].reverse().map((code) => ({ t: "key", code, down: false }));
    this.send([...down, ...up]);
  }

  releaseAll() {
    if (this.held?.size) this.send([...this.held].map((code) => ({ t: "key", code, down: false })));
    this.held = new Set();
  }

  bindUI() {
    this.held = new Set();
    const c = this.canvas;
    $("#rv-close").addEventListener("click", () => this.close());
    $("#rv-quality").addEventListener("change", () => this.request());
    $("#rv-monitor").addEventListener("change", () => this.request());
    $("#rv-control").addEventListener("click", () => { this.wantControl = !this.control; this.releaseAll(); this.request(); });
    $("#rv-full").addEventListener("click", async () => {
      const el = $("#rv");
      if (document.fullscreenElement) return document.exitFullscreen();
      try {
        await el.requestFullscreen({ navigationUI: "hide" });
        await navigator.keyboard?.lock?.(); // Chrome: captura Esc/Alt+Tab no modo tela cheia
        await screen.orientation?.lock?.("landscape").catch(() => {});
      } catch { /* sem suporte */ }
    });
    $("#rv-keys").addEventListener("click", () => $("#rv-keypanel").classList.toggle("hidden"));
    $("#rv-keypanel").innerHTML = SHORTCUTS.map(([label, codes]) => `<button class="btn sm ghost" data-codes="${codes.join(",")}">${label}</button>`).join("");
    $("#rv-keypanel").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-codes]");
      if (b) this.keys(b.dataset.codes.split(","));
    });

    // mouse (desktop)
    c.addEventListener("contextmenu", (e) => e.preventDefault());
    c.addEventListener("pointermove", (e) => { if (e.pointerType === "mouse") this.queueMove(this.pos(e)); else this.touchMove(e); });
    c.addEventListener("pointerdown", (e) => {
      c.focus();
      if (e.pointerType !== "mouse") return this.touchDown(e);
      const b = ["left", "middle", "right"][e.button];
      if (b) { c.setPointerCapture(e.pointerId); this.send([{ t: "button", b, down: true, ...this.pos(e) }]); }
      e.preventDefault();
    });
    c.addEventListener("pointerup", (e) => {
      if (e.pointerType !== "mouse") return this.touchUp(e);
      const b = ["left", "middle", "right"][e.button];
      if (b) this.send([{ t: "button", b, down: false, ...this.pos(e) }]);
    });
    c.addEventListener("pointercancel", (e) => this.touchUp(e, true));
    c.addEventListener("wheel", (e) => {
      if (!this.control) return;
      e.preventDefault();
      const k = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? 800 : 1;
      this.send([{ t: "wheel", dy: Math.round(e.deltaY * k), dx: Math.round(e.deltaX * k) }]);
    }, { passive: false });

    // teclado físico: vai para o PC remoto enquanto o visualizador estiver aberto,
    // exceto quando o foco está num campo da própria barra (seleção de monitor/qualidade)
    const capturing = (e) => this.open && this.control && SUPPORTED_CODES.has(e.code)
      && !(e.target instanceof HTMLSelectElement) && e.target !== $("#rv-ime");
    document.addEventListener("keydown", (e) => {
      if (!capturing(e)) return;
      e.preventDefault();
      this.held.add(e.code);
      this.send([{ t: "key", code: e.code, down: true }]);
    }, true);
    document.addEventListener("keyup", (e) => {
      if (!capturing(e)) return;
      e.preventDefault();
      this.held.delete(e.code);
      this.send([{ t: "key", code: e.code, down: false }]);
    }, true);
    window.addEventListener("blur", () => this.releaseAll()); // trocou de janela com tecla pressionada
    // botões da barra não ficam com o foco (Enter/Espaço seguiriam para eles, não para o PC)
    $$(".rv-tools button, #rv-keypanel").forEach((b) => b.addEventListener("mouseup", () => c.focus()));

    // teclado virtual do celular: campo invisível que recebe o texto digitado
    const ime = $("#rv-ime");
    const SENTINEL = " ";
    $("#rv-kbd").addEventListener("click", () => { ime.value = SENTINEL; ime.focus(); });
    ime.addEventListener("input", (e) => {
      if (e.inputType === "deleteContentBackward") this.keys(["Backspace"]);
      else if (e.data) this.send([{ t: "text", text: e.data }]);
      else if (ime.value.length > SENTINEL.length) this.send([{ t: "text", text: ime.value.slice(SENTINEL.length) }]);
      ime.value = SENTINEL;
    });
    ime.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); this.keys(["Enter"]); }
    });

    document.addEventListener("keydown", (e) => {
      // sem controle, Esc fecha o visualizador; com controle, Esc vai para o PC (use o botão Encerrar)
      if (this.open && !this.control && e.key === "Escape" && !document.fullscreenElement) this.close();
    });
  }

  // ------------------------------------------------------------ toque (celular/tablet)
  // toque = clique · toque longo = botão direito · arrastar = mover ponteiro · dois dedos = rolagem
  touchDown(e) {
    if (!this.control) return;
    e.preventDefault();
    const t = (this.touch ??= { pts: new Map() });
    t.pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (t.pts.size === 1) {
      t.start = performance.now();
      t.origin = { x: e.clientX, y: e.clientY };
      t.moved = false;
      t.long = setTimeout(() => {
        if (!t.moved && t.pts.size === 1) {
          const p = this.pos(e);
          this.send([{ t: "button", b: "right", down: true, ...p }, { t: "button", b: "right", down: false, ...p }]);
          navigator.vibrate?.(30);
          t.done = true;
        }
      }, 550);
      t.done = false;
    } else {
      clearTimeout(t.long);
      t.scrollY = [...t.pts.values()].reduce((s, p) => s + p.y, 0) / t.pts.size;
    }
  }

  touchMove(e) {
    const t = this.touch;
    if (!this.control || !t?.pts.has(e.pointerId)) return;
    e.preventDefault();
    t.pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (t.pts.size >= 2) {
      const y = [...t.pts.values()].reduce((s, p) => s + p.y, 0) / t.pts.size;
      const dy = t.scrollY - y;
      if (Math.abs(dy) > 12) { this.send([{ t: "wheel", dy: Math.round(dy * 4), dx: 0 }]); t.scrollY = y; }
      t.moved = true;
      return;
    }
    if (Math.hypot(e.clientX - t.origin.x, e.clientY - t.origin.y) > 10) { t.moved = true; clearTimeout(t.long); }
    if (t.moved) this.queueMove(this.pos(e));
  }

  touchUp(e, cancel = false) {
    const t = this.touch;
    if (!t?.pts.has(e.pointerId)) return;
    t.pts.delete(e.pointerId);
    clearTimeout(t.long);
    if (cancel || t.pts.size) return;
    if (!t.moved && !t.done && performance.now() - t.start < 550) {
      const p = this.pos(e);
      this.send([{ t: "button", b: "left", down: true, ...p }, { t: "button", b: "left", down: false, ...p }]);
    }
    this.touch = null;
  }
}
