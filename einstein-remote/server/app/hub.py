"""Hub de conexões em tempo real.

O servidor atua como *relay* autenticado entre:

* **Agentes** (computadores Windows) – conexão de saída do agente para o
  servidor, de modo que o PC não precisa abrir portas no roteador.
* **Clientes** (dashboard web / mobile) – navegadores autenticados.

Nenhum comando chega a um agente sem passar pela verificação de sessão,
papel (role), lista de ações permitidas e confirmação (para ações críticas).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from .db import Database

# Ações que o servidor aceita encaminhar. Cada fase adiciona novas entradas.
# value = (papel mínimo, exige confirmação explícita)
ALLOWED_ACTIONS: dict[str, tuple[str, bool]] = {
    "ping": ("viewer", False),
    "system.info": ("viewer", False),
}

ROLE_LEVEL = {"viewer": 0, "admin": 1, "owner": 2}

HISTORY_LEN = 300  # amostras em memória por dispositivo (~10 min a cada 2 s)


def summarize(metrics: dict) -> dict:
    """Extrai os indicadores principais de uma amostra completa do agente."""
    disks = metrics.get("disks") or []
    system_disk = next((d for d in disks if d.get("system")), disks[0] if disks else None)
    gpus = metrics.get("gpu") or []
    temps = metrics.get("temperatures") or {}
    net = metrics.get("network") or {}
    return {
        "ts": metrics.get("ts", time.time()),
        "cpu": (metrics.get("cpu") or {}).get("percent"),
        "ram": (metrics.get("memory") or {}).get("percent"),
        "disk": system_disk.get("percent") if system_disk else None,
        "net_down": net.get("down_bps"),
        "net_up": net.get("up_bps"),
        "gpu": gpus[0].get("util") if gpus else None,
        "temp": temps.get("max"),
    }


@dataclass
class AgentConn:
    device_id: str
    ws: WebSocket
    info: dict
    connected_at: float = field(default_factory=time.time)
    last_metrics: dict | None = None
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_LEN))
    last_persist: float = 0.0
    cpu_high_streak: int = 0
    ram_high_streak: int = 0
    last_alert: dict[str, float] = field(default_factory=dict)


@dataclass(eq=False)  # identidade por objeto (usado em set)
class ClientConn:
    ws: WebSocket
    user: dict
    session_id: str


@dataclass
class PendingCommand:
    client: ClientConn
    device_id: str
    action: str
    created: float = field(default_factory=time.time)


class Hub:
    def __init__(self, db: Database, settings):
        self.db = db
        self.settings = settings
        self.agents: dict[str, AgentConn] = {}
        self.clients: set[ClientConn] = set()
        self.pending: dict[str, PendingCommand] = {}
        self._lock = asyncio.Lock()

    # -- envio seguro --------------------------------------------------------
    @staticmethod
    async def _send(ws: WebSocket, msg: dict) -> bool:
        try:
            await ws.send_json(msg)
            return True
        except Exception:  # conexão já encerrada
            return False

    async def broadcast(self, msg: dict) -> None:
        dead = [c for c in list(self.clients) if not await self._send(c.ws, msg)]
        for c in dead:
            self.clients.discard(c)

    async def notify(self, level: str, kind: str, message: str, device_id: str | None = None) -> None:
        note = self.db.add_notification(level, kind, message, device_id)
        await self.broadcast({"type": "notification", "data": note})

    # -- snapshot para clientes ------------------------------------------------
    def device_view(self, row: dict) -> dict:
        conn = self.agents.get(row["id"])
        return {
            **row,
            "online": conn is not None,
            "info": conn.info if conn else None,
            "connected_at": conn.connected_at if conn else None,
            "metrics": conn.last_metrics if conn else None,
            "history": list(conn.history) if conn else [],
        }

    def snapshot(self) -> list[dict]:
        return [self.device_view(d) for d in self.db.list_devices()]

    # -- agentes ---------------------------------------------------------------
    async def agent_connected(self, device: dict, ws: WebSocket, info: dict) -> AgentConn:
        async with self._lock:
            old = self.agents.get(device["id"])
            conn = AgentConn(device_id=device["id"], ws=ws, info=info)
            self.agents[device["id"]] = conn
        if old:
            try:
                await old.ws.close(code=4000, reason="substituída por nova conexão")
            except Exception:
                pass
        self.db.update_device_seen(device["id"], info.get("hostname"), info.get("os"))
        self.db.audit("agent.connected", device_id=device["id"], detail={"hostname": info.get("hostname")})
        await self.broadcast({"type": "device", "data": self.device_view(self.db.get_device_public(device["id"]))})
        await self.notify("info", "device_online", f"{device['name']} conectado", device["id"])
        return conn

    async def agent_disconnected(self, conn: AgentConn) -> None:
        async with self._lock:
            if self.agents.get(conn.device_id) is not conn:
                return  # já foi substituída por uma conexão mais nova
            del self.agents[conn.device_id]
        device = self.db.get_device_public(conn.device_id)
        self.db.update_device_seen(conn.device_id)
        self.db.audit("agent.disconnected", device_id=conn.device_id)
        # falha todos os comandos pendentes deste agente
        for cid, p in list(self.pending.items()):
            if p.device_id == conn.device_id:
                self.pending.pop(cid, None)
                await self._send(p.client.ws, {"type": "result", "id": cid, "ok": False, "error": "Agente desconectado"})
        if device and device["revoked"]:
            await self.broadcast({"type": "device_removed", "device_id": conn.device_id})
        elif device:
            await self.broadcast({"type": "device", "data": self.device_view(device)})
            await self.notify("warning", "device_offline", f"{device['name']} desconectado", conn.device_id)

    async def agent_metrics(self, conn: AgentConn, metrics: dict) -> None:
        summary = summarize(metrics)
        conn.last_metrics = metrics
        conn.history.append(summary)
        now = time.time()
        if now - conn.last_persist >= self.settings.history_persist_interval:
            conn.last_persist = now
            self.db.add_metric(conn.device_id, summary)
            self.db.update_device_seen(conn.device_id)
        await self.broadcast({"type": "metrics", "device_id": conn.device_id, "data": metrics, "summary": summary})
        await self._check_alerts(conn, summary)

    async def _alert(self, conn: AgentConn, kind: str, level: str, message: str) -> None:
        now = time.time()
        if now - conn.last_alert.get(kind, 0) < self.settings.alert_cooldown:
            return
        conn.last_alert[kind] = now
        await self.notify(level, kind, message, conn.device_id)

    async def _check_alerts(self, conn: AgentConn, s: dict) -> None:
        name = conn.info.get("hostname", conn.device_id)
        cpu, ram, temp = s.get("cpu"), s.get("ram"), s.get("temp")
        conn.cpu_high_streak = conn.cpu_high_streak + 1 if cpu is not None and cpu >= self.settings.cpu_alert_percent else 0
        conn.ram_high_streak = conn.ram_high_streak + 1 if ram is not None and ram >= self.settings.ram_alert_percent else 0
        if conn.cpu_high_streak == 5:  # ~10 s sustentado
            await self._alert(conn, "high_cpu", "warning", f"{name}: uso de CPU elevado ({cpu:.0f}%)")
        if conn.ram_high_streak == 5:
            await self._alert(conn, "high_ram", "warning", f"{name}: memória RAM próxima do limite ({ram:.0f}%)")
        if temp is not None and temp >= self.settings.temp_alert_celsius:
            await self._alert(conn, "high_temp", "critical", f"{name}: temperatura elevada ({temp:.0f} °C)")

    # -- comandos --------------------------------------------------------------
    async def send_command(self, client: ClientConn, msg: dict) -> None:
        req_id = str(msg.get("request_id") or "")[:64]
        device_id = msg.get("device_id")
        action = msg.get("action")
        params = msg.get("params") or {}

        async def reject(error: str) -> None:
            self.db.audit(
                "command.rejected", user=client.user, device_id=device_id, detail={"action": action, "error": error}, success=False
            )
            await self._send(client.ws, {"type": "result", "request_id": req_id, "ok": False, "error": error})

        if action not in ALLOWED_ACTIONS:
            return await reject("Ação não permitida")
        min_role, needs_confirm = ALLOWED_ACTIONS[action]
        if ROLE_LEVEL.get(client.user["role"], -1) < ROLE_LEVEL[min_role]:
            return await reject("Permissão insuficiente")
        if needs_confirm and msg.get("confirm") is not True:
            return await reject("Ação crítica exige confirmação")
        if not isinstance(params, dict):
            return await reject("Parâmetros inválidos")
        conn = self.agents.get(device_id)
        if not conn:
            return await reject("Dispositivo offline")

        cmd_id = uuid.uuid4().hex
        self.pending[cmd_id] = PendingCommand(client=client, device_id=device_id, action=action)
        self.db.audit("command.sent", user=client.user, device_id=device_id, detail={"action": action, "params": params})
        await self._send(client.ws, {"type": "ack", "request_id": req_id, "id": cmd_id})
        if not await self._send(conn.ws, {"type": "command", "id": cmd_id, "action": action, "params": params}):
            self.pending.pop(cmd_id, None)
            await self._send(client.ws, {"type": "result", "id": cmd_id, "request_id": req_id, "ok": False, "error": "Falha ao enviar"})

    async def command_result(self, conn: AgentConn, msg: dict) -> None:
        cmd_id = msg.get("id")
        pending = self.pending.pop(cmd_id, None)
        if not pending or pending.device_id != conn.device_id:
            return  # resultado desconhecido ou de outro dispositivo: ignorado
        ok = bool(msg.get("ok"))
        self.db.audit(
            "command.result",
            user=pending.client.user,
            device_id=conn.device_id,
            detail={"action": pending.action, "ok": ok, "error": msg.get("error")},
            success=ok,
        )
        await self._send(
            pending.client.ws,
            {"type": "result", "id": cmd_id, "ok": ok, "data": msg.get("data"), "error": msg.get("error")},
        )

    async def expire_pending(self) -> None:
        now = time.time()
        for cid, p in list(self.pending.items()):
            if now - p.created > self.settings.command_timeout:
                self.pending.pop(cid, None)
                await self._send(p.client.ws, {"type": "result", "id": cid, "ok": False, "error": "Tempo esgotado"})

    # -- revogação -------------------------------------------------------------
    async def close_session(self, session_id: str) -> None:
        for c in [c for c in list(self.clients) if c.session_id == session_id]:
            self.clients.discard(c)
            try:
                await c.ws.close(code=4401, reason="sessão revogada")
            except Exception:
                pass

    async def close_device(self, device_id: str) -> None:
        conn = self.agents.get(device_id)
        if conn:
            try:
                await conn.ws.close(code=4403, reason="dispositivo revogado")
            except Exception:
                pass
            await self.agent_disconnected(conn)
