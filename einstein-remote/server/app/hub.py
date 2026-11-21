"""Hub de conexões em tempo real.

O servidor atua como *relay* autenticado entre:

* **Agentes** (computadores Windows) – conexão de saída do agente para o
  servidor, de modo que o PC não precisa abrir portas no roteador.
* **Clientes** (dashboard web / mobile) – navegadores autenticados.

Nenhum comando chega a um agente sem passar pela verificação de sessão,
papel (role), acesso ao dispositivo, lista de ações permitidas e
confirmação (para ações críticas). Cada cliente só recebe dados dos
dispositivos que tem permissão para ver.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from fastapi import WebSocket

from .db import Database
from .preferences import Preferences
from .screens import ScreenManager
from .transfers import TransferManager

@dataclass(frozen=True)
class ActionPolicy:
    role: str  # papel mínimo
    confirm: bool = False  # exige confirmação explícita do usuário no painel
    audit: bool = True  # registra envio/resultado na auditoria (leituras automáticas não)


# Ações que o servidor aceita encaminhar. Cada fase adiciona novas entradas.
ALLOWED_ACTIONS: dict[str, ActionPolicy] = {
    "ping": ActionPolicy("viewer"),
    "system.info": ActionPolicy("viewer"),
    # SYSTEM MONITOR (Fase 4): leituras atualizadas automaticamente pelo painel
    "system.processes": ActionPolicy("viewer", audit=False),
    "system.apps": ActionPolicy("viewer", audit=False),
    "system.services": ActionPolicy("viewer", audit=False),
    "system.hardware": ActionPolicy("viewer", audit=False),
    "system.network": ActionPolicy("viewer", audit=False),
    "process.kill": ActionPolicy("admin", confirm=True),
    # Tela remota (Fase 5): somente administradores; início sempre auditado e notificado
    "screen.monitors": ActionPolicy("admin", audit=False),
    "screen.start": ActionPolicy("admin"),
    "screen.stop": ActionPolicy("viewer", audit=False),
    # Energia (Fase 6): ações que tiram o PC do ar exigem confirmação explícita
    "power.status": ActionPolicy("viewer", audit=False),
    "power.shutdown": ActionPolicy("admin", confirm=True),
    "power.restart": ActionPolicy("admin", confirm=True),
    "power.sleep": ActionPolicy("admin", confirm=True),
    "power.hibernate": ActionPolicy("admin", confirm=True),
    "power.lock": ActionPolicy("admin"),
    "power.cancel": ActionPolicy("admin"),
    # Arquivos (Fase 7): somente pastas liberadas no PC; transferências passam por REST
    "files.roots": ActionPolicy("admin", audit=False),
    "files.list": ActionPolicy("admin", audit=False),
    "files.mkdir": ActionPolicy("admin"),
    # Limpeza e otimização: apagar exige confirmação; o dono pode bloquear tudo no PC
    "maint.scan": ActionPolicy("admin", audit=False),
    "maint.clean": ActionPolicy("admin", confirm=True),
    "maint.startup": ActionPolicy("admin", audit=False),
    "maint.startup.set": ActionPolicy("admin"),
    "perf.status": ActionPolicy("viewer", audit=False),
    "perf.mode": ActionPolicy("admin"),
    "perf.plan": ActionPolicy("admin"),
    "perf.game": ActionPolicy("admin"),
}
POWER_LABELS = {"shutdown": "desligamento", "restart": "reinício", "sleep": "suspensão", "hibernate": "hibernação"}

ROLE_LEVEL = {"viewer": 0, "admin": 1, "owner": 2}

HISTORY_LEN = 300  # amostras em memória por dispositivo (~10 min a cada 2 s)


def role_at_least(user: dict, role: str) -> bool:
    return ROLE_LEVEL.get(user["role"], -1) >= ROLE_LEVEL[role]


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
    high_since: dict[str, float] = field(default_factory=dict)
    last_alert: dict[str, float] = field(default_factory=dict)
    exit_state: str | None = None  # 'paused' | 'stopping' quando o dono encerra no próprio PC
    name: str = ""  # nome do computador (evita consultar o banco a cada amostra)
    power: dict | None = None  # ação de energia agendada


# Motivos de desconexão informados pelo agente antes de fechar
EXIT_STATES = {"paused": "paused", "stopping": "stopped", "shutdown": "power_off", "restart": "restarting",
               "sleep": "sleeping", "hibernate": "hibernating"}


# Fila de saída por painel: um painel lento/travado nunca atrasa os outros nem os agentes.
CLIENT_QUEUE = 512  # mensagens; acima disso o painel é desconectado (reconecta sozinho)
DROPPABLE = {"metrics", "transfer"}  # descartáveis quando o painel está atrasado (chegam de novo em ~1 s)


@dataclass(eq=False)  # identidade por objeto (usado em set)
class ClientConn:
    ws: WebSocket
    user: dict
    session_id: str
    access: set[str] | None = None  # None = todos os dispositivos
    outbox: asyncio.Queue | None = None
    writer: asyncio.Task | None = None
    dropped: int = 0

    def can_see(self, device_id: str | None) -> bool:
        if device_id is None:
            return True
        return self.access is None or device_id in self.access

    def can_see_note(self, note: dict) -> bool:
        if note.get("device_id"):
            return self.can_see(note["device_id"])
        return role_at_least(self.user, "admin")  # alertas de segurança globais


@dataclass
class PendingCommand:
    client: ClientConn
    device_id: str
    action: str
    audit: bool = True
    params: dict = field(default_factory=dict)
    created: float = field(default_factory=time.time)


class Hub:
    def __init__(self, db: Database, settings, prefs: Preferences):
        self.db = db
        self.settings = settings
        self.prefs = prefs
        self.agents: dict[str, AgentConn] = {}
        self.clients: set[ClientConn] = set()
        self.pending: dict[str, PendingCommand] = {}
        self._lock = asyncio.Lock()
        self._global_alerts: dict[str, float] = {}
        self.screens = ScreenManager(self)
        self.transfers = TransferManager(self)
        self.server_calls: dict[str, tuple[str, asyncio.Future]] = {}
        self.push = None  # PushDispatcher (definido em main.py)

    # -- envio seguro --------------------------------------------------------
    @staticmethod
    async def _send(ws: WebSocket, msg: dict) -> bool:
        try:
            await ws.send_json(msg)
            return True
        except Exception:  # conexão já encerrada
            return False

    @staticmethod
    async def _send_bytes(ws: WebSocket, data: bytes) -> bool:
        try:
            await ws.send_bytes(data)
            return True
        except Exception:
            return False

    async def server_call(self, device_id: str, action: str, params: dict, timeout: float = 30) -> dict:
        """Comando iniciado pelo próprio servidor (ex.: abrir transferência). Retorna `data` ou levanta erro."""
        from .transfers import TransferError

        conn = self.agents.get(device_id)
        if not conn:
            raise TransferError("Computador offline.")
        cmd_id = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.server_calls[cmd_id] = (device_id, fut)
        try:
            if not await self._send(conn.ws, {"type": "command", "id": cmd_id, "action": action, "params": params}):
                raise TransferError("Conexão com o computador perdida.")
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise TransferError("O computador não respondeu a tempo.") from None
        finally:
            self.server_calls.pop(cmd_id, None)
        if not msg.get("ok"):
            raise TransferError(msg.get("error") or "Falha no computador.")
        return msg.get("data") or {}

    # -- envio para painéis (sempre pela fila, nunca bloqueia quem chamou) ----
    def start_client(self, client: ClientConn) -> None:
        client.outbox = asyncio.Queue(maxsize=CLIENT_QUEUE)
        client.writer = asyncio.create_task(self._client_writer(client))
        self.clients.add(client)

    async def stop_client(self, client: ClientConn) -> None:
        self.clients.discard(client)
        if client.writer:
            client.writer.cancel()

    async def _client_writer(self, client: ClientConn) -> None:
        try:
            while True:
                text = await client.outbox.get()
                await asyncio.wait_for(client.ws.send_text(text), timeout=20)
        except asyncio.CancelledError:
            pass
        except Exception:  # travado há 20 s ou conexão caiu
            self.clients.discard(client)
            try:
                await client.ws.close(code=1013)
            except Exception:
                pass

    def send_client(self, client: ClientConn, msg: dict, text: str | None = None) -> bool:
        q = client.outbox
        if q is None:
            return False
        if msg.get("type") in DROPPABLE and q.qsize() >= CLIENT_QUEUE // 2:
            client.dropped += 1  # atrasado: descarta só o que é reenviado em seguida
            return False
        try:
            q.put_nowait(text if text is not None else json.dumps(msg))
            return True
        except asyncio.QueueFull:  # nem os avisos importantes cabem: desconecta (reconecta e recebe tudo de novo)
            self.clients.discard(client)
            if client.writer:
                client.writer.cancel()
            asyncio.create_task(self._close_quietly(client.ws, 1013))
            return False

    @staticmethod
    async def _close_quietly(ws: WebSocket, code: int) -> None:
        try:
            await ws.close(code=code)
        except Exception:
            pass

    async def broadcast(self, msg: dict, device_id: str | None = None) -> None:
        text = json.dumps(msg)  # serializa uma vez para todos
        for c in [c for c in list(self.clients) if c.can_see(device_id)]:
            self.send_client(c, msg, text)

    async def notify(self, level: str, kind: str, message: str, device_id: str | None = None) -> None:
        note = self.db.add_notification(level, kind, message, device_id)
        if self.push:
            self.push.dispatch(note)  # notificação push para aparelhos com o app fechado
        msg = {"type": "notification", "data": note}
        text = json.dumps(msg)
        for c in [c for c in list(self.clients) if c.can_see_note(note)]:
            self.send_client(c, msg, text)

    async def security_alert(self, key: str, message: str, seconds: float = 60) -> None:
        """Alerta de tentativa indevida com limite de 1 por minuto por origem (a auditoria registra todas)."""
        now = time.time()
        if now - self._global_alerts.get(f"sec:{key}", 0) < seconds:
            return
        self._global_alerts[f"sec:{key}"] = now
        if len(self._global_alerts) > 10_000:  # não cresce sem limite sob ataque distribuído
            cutoff = now - 3600
            self._global_alerts = {k: v for k, v in self._global_alerts.items() if v > cutoff}
        await self.notify("critical", "unauthorized", message)

    def rename_agent(self, device_id: str, name: str) -> None:
        conn = self.agents.get(device_id)
        if conn:
            conn.name = name

    def _cooldown_ok(self, store: dict[str, float], key: str) -> bool:
        now = time.time()
        if now - store.get(key, 0) < self.prefs["alert_cooldown_minutes"] * 60:
            return False
        store[key] = now
        return True

    async def connection_failure(self, key: str, message: str, device_id: str | None = None) -> None:
        """Notifica falhas de conexão/autenticação de agentes (com cooldown por origem)."""
        if self.prefs["notify_connection_failures"] and self._cooldown_ok(self._global_alerts, key):
            await self.notify("critical", "connection_failure", message, device_id)

    # -- snapshot para clientes ------------------------------------------------
    def device_view(self, row: dict) -> dict:
        conn = self.agents.get(row["id"])
        return {
            **row,
            "state": "online" if conn else (row.get("last_state") or "offline"),
            "online": conn is not None,
            "info": conn.info if conn else None,
            "connected_at": conn.connected_at if conn else None,
            "metrics": conn.last_metrics if conn else None,
            "power": conn.power if conn else None,
            "history": list(conn.history) if conn else [],
        }

    def snapshot(self, access: set[str] | None) -> list[dict]:
        return [self.device_view(d) for d in self.db.list_devices() if access is None or d["id"] in access]

    # -- agentes ---------------------------------------------------------------
    async def agent_connected(self, device: dict, ws: WebSocket, info: dict) -> AgentConn:
        async with self._lock:
            old = self.agents.get(device["id"])
            conn = AgentConn(device_id=device["id"], ws=ws, info=info, name=device["name"])
            self.agents[device["id"]] = conn
        if old:
            try:
                await old.ws.close(code=4000, reason="substituída por nova conexão")
            except Exception:
                pass
        self.db.update_device_seen(device["id"], info.get("hostname"), info.get("os"))
        self.db.set_device_state(device["id"], "online", str(info.get("agent_version") or "")[:32] or None)
        mac = info.get("mac") if isinstance(info.get("mac"), str) and len(info.get("mac")) <= 32 else None
        ip = info.get("ip") if isinstance(info.get("ip"), str) and len(info.get("ip")) <= 64 else None
        self.db.set_device_network(device["id"], mac, ip)
        self.db.audit(
            "agent.connected", device_id=device["id"],
            detail={"hostname": info.get("hostname"), "agent_version": info.get("agent_version")},
        )
        await self.broadcast(
            {"type": "device", "data": self.device_view(self.db.get_device_public(device["id"]))}, device["id"]
        )
        if self.prefs["notify_device_online"]:
            await self.notify("info", "device_online", f"{device['name']} conectado", device["id"])
        return conn

    async def agent_disconnected(self, conn: AgentConn) -> None:
        async with self._lock:
            if self.agents.get(conn.device_id) is not conn:
                return  # já foi substituída por uma conexão mais nova
            del self.agents[conn.device_id]
        state = EXIT_STATES.get(conn.exit_state or "", "offline")
        self.db.update_device_seen(conn.device_id)
        self.db.set_device_state(conn.device_id, state)
        device = self.db.get_device_public(conn.device_id)
        self.db.audit("agent.disconnected", device_id=conn.device_id, detail={"reason": state})
        await self.screens.agent_gone(conn.device_id)
        await self.transfers.agent_gone(conn.device_id)
        for cid, (dev, fut) in list(self.server_calls.items()):
            if dev == conn.device_id and not fut.done():
                fut.set_result({"ok": False, "error": "O computador desconectou."})
        # falha todos os comandos pendentes deste agente
        for cid, p in list(self.pending.items()):
            if p.device_id == conn.device_id:
                self.pending.pop(cid, None)
                self.send_client(p.client, {"type": "result", "id": cid, "ok": False, "error": "Agente desconectado"})
        if device and device["revoked"]:
            await self.broadcast({"type": "device_removed", "device_id": conn.device_id}, conn.device_id)
        elif device:
            await self.broadcast({"type": "device", "data": self.device_view(device)}, conn.device_id)
            if self.prefs["notify_device_offline"]:
                if state in ("power_off", "restarting", "sleeping", "hibernating"):
                    text = {"power_off": "desligado", "restarting": "reiniciando", "sleeping": "suspenso",
                            "hibernating": "hibernando"}[state]
                    await self.notify("info", "device_power", f"{device['name']}: {text} (pedido pelo painel)", conn.device_id)
                elif state == "paused":
                    await self.notify("info", "device_paused", f"{device['name']}: monitoramento pausado no próprio computador", conn.device_id)
                elif state == "stopped":
                    await self.notify("info", "device_stopped", f"{device['name']}: agente encerrado no próprio computador", conn.device_id)
                else:
                    await self.notify("warning", "device_offline", f"{device['name']} desconectado", conn.device_id)

    async def game_event(self, conn: AgentConn, msg: dict) -> None:
        """Modo jogo automático: jogo abriu (desempenho máximo) ou fechou (volta ao modo anterior)."""
        device = self.db.get_device_public(conn.device_id)
        name = device["name"] if device else conn.device_id
        game = str(msg.get("game") or "jogo")[:80]
        if msg.get("event") == "started":
            if msg.get("error"):
                await self.notify("warning", "game_mode", f"{name}: {game} aberto, mas o modo desempenho falhou — {str(msg['error'])[:200]}", conn.device_id)
            else:
                await self.notify("info", "game_mode", f"{name}: modo jogo ativado ({game}) — Melhor desempenho", conn.device_id)
        elif msg.get("event") == "stopped":
            await self.notify("info", "game_mode", f"{name}: modo jogo desativado ({game} fechado)", conn.device_id)

    async def power_event(self, conn: AgentConn, msg: dict) -> None:
        """Agendamentos/cancelamentos vindos do agente (inclusive cancelamento feito no próprio PC)."""
        device = self.db.get_device_public(conn.device_id)
        name = device["name"] if device else conn.device_id
        event, action = msg.get("event"), msg.get("action")
        label = POWER_LABELS.get(action, "ação de energia")
        if event == "scheduled" and action in POWER_LABELS:
            conn.power = {k: msg.get(k) for k in ("action", "at", "delay", "by")}
            conn.exit_state = action
            delay = int(msg.get("delay") or 0)
            when = "agora" if delay < 5 else (f"em {delay} s" if delay < 120 else f"em {round(delay / 60)} min")
            await self.notify("warning", "power", f"{name}: {label} {when} (pedido por {msg.get('by')})", conn.device_id)
            self.db.audit("power.scheduled", device_id=conn.device_id, detail=conn.power)
        elif event in ("canceled", "failed"):
            conn.power = None
            conn.exit_state = None
            if event == "canceled":
                who = "no próprio computador" if msg.get("by") == "owner" else f"por {msg.get('by')}"
                await self.notify("info", "power", f"{name}: {label} cancelado(a) {who}", conn.device_id)
            else:
                await self.notify("warning", "power", f"{name}: {label} falhou — {msg.get('error')}", conn.device_id)
            self.db.audit(f"power.{event}", device_id=conn.device_id, detail={"action": action, "by": msg.get("by"), "error": msg.get("error")})
        else:
            return
        await self.broadcast({"type": "device", "data": self.device_view(device)}, conn.device_id)

    def agent_status(self, conn: AgentConn, state: str) -> None:
        """O agente avisa que vai fechar por decisão do proprietário (pausa ou encerramento)."""
        if state in EXIT_STATES:
            if state == "stopping" and conn.exit_state in POWER_LABELS:
                return  # o Windows encerrou o agente porque o PC está desligando/reiniciando
            conn.exit_state = state

    async def agent_metrics(self, conn: AgentConn, metrics: dict) -> None:
        summary = summarize(metrics)
        conn.last_metrics = metrics
        conn.history.append(summary)
        now = time.time()
        if now - conn.last_persist >= self.settings.history_persist_interval:
            conn.last_persist = now
            self.db.add_metric(conn.device_id, summary)
            self.db.update_device_seen(conn.device_id)
        await self.broadcast(
            {"type": "metrics", "device_id": conn.device_id, "data": metrics, "summary": summary}, conn.device_id
        )
        await self._check_alerts(conn, summary)

    async def _check_alerts(self, conn: AgentConn, s: dict) -> None:
        p = self.prefs
        name = conn.name or conn.device_id
        now = time.time()
        checks = [
            ("high_cpu", s.get("cpu"), p["cpu_alert_percent"], "warning", "uso de CPU elevado ({v:.0f}%)"),
            ("high_ram", s.get("ram"), p["ram_alert_percent"], "warning", "memória RAM próxima do limite ({v:.0f}%)"),
            ("high_temp", s.get("temp"), p["temp_alert_celsius"], "critical", "temperatura elevada ({v:.0f} °C)"),
        ]
        for kind, value, limit, level, text in checks:
            if value is None or value < limit:
                conn.high_since.pop(kind, None)
                continue
            since = conn.high_since.setdefault(kind, now)
            if now - since >= p["alert_sustain_seconds"] and p[f"notify_{kind}"] and self._cooldown_ok(conn.last_alert, kind):
                await self.notify(level, kind, f"{name}: " + text.format(v=value), conn.device_id)

    # -- comandos --------------------------------------------------------------
    async def send_command(self, client: ClientConn, msg: dict) -> None:
        req_id = str(msg.get("request_id") or "")[:64]
        device_id = msg.get("device_id")
        action = msg.get("action")
        params = msg.get("params") or {}

        async def reject(error: str) -> None:
            self.db.audit(
                "command.rejected", user=client.user, device_id=device_id if isinstance(device_id, str) else None,
                detail={"action": action, "error": error}, success=False,
            )
            self.send_client(client, {"type": "result", "request_id": req_id, "ok": False, "error": error})

        if action not in ALLOWED_ACTIONS:
            return await reject("Ação não permitida")
        policy = ALLOWED_ACTIONS[action]
        if not role_at_least(client.user, policy.role):
            return await reject("Permissão insuficiente")
        if not isinstance(device_id, str) or not client.can_see(device_id):
            return await reject("Sem acesso a este dispositivo")
        if policy.confirm and msg.get("confirm") is not True:
            return await reject("Ação crítica exige confirmação")
        if not isinstance(params, dict):
            return await reject("Parâmetros inválidos")
        conn = self.agents.get(device_id)
        if not conn:
            return await reject("Dispositivo offline")
        params = {k: v for k, v in params.items() if not str(k).startswith("_")}  # campos internos são do servidor
        if action.startswith("power."):
            params["_user"] = client.user["username"]  # quem pediu aparece no aviso do Windows
        if action.startswith("screen."):
            params, local = self.screens.prepare(client, action, device_id, params)
            if local is not None:
                return self.send_client(client, {"type": "result", "request_id": req_id, "ok": True, "data": local})

        cmd_id = uuid.uuid4().hex
        self.pending[cmd_id] = PendingCommand(
            client=client, device_id=device_id, action=action, audit=policy.audit, params=params
        )
        if policy.audit:
            self.db.audit("command.sent", user=client.user, device_id=device_id, detail={"action": action, "params": params})
        self.send_client(client, {"type": "ack", "request_id": req_id, "id": cmd_id})
        if not await self._send(conn.ws, {"type": "command", "id": cmd_id, "action": action, "params": params}):
            self.pending.pop(cmd_id, None)
            self.send_client(client, {"type": "result", "id": cmd_id, "request_id": req_id, "ok": False, "error": "Falha ao enviar"})

    async def command_result(self, conn: AgentConn, msg: dict) -> None:
        cmd_id = msg.get("id")
        call = self.server_calls.get(cmd_id)
        if call and call[0] == conn.device_id and not call[1].done():
            call[1].set_result(msg)
            return
        pending = self.pending.pop(cmd_id, None)
        if not pending or pending.device_id != conn.device_id:
            return  # resultado desconhecido ou de outro dispositivo: ignorado
        ok = bool(msg.get("ok"))
        if pending.audit:
            detail = {"action": pending.action, "ok": ok, "error": msg.get("error")}
            if pending.action == "process.kill":
                detail["process"] = f"{pending.params.get('name')} (PID {pending.params.get('pid')})"
            self.db.audit("command.result", user=pending.client.user, device_id=conn.device_id, detail=detail, success=ok)
        self.send_client(pending.client, {"type": "result", "id": cmd_id, "ok": ok, "data": msg.get("data"), "error": msg.get("error")})
        if ok and pending.action == "screen.start":
            self.screens.started(pending.client, conn.device_id, pending.params, msg.get("data") or {})
        if pending.audit and pending.action != "screen.start" and self.prefs["notify_commands"]:
            who = pending.client.user["username"]
            if pending.action == "process.kill":
                proc = f"{pending.params.get('name')} (PID {pending.params.get('pid')})"
                text = f"Processo {proc} encerrado por {who}" if ok else f"Encerramento de {proc} falhou: {msg.get('error')}"
            else:
                status = "executado" if ok else f"falhou ({msg.get('error')})"
                text = f"Comando '{pending.action}' {status} por {who}"
            await self.notify("info" if ok else "warning", "command", text, conn.device_id)

    async def expire_pending(self) -> None:
        now = time.time()
        for cid, p in list(self.pending.items()):
            if now - p.created > self.settings.command_timeout:
                self.pending.pop(cid, None)
                self.send_client(p.client, {"type": "result", "id": cid, "ok": False, "error": "Tempo esgotado"})

    # -- revogação -------------------------------------------------------------
    async def _close_clients(self, clients: list[ClientConn], reason: str) -> None:
        for c in clients:
            self.clients.discard(c)
            try:
                await c.ws.close(code=4401, reason=reason)
            except Exception:
                pass

    async def close_session(self, session_id: str) -> None:
        await self._close_clients([c for c in list(self.clients) if c.session_id == session_id], "sessão revogada")

    async def close_user(self, user_id: int) -> None:
        """Encerra as conexões do usuário (o navegador reconecta com permissões atualizadas)."""
        await self._close_clients([c for c in list(self.clients) if c.user["id"] == user_id], "permissões alteradas")

    async def close_device(self, device_id: str) -> None:
        conn = self.agents.get(device_id)
        if conn:
            try:
                await conn.ws.close(code=4403, reason="dispositivo revogado")
            except Exception:
                pass
            await self.agent_disconnected(conn)
