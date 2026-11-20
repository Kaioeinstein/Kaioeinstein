"""Executa o agente numa thread própria (com seu event loop), para a bandeja/GUI."""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Callable

from .client import Agent, AuthRejected, Status
from .config import AgentConfig

log = logging.getLogger("einstein.runner")


class AgentRunner:
    def __init__(self, on_status: Callable[[Status], None] | None = None,
                 on_screen: Callable[[dict], None] | None = None):
        self.on_status = on_status
        self.on_screen = on_screen
        self.agent: Agent | None = None
        self._thread: threading.Thread | None = None
        self.status = Status("unpaired")

    def _emit(self, st: Status) -> None:
        self.status = st
        if self.on_status:
            self.on_status(st)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> bool:
        if self.running:
            return True
        cfg = AgentConfig.load()
        if not cfg.paired:
            self._emit(Status("unpaired"))
            return False
        self.agent = Agent(cfg, on_status=self._emit, on_screen=self.on_screen)
        self._thread = threading.Thread(target=self._run, name="einstein-agent", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        try:
            asyncio.run(self.agent.run_forever())
        except AuthRejected:
            pass  # estado 'rejected' já emitido
        except ValueError as exc:  # transporte inseguro recusado
            self._emit(Status("rejected", str(exc)))
        except Exception as exc:
            log.exception("Agente terminou com erro")
            self._emit(Status("offline", str(exc)))

    def stop(self, timeout: float = 10) -> None:
        if self.agent:
            self.agent.stop()
        if self._thread:
            self._thread.join(timeout)
        self._thread = None

    def restart(self) -> bool:
        self.stop()
        return self.start()

    def stop_screen(self) -> bool:
        """Encerra o compartilhamento de tela pelo próprio PC (bandeja)."""
        import asyncio

        a = self.agent
        if not (a and a._loop and a.screen and a.screen.active):
            return False
        asyncio.run_coroutine_threadsafe(a.screen.stop("owner"), a._loop).result(10)
        return True

    @staticmethod
    def set_paused(value: bool) -> None:
        cfg = AgentConfig.load()
        cfg.paused = value
        cfg.save()
