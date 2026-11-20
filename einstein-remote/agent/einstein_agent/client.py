"""Conexão do agente com o servidor (WebSocket de saída, com reconexão)."""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
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
    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg
        self.collector = MetricsCollector()
        self._cfg_mtime = self._mtime()
        self._stop = asyncio.Event()

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

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        check_transport(self.cfg)
        backoff = 1.0
        while not self._stop.is_set():
            self._reload_if_changed()
            if self.cfg.paused:
                log.info("Agente pausado pelo proprietário. Use 'einstein-agent resume' para retomar.")
                await self._sleep(5)
                continue
            try:
                await self._session()
                backoff = 1.0
            except AuthRejected as exc:
                log.error("Autenticação recusada: %s. Vincule o dispositivo novamente.", exc)
                raise
            except (OSError, ConnectionClosed, InvalidStatus, asyncio.TimeoutError) as exc:
                log.warning("Conexão perdida (%s). Nova tentativa em %.0fs", exc, backoff)
            await self._sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _session(self) -> None:
        url = self.cfg.ws_url()
        log.info("Conectando a %s", url)
        async with connect(url, ssl=ssl_context(self.cfg), max_size=8 * 1024 * 1024, open_timeout=15) as ws:
            await ws.send(
                json.dumps({"type": "auth", "device_id": self.cfg.device_id, "secret": self.cfg.secret(), "info": system_info()})
            )
            reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
            if reply.get("type") != "auth_ok":
                raise AuthRejected(reply.get("error", "recusado"))
            log.info("Conectado como '%s'. Transmitindo métricas a cada %.1fs.", reply.get("name"), self.cfg.interval)
            sender = asyncio.create_task(self._send_metrics(ws))
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "command":
                        asyncio.create_task(self._handle_command(ws, msg))
            finally:
                sender.cancel()

    async def _send_metrics(self, ws) -> None:
        while True:
            self._reload_if_changed()
            if self.cfg.paused or self._stop.is_set():
                await ws.close(code=1000, reason="pausado")
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
