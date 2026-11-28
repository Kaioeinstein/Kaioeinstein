// Assistente EINSTEIN: fale ou digite; responde com emojis (e em voz alta) e executa comandos reais no PC.
// Voz: reconhecimento e síntese do próprio navegador (pt-BR) — nada sai do seu painel/servidor para terceiros
// além do que o navegador já usa no reconhecimento de voz. Ações críticas sempre pedem confirmação.
import { $, bytes, esc } from "./util.js";

const norm = (s) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^\w\s%]/g, " ").replace(/\s+/g, " ").trim();
const NUMS = { zero: 0, dez: 10, vinte: 20, trinta: 30, quarenta: 40, cinquenta: 50, sessenta: 60, setenta: 70, oitenta: 80, noventa: 90, cem: 100, metade: 50, maximo: 100, um: 1, dois: 2, tres: 3, cinco: 5, quinze: 15 };
const num = (t) => { const m = t.match(/(\d{1,3})/); if (m) return Number(m[1]); for (const [w, v] of Object.entries(NUMS)) if (new RegExp(`\\b${w}\\b`).test(t)) return v; return null; };
const stripEmoji = (s) => s.replace(/[\p{Extended_Pictographic}\u{FE0F}\u{200D}]/gu, "").replace(/\s+/g, " ").trim();
const VIEWS = { monitor: "monitor", processos: "monitor", arquivos: "files", otimizar: "optimize", ajustes: "settings", configuracoes: "settings",
  alertas: "notifications", notificacoes: "notifications", dispositivos: "devices", pcs: "devices", painel: "dashboard", inicio: "dashboard", seguranca: "security" };

