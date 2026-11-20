"""Vinculação e gerenciamento de dispositivos, notificações e auditoria."""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import security
from .auth import client_ip, current_user, require_role
from .hub import role_at_least

router = APIRouter(prefix="/api", tags=["devices"])


class PairingRequest(BaseModel):
    device_name: str = Field(min_length=1, max_length=64)


class AgentPairRequest(BaseModel):
    code: str = Field(min_length=8, max_length=16)
    hostname: str = Field(default="", max_length=128)
    os: str = Field(default="", max_length=128)


class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


@router.post("/pairing-codes")
def create_pairing_code(body: PairingRequest, request: Request, user: dict = Depends(require_role("admin"))):
    """Gera um código de uso único (10 min) para vincular um novo computador."""
    st = request.app.state
    code = security.new_pairing_code()
    expires = st.db.create_pairing_code(security.hash_token(code), user["id"], body.device_name.strip(), st.settings.pairing_ttl)
    st.db.audit("device.pairing_code", user=user, detail={"device_name": body.device_name}, ip=client_ip(request))
    return {"code": code, "expires_at": expires}


@router.post("/agent/pair")
def agent_pair(body: AgentPairRequest, request: Request):
    """Chamado pelo agente. Troca o código de pareamento por credenciais permanentes do dispositivo."""
    st = request.app.state
    ip = client_ip(request)
    key = f"pair:{ip}"
    if st.limiter.is_locked(key):
        raise HTTPException(429, "Muitas tentativas. Aguarde alguns minutos.")
    row = st.db.consume_pairing_code(security.hash_token(security.normalize_pairing_code(body.code)))
    if not row:
        st.limiter.fail(key)
        st.db.audit("device.pair_failed", ip=ip, success=False)
        raise HTTPException(401, "Código inválido ou expirado")
    device_id = uuid.uuid4().hex
    secret = security.new_token(48)
    st.db.create_device(device_id, row["device_name"], row["user_id"], security.hash_token(secret), body.hostname, body.os)
    creator = st.db.get_user(row["user_id"])
    if creator and st.db.accessible_device_ids(creator) is not None:
        st.db.grant_device(creator["id"], device_id)  # quem vincula passa a ter acesso
    st.db.audit("device.paired", user=creator, device_id=device_id, detail={"hostname": body.hostname}, ip=ip)
    return {"device_id": device_id, "device_secret": secret, "name": row["device_name"]}


def _require_access(request: Request, user: dict, device_id: str) -> dict:
    st = request.app.state
    dev = st.db.get_device(device_id)
    access = st.db.accessible_device_ids(user)
    if not dev or dev["revoked"] or (access is not None and device_id not in access):
        raise HTTPException(404, "Dispositivo não encontrado")
    return dev


@router.get("/devices")
def list_devices(request: Request, user: dict = Depends(current_user)):
    st = request.app.state
    return st.hub.snapshot(st.db.accessible_device_ids(user))


@router.patch("/devices/{device_id}")
def rename_device(device_id: str, body: RenameRequest, request: Request, user: dict = Depends(require_role("admin"))):
    st = request.app.state
    _require_access(request, user, device_id)
    st.db.rename_device(device_id, body.name.strip())
    st.db.audit("device.renamed", user=user, device_id=device_id, detail={"name": body.name}, ip=client_ip(request))
    return {"ok": True}


@router.delete("/devices/{device_id}")
async def revoke_device(device_id: str, request: Request, user: dict = Depends(require_role("admin"))):
    """Revoga o dispositivo: as credenciais do agente deixam de valer imediatamente."""
    st = request.app.state
    _require_access(request, user, device_id)
    st.db.revoke_device(device_id)
    st.db.audit("device.revoked", user=user, device_id=device_id, ip=client_ip(request))
    await st.hub.close_device(device_id)
    await st.hub.broadcast({"type": "device_removed", "device_id": device_id})
    return {"ok": True}


@router.get("/devices/{device_id}/history")
def device_history(device_id: str, request: Request, hours: float = 24, user: dict = Depends(current_user)):
    _require_access(request, user, device_id)
    hours = max(0.1, min(hours, 24 * 30))
    return request.app.state.db.metric_history(device_id, time.time() - hours * 3600)


@router.get("/notifications")
def notifications(request: Request, user: dict = Depends(current_user)):
    """Notificações visíveis ao usuário; o estado 'lida' é individual."""
    st = request.app.state
    access = st.db.accessible_device_ids(user)
    is_admin = role_at_least(user, "admin")
    out = []
    for n in st.db.list_notifications(500):
        visible = (access is None or n["device_id"] in access) if n["device_id"] else is_admin
        if visible:
            out.append({**n, "read": 1 if n["id"] <= user["notif_read_upto"] else 0})
            if len(out) >= 100:
                break
    return out


@router.post("/notifications/read-all")
def notifications_read(request: Request, user: dict = Depends(current_user)):
    st = request.app.state
    st.db.update_user(user["id"], notif_read_upto=st.db.last_notification_id())
    return {"ok": True}


@router.get("/audit")
def audit(request: Request, limit: int = 100, user: dict = Depends(require_role("admin"))):
    st = request.app.state
    limit = max(1, min(limit, 500))
    access = st.db.accessible_device_ids(user)
    if access is None:
        return st.db.list_audit(limit)
    # administrador restrito: só eventos sem dispositivo ou dos dispositivos liberados
    rows = [r for r in st.db.list_audit(2000) if r["device_id"] is None or r["device_id"] in access]
    return rows[:limit]
