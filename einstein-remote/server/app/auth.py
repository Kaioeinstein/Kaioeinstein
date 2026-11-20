"""Autenticação de usuários e controle de sessões."""
from __future__ import annotations

import time
import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import security

router = APIRouter(prefix="/api/auth", tags=["auth"])
REFRESH_COOKIE = "er_refresh"


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: str = Field(min_length=1, max_length=256)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _state(request: Request):
    return request.app.state


def resolve_access_token(app_state, token: str) -> tuple[dict, dict]:
    """Valida o JWT *e* a sessão no banco (permitindo revogação imediata)."""
    try:
        payload = security.decode_access_token(token, app_state.settings.secret_key)
    except jwt.PyJWTError:
        raise HTTPException(401, "Token inválido ou expirado")
    session = app_state.db.get_session(payload["sid"])
    if not session or session["revoked"] or session["expires_at"] < time.time():
        raise HTTPException(401, "Sessão revogada ou expirada")
    user = app_state.db.get_user(int(payload["sub"]))
    if not user:
        raise HTTPException(401, "Usuário não encontrado")
    return user, session


def current_user(request: Request) -> dict:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "Autenticação necessária")
    user, session = resolve_access_token(_state(request), header[7:].strip())
    request.state.session_id = session["id"]
    return user


def require_role(min_role: str):
    levels = {"viewer": 0, "admin": 1, "owner": 2}

    def dep(user: dict = Depends(current_user)) -> dict:
        if levels.get(user["role"], -1) < levels[min_role]:
            raise HTTPException(403, "Permissão insuficiente")
        return user

    return dep


def public_user(user: dict) -> dict:
    return {"id": user["id"], "username": user["username"], "role": user["role"]}


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


def _issue_session(request: Request, response: Response, user: dict) -> dict:
    st = _state(request)
    sid = uuid.uuid4().hex
    refresh = security.new_token()
    st.db.create_session(
        sid, user["id"], security.hash_token(refresh), st.settings.refresh_ttl, client_ip(request), request.headers.get("user-agent")
    )
    _set_refresh_cookie(response, refresh, st.settings)
    return {
        "access_token": security.create_access_token(user["id"], sid, user["role"], st.settings.secret_key, st.settings.access_ttl),
        "expires_in": st.settings.access_ttl,
        "user": public_user(user),
    }


def _set_refresh_cookie(response: Response, refresh: str, settings) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=settings.refresh_ttl,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/api/auth",
    )


def _require_xhr(request: Request) -> None:
    # Defesa adicional contra CSRF: navegadores não enviam este cabeçalho em formulários cross-site.
    if request.headers.get("x-requested-with") != "EinsteinRemote":
        raise HTTPException(400, "Cabeçalho X-Requested-With ausente")


@router.get("/status")
def auth_status(request: Request):
    return {"setup_required": _state(request).db.count_users() == 0}


@router.post("/setup")
def setup(creds: Credentials, request: Request, response: Response):
    """Cria a conta do proprietário. Só funciona enquanto não houver nenhum usuário."""
    _require_xhr(request)
    st = _state(request)
    if st.db.count_users() > 0:
        raise HTTPException(409, "Configuração inicial já concluída")
    err = security.validate_password_strength(creds.password)
    if err:
        raise HTTPException(422, err)
    uid = st.db.create_user(creds.username, security.hash_password(creds.password), "owner")
    st.db.update_user(uid, last_login=time.time())
    user = st.db.get_user(uid)
    st.db.audit("auth.setup", user=user, ip=client_ip(request))
    return _issue_session(request, response, user)


