"""Endpoints WebSocket para agentes e clientes.

A autenticação é feita na *primeira mensagem* (e não na URL) para que
credenciais não apareçam em logs de proxy/servidor.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from . import security
from .auth import public_user, resolve_access_token
from .hub import ClientConn

router = APIRouter()
AUTH_TIMEOUT = 10
MAX_MESSAGE = 4 * 1024 * 1024


async def _first_message(ws: WebSocket) -> dict | None:
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT)
        msg = json.loads(raw)
        return msg if isinstance(msg, dict) and msg.get("type") == "auth" else None
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect, RuntimeError):
        return None


@router.websocket("/ws/agent")
async def agent_socket(ws: WebSocket):
    st = ws.app.state
    await ws.accept()
    msg = await _first_message(ws)
    device = st.db.get_device(str(msg.get("device_id", ""))) if msg else None
    secret = str(msg.get("secret", "")) if msg else ""
    if not device or device["revoked"] or not security.tokens_equal(device["secret_hash"], security.hash_token(secret)):
        st.db.audit(
            "agent.auth_failed",
            device_id=(msg or {}).get("device_id") if isinstance((msg or {}).get("device_id"), str) else None,
            ip=ws.client.host if ws.client else None,
            success=False,
        )
        await ws.send_json({"type": "auth_error", "error": "Credenciais do dispositivo inválidas"})
        await ws.close(code=4401)
        return

    info = msg.get("info") if isinstance(msg.get("info"), dict) else {}
    await ws.send_json({"type": "auth_ok", "device_id": device["id"], "name": device["name"]})
    conn = await st.hub.agent_connected(device, ws, info)
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > MAX_MESSAGE:
                continue
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            kind = data.get("type")
            if kind == "metrics" and isinstance(data.get("data"), dict):
                await st.hub.agent_metrics(conn, data["data"])
            elif kind == "result":
                await st.hub.command_result(conn, data)
            elif kind == "info" and isinstance(data.get("data"), dict):
                conn.info = data["data"]
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await st.hub.agent_disconnected(conn)


@router.websocket("/ws/client")
async def client_socket(ws: WebSocket):
    st = ws.app.state
    await ws.accept()
    msg = await _first_message(ws)
    try:
        if not msg:
            raise HTTPException(401)
        user, session = resolve_access_token(st, str(msg.get("token", "")))
    except HTTPException:
        await ws.send_json({"type": "auth_error", "error": "Não autenticado"})
        await ws.close(code=4401)
        return

    client = ClientConn(ws=ws, user=user, session_id=session["id"])
    st.hub.clients.add(client)
    await ws.send_json({"type": "hello", "user": public_user(user), "devices": st.hub.snapshot()})
    try:
        while True:
            data = await ws.receive_json()
            if not isinstance(data, dict):
                continue
            # revalida a sessão a cada comando (revogação tem efeito imediato)
            s = st.db.get_session(client.session_id)
            if not s or s["revoked"]:
                await ws.close(code=4401)
                break
            if data.get("type") == "command":
                await st.hub.send_command(client, data)
            elif data.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        st.hub.clients.discard(client)
