"""Transmissão da tela e controle remoto (mouse/teclado) — sessão autorizada e visível.

Protocolo (WebSocket já autenticado do agente):
  quadro (binário):  0x01 | uint16 tamanho do cabeçalho | cabeçalho JSON | JPEG
                     cabeçalho = {seq, w, h, sw, sh, ts}
  confirmação:       servidor → agente {"type": "screen_ack", "seq": n}
  entrada:           servidor → agente {"type": "input", "events": [...]}
  fim pelo dono:     agente → servidor {"type": "screen_event", "event": "stopped", "reason": "owner"}

Controle de fluxo por créditos: no máximo ``MAX_INFLIGHT`` quadros sem confirmação;
um celular em rede lenta recebe menos quadros em vez de acumular atraso.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import io
import json
import logging
import struct
import sys
import time
import zlib
from typing import Awaitable, Callable

from PIL import Image, ImageDraw

from .config import AgentConfig
from .inputs import Injector, InputError, create_injector, validate

log = logging.getLogger("einstein.screen")
FRAME = 0x01
MAX_INFLIGHT = 2
ACK_TIMEOUT = 3.0
KEYFRAME_EVERY = 1.5  # reenvia mesmo sem mudança, para quem acabou de entrar
MAX_EVENTS = 64


def enable_dpi_awareness() -> None:
    """Sem isso, em telas com escala (125 %, 150 %) captura e cliques ficariam desalinhados."""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # por monitor
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _mss():
    import mss

    return (getattr(mss, "MSS", None) or mss.mss)()


def clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


class ScreenRefused(Exception):
    pass


class Capturer:
    """Usa uma thread fixa: o mss (GDI no Windows) precisa ser usado na thread que o criou."""

    def __init__(self) -> None:
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="einstein-capture")
        self._sct = None

    def _get(self):
        if self._sct is None:
            self._sct = _mss()
        return self._sct

    def monitors(self) -> list[dict]:
        def run():
            mons = self._get().monitors
            out = []
            for i, m in enumerate(mons[1:], start=1):
                out.append({"index": i, "left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"],
                            "primary": bool(m.get("is_primary")) or (m["left"] == 0 and m["top"] == 0)})
            return out

        return self.pool.submit(run).result(10)

    def grab(self, monitor: dict, max_width: int, quality: int, cursor: tuple[int, int] | None, last_crc: int | None):
        """Captura + JPEG. Retorna (bytes|None se igual ao anterior, crc, (w, h))."""

        def run():
            shot = self._get().grab({k: monitor[k] for k in ("left", "top", "width", "height")})
            crc = zlib.crc32(shot.bgra, zlib.crc32(repr(cursor).encode()))
            if crc == last_crc:
                return None, crc, None
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            scale = 1.0
            if max_width and img.width > max_width:
                scale = max_width / img.width
                img = img.resize((max_width, max(1, round(img.height * scale))), Image.BILINEAR)
            if cursor:
                draw_cursor(img, (cursor[0] - monitor["left"]) * scale, (cursor[1] - monitor["top"]) * scale, scale)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=quality, optimize=False, subsampling=0 if quality >= 85 else 2)
            return buf.getvalue(), crc, img.size

        return self.pool.submit(run).result(15)

    def close(self) -> None:
        def run():
            if self._sct is not None:
                self._sct.close()
                self._sct = None

        try:
            self.pool.submit(run).result(5)
        except Exception:
            pass


def draw_cursor(img: Image.Image, x: float, y: float, scale: float) -> None:
    """A captura de tela não inclui o ponteiro; desenhamos uma seta no lugar dele."""
    if not (0 <= x < img.width and 0 <= y < img.height):
        return
    s = max(0.6, min(1.5, scale * 1.4))
    pts = [(0, 0), (0, 17), (4.5, 13), (8, 20), (10.5, 19), (7, 12.5), (12.5, 12.5)]
    poly = [(x + px * s, y + py * s) for px, py in pts]
    d = ImageDraw.Draw(img)
    d.polygon(poly, fill=(255, 255, 255), outline=(0, 0, 0))


def frame_packet(header: dict, jpeg: bytes) -> bytes:
    h = json.dumps(header, separators=(",", ":")).encode()
    return bytes([FRAME]) + struct.pack(">H", len(h)) + h + jpeg


def parse_frame(packet: bytes) -> tuple[dict, bytes]:
    if not packet or packet[0] != FRAME:
        raise ValueError("pacote não é um quadro")
    (n,) = struct.unpack(">H", packet[1:3])
    return json.loads(packet[3:3 + n]), packet[3 + n:]


class ScreenController:
    """Uma sessão por computador (vários espectadores compartilham o mesmo fluxo)."""

    def __init__(self, send_bytes: Callable[[bytes], Awaitable[None]], send_json: Callable[[dict], Awaitable[None]],
                 overlay=None, injector_factory: Callable[[], Injector | None] = create_injector,
                 on_change: Callable[[dict], None] | None = None):
        from .overlay import OVERLAY

        self.send_bytes = send_bytes
        self.send_json = send_json
        self.overlay = overlay if overlay is not None else OVERLAY
        self.injector_factory = injector_factory
        self.on_change = on_change
        self.capturer: Capturer | None = None
        self.injector: Injector | None = None
        self.active = False
        self.viewers: list[str] = []
        self.approved: set[str] = set()
        self.control = False
        self.monitor: dict | None = None
        self.settings = {"max_width": 1280, "quality": 60, "fps": 12}
        self.task: asyncio.Task | None = None
        self.seq = 0
        self.acked = 0
        self.last_ack_time = 0.0
        self.frames_sent = 0
        self.bytes_sent = 0
        self.started_at = 0.0
        self.input_errors = 0

    # -------------------------------------------------------------- estado visível
    def state(self) -> dict:
        return {"active": self.active, "viewers": list(self.viewers), "control": self.control,
                "frames": self.frames_sent, "bytes": self.bytes_sent}

    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change(self.state())
            except Exception:
                log.exception("Falha ao notificar mudança de sessão de tela")

    # -------------------------------------------------------------- comandos
    async def monitors(self) -> dict:
        cap = self.capturer or Capturer()
        try:
            return {"monitors": await asyncio.to_thread(cap.monitors)}
        finally:
            if cap is not self.capturer:
                await asyncio.to_thread(cap.close)

    async def start(self, params: dict) -> dict:
        cfg = AgentConfig.load()
        user = str(params.get("_user") or "painel")[:64]
        viewers = [str(v)[:64] for v in (params.get("_viewers") or [user])][:10]
        want_control = bool(params.get("_control"))

        if cfg.screen_access == "deny":
            raise ScreenRefused("O proprietário bloqueou neste computador o acesso remoto à tela.")
        if cfg.screen_access == "ask" and user not in self.approved:
            log.info("Pedindo autorização ao dono do PC para %s", user)
            ok = await asyncio.to_thread(self.overlay.ask, user, want_control and cfg.allow_remote_input)
            if not ok:
                raise ScreenRefused("O acesso à tela foi recusado no computador (ou ninguém respondeu).")
        self.approved.add(user)

        if self.capturer is None:
            self.capturer = Capturer()
        mons = await asyncio.to_thread(self.capturer.monitors)
        if not mons:
            raise ScreenRefused("Nenhum monitor encontrado.")
        idx = clamp(params.get("monitor"), 1, len(mons), 1)
        self.monitor = mons[idx - 1]
        self.settings = {
            "max_width": clamp(params.get("max_width"), 0, 3840, 1280),
            "quality": clamp(params.get("quality"), 20, 90, 60),
            "fps": clamp(params.get("fps"), 1, 30, 12),
        }
        control = want_control and cfg.allow_remote_input
        if control and self.injector is None:
            self.injector = await asyncio.to_thread(self.injector_factory)
            if self.injector is None:
                control = False
        self.control = control
        self.viewers = viewers
        first = not self.active
        self.active = True
        if first:
            self.started_at = time.time()
            self.seq = self.acked = self.frames_sent = self.bytes_sent = 0
            self.task = asyncio.create_task(self._stream())
            log.warning("Compartilhamento de tela INICIADO para %s (controle: %s)", ", ".join(viewers), "sim" if control else "não")
        else:
            self.acked = self.seq  # reinicia créditos ao trocar monitor/qualidade
        self._last_crc = None
        await asyncio.to_thread(self._update_banner_sync, asyncio.get_running_loop())
        self._changed()
        return {"monitors": mons, "monitor": idx, "width": self.monitor["width"], "height": self.monitor["height"],
                "control": control, "control_blocked": want_control and not cfg.allow_remote_input,
                "access_mode": cfg.screen_access, **self.settings}

    def _update_banner_sync(self, loop) -> None:
        def owner_stop():
            asyncio.run_coroutine_threadsafe(self.stop("owner"), loop)

        if not self.overlay.show_banner(self.viewers, self.control, owner_stop):
            log.warning("Faixa de aviso indisponível neste sistema; a sessão segue registrada no log e na bandeja.")

    async def update_viewers(self, params: dict) -> dict:
        """screen.stop vindo do servidor: atualiza quem assiste; encerra se não sobrou ninguém."""
        viewers = [str(v)[:64] for v in (params.get("_viewers") or [])][:10]
        if not viewers:
            await self.stop("viewers_left")
            return {"active": False}
        self.viewers = viewers
        self.control = bool(params.get("_control")) and self.control
        await asyncio.to_thread(self._update_banner_sync, asyncio.get_running_loop())
        self._changed()
        return {"active": True, "viewers": viewers}

    async def stop(self, reason: str = "stopped") -> None:
        if not self.active:
            return
        self.active = False
        duration = time.time() - self.started_at
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None
        if self.injector:
            await asyncio.to_thread(self.injector.release_all)
        await asyncio.to_thread(self.overlay.hide_banner)
        log.warning("Compartilhamento de tela ENCERRADO (%s) após %.0fs, %d quadros", reason, duration, self.frames_sent)
        self.viewers = []
        self.approved.clear()
        self.control = False
        if reason in ("owner",):
            try:
                await self.send_json({"type": "screen_event", "event": "stopped", "reason": reason})
            except Exception:
                pass
        self._changed()

    async def close(self) -> None:
        """Conexão caiu: encerra tudo localmente."""
        await self.stop("disconnected")
        if self.capturer:
            await asyncio.to_thread(self.capturer.close)
            self.capturer = None

    # -------------------------------------------------------------- fluxo de quadros
    def ack(self, seq) -> None:
        try:
            seq = int(seq)
        except (TypeError, ValueError):
            return
        if seq > self.acked:
            self.acked = min(seq, self.seq)
            self.last_ack_time = time.time()

    async def _stream(self) -> None:
        last_sent = 0.0
        self._last_crc = None
        try:
            while self.active:
                interval = 1.0 / self.settings["fps"]
                t0 = time.time()
                if self.seq - self.acked >= MAX_INFLIGHT:
                    # sem confirmação há muito tempo: assume perda e libera um crédito
                    if time.time() - max(self.last_ack_time, last_sent) > ACK_TIMEOUT:
                        self.acked = self.seq - MAX_INFLIGHT + 1
                    await asyncio.sleep(0.01)
                    continue
                cursor = await asyncio.to_thread(self._cursor)
                force = time.time() - last_sent > KEYFRAME_EVERY
                jpeg, crc, size = await asyncio.to_thread(
                    self.capturer.grab, self.monitor, self.settings["max_width"], self.settings["quality"], cursor,
                    None if force else self._last_crc,
                )
                self._last_crc = crc
                if jpeg is not None:
                    self.seq += 1
                    header = {"seq": self.seq, "w": size[0], "h": size[1], "sw": self.monitor["width"],
                              "sh": self.monitor["height"], "ts": round(time.time() * 1000)}
                    packet = frame_packet(header, jpeg)
                    await self.send_bytes(packet)
                    last_sent = time.time()
                    self.frames_sent += 1
                    self.bytes_sent += len(packet)
                await asyncio.sleep(max(0.0, interval - (time.time() - t0)))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Transmissão de tela interrompida")
            await self.stop("error")

    def _cursor(self):
        inj = self.injector
        if inj is None:
            try:
                self.injector = inj = self.injector_factory()  # também serve para ler a posição do ponteiro
            except Exception:
                return None
        try:
            return inj.cursor() if inj else None
        except Exception:
            return None

    # -------------------------------------------------------------- entrada remota
    async def handle_input(self, events) -> int:
        if not (self.active and self.control and self.injector and self.monitor):
            return 0
        if not AgentConfig.load().allow_remote_input:  # dono desligou no meio da sessão
            self.control = False
            await asyncio.to_thread(self.injector.release_all)
            await asyncio.to_thread(self._update_banner_sync, asyncio.get_running_loop())
            self._changed()
            return 0
        applied = 0
        for raw in list(events or [])[:MAX_EVENTS]:
            try:
                ev = validate(raw)
                await asyncio.to_thread(self.injector.apply, ev, self.monitor)
                applied += 1
            except InputError as exc:
                self.input_errors += 1
                if self.input_errors <= 5:
                    log.info("Evento de entrada ignorado: %s", exc)
        return applied
