"""Gestão de usuários, permissões por dispositivo e configurações de alertas.

Regras:
* Apenas o **proprietário** cria, altera e remove usuários.
* Existe exatamente um proprietário; ele não pode ser removido nem rebaixado.
* Usuários podem ter acesso a *todos* os dispositivos ou a uma lista específica.
* Qualquer mudança de permissão encerra as conexões em tempo real do usuário
  afetado, que reconecta já com as novas permissões.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import security
from .auth import client_ip, current_user, require_role
from .preferences import SettingsUpdate

router = APIRouter(prefix="/api", tags=["users"])

USERNAME = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")


class UserCreate(BaseModel):
    username: str = USERNAME
    password: str = Field(min_length=1, max_length=256)
    role: Literal["admin", "viewer"]
    all_devices: bool = True
    device_ids: list[str] = Field(default_factory=list, max_length=500)


class UserUpdate(BaseModel):
    role: Literal["admin", "viewer"] | None = None
    all_devices: bool | None = None
    device_ids: list[str] | None = Field(default=None, max_length=500)
    new_password: str | None = Field(default=None, max_length=256)


def _valid_device_ids(request: Request, ids: list[str]) -> list[str]:
    known = {d["id"] for d in request.app.state.db.list_devices()}
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise HTTPException(422, "Dispositivo inexistente na lista de acesso")
    return sorted(set(ids))


def _user_view(db, user_id: int) -> dict:
    return next(u for u in db.list_users() if u["id"] == user_id)


@router.get("/users")
def list_users(request: Request, user: dict = Depends(require_role("owner"))):
    return request.app.state.db.list_users()


@router.post("/users", status_code=201)
def create_user(body: UserCreate, request: Request, user: dict = Depends(require_role("owner"))):
    st = request.app.state
    if st.db.get_user_by_name(body.username):
        raise HTTPException(409, "Nome de usuário já existe")
    err = security.validate_password_strength(body.password)
    if err:
        raise HTTPException(422, err)
    ids = _valid_device_ids(request, body.device_ids)
    uid = st.db.create_user(body.username, security.hash_password(body.password), body.role)
    st.db.update_user(uid, all_devices=1 if body.all_devices else 0, notif_read_upto=st.db.last_notification_id())
    st.db.set_user_devices(uid, ids)
    st.db.audit(
        "user.created", user=user, ip=client_ip(request),
        detail={"username": body.username, "role": body.role, "all_devices": body.all_devices, "devices": len(ids)},
    )
    return _user_view(st.db, uid)


@router.patch("/users/{user_id}")
async def update_user(user_id: int, body: UserUpdate, request: Request, user: dict = Depends(require_role("owner"))):
    st = request.app.state
    target = st.db.get_user(user_id)
    if not target:
        raise HTTPException(404, "Usuário não encontrado")
    if target["role"] == "owner":
        raise HTTPException(403, "O proprietário não pode ser alterado por aqui")
    changes: dict = {}
    if body.role is not None:
        changes["role"] = body.role
    if body.all_devices is not None:
        changes["all_devices"] = 1 if body.all_devices else 0
    if body.new_password:
        err = security.validate_password_strength(body.new_password)
        if err:
            raise HTTPException(422, err)
        changes["password_hash"] = security.hash_password(body.new_password)
    if body.device_ids is not None:
        st.db.set_user_devices(user_id, _valid_device_ids(request, body.device_ids))
    st.db.update_user(user_id, **changes)

    if "password_hash" in changes:  # redefinição de senha encerra todas as sessões
        for sid in st.db.revoke_user_sessions(user_id):
            await st.hub.close_session(sid)
    await st.hub.close_user(user_id)
    detail = {k: v for k, v in body.model_dump(exclude_none=True).items() if k != "new_password"}
    if body.new_password:
        detail["password_reset"] = True
    st.db.audit("user.updated", user=user, ip=client_ip(request), detail={"username": target["username"], **detail})
    return _user_view(st.db, user_id)


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, request: Request, user: dict = Depends(require_role("owner"))):
    st = request.app.state
    target = st.db.get_user(user_id)
    if not target:
        raise HTTPException(404, "Usuário não encontrado")
    if target["role"] == "owner":
        raise HTTPException(403, "O proprietário não pode ser removido")
    for sid in st.db.revoke_user_sessions(user_id):
        await st.hub.close_session(sid)
    await st.hub.close_user(user_id)
    st.db.delete_user(user_id)
    st.db.audit("user.deleted", user=user, ip=client_ip(request), detail={"username": target["username"]})
    return {"ok": True}


# ------------------------------------------------------------ configurações
@router.get("/settings")
def get_settings(request: Request, user: dict = Depends(current_user)):
    return request.app.state.prefs.all()


@router.put("/settings")
def update_settings(body: SettingsUpdate, request: Request, user: dict = Depends(require_role("admin"))):
    st = request.app.state
    changes = st.prefs.update(body)
    if changes:
        st.db.audit("settings.updated", user=user, ip=client_ip(request), detail=changes)
    return st.prefs.all()
