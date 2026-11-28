"""Assistente EINSTEIN no terminal do PC: um robozinho com rosto de tela que muda de expressão.

    einstein-agent assistente            (ou pelo menu da bandeja: "Assistente no terminal")
    einstein-agent assistente --sem-voz

Roda no próprio PC (quem digita é o dono, no teclado): executa direto, sem passar pelo servidor.
Respostas com emojis e faladas pela voz do Windows (SAPI). Desligar, reiniciar, suspender e limpar pedem "sim".
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import datetime

from . import __version__

WINDOWS = sys.platform == "win32"

# ----------------------------------------------------------------- o robô (rosto = tela com expressões)
# olhos e boca em caracteres de largura 1 (emojis ficam no balão: a largura deles varia de terminal para terminal)
FACES = {
    "feliz": ("  ◕     ◕  ", "    ╰─╯    "),
    "pensando": ("  ◔     ◔  ", "    ───    "),
    "ouvindo": ("  ●     ●  ", "     ○     "),
    "alerta": ("  ◉     ◉  ", "    ═══    "),
    "dormindo": ("  ─     ─  ", "    ‿‿‿    "),
    "legal": ("  ▀▀▀ ▀▀▀  ", "    ╰─╯    "),
    "triste": ("  ◕     ◕  ", "    ╭─╮    "),
    "piscando": ("  ◕     ─  ", "    ╰─╯    "),
}
COLORS = {"feliz": "\x1b[38;5;48m", "pensando": "\x1b[38;5;141m", "ouvindo": "\x1b[38;5;45m", "alerta": "\x1b[38;5;214m",
          "dormindo": "\x1b[38;5;245m", "legal": "\x1b[38;5;48m", "triste": "\x1b[38;5;75m", "piscando": "\x1b[38;5;48m"}
P, G, D, R = "\x1b[38;5;135m", "\x1b[38;5;48m", "\x1b[2m", "\x1b[0m"


def robot(face: str = "feliz", color: bool = True) -> list[str]:
    eyes, mouth = FACES.get(face, FACES["feliz"])
    c, p, g, r = (COLORS.get(face, G), P, G, R) if color else ("", "", "", "")
    return [
        f"{p}         ╷{r}",
        f"{p}         ●{r}",
        f"{p}  ╭──────┴──────────╮{r}",
        f"{p}  │ ╭─────────────╮ │{r}",
        f"{p}  │ │ {c}{eyes}{p} │ │{r}",
        f"{p}  │ │ {c}{mouth}{p} │ │{r}",
        f"{p}  │ ╰─────────────╯ │{r}",
        f"{p}  ╰────┬───────┬────╯{r}",
        f"{p}    ▐{g}█{p}▌│{g} ◆ ◆ ◆ {p}│▐{g}█{p}▌{r}",
        f"{p}       ╰───────╯{r}",
    ]


def _enable_ansi() -> None:
    if WINDOWS:
        try:
            import ctypes

            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            if k.GetConsoleMode(h, ctypes.byref(mode)):
                k.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            k.SetConsoleOutputCP(65001)
        except Exception:
            pass
    for s in (sys.stdout, sys.stdin):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ----------------------------------------------------------------- voz (Windows SAPI)
class Voice:
    def __init__(self, enabled: bool):
        self.enabled = enabled and WINDOWS
        self.proc: subprocess.Popen | None = None

    def say(self, text: str) -> None:
        if not self.enabled:
            return
        clean = re.sub(r"[^\w\s,.!?%:;()\-–—/°]", " ", text)
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            return
        self.stop()
        from .power import system_exe

        ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'pt*' } | Select-Object -First 1; "
              "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; $s.Rate = 1; $s.Speak($env:EINSTEIN_SAY)")
        self.proc = subprocess.Popen([system_exe("WindowsPowerShell\\v1.0\\powershell"), "-NoProfile", "-Command", ps],
                                     env=dict(os.environ, EINSTEIN_SAY=clean), creationflags=0x08000000,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # nosec B603

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()


# ----------------------------------------------------------------- inteligência (comandos em português)
def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s%]", " ", s)).strip()


NUMS = {"dez": 10, "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60, "setenta": 70,
        "oitenta": 80, "noventa": 90, "cem": 100, "metade": 50, "maximo": 100, "zero": 0, "cinco": 5, "quinze": 15}


def number(t: str) -> int | None:
    m = re.search(r"\d{1,3}", t)
    if m:
        return int(m.group())
    for w, v in NUMS.items():
        if re.search(rf"\b{w}\b", t):
            return v
    return None


def gb(n: float) -> str:
    return f"{n / 2**30:.1f} GB" if n >= 2**30 else f"{n / 2**20:.0f} MB"


class Brain:
    """Entende o pedido e executa no próprio PC. Devolve (expressão, resposta)."""

    def __init__(self):
        self.pending = None  # (descrição, função) aguardando "sim"
        self._collector = None

    def metrics(self) -> dict:
        from .metrics import MetricsCollector

        if self._collector is None:
            self._collector = MetricsCollector()
            time.sleep(0.6)
        return self._collector.collect()

    def ask(self, text: str, run) -> tuple[str, str]:
        self.pending = (time.time(), run)
        return "alerta", f"⚠️ {text}\n   Digite \"sim\" ✅ para confirmar ou \"não\" ❌."

    def handle(self, raw: str) -> tuple[str, str]:
        t = norm(raw)
        if self.pending and time.time() - self.pending[0] < 120:
            if re.match(r"(sim|s|confirmo|pode|ok|bora|manda)\b", t):
                run = self.pending[1]
                self.pending = None
                return run()
            if re.match(r"(nao|n|cancela|deixa)\b", t):
                self.pending = None
                return "feliz", "👍 Cancelado, nada foi feito."
        self.pending = None

        if re.match(r"(oi|ola|e ai|eai|opa|hey|bom dia|boa tarde|boa noite|einstein)\b", t) and len(t.split()) <= 4:
            return "feliz", "👋 Oi! Tô aqui, pronto. 🤖⚡ O que vamos fazer? (digite \"ajuda\")"
        if re.search(r"\b(ajuda|comandos|o que (voce )?(sabe|faz))\b", t):
            return "feliz", ("🧠 Eu sei:\n"
                             "📊 status · cpu · memória · disco · temperatura\n"
                             "🪟 programas abertos · 🔎 o que está pesando\n"
                             "🔊 volume 40 · aumentar/diminuir volume · silenciar\n"
                             "🎮 ligar/desligar modo jogo · 🚀 modo desempenho/economia/equilibrado\n"
                             "🧹 limpar disco · 🔒 bloquear\n"
                             "⏻ desligar em 10 minutos · 🔄 reiniciar · 🌙 suspender · ❎ cancelar desligamento\n"
                             "🌐 abrir painel · 🕒 que horas são · 🔈 voz (liga/desliga a fala) · 👋 sair")
        if re.search(r"\b(obrigad|valeu|vlw)\b", t):
            return "legal", "😎 Tamo junto! Sempre às ordens. ⚡"
        if re.search(r"\b(que horas|hora agora|que dia)\b", t):
            n = datetime.now()
            dias = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
            return "feliz", f"🕒 São {n:%H:%M} — {dias[n.weekday()]}, {n:%d/%m/%Y}. 📅"
        if re.search(r"\b(abr\w* o? ?painel|painel)\b", t):
            from .config import AgentConfig

            url = AgentConfig.load().server_url
            if not url:
                return "triste", "😕 Este PC ainda não foi vinculado a um painel."
            import webbrowser

            webbrowser.open(url)
            return "feliz", f"🌐 Abrindo o painel: {url}"

        from . import audio, maintenance, power

        # energia
        if re.search(r"\bcancel\w*\b.*\b(deslig|reinici|agend)", t):
            ok = power.cancel_native()
            return ("feliz", "✅ Cancelado! O PC continua ligado. 😮‍💨") if ok else ("feliz", "👌 Não havia nada agendado.")
        if re.search(r"\bbloque\w*\b", t):
            power.lock()
            return "dormindo", "🔒 Tela bloqueada. Até já! 👋"
        m = re.search(r"\b(deslig\w*|reinici\w*|suspend\w*|hibern\w*)\b", t)
        if m and "modo" not in t:
            w = m.group(1)
            action = "shutdown" if w.startswith("deslig") else "restart" if w.startswith("reinici") else "hibernate" if w.startswith("hibern") else "sleep"
            n = number(t)
            unit = 3600 if "hora" in t else 1 if "segund" in t else 60
            delay = min(n * unit, 86400) if n is not None else (60 if action in ("shutdown", "restart") else 0)
            label = {"shutdown": "⏻ Desligar", "restart": "🔄 Reiniciar", "sleep": "🌙 Suspender", "hibernate": "💤 Hibernar"}[action]
            when = "agora" if delay < 5 else f"em {delay} s" if delay < 120 else f"em {round(delay / 60)} min"

            def run():
                if action in ("shutdown", "restart"):
                    power.schedule_native(action, delay, "EINSTEIN REMOTE: pedido no assistente do terminal.")
                    return "feliz", f"✅ {label[2:]} {when}. Para desistir: \"cancelar desligamento\". ⏳"
                if delay:
                    time.sleep(delay)
                power.suspend(action == "hibernate")
                return "dormindo", "😴 Voltei! Bom te ver de novo. 👋"

            return self.ask(f"{label} este PC {when}? Programas abertos podem ser fechados.", run)

        # volume
        if re.search(r"\b(volume|som|audio|silenci\w*|mudo|mutar|desmut\w*)\b", t):
            if re.search(r"\b(tira\w*|desativ\w*|volta\w*)\b.*\b(mudo|silencio)|\bdesmut", t):
                r = audio.change({"muted": False}, local=True)
            elif re.search(r"\b(silenci\w*|mudo|mutar)\b", t):
                r = audio.change({"muted": True}, local=True)
            elif re.search(r"\b(aument\w*|sobe|subir|mais)\b", t):
                r = audio.change({"step": number(t) or 10}, local=True)
            elif re.search(r"\b(diminu\w*|abaix\w*|baix\w*|menos)\b", t):
                r = audio.change({"step": -(number(t) or 10)}, local=True)
            elif number(t) is not None:
                r = audio.change({"volume": min(100, number(t))}, local=True)
            else:
                r = audio.status()
            if r["muted"]:
                return "dormindo", f"🔇 Som desligado (volume em {r['volume']}%)."
            return "feliz", f"{'🔈' if r['volume'] == 0 else '🔉' if r['volume'] < 50 else '🔊'} Volume em {r['volume']}%."

        # modo jogo / energia
        if re.search(r"\bmodo (de )?jogo\b", t):
            off = re.search(r"\b(deslig\w*|desativ\w*|tira\w*|para\w*)\b", t)
            maintenance.configure_game({"auto": not off}, local=True)
            return ("feliz", "🎮❌ Modo jogo automático desligado.") if off else \
                ("legal", "🎮✅ Modo jogo automático ligado! Abriu um jogo, vou para Melhor desempenho. 🔥")
        m = re.search(r"\bmodo (desempenho|performance|turbo|economia|economico|equilibrado|balanceado)\b", t)
        if m:
            mode = "desempenho" if re.search("desemp|perform|turbo", m.group(1)) else "economia" if "econom" in m.group(1) else "equilibrado"
            maintenance.set_mode(mode)
            return "legal", {"desempenho": "🚀 Melhor desempenho ativado!", "economia": "🔋 Economia de energia ativada.",
                             "equilibrado": "⚖️ Modo equilibrado ativado."}[mode]

        # limpeza
        if re.search(r"\b(limp\w*|faxina)\b", t):
            s = maintenance.scan()
            cats = [c for c in s["categories"] if c["bytes"] > 0]
            if not cats:
                return "legal", "✨ Já está tudo limpo! 🧼"
            lista = "\n   ".join(f"• {c['label']}: {gb(c['bytes'])}" for c in cats)

            def run():
                r = maintenance.clean([c["id"] for c in cats], local=True)
                return "legal", f"🧹✅ {gb(r['freed'])} liberados ({r['files']} arquivos).{' ' + str(r['skipped']) + ' em uso ficaram.' if r['skipped'] else ''} 🎉"

            return self.ask(f"🧹 Posso liberar {gb(s['total'])}:\n   {lista}", run)

        # informações
        from . import sysmon

        if re.search(r"\b(programas|apps|aplicativos|janelas)\b.*\b(abert\w*|rodando)\b|\bo que (esta|ta) aberto\b", t):
            r = sysmon.open_apps()
            if not r.get("supported"):
                return "triste", f"🤷 {r.get('reason')}"
            apps = r["apps"]
            if not apps:
                return "feliz", "🪟 Nenhum programa com janela aberta."
            return "feliz", f"🪟 {len(apps)} programa(s) aberto(s):\n   " + "\n   ".join(
                f"• {a['name'] or '?'}{' (' + gb(a['memory']) + ')' if a.get('memory') else ''}" for a in apps[:8])
        if re.search(r"\b(pesando|consumindo|travando|lento|lerdo)\b", t):
            sysmon.SAMPLER.snapshot()
            time.sleep(1.0)
            procs = sysmon.SAMPLER.snapshot()["processes"][:3]
            return "pensando", "🔎 O que mais está usando o PC:\n   " + "\n   ".join(
                f"• {p['name']}: {p['cpu']}% CPU, {gb(p['memory'])}" for p in procs)
        if re.search(r"\b(cpu|processador|memoria|ram|disco|espaco|temperatura|quente|status|como (esta|ta)|resumo|pc|computador)\b", t):
            m = self.metrics()
            cpu, ram = m["cpu"]["percent"], m["memory"]["percent"]
            temp = (m.get("temperatures") or {}).get("max")
            sysd = next((d for d in m.get("disks", []) if d.get("system")), (m.get("disks") or [None])[0])
            ok = cpu < 85 and ram < 90 and (temp is None or temp < 85)
            lines = [f"{'✅' if ok else '⚠️'} Status do PC:",
                     f"   🧠 CPU {cpu:.0f}%   💾 RAM {ram:.0f}% ({gb(m['memory']['used'])} de {gb(m['memory']['total'])})"]
            if temp is not None:
                lines.append(f"   🌡️ {temp:.0f}°C {'🔥' if temp >= 85 else '♨️' if temp >= 70 else '❄️'}")
            if sysd:
                lines.append(f"   💽 {gb(sysd['free'])} livres no disco {sysd['mount']}")
            lines.append("   😎 Tudo tranquilo." if ok else "   🔎 Digite \"o que está pesando\" que eu investigo.")
            return ("feliz" if ok else "alerta"), "\n".join(lines)
        return "triste", f"🤔 Não entendi \"{raw}\". Digite \"ajuda\" para ver o que eu sei."


# ----------------------------------------------------------------- tela
def draw(face: str, history: list[tuple[str, str]], voice_on: bool) -> None:
    width = shutil.get_terminal_size((80, 24)).columns
    out = ["\x1b[2J\x1b[H"]
    title = f"{P}EINSTEIN{R} {G}assistente{R} {D}· v{__version__} · voz {'ligada 🔊' if voice_on else 'desligada 🔇'} · \"sair\" para fechar{R}"
    out.append(title)
    out.append("")
    out += robot(face)
    out.append("")
    for who, text in history[-6:]:
        if who == "me":
            out.append(f"{D}você ›{R} {text}")
        else:
            for i, line in enumerate(text.split("\n")):
                out.append(f"{G}🤖 ›{R} {line}" if i == 0 else f"     {line}")
        out.append("")
    sys.stdout.write("\n".join(o[: width * 3] for o in out) + "\n")
    sys.stdout.flush()


def run(voice: bool = True) -> int:
    _enable_ansi()
    brain = Brain()
    speaker = Voice(voice)
    history: list[tuple[str, str]] = []
    hello = "👋 Oi! Eu sou o EINSTEIN, seu assistente aqui no PC. 🤖⚡ Digite um comando — ex.: \"status\", \"volume 30\" ou \"ajuda\"."
    history.append(("bot", hello))
    draw("piscando", history, speaker.enabled)
    time.sleep(0.25)
    draw("feliz", history, speaker.enabled)
    speaker.say(hello)
    while True:
        try:
            text = input(f"{P}você ›{R} ").strip()
        except (EOFError, KeyboardInterrupt):
            text = "sair"
        if not text:
            continue
        if norm(text) in ("sair", "tchau", "fechar", "exit", "quit"):
            speaker.say("Até mais!")
            draw("dormindo", history + [("me", text), ("bot", "👋 Até mais! Estarei por aqui na bandeja. 😴")], speaker.enabled)
            time.sleep(0.8)
            speaker.stop()
            return 0
        history.append(("me", text))
        if norm(text) in ("voz", "voz ligar", "voz desligar", "silenciar voz"):
            speaker.enabled = WINDOWS and not speaker.enabled
            speaker.stop()
            history.append(("bot", "🔊 Voz ligada!" if speaker.enabled else "🔇 Voz desligada — só texto."))
            draw("feliz", history, speaker.enabled)
            continue
        draw("pensando", history, speaker.enabled)
        try:
            face, reply = brain.handle(text)
        except Exception as exc:
            face, reply = "triste", f"⚠️ Não consegui: {exc}"
        history.append(("bot", reply))
        draw(face, history, speaker.enabled)
        speaker.say(reply)
