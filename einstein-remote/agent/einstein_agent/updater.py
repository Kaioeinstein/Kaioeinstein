"""Atualização automática do agente instalado (executável do Windows).

O servidor do próprio dono oferece o pacote novo (link de uso único) com o SHA-256 esperado.
O agente: baixa pela mesma conexão segura (TLS, mesma CA do vínculo), confere tamanho e SHA-256,
extrai numa pasta temporária e inicia um ajudante que espera o agente fechar, copia os arquivos para
a pasta do programa e reabre o ícone da bandeja. Vínculo e permissões (pasta de configuração) não são tocados.
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path

from .config import AgentConfig

log = logging.getLogger("einstein.updater")
MAX_SIZE = 300 * 1024 * 1024
TRAY_EXE = "EinsteinRemoteAgent.exe"


class UpdateRefused(Exception):
    pass


HELPER = r"""
param([int]$AgentPid, [string]$Src, [string]$Dst, [string]$Version, [string]$Work)
$ErrorActionPreference = "SilentlyContinue"
Wait-Process -Id $AgentPid -Timeout 60
Stop-Process -Id $AgentPid -Force
Get-Process EinsteinRemoteAgent, einstein-agent | Where-Object { $_.Path -like "$Dst*" } | Stop-Process -Force
Start-Sleep -Seconds 1
for ($i = 0; $i -lt 5; $i++) {
    robocopy $Src $Dst /E /R:3 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -lt 8) { break }
    Start-Sleep -Seconds 2
}
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{EFAF17C3-F513-4AC6-BEF6-172EC2E25FAB}_is1"
if (Test-Path $key) {
    Set-ItemProperty $key DisplayVersion $Version
    Set-ItemProperty $key DisplayName "EINSTEIN REMOTE AGENT"
}
Start-Process (Join-Path $Dst "EinsteinRemoteAgent.exe")
if ($Work -like "*einstein-agent-update-*") { Remove-Item $Work -Recurse -Force }
"""


def install_dir() -> Path | None:
    """Pasta do programa instalado, ou None quando roda pelo Python (desenvolvimento)."""
    if not getattr(sys, "frozen", False):
        return None
    d = Path(sys.executable).parent
    return d if (d / TRAY_EXE).exists() else None


def download(cfg: AgentConfig, url: str, expected_sha: str, size: int | None, dest: Path) -> None:
    from .client import ssl_context

    if not url.startswith("/api/agent/package?token="):
        raise UpdateRefused("Endereço de atualização inválido.")
    full = cfg.server_url.rstrip("/") + url
    req = urllib.request.Request(full, headers={"User-Agent": "einstein-agent-updater"})
    h = hashlib.sha256()
    total = 0
    # mesmo servidor e mesma verificação TLS do vínculo
    with urllib.request.urlopen(req, timeout=60, context=ssl_context(cfg)) as r, open(dest, "wb") as f:  # nosec B310
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SIZE:
                raise UpdateRefused("Pacote grande demais.")
            h.update(chunk)
            f.write(chunk)
    if size and total != int(size):
        raise UpdateRefused("Download incompleto.")
    if h.hexdigest().lower() != str(expected_sha).lower():
        raise UpdateRefused("SHA-256 do pacote não confere — atualização cancelada.")


def extract(zip_path: Path, dest: Path) -> Path:
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():  # nada fora da pasta de destino (zip-slip)
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest.resolve()) + os.sep) and target != dest.resolve():
                raise UpdateRefused("Pacote com caminhos inválidos.")
        z.extractall(dest)
    root = dest
    if not (root / TRAY_EXE).exists():
        subs = [p for p in dest.iterdir() if (p / TRAY_EXE).exists()]
        if not subs:
            raise UpdateRefused("Pacote sem o executável do agente.")
        root = subs[0]
    return root


def prepare(params: dict, cfg: AgentConfig) -> dict:
    """Baixa, confere e prepara. Devolve o necessário para o ajudante trocar os arquivos."""
    from . import __version__

    version = str(params.get("version", ""))
    if sys.platform != "win32":
        raise UpdateRefused("Atualização automática disponível no agente instalado no Windows.")
    dst = install_dir()
    if dst is None:
        raise UpdateRefused("Agente rodando pelo Python (desenvolvimento): atualize pelo código-fonte.")
    if version == __version__:
        raise UpdateRefused("Este agente já está na versão oferecida.")
    work = Path(tempfile.mkdtemp(prefix="einstein-agent-update-"))
    zip_path = work / "agent.zip"
    download(cfg, str(params.get("url", "")), str(params.get("sha256", "")), params.get("size"), zip_path)
    src = extract(zip_path, work / "files")
    zip_path.unlink(missing_ok=True)
    return {"src": src, "dst": dst, "version": version, "work": work}


def launch_helper(prep: dict) -> None:
    from .power import system_exe

    script = prep["work"] / "update-helper.ps1"
    script.write_text(HELPER, encoding="utf-8-sig")
    flags = 0x00000008 | 0x00000200 | 0x01000000  # DETACHED_PROCESS | NEW_PROCESS_GROUP | BREAKAWAY_FROM_JOB
    args = [system_exe("WindowsPowerShell\\v1.0\\powershell"), "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-File", str(script), "-AgentPid", str(os.getpid()),
            "-Src", str(prep["src"]), "-Dst", str(prep["dst"]), "-Version", prep["version"], "-Work", str(prep["work"])]
    try:
        subprocess.Popen(args, creationflags=flags, close_fds=True)  # nosec B603 - argumentos fixos
    except OSError:
        subprocess.Popen(args, creationflags=flags & ~0x01000000, close_fds=True)  # nosec B603
    log.warning("Atualizando o agente para %s: fechando para o ajudante trocar os arquivos.", prep["version"])
    # sai depois que o resultado chegou ao servidor; o ajudante reabre o ícone da bandeja
    threading.Timer(2.0, os._exit, args=(0,)).start()