export class Assistant {
  /** @param {{device: () => any, live: () => any, go: (v: string) => void, user: () => any, openScreen: () => void}} ctx */
  constructor(ctx) {
    this.ctx = ctx;
    this.pending = null;
    this.speak = localStorage.getItem("er.ai.voice") !== "0";
    this.listening = false;
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    this.rec = SR ? new SR() : null;
    if (this.rec) {
      this.rec.lang = "pt-BR";
      this.rec.interimResults = true;
      this.rec.maxAlternatives = 1;
      this.rec.onresult = (e) => {
        const r = e.results[e.results.length - 1];
        $("#ai-input").value = r[0].transcript;
        if (r.isFinal) { this.stopListening(); this.submit(); }
      };
      this.rec.onerror = (e) => {
        this.stopListening();
        if (e.error === "not-allowed") this.say("🎙️❌ O navegador bloqueou o microfone. Toque no cadeado da barra de endereço › Permissões › Microfone › Permitir.");
        else if (e.error !== "aborted" && e.error !== "no-speech") this.say(`🎙️ Não consegui ouvir (${e.error}). Tente de novo ou digite.`);
      };
      this.rec.onend = () => this.stopListening();
    }
    $("#ai-orb").addEventListener("click", () => this.toggle());
    $("#ai-close").addEventListener("click", () => this.toggle(false));
    $("#ai-form").addEventListener("submit", (e) => { e.preventDefault(); this.submit(); });
    $("#ai-mic").addEventListener("click", () => (this.listening ? this.stopListening(true) : this.listen()));
    $("#ai-mic").classList.toggle("hidden", !this.rec);
    $("#ai-voice").addEventListener("click", () => {
      this.speak = !this.speak;
      localStorage.setItem("er.ai.voice", this.speak ? "1" : "0");
      this.syncVoice();
      if (!this.speak) speechSynthesis?.cancel();
    });
    $("#ai-chips").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-say]");
      if (b) { $("#ai-input").value = b.dataset.say; this.submit(); }
    });
    this.syncVoice();
  }

  syncVoice() {
    $("#ai-voice").textContent = this.speak ? "🔊" : "🔇";
    $("#ai-voice").setAttribute("aria-label", this.speak ? "Desligar voz" : "Ligar voz");
  }

  toggle(open) {
    const panel = $("#ai-panel");
    const show = open ?? panel.classList.contains("hidden");
    panel.classList.toggle("hidden", !show);
    $("#ai-orb").classList.toggle("open", show);
    document.body.classList.toggle("ai-open", show);
    if (show && !this.greeted) {
      this.greeted = true;
      const name = this.ctx.user()?.username || "";
      const h = new Date().getHours();
      const hi = h < 12 ? "Bom dia" : h < 18 ? "Boa tarde" : "Boa noite";
      this.say(`👋 ${hi}${name ? `, ${name}` : ""}! Sou o EINSTEIN, seu assistente. 🧠⚡ Fale 🎙️ ou digite — por exemplo: "como está o PC?" ou "volume 30".`);
    }
    if (show) setTimeout(() => $("#ai-input").focus(), 50);
  }

  listen() {
    if (!this.rec || this.listening) return;
    speechSynthesis?.cancel();
    try { this.rec.start(); } catch { return; }
    this.listening = true;
    $("#ai-mic").classList.add("on");
    $("#ai-orb").classList.add("listening");
    $("#ai-input").placeholder = "Ouvindo… 🎙️";
  }

  stopListening(abort = false) {
    if (abort) try { this.rec?.abort(); } catch { /* já parou */ }
    this.listening = false;
    $("#ai-mic").classList.remove("on");
    $("#ai-orb").classList.remove("listening");
    $("#ai-input").placeholder = "Fale ou digite um comando…";
  }

  add(who, text) {
    const el = document.createElement("div");
    el.className = `ai-msg ${who}`;
    el.innerHTML = esc(text).replace(/\n/g, "<br>");
    $("#ai-log").appendChild(el);
    $("#ai-log").scrollTop = $("#ai-log").scrollHeight;
  }

  say(text) {
    this.add("bot", text);
    if (this.speak && window.speechSynthesis) {
      const u = new SpeechSynthesisUtterance(stripEmoji(text));
      u.lang = "pt-BR";
      const v = speechSynthesis.getVoices().find((x) => x.lang?.toLowerCase().startsWith("pt-br")) ||
        speechSynthesis.getVoices().find((x) => x.lang?.toLowerCase().startsWith("pt"));
      if (v) u.voice = v;
      u.rate = 1.05;
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
    }
  }

  async submit() {
    const text = $("#ai-input").value.trim();
    if (!text) return;
    $("#ai-input").value = "";
    this.add("me", text);
    $("#ai-orb").classList.add("thinking");
    try {
      const reply = await this.handle(text);
      if (reply) this.say(reply);
    } catch (e) {
      this.say(`⚠️ ${e.message}`);
    } finally {
      $("#ai-orb").classList.remove("thinking");
    }
  }

  // ---------------------------------------------------------------- comandos
  dev() {
    const d = this.ctx.device();
    if (!d) throw new Error("Nenhum computador vinculado ainda. 💻➕ Vá em Dispositivos › Gerar código.");
    return d;
  }

  async cmd(action, params = {}, confirm = false) {
    const d = this.dev();
    if (!d.online) throw new Error(`😴 ${d.name} está offline agora.`);
    return this.ctx.live().command(d.id, action, params, { confirm, timeout: 120000 });
  }

  ask(label, run) {
    this.pending = { run, at: Date.now() };
    return `⚠️ ${label}\nDiga "sim" ✅ para confirmar ou "não" ❌ para cancelar.`;
  }

  async handle(raw) {
    const t = norm(raw);

    // confirmação pendente (vale 60 s)
    if (this.pending && Date.now() - this.pending.at < 60000) {
      if (/^(sim|s|confirmo|confirmar|pode|pode sim|ok|isso|manda ver|bora)\b/.test(t)) { const p = this.pending; this.pending = null; return p.run(); }
      if (/^(nao|n|cancela|cancelar|negativo|deixa)\b/.test(t)) { this.pending = null; return "👍 Cancelado, nada foi feito."; }
    }
    this.pending = null;

    if (/^(oi|ola|e ai|eai|hey|opa|bom dia|boa tarde|boa noite|einstein)\b/.test(t) && t.split(" ").length <= 4) {
      return `👋 Olá! Tudo pronto por aqui. 🚀 O que vamos fazer? (diga "ajuda" para ver tudo que eu sei fazer)`;
    }
    if (/\b(ajuda|o que (voce )?(sabe|faz|pode)|comandos)\b/.test(t)) {
      return "🧠 Eu sei:\n📊 \"como está o PC?\" · \"CPU\" · \"memória\" · \"disco\" · \"temperatura\"\n🪟 \"quais programas estão abertos?\" · \"o que está pesando?\"\n🔊 \"volume 40\" · \"aumentar/diminuir volume\" · \"silenciar\"\n🎮 \"ligar modo jogo\" · \"modo desempenho\" · \"modo economia\"\n🧹 \"limpar disco\"\n🔒 \"bloquear o PC\" · ⏻ \"desligar em 10 minutos\" · 🔄 \"reiniciar\" · 🌙 \"suspender\" · \"cancelar desligamento\"\n🖥️ \"ver a tela\" · 📂 \"abrir arquivos/monitor/otimizar/ajustes\"\n⏱️ \"latência\" · 🕒 \"que horas são?\"";
    }
    if (/\b(obrigad|valeu|vlw|show|top)\b/.test(t)) return "😊 Sempre às ordens! ⚡";
    if (/\b(que horas|hora agora|que dia|data de hoje)\b/.test(t)) {
      const now = new Date();
      return `🕒 Agora são ${now.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })} — ${now.toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long" })}. 📅`;
    }

    // navegação
    const nav = t.match(/\b(abr\w*|mostr\w*|ir para|vai para|va para|entra\w*)\b.*\b(monitor|processos|arquivos|otimizar|ajustes|configuracoes|alertas|notificacoes|dispositivos|pcs|painel|inicio|seguranca)\b/);
    if (nav) { this.ctx.go(VIEWS[nav[2]]); return `📂 Abrindo ${nav[2]}… ✨`; }
    if (/\b(ver|abrir|mostrar|acessar|compartilhar)\b.*\btela\b/.test(t)) {
      this.dev();
      this.ctx.openScreen();
      return "🖥️ Abrindo a tela do PC… (no computador aparece o aviso de tela compartilhada) 👀";
    }

    // energia (sempre com confirmação)
    if (/\bcancel\w*\b.*\b(deslig|reinici|suspens|hibern|agend)/.test(t)) {
      const r = await this.cmd("power.cancel");
      return r.canceled ? "✅ Cancelado! O PC continua ligado. 😮‍💨" : "👌 Não havia nada agendado.";
    }
    if (/\bbloque\w*\b/.test(t) && /\b(pc|computador|tela|maquina|notebook)\b/.test(t)) {
      await this.cmd("power.lock");
      return "🔒 Pronto, tela do PC bloqueada.";
    }
    const pw = t.match(/\b(deslig\w*|reinici\w*|suspend\w*|hibern\w*)\b/);
    if (pw && !/\bmodo\b/.test(t)) {
      const action = pw[1].startsWith("deslig") ? "shutdown" : pw[1].startsWith("reinici") ? "restart" : pw[1].startsWith("hibern") ? "hibernate" : "sleep";
      const n = num(t);
      const unit = /\bhora/.test(t) ? 3600 : /\bsegund/.test(t) ? 1 : 60;
      const delay = n != null ? Math.min(n * unit, 86400) : (action === "shutdown" || action === "restart" ? 60 : 0);
      const label = { shutdown: "⏻ Desligar", restart: "🔄 Reiniciar", sleep: "🌙 Suspender", hibernate: "💤 Hibernar" }[action];
      const when = delay < 5 ? "agora" : delay < 120 ? `em ${delay} s` : `em ${Math.round(delay / 60)} min`;
      const d = this.dev();
      return this.ask(`${label} "${d.name}" ${when}?${action === "sleep" || action === "hibernate" ? " Você perde o acesso pelo celular até ele acordar." : " Programas abertos serão fechados."}`,
        async () => {
          await this.cmd(`power.${action}`, { delay }, true);
          return `✅ Feito: ${label.slice(2).toLowerCase()} ${when}. Para desistir, diga "cancelar desligamento". ⏳`;
        });
    }

    // volume
    if (/\b(volume|som|audio)\b/.test(t) || /\b(silenci|mudo|mutar|desmut)\w*/.test(t)) {
      let r;
      if (/\b(tira\w*|desativ\w*|desmut\w*|volta\w*)\b.*\b(mudo|silencio)|\bdesmut/.test(t)) r = await this.cmd("audio.set", { muted: false });
      else if (/\b(silenci\w*|mudo|mutar|muta)\b/.test(t)) r = await this.cmd("audio.set", { muted: true });
      else if (/\b(aument\w*|sobe|subir|mais alto|mais)\b/.test(t)) r = await this.cmd("audio.set", { step: num(t) ?? 10 });
      else if (/\b(diminu\w*|abaix\w*|baix\w*|menos)\b/.test(t)) r = await this.cmd("audio.set", { step: -(num(t) ?? 10) });
      else if (num(t) != null) r = await this.cmd("audio.set", { volume: Math.min(100, num(t)) });
      else r = await this.cmd("audio.status");
      return r.muted ? `🔇 Som desligado (volume em ${r.volume}%).` : `${r.volume === 0 ? "🔈" : r.volume < 50 ? "🔉" : "🔊"} Volume em ${r.volume}%.`;
    }

    // modo jogo / energia
    if (/\bmodo (de )?jogo\b|\bgame ?mode\b/.test(t)) {
      const off = /\b(deslig\w*|desativ\w*|tira\w*|para\w*)\b/.test(t);
      const r = await this.cmd("perf.game", { auto: !off });
      return off ? "🎮❌ Modo jogo automático desligado." : `🎮✅ Modo jogo automático ligado! Abriu um jogo, o PC vai para Melhor desempenho. ${r.state?.active ? `(já detectei: ${r.state.game} 🔥)` : "🚀"}`;
    }
    const mode = t.match(/\bmodo (desempenho|performance|turbo|economia|economico|equilibrado|balanceado)\b/);
    if (mode) {
      const m = /desemp|perform|turbo/.test(mode[1]) ? "desempenho" : /econom/.test(mode[1]) ? "economia" : "equilibrado";
      await this.cmd("perf.mode", { mode: m });
      return { desempenho: "🚀 Modo Melhor desempenho ativado!", economia: "🔋 Modo economia de energia ativado.", equilibrado: "⚖️ Modo equilibrado ativado." }[m];
    }

    // limpeza (com confirmação)
    if (/\b(limp\w*|faxina)\b/.test(t)) {
      const s = await this.cmd("maint.scan");
      const ids = s.categories.filter((c) => c.bytes > 0).map((c) => c.id);
      if (!ids.length) return "✨ Já está tudo limpo! Nada para apagar. 🧼";
      const list = s.categories.filter((c) => c.bytes > 0).map((c) => `• ${c.label}: ${bytes(c.bytes)}`).join("\n");
      return this.ask(`🧹 Posso liberar ${bytes(s.total)}:\n${list}`, async () => {
        const r = await this.cmd("maint.clean", { categories: ids }, true);
        return `🧹✅ Limpeza feita: ${bytes(r.freed)} liberados (${r.files} arquivos).${r.skipped ? ` ${r.skipped} estavam em uso e ficaram.` : ""} 🎉`;
      });
    }

    // informações
    if (/\b(programas|apps|aplicativos|janelas)\b.*\b(abert\w*|rodando|executando)\b|\bo que (esta|ta) aberto\b/.test(t)) {
      const r = await this.cmd("system.apps");
      if (!r.supported) return `🤷 ${r.reason || "Não consigo listar as janelas neste sistema."}`;
      if (!r.apps.length) return "🪟 Nenhum programa com janela aberta agora.";
      const names = r.apps.slice(0, 8).map((a) => `• ${a.name || "?"}${a.memory ? ` (${bytes(a.memory)})` : ""}`).join("\n");
      return `🪟 ${r.apps.length} programa(s) aberto(s):\n${names}${r.apps.length > 8 ? "\n…" : ""}`;
    }
    if (/\b(pesando|consumindo|travando|lento|lerdo|gastando)\b/.test(t)) {
      const r = await this.cmd("system.processes");
      const top = r.processes.slice(0, 3).map((p) => `• ${p.name}: ${p.cpu}% CPU, ${bytes(p.memory)}`).join("\n");
      return `🔎 O que mais está usando o PC agora:\n${top}\n💡 Dica: diga "abrir monitor" para encerrar algum.`;
    }
    if (/\b(latencia|ping|velocidade da conexao)\b/.test(t)) {
      const t0 = performance.now();
      await this.cmd("ping");
      const ms = Math.round(performance.now() - t0);
      return `⏱️ Ida e volta até o PC: ${ms} ms ${ms < 100 ? "⚡ rapidinho!" : ms < 300 ? "👍" : "🐢"}`;
    }
    const m = this.dev().metrics;
    const d = this.dev();
    const needM = () => { if (!d.online || !m) throw new Error(`😴 ${d.name} está offline — sem dados agora.`); };
    if (/\b(cpu|processador)\b/.test(t)) { needM(); return `🧠 CPU em ${Math.round(m.cpu.percent)}%${m.cpu.freq_mhz ? ` a ${(m.cpu.freq_mhz / 1000).toFixed(1)} GHz` : ""}. ${m.cpu.percent > 85 ? "🔥 Bem puxada!" : "✅"}`; }
    if (/\b(memoria|ram)\b/.test(t)) { needM(); return `💾 Memória em ${Math.round(m.memory.percent)}% (${bytes(m.memory.used)} de ${bytes(m.memory.total)}). ${m.memory.percent > 90 ? "⚠️ Quase cheia — diga \"o que está pesando?\"" : "✅"}`; }
    if (/\b(disco|armazenamento|espaco|hd|ssd)\b/.test(t)) {
      needM();
      return `💽 ${m.disks.map((x) => `${x.mount}: ${bytes(x.free)} livres (${Math.round(x.percent)}% usado)`).join(" · ")}${m.disks.some((x) => x.percent > 90) ? "\n🧹 Diga \"limpar disco\" para liberar espaço." : ""}`;
    }
    if (/\b(temperatura|quente|esquent\w*|calor)\b/.test(t)) {
      needM();
      const tt = m.temperatures?.max;
      return tt == null ? "🌡️ Este PC não informa temperatura (sensor indisponível)." : `🌡️ Temperatura máxima: ${Math.round(tt)}°C ${tt >= 85 ? "🔥 Muito quente!" : tt >= 70 ? "♨️ Morno." : "❄️ Tranquilo."}`;
    }
    if (/\b(como (esta|ta|anda)|status|resumo|situacao|tudo bem)\b/.test(t) || /\b(pc|computador|notebook|maquina)\b/.test(t)) {
      if (!d.online) return `😴 ${d.name} está offline agora.`;
      needM();
      const gpu = m.gpu?.[0];
      const tt = m.temperatures?.max;
      const sys = m.disks?.find((x) => x.system) || m.disks?.[0];
      const ok = m.cpu.percent < 85 && m.memory.percent < 90 && (tt == null || tt < 85);
      return `${ok ? "✅" : "⚠️"} ${d.name} online!\n🧠 CPU ${Math.round(m.cpu.percent)}% · 💾 RAM ${Math.round(m.memory.percent)}%${gpu?.util != null ? ` · 🎮 GPU ${Math.round(gpu.util)}%` : ""}${tt != null ? ` · 🌡️ ${Math.round(tt)}°C` : ""}${sys ? `\n💽 ${bytes(sys.free)} livres no disco` : ""}\n${ok ? "😎 Tudo tranquilo." : "🔎 Diga \"o que está pesando?\" para eu investigar."}`;
    }
    return `🤔 Não entendi "${raw}". Diga "ajuda" 🧠 para ver o que eu sei fazer.`;
  }
}
