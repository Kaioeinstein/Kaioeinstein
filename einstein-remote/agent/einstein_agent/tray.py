"""Ícone na bandeja do sistema — a forma *visível* de operar o agente.

O proprietário vê sempre o estado (cor do ícone + dica), e pode pausar,
retomar, abrir o painel, ligar/desligar o início automático, ver o log,
desvincular o computador ou encerrar o agente.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__, autostart, dialogs
from .client import Status
from .config import AgentConfig, config_dir
from .icons import make_icon
from .logs import log_file, setup_logging
from .pairing import unpair
from .runner import AgentRunner
from .singleton import SingleInstance

log = logging.getLogger("einstein.tray")
TITLE = "EINSTEIN REMOTE"

STATE_TEXT = {
    "starting": "Iniciando…",
    "connecting": "Conectando…",
    "online": "Online",
    "offline": "Sem conexão — tentando novamente",
    "paused": "Pausado por você",
    "rejected": "Acesso recusado — vincule novamente",
    "unpaired": "Não vinculado",
    "stopped": "Parado",
}


def open_path(path: Path) -> None:
    """Abre arquivo/pasta no aplicativo padrão do sistema."""
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - caminho local do próprio agente
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class TrayApp:
    def __init__(self, backend_icon_cls=None):
        import pystray

        self.pystray = pystray
        self.runner = AgentRunner(on_status=self._on_status)
        self.status = Status("starting")
        self._was_online = False
        self._lock = threading.Lock()
        icon_cls = backend_icon_cls or pystray.Icon
        self.icon = icon_cls("einstein-remote", make_icon("starting"), TITLE, menu=self._menu())

    # -- estado -------------------------------------------------------------------
    @property
    def cfg(self) -> AgentConfig:
        return AgentConfig.load()

    def status_text(self) -> str:
        text = STATE_TEXT.get(self.status.state, self.status.state)
        if self.status.state == "online" and self.status.detail:
            text += f" — {self.status.detail}"
        return text

    def tooltip(self) -> str:
        return f"{TITLE} · {self.status_text()}"[:120]  # limite do Windows: 127

    def _on_status(self, st: Status) -> None:
        with self._lock:
            prev = self.status
            self.status = st
        try:  # falha ao redesenhar o ícone nunca impede avisos e menu
            self.icon.icon = make_icon(st.state)
            self.icon.title = self.tooltip()
        except Exception:
            log.debug("Não foi possível atualizar o ícone", exc_info=True)
        self._refresh_menu()
        if st.state == "online" and not self._was_online:
            self._was_online = True
            self.notify("Conectado. Este computador está visível no seu painel EINSTEIN REMOTE.")
        elif st.state == "rejected" and prev.state != "rejected":
            self.notify("O servidor recusou este computador (vínculo revogado). Use “Vincular este computador…”.")
        elif st.state == "offline" and prev.state == "online":
            self.notify("Conexão com o servidor perdida. Tentando reconectar…")
        if st.state != "online" and prev.state == "online":
            self._was_online = False

    def _refresh_menu(self) -> None:
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def notify(self, message: str) -> None:
        log.info("Aviso: %s", message)
        try:
            if getattr(self.icon, "HAS_NOTIFICATION", False):
                self.icon.notify(message, TITLE)
        except Exception:
            pass

    # -- menu ---------------------------------------------------------------------
    def _menu(self):
        M = self.pystray.MenuItem
        SEP = self.pystray.Menu.SEPARATOR
        paired = lambda item=None: self.cfg.paired  # noqa: E731
        return self.pystray.Menu(
            M(lambda item: self.status_text(), None, enabled=False),
            M(lambda item: f"Computador: {self.cfg.device_name or '—'}", None, enabled=False, visible=paired),
            SEP,
            M("Abrir painel", self.open_dashboard, default=True, enabled=paired),
            M(lambda item: "Retomar monitoramento" if self.cfg.paused else "Pausar monitoramento", self.toggle_pause,
              visible=lambda item: self.cfg.paired and self.status.state != "rejected"),
            M("Vincular este computador…", self.pair,
              visible=lambda item: not self.cfg.paired or self.status.state == "rejected"),
            M("Iniciar com o Windows", self.toggle_autostart, checked=lambda item: autostart.is_enabled(),
              visible=sys.platform == "win32"),
            M("Permitir encerrar processos pelo painel", self.toggle_process_kill,
              checked=lambda item: self.cfg.allow_process_kill, visible=paired),
            SEP,
            M("Ver registro (log)", self.open_log),
            M("Abrir pasta de configuração", self.open_config),
            M("Desvincular este computador…", self.unpair, visible=paired),
            SEP,
            M(f"Sobre (v{__version__})", self.about),
            M("Sair", self.quit),
        )

    # -- ações --------------------------------------------------------------------
    def open_dashboard(self, *_):
        url = self.cfg.server_url
        if url:
            webbrowser.open(url)

    def toggle_pause(self, *_):
        paused = not self.cfg.paused
        self.runner.set_paused(paused)
        log.info("Monitoramento %s pelo proprietário (bandeja)", "pausado" if paused else "retomado")
        if not self.runner.running:
            self.runner.start()
        self._refresh_menu()

    def toggle_autostart(self, *_):
        try:
            if autostart.is_enabled():
                autostart.disable()
            else:
                autostart.enable(tray=True)
        except Exception as exc:
            dialogs.info(TITLE, f"Não foi possível alterar o início automático:\n{exc}", warning=True)
        self._refresh_menu()

    def toggle_process_kill(self, *_):
        cfg = self.cfg
        cfg.allow_process_kill = not cfg.allow_process_kill
        cfg.save()
        log.info("Encerramento remoto de processos %s pelo proprietário (bandeja)",
                 "permitido" if cfg.allow_process_kill else "bloqueado")
        self._refresh_menu()

    def pair(self, *_):
        def run():
            from .pair_window import run_pair_window

            if run_pair_window():
                self.runner.restart()
            self._refresh_menu()

        threading.Thread(target=run, name="pair-window", daemon=True).start()

    def open_log(self, *_):
        open_path(log_file())

    def open_config(self, *_):
        config_dir().mkdir(parents=True, exist_ok=True)
        open_path(config_dir())

    def unpair(self, *_):
        if not dialogs.confirm(
            TITLE,
            "Desvincular este computador?\n\nO agente para de enviar dados, a credencial local é apagada e o "
            "início automático é desativado. No painel, revogue também o dispositivo.",
        ):
            return
        self.runner.stop()
        unpair()
        log.info("Computador desvinculado pelo proprietário (bandeja)")
        self._on_status(Status("unpaired"))

    def about(self, *_):
        cfg = self.cfg
        dialogs.info(
            TITLE,
            f"EINSTEIN REMOTE AGENT v{__version__}\n\n"
            f"Estado: {self.status_text()}\n"
            f"Servidor: {cfg.server_url or '—'}\n"
            f"Computador: {cfg.device_name or '—'}\n"
            f"Início automático: {'ativado' if autostart.is_enabled() else 'desativado'}\n\n"
            f"Registro: {log_file()}",
        )

    def quit(self, *_):
        log.info("Agente encerrado pelo proprietário (bandeja)")
        self.runner.stop()
        self.icon.stop()

    # -- ciclo de vida --------------------------------------------------------------
    def _setup(self, icon) -> None:
        icon.visible = True
        if not self.runner.start():
            self._on_status(Status("unpaired"))
            self.notify("Este computador ainda não foi vinculado. Clique no ícone › “Vincular este computador…”.")
        else:
            self.notify("Agente ativo. Clique no ícone para pausar, abrir o painel ou sair.")

    def run(self) -> None:
        self.icon.run(setup=self._setup)


def main() -> int:
    setup_logging(console=False)
    lock = SingleInstance("agent")
    if not lock.acquire():
        dialogs.info(TITLE, "O EINSTEIN REMOTE AGENT já está em execução.\nProcure o ícone na bandeja do sistema.")
        return 0
    try:
        log.info("Bandeja iniciada (v%s)", __version__)
        if not AgentConfig.load().paired:
            from .pair_window import run_pair_window

            run_pair_window()  # se cancelar, a bandeja abre em modo "não vinculado"
        TrayApp().run()
    finally:
        lock.release()
    return 0
