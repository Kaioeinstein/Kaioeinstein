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

from . import commands, maintenance
from .config import AgentConfig, config_path
from .metrics import MetricsCollector, system_info
from .power import PowerController, PowerError
from .screen import ScreenController, ScreenRefused
from .transfers import UPLOAD, Transfers

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
    def __init__(self, cfg: AgentConfig, on_status: Callable[[Status], None] | None = None,
                 on_screen: Callable[[dict], None] | None = None,
                 on_power: Callable[[dict], None] | None = None):
        self.cfg = cfg
        self.on_screen = on_screen
        self.power = PowerController(on_change=on_power)  # sobrevive a reconexões (prazos de suspensão)
        self.game = maintenance.GameMode(emit=self._emit_threadsafe)  # modo jogo: independe da conexão
        self.screen: ScreenController | None = None
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

    def _emit_threadsafe(self, msg: dict) -> None:
        """Envia um evento ao servidor a partir de outra thread (modo jogo); sem conexão, só fica no log."""
        ws, loop = self._ws, self._loop
        if ws is None or loop is None or loop.is_closed():
            return

        async def send():
            try:
                await ws.send(json.dumps(msg))
            except Exception:
                pass

        asyncio.run_coroutine_threadsafe(send(), loop)

    # -- controle (pode ser chamado de outra thread) ------------------------------
    def stop(self) -> None:
        """Encerra o agente. Seguro para chamar de qualquer thread."""
        if self._loop and self._stop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._stop.set)

    # -- ciclo principal ----------------------------------------------------------
    async def run_forever(self) -> None:
        self._loop = asyncio.get_running_loop()
        try:
            from . import files

            removed = await asyncio.to_thread(files.cleanup_partials)
            if removed:
                log.info("%d arquivo(s) temporário(s) de transferências antigas removido(s)", removed)
        except Exception:
            log.debug("Limpeza de temporários falhou", exc_info=True)
        self._stop = asyncio.Event()
        check_transport(self.cfg)
        self.game.start()
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
            self.game.stop()  # volta ao modo de energia anterior se um jogo estava aberto
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
            self.screen = ScreenController(
                send_bytes=ws.send, send_json=lambda m: ws.send(json.dumps(m)), on_change=self.on_screen
            )
            self.power.send_json = lambda m: ws.send(json.dumps(m))
            self._ws = ws
            self.transfers = Transfers()
            self.transfers.bind(ws.send, lambda m: ws.send(json.dumps(m)))
            if self.power.pending:  # reconectou durante um prazo: servidor volta a saber
                await self.power._emit({"event": "scheduled", **self.power.pending})
            self._inputs: asyncio.Queue = asyncio.Queue(maxsize=256)
            sender = asyncio.create_task(self._send_metrics(ws))
            receiver = asyncio.create_task(self._receive(ws))
            stopper = asyncio.create_task(self._stop.wait())
            input_worker = asyncio.create_task(self._input_worker())
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
                input_worker.cancel()
                self._ws = None
                await self.screen.close()  # solta teclas/botões, remove a faixa
                await self.transfers.close()  # apaga temporários de uploads interrompidos
        return True

    async def _receive(self, ws) -> None:
        async for raw in ws:
            if isinstance(raw, bytes):
                if raw[:1] == bytes([UPLOAD]):
                    await self.transfers.chunk(raw)  # em ordem: um bloco por vez
                continue
            msg = json.loads(raw)
            kind = msg.get("type")
            if kind == "file_ack":
                self.transfers.ack(msg)
                continue
            if kind == "file_end":
                await self.transfers.end(msg)
                continue
            if kind == "file_cancel":
                await self.transfers.cancel(str(msg.get("tid")))
                continue
            if kind == "command":
                asyncio.create_task(self._handle_command(ws, msg))
            elif kind == "screen_ack":
                self.screen.ack(msg.get("seq"))
            elif kind == "input":
                try:
                    self._inputs.put_nowait(msg.get("events"))
                except asyncio.QueueFull:
                    log.warning("Fila de entrada cheia; eventos descartados")

    async def _input_worker(self) -> None:
        """Aplica eventos de mouse/teclado estritamente na ordem em que chegaram."""
        while True:
            events = await self._inputs.get()
            try:
                await self.screen.handle_input(events)
            except Exception:
                log.exception("Falha ao aplicar entrada remota")

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

    async def _screen_command(self, action: str, params: dict):
        try:
            if action == "screen.monitors":
                return True, await self.screen.monitors(), None
            if action == "screen.start":
                return True, await self.screen.start(params), None
            if action == "screen.stop":
                return True, await self.screen.update_viewers(params), None
        except ScreenRefused as exc:
            return False, None, str(exc)
        except Exception as exc:
            log.exception("Falha no comando de tela %s", action)
            return False, None, f"{type(exc).__name__}: {exc}"
        return False, None, f"Ação '{action}' não suportada por este agente"

    async def _files_command(self, action: str, params: dict):
        from . import files

        try:
            if action == "files.roots":
                return True, await asyncio.to_thread(files.list_roots), None
            if action == "files.list":
                return True, await asyncio.to_thread(files.list_dir, params.get("root"), params.get("path")), None
            if action == "files.mkdir":
                return True, await asyncio.to_thread(files.mkdir, params.get("root"), params.get("path"), params.get("name")), None
            if action == "files.download":
                return True, await self.transfers.start_download(params), None
            if action == "files.upload_start":
                return True, await self.transfers.start_upload(params), None
        except files.FileError as exc:
            return False, None, str(exc)
        except FileNotFoundError:
            return False, None, "Arquivo ou pasta não encontrado."
        except PermissionError:
            return False, None, "Sem permissão no computador para este item."
        except Exception as exc:
            log.exception("Falha no comando de arquivos %s", action)
            return False, None, f"{type(exc).__name__}: {exc}"
        return False, None, f"Ação '{action}' não suportada por este agente"

    async def _audio_command(self, action: str, params: dict):
        from . import audio

        try:
            if action == "audio.status":
                return True, await asyncio.to_thread(audio.status), None
            if action == "audio.set":
                data = await asyncio.to_thread(audio.change, params)
                log.info("Volume alterado pelo painel: %s%%%s", data.get("volume"), " (mudo)" if data.get("muted") else "")
                return True, data, None
            return False, None, f"Ação '{action}' não suportada por este agente"
        except audio.AudioError as exc:
            return False, None, str(exc)
        except Exception as exc:
            log.exception("Falha no controle de volume")
            return False, None, f"{type(exc).__name__}: {exc}"

    async def _update_command(self, params: dict):
        """Pedido do servidor do dono: baixar, conferir (SHA-256) e trocar o agente pela versão nova."""
        from . import updater

        try:
            prep = await asyncio.to_thread(updater.prepare, params, self.cfg)
        except updater.UpdateRefused as exc:
            log.info("Atualização não aplicada: %s", exc)
            return False, None, str(exc)
        except Exception as exc:
            log.exception("Falha ao baixar a atualização")
            return False, None, f"{type(exc).__name__}: {exc}"
        updater.launch_helper(prep)
        return True, {"version": prep["version"], "restarting": True}, None

    async def _maint_command(self, action: str, params: dict):
        m = maintenance
        try:
            if action == "maint.scan":
                data = await asyncio.to_thread(m.scan)
            elif action == "maint.clean":
                data = await asyncio.to_thread(m.clean, [str(i) for i in params.get("categories") or []])
            elif action == "maint.startup":
                data = {"items": await asyncio.to_thread(m.startup_items), "windows": m.WINDOWS}
            elif action == "maint.startup.set":
                data = await asyncio.to_thread(m.set_startup, str(params.get("id", "")), bool(params.get("enabled")))
            elif action == "perf.status":
                data = await asyncio.to_thread(m.perf_status, self.game)
            elif action == "perf.mode":
                await asyncio.to_thread(m._allowed)
                data = {"mode": await asyncio.to_thread(m.set_mode, str(params.get("mode", "")))}
            elif action == "perf.plan":
                await asyncio.to_thread(m._allowed)
                data = {"plan": await asyncio.to_thread(m.set_plan, str(params.get("guid", "")))}
            elif action == "perf.game":
                data = await asyncio.to_thread(m.configure_game, params)
                await asyncio.to_thread(self.game.tick)  # aplica na hora (jogo já aberto / modo desligado)
                data["state"] = self.game.state()
            else:
                return False, None, f"Ação '{action}' não suportada por este agente"
            return True, data, None
        except m.MaintenanceRefused as exc:
            return False, None, str(exc)
        except Exception as exc:
            log.exception("Falha no comando %s", action)
            return False, None, f"{type(exc).__name__}: {exc}"

    async def _power_command(self, action: str, params: dict):
        from . import power

        try:
            if action == "power.status":
                return True, {**power.capabilities(), **self.power.state()}, None
            if action == "power.cancel":
                return True, await self.power.cancel(str(params.get("_user") or "painel")), None
            return True, await self.power.request(action.split(".", 1)[1], params), None
        except PowerError as exc:
            return False, None, str(exc)
        except Exception as exc:
            log.exception("Falha no comando de energia %s", action)
            return False, None, f"{type(exc).__name__}: {exc}"

    async def _handle_command(self, ws, msg: dict) -> None:
        action = str(msg.get("action"))
        params = msg.get("params") or {}
        if action != "screen.stop":
            log.info("Comando recebido: %s", action)
        if action.startswith("screen."):
            ok, data, error = await self._screen_command(action, params)
        elif action.startswith("power."):
            ok, data, error = await self._power_command(action, params)
        elif action.startswith("files."):
            ok, data, error = await self._files_command(action, params)
        elif action.startswith("audio."):
            ok, data, error = await self._audio_command(action, params)
        elif action == "agent.update":
            ok, data, error = await self._update_command(params)
        elif action.startswith(("maint.", "perf.")):
            ok, data, error = await self._maint_command(action, params)
        else:
            ok, data, error = await commands.execute(action, params)
        try:
            await ws.send(json.dumps({"type": "result", "id": msg.get("id"), "ok": ok, "data": data, "error": error}))
        except ConnectionClosed:
            pass
