"""Sessões de tela remota (Fase 5).

Regras aplicadas pelo servidor:
* Só administradores (ou proprietário) com acesso ao computador iniciam uma sessão.
* Quadros de um computador vão SOMENTE para os clientes daquela sessão.
* Eventos de mouse/teclado só são aceitos de quem abriu a sessão com controle,
  com limite de taxa.
* Nome de quem assiste e se há controle são definidos pelo servidor (nunca pelo
  cliente) e enviados ao agente para exibir na faixa de aviso do PC.
* Início, fim, duração e motivo ficam na auditoria; o início sempre gera notificação.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

FRAME_PREFIX = b"S"
MAX_EVENTS_PER_MSG = 64
MAX_EVENTS_PER_SEC = 400


@dataclass(eq=False)
class ScreenSession:
    device_id: str
    viewers: dict = field(default_factory=dict)  # ClientConn -> controle (bool)
    started: float = field(default_factory=time.time)
    frames: int = 0
    bytes: int = 0

    def names(self) -> list[str]:
        seen = []
        for c in self.viewers:
            if c.user["username"] not in seen:
                seen.append(c.user["username"])
        return seen

    def any_control(self) -> bool:
        return any(self.viewers.values())


class ScreenManager:
    def __init__(self, hub):
        self.hub = hub
        self.sessions: dict[str, ScreenSession] = {}
        self._rate: dict[int, tuple[float, int]] = {}
        self._busy: set[int] = set()

    # -------------------------------------------------------------- comandos
    def prepare(self, client, action: str, device_id: str, params: dict) -> tuple[dict, dict | None]:
        """Ajusta parâmetros antes de enviar ao agente. Retorna (params, resposta_local_ou_None)."""
        params = {k: v for k, v in params.items() if not str(k).startswith("_")}  # cliente não define campos internos
        session = self.sessions.get(device_id)
        if action == "screen.start":
            names = session.names() if session else []
            if client.user["username"] not in names:
                names.append(client.user["username"])
            control = bool(params.get("control")) or (session.any_control() if session else False)
            params.update(_user=client.user["username"], _viewers=names, _control=control)
        elif action == "screen.stop":
            if not session or client not in session.viewers:
                return params, {"active": False}
            del session.viewers[client]
            params.update(_viewers=session.names(), _control=session.any_control())
            if not session.viewers:
                self._end(device_id, "viewers_left")
        return params, None

    def started(self, client, device_id: str, requested: dict, result: dict) -> None:
        """Agente aceitou screen.start: registra o cliente como espectador (e controlador, se permitido)."""
        session = self.sessions.get(device_id)
        new = session is None
        if new:
            session = self.sessions[device_id] = ScreenSession(device_id)
        control = bool(requested.get("control")) and bool((result or {}).get("control"))
        session.viewers[client] = control
        device = self.hub.db.get_device_public(device_id)
        name = device["name"] if device else device_id
        self.hub.db.audit("screen.started", user=client.user, device_id=device_id,
                          detail={"control": control, "monitor": (result or {}).get("monitor")})
        asyncio.create_task(self.hub.notify(
            "info", "screen",
            f"Tela de {name} acessada por {client.user['username']}" + (" com controle de mouse e teclado" if control else " (somente visualização)"),
            device_id,
        ))

    def _end(self, device_id: str, reason: str) -> ScreenSession | None:
        session = self.sessions.pop(device_id, None)
        if session:
            self.hub.db.audit("screen.ended", device_id=device_id, detail={
                "reason": reason, "duration_s": round(time.time() - session.started), "frames": session.frames,
                "viewers": session.names()})
        return session

    # -------------------------------------------------------------- eventos do agente
    async def frame(self, device_id: str, data: bytes) -> None:
        session = self.sessions.get(device_id)
        if not session:
            return
        session.frames += 1
        session.bytes += len(data)
        packet = FRAME_PREFIX + device_id.encode() + data
        for client in list(session.viewers):
            if id(client) in self._busy:
                continue  # cliente lento: descarta este quadro em vez de atrasar todos
            self._busy.add(id(client))
            asyncio.create_task(self._send(client, packet))

    async def _send(self, client, packet: bytes) -> None:
        try:
            await asyncio.wait_for(client.ws.send_bytes(packet), timeout=10)
        except Exception:
            pass
        finally:
            self._busy.discard(id(client))

    async def agent_event(self, device_id: str, msg: dict) -> None:
        if msg.get("event") == "stopped":
            session = self._end(device_id, msg.get("reason") or "agent")
            if session:
                await self._tell_viewers(session, msg.get("reason") or "agent")
                device = self.hub.db.get_device_public(device_id)
                await self.hub.notify("info", "screen", f"Compartilhamento de tela encerrado no próprio computador ({device['name'] if device else device_id})", device_id)

    async def agent_gone(self, device_id: str) -> None:
        session = self._end(device_id, "disconnected")
        if session:
            await self._tell_viewers(session, "disconnected")

    async def _tell_viewers(self, session: ScreenSession, reason: str) -> None:
        for c in list(session.viewers):
            await self.hub._send(c.ws, {"type": "screen_stopped", "device_id": session.device_id, "reason": reason})

    # -------------------------------------------------------------- mensagens do cliente
    async def client_message(self, client, msg: dict) -> None:
        device_id = msg.get("device_id")
        session = self.sessions.get(device_id) if isinstance(device_id, str) else None
        if not session or client not in session.viewers:
            return
        conn = self.hub.agents.get(device_id)
        if not conn:
            return
        if msg.get("type") == "screen_ack":
            await self.hub._send(conn.ws, {"type": "screen_ack", "seq": msg.get("seq")})
        elif msg.get("type") == "input":
            if not session.viewers.get(client):
                return  # sessão somente de visualização
            events = msg.get("events")
            if not isinstance(events, list) or not events:
                return
            events = events[:MAX_EVENTS_PER_MSG]
            now = time.time()
            start, count = self._rate.get(id(client), (now, 0))
            if now - start >= 1:
                start, count = now, 0
            if count + len(events) > MAX_EVENTS_PER_SEC:
                return
            self._rate[id(client)] = (start, count + len(events))
            await self.hub._send(conn.ws, {"type": "input", "events": events})

    async def client_gone(self, client) -> None:
        for device_id, session in list(self.sessions.items()):
            if client in session.viewers:
                del session.viewers[client]
                conn = self.hub.agents.get(device_id)
                if not session.viewers:
                    self._end(device_id, "viewer_disconnected")
                if conn:
                    await self.hub._send(conn.ws, {
                        "type": "command", "id": uuid.uuid4().hex, "action": "screen.stop",
                        "params": {"_viewers": session.names(), "_control": session.any_control()},
                    })
        self._rate.pop(id(client), None)
