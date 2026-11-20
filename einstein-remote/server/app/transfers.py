"""Transferência de arquivos entre navegador ↔ servidor ↔ agente (e entre dois agentes).

* Download: o navegador recebe um link de uso único (60 s) e baixa por HTTP com o
  gerenciador de downloads nativo (funciona no celular e com arquivos grandes, sem
  carregar tudo na memória). O servidor repassa os blocos do agente à medida que o
  navegador consome (controle de fluxo real) e confere o SHA-256 informado pelo
  agente: se não bater, a conexão é interrompida e o download fica marcado como falho.
* Upload: o corpo da requisição é repassado ao agente em blocos de 256 KB com janela
  de confirmações; o agente só coloca o arquivo no lugar após conferir tamanho e SHA-256.
* Cópia entre computadores: download do PC A alimenta diretamente o upload no PC B.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import struct
import time
import uuid
from dataclasses import dataclass, field

CHUNK = 256 * 1024
WINDOW = 8
DOWNLOAD, UPLOAD = 0x02, 0x03
TOKEN_TTL = 60


class TransferError(Exception):
    pass


def packet(kind: int, header: dict, data: bytes) -> bytes:
    h = json.dumps(header, separators=(",", ":")).encode()
    return bytes([kind]) + struct.pack(">H", len(h)) + h + data


def parse(pkt: bytes) -> tuple[int, dict, bytes]:
    (n,) = struct.unpack(">H", pkt[1:3])
    return pkt[0], json.loads(pkt[3:3 + n]), pkt[3 + n:]


@dataclass(eq=False)
class Transfer:
    tid: str
    device_id: str
    kind: str  # download | upload
    user: dict
    name: str = ""
    size: int = 0
    path: str = ""
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=WINDOW * 2))
    done: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    acked: int = 0
    ack_event: asyncio.Event = field(default_factory=asyncio.Event)
    agent_sha: str | None = None
    moved: int = 0
    created: float = field(default_factory=time.time)
    last_progress: float = 0.0


class TransferManager:
    def __init__(self, hub):
        self.hub = hub
        self.active: dict[str, Transfer] = {}
        self.tokens: dict[str, tuple[str, float]] = {}

    # -------------------------------------------------------------- utilidades
    def _agent(self, device_id: str):
        conn = self.hub.agents.get(device_id)
        if not conn:
            raise TransferError("Computador offline.")
        return conn

    async def _progress(self, t: Transfer, state: str = "running", force: bool = False) -> None:
        now = time.time()
        if not force and now - t.last_progress < 0.5:
            return
        t.last_progress = now
        msg = {"type": "transfer", "tid": t.tid, "kind": t.kind, "name": t.name, "device_id": t.device_id,
               "done": t.moved, "size": t.size, "state": state}
        for c in [c for c in list(self.hub.clients) if c.user["id"] == t.user["id"]]:
            await self.hub._send(c.ws, msg)

    async def _cancel_agent(self, t: Transfer) -> None:
        conn = self.hub.agents.get(t.device_id)
        if conn:
            await self.hub._send(conn.ws, {"type": "file_cancel", "tid": t.tid})

    # -------------------------------------------------------------- download
    async def open_download(self, user: dict, device_id: str, root, path: str) -> Transfer:
        self._agent(device_id)
        t = Transfer(tid=uuid.uuid4().hex, device_id=device_id, kind="download", user=user, path=path)
        self.active[t.tid] = t
        try:
            info = await self.hub.server_call(device_id, "files.download", {"root": root, "path": path, "_tid": t.tid})
        except Exception:
            self.active.pop(t.tid, None)
            raise
        t.name, t.size = info["name"], int(info["size"])
        return t

    def issue_token(self, t: Transfer) -> str:
        token = secrets.token_urlsafe(32)
        self.tokens[token] = (t.tid, time.time() + TOKEN_TTL)
        return token

    def redeem(self, token: str) -> Transfer:
        entry = self.tokens.pop(token, None)  # uso único
        if not entry or entry[1] < time.time():
            raise TransferError("Link de download inválido ou expirado.")
        t = self.active.get(entry[0])
        if not t:
            raise TransferError("Transferência não está mais disponível.")
        return t

    async def iterate(self, t: Transfer):
        """Gera os blocos do download conferindo o SHA-256 no final."""
        sha = hashlib.sha256()
        conn = self._agent(t.device_id)
        try:
            while True:
                item = await asyncio.wait_for(t.queue.get(), timeout=60)
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                seq, data = item
                sha.update(data)
                t.moved += len(data)
                yield data
                await self.hub._send(conn.ws, {"type": "file_ack", "tid": t.tid, "seq": seq})
                await self._progress(t)
            digest = sha.hexdigest()
            if t.moved != t.size or digest != t.agent_sha:
                raise TransferError("Falha na verificação de integridade do download.")
            await self._progress(t, "done", force=True)
        except BaseException:  # inclui o navegador desistir do download (GeneratorExit)
            await self._cancel_agent(t)
            await self._progress(t, "error", force=True)
            raise
        finally:
            self.active.pop(t.tid, None)
        self.hub.db.audit("file.download", user=t.user, device_id=t.device_id,
                          detail={"path": t.path, "size": t.moved, "sha256": digest})

    # -------------------------------------------------------------- upload
    async def upload(self, user: dict, device_id: str, root, path: str, name: str, size: int, chunks,
                     overwrite: bool = False, expected_sha: str | None = None) -> dict:
        conn = self._agent(device_id)
        t = Transfer(tid=uuid.uuid4().hex, device_id=device_id, kind="upload", user=user, name=name, size=size,
                     path=f"{path}/{name}".strip("/"))
        self.active[t.tid] = t
        sha = hashlib.sha256()
        try:
            info = await self.hub.server_call(device_id, "files.upload_start", {
                "root": root, "path": path, "name": name, "size": size, "overwrite": overwrite, "_tid": t.tid})
            t.name = info.get("name", name)
            seq = 0
            buf = bytearray()

            async def flush(data: bytes):
                nonlocal seq
                while seq - t.acked >= WINDOW:
                    if t.done.done():
                        t.done.result()  # agente reportou erro no meio do caminho
                    t.ack_event.clear()
                    await asyncio.wait_for(t.ack_event.wait(), timeout=60)
                seq += 1
                sha.update(data)
                t.moved += len(data)
                if not await self.hub._send_bytes(conn.ws, packet(UPLOAD, {"tid": t.tid, "seq": seq}, data)):
                    raise TransferError("Conexão com o computador perdida.")
                await self._progress(t)

            async for part in chunks:
                if t.done.done():
                    t.done.result()
                buf.extend(part)
                if t.moved + len(buf) > size:
                    raise TransferError("Recebido mais dados que o tamanho informado.")
                while len(buf) >= CHUNK:
                    await flush(bytes(buf[:CHUNK]))
                    del buf[:CHUNK]
            if buf:
                await flush(bytes(buf))
            digest = sha.hexdigest()
            if t.moved != size:
                raise TransferError(f"Envio incompleto ({t.moved} de {size} bytes).")
            if expected_sha and expected_sha.lower() != digest:
                raise TransferError("O arquivo chegou ao servidor diferente do que saiu do seu aparelho (SHA-256).")
            await self.hub._send(conn.ws, {"type": "file_end", "tid": t.tid, "sha256": digest})
            result = await asyncio.wait_for(t.done, timeout=120)
            if result.get("sha256") != digest:
                raise TransferError("Falha na verificação de integridade no computador.")
            await self._progress(t, "done", force=True)
            self.hub.db.audit("file.upload", user=user, device_id=device_id,
                              detail={"path": result.get("path"), "size": t.moved, "sha256": digest})
            return {**result, "verified": True}
        except BaseException:
            await self._cancel_agent(t)
            await self._progress(t, "error", force=True)
            raise
        finally:
            self.active.pop(t.tid, None)

    # -------------------------------------------------------------- cópia entre computadores
    async def copy(self, user: dict, src: dict, dst: dict, overwrite: bool = False) -> dict:
        t = await self.open_download(user, src["device_id"], src["root"], src["path"])
        name = t.name
        try:
            result = await self.upload(user, dst["device_id"], dst["root"], dst["path"], name, t.size,
                                       self.iterate(t), overwrite=overwrite, expected_sha=None)
        except BaseException:
            self.active.pop(t.tid, None)
            await self._cancel_agent(t)
            raise
        self.hub.db.audit("file.copy", user=user, device_id=src["device_id"],
                          detail={"from": src, "to": {**dst, "name": result.get("name")}, "size": result.get("size"),
                                  "sha256": result.get("sha256")})
        return result

    # -------------------------------------------------------------- eventos do agente
    async def agent_packet(self, device_id: str, pkt: bytes) -> None:
        try:
            kind, header, data = parse(pkt)
        except Exception:
            return
        t = self.active.get(header.get("tid"))
        if kind != DOWNLOAD or not t or t.device_id != device_id or t.kind != "download":
            return
        await t.queue.put((header.get("seq"), data))

    async def agent_message(self, device_id: str, msg: dict) -> None:
        t = self.active.get(msg.get("tid"))
        if not t or t.device_id != device_id:
            return
        if msg.get("type") == "file_ack" and t.kind == "upload":
            try:
                t.acked = max(t.acked, int(msg.get("seq") or 0))
            except (TypeError, ValueError):
                return
            t.ack_event.set()
        elif msg.get("type") == "file_event":
            if msg.get("event") == "done":
                if t.kind == "download":
                    t.agent_sha = msg.get("sha256")
                    await t.queue.put(None)
                elif not t.done.done():
                    t.done.set_result(msg)
            else:
                err = TransferError(str(msg.get("error") or "Falha no computador."))
                if t.kind == "download":
                    await t.queue.put(err)
                elif not t.done.done():
                    t.done.set_exception(err)
                    t.ack_event.set()

    async def agent_gone(self, device_id: str) -> None:
        for t in [t for t in self.active.values() if t.device_id == device_id]:
            err = TransferError("O computador desconectou durante a transferência.")
            if t.kind == "download":
                try:
                    t.queue.put_nowait(err)
                except asyncio.QueueFull:
                    pass
            elif not t.done.done():
                t.done.set_exception(err)
                t.ack_event.set()

    def expire(self) -> None:
        now = time.time()
        for token, (tid, exp) in list(self.tokens.items()):
            if exp < now:
                self.tokens.pop(token, None)
                t = self.active.get(tid)
                if t and t.moved == 0:  # link nunca usado: libera o arquivo no PC
                    self.active.pop(tid, None)
                    asyncio.create_task(self._cancel_agent(t))
