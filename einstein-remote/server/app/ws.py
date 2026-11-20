"""Endpoints WebSocket para agentes e clientes.

A autenticação é feita na *primeira mensagem* (e não na URL) para que
credenciais não apareçam em logs de proxy/servidor.
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from . import security
from .auth import public_user, resolve_access_token
from .hub import ClientConn

router = APIRouter()
AUTH_TIMEOUT = 10
MAX_MESSAGE = 4 * 1024 * 1024
MAX_FRAME = 8 * 1024 * 1024
SESSION_RECHECK = 5.0


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
        ip = ws.client.host if ws.client else "?"
        st.db.audit("agent.auth_failed", device_id=device["id"] if device else None, ip=ip, success=False)
        await st.hub.connection_failure(
            f"agent_auth:{ip}", f"Agente recusado: credencial inválida ou revogada (IP {ip})", device["id"] if device else None
        )
        await ws.send_json({"type": "auth_error", "error": "Credenciais do dispositivo inválidas"})
        await ws.close(code=4401)
        return

    info = msg.get("info") if isinstance(msg.get("info"), dict) else {}
    await ws.send_json({"type": "auth_ok", "device_id": device["id"], "name": device["name"]})
    conn = await st.hub.agent_connected(device, ws, info)
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:  # quadro de tela (binário)
                if len(message["bytes"]) <= MAX_FRAME:
                    await st.hub.screens.frame(conn.device_id, message["bytes"])
                continue
            raw = message.get("text") or ""
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
            elif kind == "screen_event":
                await st.hub.screens.agent_event(conn.device_id, data)
            elif kind == "status" and isinstance(data.get("state"), str):
                st.hub.agent_status(conn, data["state"])
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

    access = st.db.accessible_device_ids(user)
    client = ClientConn(ws=ws, user=user, session_id=session["id"], access=access)
    st.hub.clients.add(client)
    await ws.send_json({"type": "hello", "user": public_user(user), "devices": st.hub.snapshot(access)})
    last_check = 0.0
    try:
        while True:
            data = await ws.receive_json()
            if not isinstance(data, dict):
                continue
            kind = data.get("type")
            # revalida a sessão a cada comando e, para eventos frequentes (mouse), a cada poucos segundos
            now = time.time()
            if kind == "command" or now - last_check > SESSION_RECHECK:
                last_check = now
                s = st.db.get_session(client.session_id)
                if not s or s["revoked"]:
                    await ws.close(code=4401)
                    break
            if kind == "command":
                await st.hub.send_command(client, data)
            elif kind in ("input", "screen_ack"):
                await st.hub.screens.client_message(client, data)
            elif kind == "ping":
                await ws.send_json({"type": "pong"})
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        st.hub.clients.discard(client)
        await st.hub.screens.client_gone(client)
