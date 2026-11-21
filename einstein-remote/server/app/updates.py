"""Atualização automática (servidor Windows instalado por setup-windows-server.ps1 + agentes).

Fonte das versões: o próprio repositório do projeto no GitHub (HTTPS, endereço fixo no código — nunca
vindo do painel). Fluxo:

1. ``/api/update/status`` compara a versão em execução com a publicada no repositório.
2. O proprietário toca em **Atualizar** (``/api/update/start``, com confirmação): o servidor grava um pedido
   e se encerra; o laço do serviço (``scripts/server-service.ps1``) roda ``scripts/update.ps1`` — baixa o
   código, guarda uma cópia da versão atual, troca os arquivos, monta o novo agente e religa o servidor.
   Se a versão nova não responder em 90 s, a anterior é restaurada sozinha.
3. Cada agente que conecta com versão antiga recebe o pacote novo (link de uso único + SHA-256) e se
   atualiza sozinho, reabrindo o ícone da bandeja.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import __version__
from .auth import client_ip, require_role

log = logging.getLogger("einstein.updates")
router = APIRouter(tags=["updates"])

REPO = "Kaioeinstein/Kaioeinstein"
BRANCH = "ccr-729bf759-udovmu"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/einstein-remote/server/app/__init__.py"
CHECK_EVERY = 30 * 60
TOKEN_TTL = 15 * 60


def parse_version(v: str) -> tuple[int, ...]:
    m = re.fullmatch(r"\s*(\d+)\.(\d+)\.(\d+)\s*", v or "")
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def newer(a: str, b: str) -> bool:
    """a é mais nova que b?"""
    return parse_version(a) > parse_version(b)


def self_update_supported() -> bool:
    return os.environ.get("EINSTEIN_SELF_UPDATE") == "1"


class Updates:
    def __init__(self, data_dir: Path):
        self.dir = data_dir / "update"
        self.latest: str | None = None
        self.checked_at = 0.0
        self.check_error: str | None = None
        self.tokens: dict[str, float] = {}
        self.refused: set[tuple[str, str]] = set()  # (dispositivo, versão) que já recusou/falhou: não insiste
        self._lock = threading.Lock()

    # -- versão publicada ----------------------------------------------------
    def fetch_latest(self) -> str | None:
        req = urllib.request.Request(VERSION_URL, headers={"User-Agent": "einstein-remote-updater"})
        # endereço fixo (constante acima), nunca vindo de quem chama
        with urllib.request.urlopen(req, timeout=10) as r:  # nosec B310
            text = r.read(4096).decode("utf-8", errors="replace")
        m = re.search(r'__version__\s*=\s*"(\d+\.\d+\.\d+)"', text)
        return m.group(1) if m else None

    async def check(self, force: bool = False) -> None:
        if os.environ.get("EINSTEIN_UPDATE_CHECK") == "0":  # testes / servidor sem internet
            return
        if not force and time.time() - self.checked_at < CHECK_EVERY:
            return
        self.checked_at = time.time()
        try:
            self.latest = await asyncio.to_thread(self.fetch_latest)
            self.check_error = None
        except Exception as exc:
            self.check_error = f"Não foi possível consultar o GitHub ({type(exc).__name__})."
            log.info("Verificação de atualização falhou: %s", exc)

    # -- estado do atualizador (escrito pelo update.ps1) ------------------------
    def state(self) -> dict:
        try:
            return json.loads((self.dir / "state.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}

    def request(self, by: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "requested").write_text(json.dumps({"by": by, "ts": time.time()}), encoding="utf-8")

    # -- pacote do agente (montado pelo update.ps1) -----------------------------
    def package(self) -> dict | None:
        try:
            info = json.loads((self.dir / "agent" / "package.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None
        path = self.dir / "agent" / str(info.get("file", ""))
        if not path.is_file() or path.parent != self.dir / "agent":
            return None
        return {**info, "path": path}

    def issue_token(self) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self.tokens = {t: exp for t, exp in self.tokens.items() if exp > now}
            self.tokens[token] = now + TOKEN_TTL
        return token

    def redeem(self, token: str) -> bool:
        with self._lock:
            exp = self.tokens.pop(token, 0)  # uso único
        return exp > time.time()

    def status(self) -> dict:
        pkg = self.package()
        return {
            "current": __version__,
            "latest": self.latest,
            "available": bool(self.latest and newer(self.latest, __version__)),
            "supported": self_update_supported(),
            "checked_at": self.checked_at or None,
            "error": self.check_error,
            "state": self.state(),
            "agent_package": pkg["version"] if pkg else None,
        }


async def offer_agent_update(hub, conn, version: str | None) -> None:
    """Agente conectou com versão antiga e há pacote novo: manda atualizar (uma tentativa por conexão)."""
    upd: Updates = hub.updates
    pkg = upd.package()
    if not pkg or not version or not newer(pkg["version"], version):
        return
    if not hub.prefs["auto_update_agents"] or (conn.device_id, pkg["version"]) in upd.refused:
        return
    token = upd.issue_token()
    params = {"version": pkg["version"], "sha256": pkg["sha256"], "size": pkg.get("size"),
              "url": f"/api/agent/package?token={token}"}
    await asyncio.sleep(3)  # deixa a conexão estabilizar (métricas, informações)
    try:
        await hub.server_call(conn.device_id, "agent.update", params, timeout=300)
        hub.db.audit("agent.update_sent", device_id=conn.device_id, detail={"from": version, "to": pkg["version"]})
        await hub.notify("info", "update", f"{conn.name or 'Computador'}: atualizando o agente {version} → {pkg['version']}", conn.device_id)
    except Exception as exc:
        upd.refused.add((conn.device_id, pkg["version"]))
        log.warning("Atualização do agente %s falhou: %s", conn.device_id, exc)
        await hub.notify("warning", "update", f"{conn.name or 'Computador'}: atualização do agente falhou — {exc}", conn.device_id)


# ------------------------------------------------------------------ API
class StartBody(BaseModel):
    confirm: bool = False


@router.get("/api/update/status")
async def update_status(request: Request, refresh: bool = False, user: dict = Depends(require_role("admin"))):
    upd: Updates = request.app.state.hub.updates
    await upd.check(force=refresh)
    return upd.status()


@router.post("/api/update/start")
async def update_start(body: StartBody, request: Request, user: dict = Depends(require_role("owner"))):
    if request.headers.get("x-requested-with") != "EinsteinRemote":
        raise HTTPException(403, "Requisição inválida.")
    if not body.confirm:
        raise HTTPException(400, "Confirme a atualização.")
    if not self_update_supported():
        raise HTTPException(409, "Atualização pelo painel disponível no servidor Windows instalado pelo setup-windows-server.ps1. "
                                 "No Docker: git pull && docker compose up -d --build.")
    st = request.app.state
    upd: Updates = st.hub.updates
    await upd.check(force=True)
    if not upd.latest or not newer(upd.latest, __version__):
        raise HTTPException(409, "Você já está na versão mais recente.")
    upd.request(user["username"])
    st.db.audit("server.update_requested", user=user, ip=client_ip(request), detail={"from": __version__, "to": upd.latest})
    await st.hub.notify("warning", "update", f"Atualizando o servidor {__version__} → {upd.latest} (pedido por {user['username']}). "
                                             "O painel volta em alguns minutos.")
    # encerra depois de responder: o laço do serviço roda a atualização e religa o servidor
    threading.Timer(1.5, os._exit, args=(0,)).start()
    return {"ok": True, "from": __version__, "to": upd.latest}


@router.get("/api/agent/package")
def agent_package(token: str, request: Request):
    upd: Updates = request.app.state.hub.updates
    if not upd.redeem(token):
        raise HTTPException(403, "Link de atualização inválido ou expirado.")
    pkg = upd.package()
    if not pkg:
        raise HTTPException(404, "Pacote do agente indisponível.")
    return FileResponse(pkg["path"], media_type="application/zip", filename=pkg["path"].name)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