@router.post("/login")
async def login(creds: Credentials, request: Request, response: Response):
    _require_xhr(request)
    st = _state(request)
    ip = client_ip(request)
    keys = (f"ip:{ip}", f"user:{creds.username.lower()}")
    if any(st.limiter.is_locked(k) for k in keys):
        st.db.audit("auth.login_blocked", detail={"username": creds.username}, ip=ip, success=False)
        raise HTTPException(429, "Muitas tentativas. Aguarde alguns minutos.")
    user = st.db.get_user_by_name(creds.username)
    if not user or not security.verify_password(creds.password, user["password_hash"]):
        for k in keys:
            st.limiter.fail(k)
        st.db.audit("auth.login_failed", detail={"username": creds.username}, ip=ip, success=False)
        await st.hub.notify("critical", "unauthorized", f"Tentativa de acesso não autorizada (usuário '{creds.username}', IP {ip})")
        raise HTTPException(401, "Usuário ou senha inválidos")
    for k in keys:
        st.limiter.reset(k)
    st.db.update_user(user["id"], last_login=time.time())
    st.db.audit("auth.login", user=user, ip=ip)
    return _issue_session(request, response, user)


@router.post("/refresh")
def refresh(request: Request, response: Response):
    """Troca o refresh token (cookie httpOnly) por um novo access token, com rotação."""
    _require_xhr(request)
    st = _state(request)
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(401, "Sem sessão")
    session = st.db.get_session_by_refresh(security.hash_token(token))
    if not session or session["revoked"] or session["expires_at"] < time.time():
        response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
        raise HTTPException(401, "Sessão expirada")
    user = st.db.get_user(session["user_id"])
    new_refresh = security.new_token()
    st.db.rotate_refresh(session["id"], security.hash_token(new_refresh))
    _set_refresh_cookie(response, new_refresh, st.settings)
    return {
        "access_token": security.create_access_token(
            user["id"], session["id"], user["role"], st.settings.secret_key, st.settings.access_ttl
        ),
        "expires_in": st.settings.access_ttl,
        "user": public_user(user),
    }


@router.post("/logout")
async def logout(request: Request, response: Response, user: dict = Depends(current_user)):
    st = _state(request)
    sid = request.state.session_id
    st.db.revoke_session(sid)
    st.db.audit("auth.logout", user=user, ip=client_ip(request))
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    await st.hub.close_session(sid)
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return public_user(user)


@router.post("/password")
async def change_password(body: PasswordChange, request: Request, user: dict = Depends(current_user)):
    """Troca a própria senha. Todas as outras sessões do usuário são encerradas."""
    st = _state(request)
    if not security.verify_password(body.current_password, user["password_hash"]):
        st.db.audit("auth.password_change_failed", user=user, ip=client_ip(request), success=False)
        raise HTTPException(403, "Senha atual incorreta")
    err = security.validate_password_strength(body.new_password)
    if err:
        raise HTTPException(422, err)
    if body.new_password == body.current_password:
        raise HTTPException(422, "A nova senha deve ser diferente da atual.")
    st.db.update_user(user["id"], password_hash=security.hash_password(body.new_password))
    revoked = st.db.revoke_user_sessions(user["id"], except_sid=request.state.session_id)
    for sid in revoked:
        await st.hub.close_session(sid)
    st.db.audit("auth.password_changed", user=user, detail={"sessions_revoked": len(revoked)}, ip=client_ip(request))
    return {"ok": True, "sessions_revoked": len(revoked)}


sessions_router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@sessions_router.get("")
def list_sessions(request: Request, user: dict = Depends(current_user)):
    current = request.state.session_id
    return [{**s, "current": s["id"] == current} for s in _state(request).db.list_sessions(user["id"])]


@sessions_router.delete("/{session_id}")
async def revoke_session(session_id: str, request: Request, user: dict = Depends(current_user)):
    st = _state(request)
    session = st.db.get_session(session_id)
    if not session or session["user_id"] != user["id"]:
        raise HTTPException(404, "Sessão não encontrada")
    st.db.revoke_session(session_id)
    st.db.audit("auth.session_revoked", user=user, detail={"session": session_id}, ip=client_ip(request))
    await st.hub.close_session(session_id)
    return {"ok": True}
