"""Transferência de arquivos pelo WebSocket do agente.

Pacotes binários (mesmo formato dos quadros de tela):
    tipo | uint16 tamanho do cabeçalho | cabeçalho JSON {tid, seq} | dados
    0x02 = bloco de download (agente → servidor)
    0x03 = bloco de upload   (servidor → agente)

Mensagens JSON:
    servidor → agente: file_ack {tid, seq} · file_end {tid, sha256} · file_cancel {tid}
    agente → servidor: file_ack {tid, seq} · file_event {tid, event: done|error, ...}
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct

from . import files

log = logging.getLogger("einstein.transfers")
DOWNLOAD, UPLOAD = 0x02, 0x03
WINDOW = 8  # blocos de 256 KB em trânsito (≈ 2 MB)


def packet(kind: int, header: dict, data: bytes) -> bytes:
    h = json.dumps(header, separators=(",", ":")).encode()
    return bytes([kind]) + struct.pack(">H", len(h)) + h + data


def parse(pkt: bytes) -> tuple[int, dict, bytes]:
    (n,) = struct.unpack(">H", pkt[1:3])
    return pkt[0], json.loads(pkt[3:3 + n]), pkt[3 + n:]


class Transfers:
    def __init__(self) -> None:
        self.send_bytes = None
        self.send_json = None
        self.downloads: dict[str, dict] = {}
        self.uploads: dict[str, files.Upload] = {}

    def bind(self, send_bytes, send_json) -> None:
        self.send_bytes, self.send_json = send_bytes, send_json

    # -------------------------------------------------------------- download
    async def start_download(self, params: dict) -> dict:
        tid = str(params.get("_tid") or "")
        if not tid or tid in self.downloads:
            raise files.FileError("Transferência inválida.")
        dl = await asyncio.to_thread(files.Download, params.get("root"), params.get("path"))
        state = {"dl": dl, "seq": 0, "acked": 0, "event": asyncio.Event()}
        self.downloads[tid] = state
        state["task"] = asyncio.create_task(self._stream(tid, state))
        log.warning("Download pelo painel: %s (%d bytes)", dl.path, dl.size)
        return {"tid": tid, "name": dl.name, "size": dl.size}

    async def _stream(self, tid: str, st: dict) -> None:
        dl: files.Download = st["dl"]
        try:
            while True:
                while st["seq"] - st["acked"] >= WINDOW:
                    st["event"].clear()
                    await asyncio.wait_for(st["event"].wait(), timeout=60)
                data = await asyncio.to_thread(dl.read)
                if not data:
                    break
                st["seq"] += 1
                await self.send_bytes(packet(DOWNLOAD, {"tid": tid, "seq": st["seq"]}, data))
            await self.send_json({"type": "file_event", "tid": tid, "event": "done", "size": dl.size,
                                  "sha256": dl.sha.hexdigest()})
        except asyncio.CancelledError:
            pass
        except asyncio.TimeoutError:
            await self._error(tid, "O servidor parou de confirmar os blocos.")
        except Exception as exc:
            log.exception("Falha no download %s", tid)
            await self._error(tid, f"{type(exc).__name__}: {exc}")
        finally:
            dl.close()
            self.downloads.pop(tid, None)

    # -------------------------------------------------------------- upload
    async def start_upload(self, params: dict) -> dict:
        tid = str(params.get("_tid") or "")
        if not tid or tid in self.uploads:
            raise files.FileError("Transferência inválida.")
        up = await asyncio.to_thread(files.Upload, params.get("root"), params.get("path"), params.get("name"),
                                     params.get("size"), bool(params.get("overwrite")), tid)
        self.uploads[tid] = up
        return {"tid": tid, "name": up.final}

    async def chunk(self, pkt: bytes) -> None:
        kind, header, data = parse(pkt)
        if kind != UPLOAD:
            return
        tid = header.get("tid")
        up = self.uploads.get(tid)
        if not up:
            return
        try:
            await asyncio.to_thread(up.write, data)
            await self.send_json({"type": "file_ack", "tid": tid, "seq": header.get("seq")})
        except Exception as exc:
            self.uploads.pop(tid, None)
            await asyncio.to_thread(up.abort)
            await self._error(tid, str(exc))

    async def end(self, msg: dict) -> None:
        tid = msg.get("tid")
        up = self.uploads.pop(tid, None)
        if not up:
            return
        try:
            result = await asyncio.to_thread(up.finish, str(msg.get("sha256") or ""))
            await self.send_json({"type": "file_event", "tid": tid, "event": "done", **result})
        except Exception as exc:
            await self._error(tid, str(exc))

    # -------------------------------------------------------------- comum
    def ack(self, msg: dict) -> None:
        st = self.downloads.get(msg.get("tid"))
        if st:
            try:
                st["acked"] = max(st["acked"], int(msg.get("seq") or 0))
            except (TypeError, ValueError):
                return
            st["event"].set()

    async def cancel(self, tid: str) -> None:
        st = self.downloads.pop(tid, None)
        if st:
            st["task"].cancel()
        up = self.uploads.pop(tid, None)
        if up:
            await asyncio.to_thread(up.abort)
            log.info("Upload %s cancelado", tid)

    async def close(self) -> None:
        for tid in list(self.downloads) + list(self.uploads):
            await self.cancel(tid)

    async def _error(self, tid: str, error: str) -> None:
        try:
            await self.send_json({"type": "file_event", "tid": tid, "event": "error", "error": error})
        except Exception:
            pass
