"""Conexão do agente com o servidor (WebSocket de saída, com reconexão).

O agente expõe um *estado* observável (``Agent.status``) usado pelo ícone da
bandeja e pela linha de comando:

=========== ===========================================
starting    iniciando
connecting  tentando conectar
online      conectado e transmitindo
offline     sem conexão; nova tentativa agendada
paused      pausado pelo proprietário (nada é coletado)
rejected    credencial recusada (dispositivo revogado)
stopped     encerrado
=========== ===========================================
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import threading
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from . import commands
from .config import AgentConfig, config_path
from .metrics import MetricsCollector, system_info

log = logging.getLogger("einstein.agent")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class AuthRejected(Exception):
    """Servidor recusou as credenciais (dispositivo revogado ou inválido)."""


@dataclass(frozen=True)
class Status:
    state: str
    detail: str = ""


def check_transport(cfg: AgentConfig) -> None:
    """Exige WSS fora de localhost, a menos que o proprietário permita explicitamente."""
    url = urlparse(cfg.ws_url())
    if url.scheme == "ws" and url.hostname not in LOCAL_HOSTS and not cfg.allow_insecure:
        raise ValueError(
            "Conexão sem criptografia recusada. Use https:// no servidor "
            "(ou habilite allow_insecure apenas em rede de testes)."
        )


def ssl_context(cfg: AgentConfig) -> ssl.SSLContext | None:
    if not cfg.ws_url().startswith("wss://"):
        return None
    ctx = ssl.create_default_context(cafile=cfg.ca_file or None)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class Agent:
    def __init__(self, cfg: AgentConfig, on_status: Callable[[Status], None] | None = None):
        self.cfg = cfg
        self.collector = MetricsCollector()
        self.on_status = on_status
        self.status = Status("starting")
        self._cfg_mtime = self._mtime()
        self._stop: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws = None
        self._lock = threading.Lock()

    # -- estado -----------------------------------------------------------------
    def _set_status(self, state: str, detail: str = "") -> None:
        new = Status(state, detail)
        if new == self.status:
            return
        self.status = new
        log.info("Estado: %s%s", state, f" ({detail})" if detail else "")
        if self.on_status:
            try:
                self.on_status(new)
            except Exception:  # um erro na interface nunca derruba o agente
                log.exception("Falha ao notificar estado")

    @staticmethod
    def _mtime() -> float:
        try:
            return config_path().stat().st_mtime
        except OSError:
            return 0.0

    def _reload_if_changed(self) -> None:
        m = self._mtime()
        if m != self._cfg_mtime:
            self._cfg_mtime = m
            self.cfg = AgentConfig.load()

    # -- controle (pode ser chamado de outra thread) ------------------------------
    def stop(self) -> None:
        """Encerra o agente. Seguro para chamar de qualquer thread."""
        if self._loop and self._stop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._stop.set)

    # -- ciclo principal ----------------------------------------------------------
    async def run_forever(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        check_transport(self.cfg)
        backoff = 1.0
        try:
            while not self._stop.is_set():
                self._reload_if_changed()
                if self.cfg.paused:
                    self._set_status("paused")
                    await self._sleep(1)
                    continue
                try:
                    self._set_status("connecting", self.cfg.server_url)
                    was_online = await self._session()
                    if self._stop.is_set():
                        break
                    self._reload_if_changed()
                    if self.cfg.paused:
                        backoff = 1.0
                        continue  # sessão encerrada por pausa: volta ao laço sem esperar
                    if was_online:
                        backoff = 1.0
                    self._set_status("offline", "conexão encerrada pelo servidor")
                except AuthRejected as exc:
                    self._set_status("rejected", str(exc))
                    log.error("Autenticação recusada: %s. Vincule o dispositivo novamente.", exc)
                    raise
                except (OSError, ConnectionClosed, InvalidStatus, asyncio.TimeoutError) as exc:
                    self._set_status("offline", f"{exc.__class__.__name__}: {exc}"[:200])
                    log.warning("Conexão perdida (%s). Nova tentativa em %.0fs", exc, backoff)
                await self._sleep(backoff)
                backoff = min(backoff * 2, 60)
        finally:
            if self.status.state != "rejected":
                self._set_status("stopped")

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _session(self) -> bool:
        url = self.cfg.ws_url()
        log.info("Conectando a %s", url)
        async with connect(url, ssl=ssl_context(self.cfg), max_size=8 * 1024 * 1024, open_timeout=15) as ws:
            await ws.send(
                json.dumps({"type": "auth", "device_id": self.cfg.device_id, "secret": self.cfg.secret(), "info": system_info()})
            )
            reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if reply.get("type") != "auth_ok":
                raise AuthRejected(reply.get("error", "recusado"))
            self._set_status("online", reply.get("name", ""))
            log.info("Conectado como '%s'. Transmitindo métricas a cada %.1fs.", reply.get("name"), self.cfg.interval)
            sender = asyncio.create_task(self._send_metrics(ws))
            receiver = asyncio.create_task(self._receive(ws))
            stopper = asyncio.create_task(self._stop.wait())
            tasks = {sender, receiver, stopper}
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if stopper in done:
                    await self._close(ws, "stopping")
                for t in done:
                    if t is not stopper and not t.cancelled() and t.exception():
                        raise t.exception()
            finally:
                for t in tasks:
                    t.cancel()
        return True

    async def _receive(self, ws) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "command":
                asyncio.create_task(self._handle_command(ws, msg))

    @staticmethod
    async def _close(ws, state: str) -> None:
        """Avisa o servidor do motivo (pausa/encerramento voluntário) antes de fechar."""
        try:
            await ws.send(json.dumps({"type": "status", "state": state}))
            await ws.close(code=1000, reason=state)
        except ConnectionClosed:
            pass

    async def _send_metrics(self, ws) -> None:
        while True:
            self._reload_if_changed()
            if self.cfg.paused:
                # o estado local muda ANTES de avisar o servidor: ícone e painel nunca divergem
                self._set_status("paused")
                await self._close(ws, "paused")
                return
            data = await asyncio.to_thread(self.collector.collect)
            await ws.send(json.dumps({"type": "metrics", "data": data}))
            await asyncio.sleep(self.cfg.interval)

    async def _handle_command(self, ws, msg: dict) -> None:
        action = str(msg.get("action"))
        log.info("Comando recebido: %s", action)
        ok, data, error = await commands.execute(action, msg.get("params") or {})
        try:
            await ws.send(json.dumps({"type": "result", "id": msg.get("id"), "ok": ok, "data": data, "error": error}))
        except ConnectionClosed:
            pass
