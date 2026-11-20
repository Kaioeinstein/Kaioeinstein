"""Autenticação de usuários e controle de sessões."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import time
import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import security, totp

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


# Com "exigir 2FA" ativo, quem ainda não ativou só pode acessar o necessário para ativar.
ENROLL_PATHS = {"/api/auth/me", "/api/auth/logout", "/api/auth/2fa/setup", "/api/auth/2fa/enable", "/api/auth/password"}


def must_enroll(app_state, user: dict) -> bool:
    return bool(app_state.prefs["require_2fa"]) and not user.get("totp_enabled")


def current_user(request: Request) -> dict:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "Autenticação necessária")
    user, session = resolve_access_token(_state(request), header[7:].strip())
    request.state.session_id = session["id"]
    if must_enroll(_state(request), user) and request.url.path not in ENROLL_PATHS:
        raise HTTPException(403, "2fa_required")
    return user


def require_role(min_role: str):
    levels = {"viewer": 0, "admin": 1, "owner": 2}

    def dep(user: dict = Depends(current_user)) -> dict:
        if levels.get(user["role"], -1) < levels[min_role]:
            raise HTTPException(403, "Permissão insuficiente")
        return user

    return dep


def public_user(user: dict) -> dict:
    return {"id": user["id"], "username": user["username"], "role": user["role"], "totp_enabled": bool(user.get("totp_enabled"))}


class TwoFactorCode(BaseModel):
    challenge: str = Field(min_length=10, max_length=2048)
    code: str = Field(min_length=6, max_length=20)


class CodeOnly(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class PasswordAndCode(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=6, max_length=20)


# --------------------------------------------------------------- alerta de novo acesso
def _device_label(ua: str) -> str:
    os_ = ("Windows" if "Windows" in ua else "Android" if "Android" in ua else "iOS" if ("iPhone" in ua or "iPad" in ua)
           else "macOS" if "Mac OS" in ua else "Linux" if "Linux" in ua else "dispositivo")
    br = ("Edge" if "Edg/" in ua else "Chrome" if "Chrome/" in ua else "Firefox" if "Firefox/" in ua
          else "Safari" if "Safari/" in ua else "navegador")
    return f"{br} em {os_}"


def _network(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
        return str(ipaddress.ip_network(f"{ip}/{24 if addr.version == 4 else 48}", strict=False))
    except ValueError:
        return ip


async def _login_alert(request: Request, user: dict) -> None:
    """Primeiro acesso de um navegador/rede desconhecidos → notificação (o 1º acesso da conta é silencioso)."""
    st = _state(request)
    label = _device_label(request.headers.get("user-agent", ""))
    ip = client_ip(request)
    fp = hashlib.sha256(f"{user['id']}|{label}|{_network(ip)}".encode()).hexdigest()
    new, known = st.db.seen_login(user["id"], fp, label)
    if new and known > 0:
        st.db.audit("auth.new_device", user=user, detail={"device": label, "network": _network(ip)}, ip=ip)
        await st.hub.notify("warning", "new_login", f"Novo acesso à conta {user['username']}: {label}, IP {ip}")


async def _complete_login(request: Request, response: Response, user: dict, method: str) -> dict:
    st = _state(request)
    st.db.update_user(user["id"], last_login=time.time())
    st.db.audit("auth.login", user=user, detail={"method": method}, ip=client_ip(request))
    await _login_alert(request, user)
    return _issue_session(request, response, user)


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
    _state(request).db.seen_login(user["id"], hashlib.sha256(
        f"{user['id']}|{_device_label(request.headers.get('user-agent', ''))}|{_network(client_ip(request))}".encode()).hexdigest(),
        _device_label(request.headers.get("user-agent", "")))
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
    if user.get("totp_enabled"):
        # senha correta, mas ainda falta o segundo fator: nenhuma sessão é criada aqui
        now = int(time.time())
        challenge = jwt.encode({"sub": str(user["id"]), "typ": "2fa", "iat": now, "exp": now + 300, "jti": uuid.uuid4().hex},
                               st.settings.secret_key, algorithm="HS256")
        st.db.audit("auth.2fa_challenge", user=user, ip=ip)
        return {"two_factor": True, "challenge": challenge}
    return await _complete_login(request, response, user, "senha")


@router.post("/2fa/verify")
async def verify_2fa(body: TwoFactorCode, request: Request, response: Response):
    """Segunda etapa do login: código do aplicativo autenticador ou código de recuperação."""
    _require_xhr(request)
    st = _state(request)
    ip = client_ip(request)
    try:
        payload = jwt.decode(body.challenge, st.settings.secret_key, algorithms=["HS256"], options={"require": ["exp", "sub"]})
        if payload.get("typ") != "2fa":
            raise jwt.InvalidTokenError()
    except jwt.PyJWTError:
        raise HTTPException(401, "A etapa de verificação expirou. Entre novamente.")
    user = st.db.get_user(int(payload["sub"]))
    if not user or not user.get("totp_enabled"):
        raise HTTPException(401, "Verificação indisponível.")
    key = f"2fa:{user['id']}"
    if st.limiter.is_locked(key):
        raise HTTPException(429, "Muitas tentativas. Aguarde alguns minutos.")
    method = None
    step = totp.verify(user["totp_secret"], body.code, user["totp_last_step"])
    if step is not None:
        st.db.update_user(user["id"], totp_last_step=step)
        method = "senha + autenticador"
    elif totp.is_recovery_format(body.code):
        hashes = json.loads(user.get("recovery_hashes") or "[]")
        h = totp.hash_recovery(body.code)
        if h in hashes:
            hashes.remove(h)  # uso único
            st.db.update_user(user["id"], recovery_hashes=json.dumps(hashes))
            method = "senha + código de recuperação"
            await st.hub.notify("warning", "recovery_code", f"{user['username']} entrou com um código de recuperação ({len(hashes)} restantes)")
    if not method:
        st.limiter.fail(key)
        st.db.audit("auth.2fa_failed", user=user, ip=ip, success=False)
        await st.hub.notify("critical", "unauthorized", f"Código de verificação incorreto para '{user['username']}' (IP {ip})")
        raise HTTPException(401, "Código incorreto.")
    st.limiter.reset(key)
    return await _complete_login(request, response, user, method)


@router.post("/2fa/setup")
def setup_2fa(request: Request, user: dict = Depends(current_user)):
    """Gera um novo segredo (ainda inativo) e o QR code para o aplicativo autenticador."""
    st = _state(request)
    if user.get("totp_enabled"):
        raise HTTPException(409, "A verificação em duas etapas já está ativa.")
    secret = totp.new_secret()
    st.db.update_user(user["id"], totp_secret=secret, totp_last_step=0)
    uri = totp.otpauth_uri(user["username"], secret)
    return {"secret": secret, "uri": uri, "qr": totp.qr_data_uri(uri)}


@router.post("/2fa/enable")
async def enable_2fa(body: CodeOnly, request: Request, user: dict = Depends(current_user)):
    """Confirma com um código do aplicativo; devolve os códigos de recuperação (mostrados uma única vez)."""
    st = _state(request)
    if user.get("totp_enabled") or not user.get("totp_secret"):
        raise HTTPException(409, "Gere um novo QR code primeiro.")
    step = totp.verify(user["totp_secret"], body.code, 0)
    if step is None:
        raise HTTPException(422, "Código incorreto. Confira se o horário do celular está automático.")
    codes = totp.new_recovery_codes()
    st.db.update_user(user["id"], totp_enabled=1, totp_last_step=step,
                      recovery_hashes=json.dumps([totp.hash_recovery(c) for c in codes]))
    st.db.audit("auth.2fa_enabled", user=user, ip=client_ip(request))
    await st.hub.notify("info", "security", f"Verificação em duas etapas ativada para {user['username']}")
    return {"enabled": True, "recovery_codes": codes}


@router.post("/2fa/disable")
async def disable_2fa(body: PasswordAndCode, request: Request, user: dict = Depends(current_user)):
    st = _state(request)
    if not user.get("totp_enabled"):
        raise HTTPException(409, "A verificação em duas etapas não está ativa.")
    if not security.verify_password(body.password, user["password_hash"]):
        raise HTTPException(403, "Senha incorreta.")
    valid = totp.verify(user["totp_secret"], body.code, user["totp_last_step"]) is not None
    if not valid and totp.is_recovery_format(body.code):  # perdeu o celular, mas tem os códigos de recuperação
        valid = totp.hash_recovery(body.code) in json.loads(user.get("recovery_hashes") or "[]")
    if not valid:
        raise HTTPException(403, "Código incorreto.")
    st.db.update_user(user["id"], totp_enabled=0, totp_secret=None, totp_last_step=0, recovery_hashes=None)
    st.db.audit("auth.2fa_disabled", user=user, ip=client_ip(request))
    await st.hub.notify("warning", "security", f"Verificação em duas etapas DESATIVADA para {user['username']}")
    return {"enabled": False}


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
def me(request: Request, user: dict = Depends(current_user)):
    return {**public_user(user), "must_enroll_2fa": must_enroll(_state(request), user)}


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


@sessions_router.delete("")
async def revoke_other_sessions(request: Request, user: dict = Depends(current_user)):
    """Encerra todas as sessões do usuário, exceto a atual."""
    st = _state(request)
    revoked = st.db.revoke_user_sessions(user["id"], except_sid=request.state.session_id)
    for sid in revoked:
        await st.hub.close_session(sid)
    st.db.audit("auth.sessions_revoked_all", user=user, detail={"count": len(revoked)}, ip=client_ip(request))
    return {"revoked": len(revoked)}


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
