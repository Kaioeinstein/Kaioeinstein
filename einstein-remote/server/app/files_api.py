"""Endpoints de transferência de arquivos (Fase 7)."""
from __future__ import annotations

import os
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .auth import client_ip, require_role
from .devices import _require_access
from .transfers import TransferError

router = APIRouter(prefix="/api", tags=["files"])
MAX_UPLOAD = int(os.environ.get("EINSTEIN_MAX_UPLOAD_MB", "4096")) * 2**20
SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class FileRef(BaseModel):
    root: int = Field(ge=0, le=1000)
    path: str = Field(default="", max_length=4096)


class CopyRequest(BaseModel):
    from_device: str
    from_root: int = Field(ge=0, le=1000)
    from_path: str = Field(max_length=4096)
    to_device: str
    to_root: int = Field(ge=0, le=1000)
    to_path: str = Field(default="", max_length=4096)
    overwrite: bool = False


def _err(exc: Exception) -> HTTPException:
    return HTTPException(409 if isinstance(exc, TransferError) else 500, str(exc))


@router.post("/devices/{device_id}/files/download")
async def start_download(device_id: str, body: FileRef, request: Request, user: dict = Depends(require_role("admin"))):
    """Prepara o download e devolve um link de uso único (60 s) para o navegador baixar nativamente."""
    st = request.app.state
    _require_access(request, user, device_id)
    try:
        t = await st.hub.transfers.open_download(user, device_id, body.root, body.path)
    except TransferError as exc:
        raise _err(exc)
    token = st.hub.transfers.issue_token(t)
    return {"url": f"/api/transfers/{token}", "name": t.name, "size": t.size, "tid": t.tid}


@router.get("/transfers/{token}")
async def transfer_download(token: str, request: Request):
    st = request.app.state
    try:
        t = st.hub.transfers.redeem(token)
    except TransferError as exc:
        raise HTTPException(404, str(exc))
    st.db.audit("file.download_started", user=t.user, device_id=t.device_id, detail={"path": t.path, "size": t.size},
                ip=client_ip(request))
    ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", t.name) or "arquivo"
    return StreamingResponse(
        st.hub.transfers.iterate(t),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(t.size),
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(t.name)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/devices/{device_id}/files/upload")
async def upload(device_id: str, request: Request, root: int = Query(ge=0, le=1000), path: str = Query("", max_length=4096),
                 name: str = Query(min_length=1, max_length=255), overwrite: bool = False,
                 user: dict = Depends(require_role("admin"))):
    """Recebe o corpo da requisição em streaming e repassa ao agente com verificação SHA-256."""
    st = request.app.state
    _require_access(request, user, device_id)
    try:
        size = int(request.headers.get("content-length", ""))
    except ValueError:
        raise HTTPException(411, "Tamanho do arquivo (Content-Length) obrigatório.")
    if size > MAX_UPLOAD:
        raise HTTPException(413, f"Arquivo maior que o limite do servidor ({MAX_UPLOAD // 2**20} MB).")
    expected = request.headers.get("x-content-sha256")
    if expected and not SHA_RE.match(expected):
        raise HTTPException(422, "Cabeçalho X-Content-SHA256 inválido.")
    try:
        result = await st.hub.transfers.upload(user, device_id, root, path, name, size, request.stream(),
                                               overwrite=overwrite, expected_sha=expected)
    except TransferError as exc:
        st.db.audit("file.upload_failed", user=user, device_id=device_id, detail={"name": name, "error": str(exc)},
                    ip=client_ip(request), success=False)
        raise _err(exc)
    if st.prefs["notify_commands"]:
        device = st.db.get_device_public(device_id)
        await st.hub.notify("info", "file", f"{user['username']} enviou “{result['name']}” para {device['name']}", device_id)
    return result


@router.post("/files/copy")
async def copy(body: CopyRequest, request: Request, user: dict = Depends(require_role("admin"))):
    """Copia um arquivo de um computador para outro, passando pelo servidor (com verificação)."""
    st = request.app.state
    _require_access(request, user, body.from_device)
    _require_access(request, user, body.to_device)
    try:
        result = await st.hub.transfers.copy(
            user,
            {"device_id": body.from_device, "root": body.from_root, "path": body.from_path},
            {"device_id": body.to_device, "root": body.to_root, "path": body.to_path},
            overwrite=body.overwrite,
        )
    except TransferError as exc:
        raise _err(exc)
    return result
