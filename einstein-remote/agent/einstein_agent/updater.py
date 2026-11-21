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
param([int]$AgentPid, [string]$Src, [string]$Dst, [string]$Version, [string]$Work, [string]$Log, [string]$Nonce)
$ErrorActionPreference = "Continue"
function L($m) { try { Add-Content -Path $Log -Value "$(Get-Date -Format s) $m" -Encoding UTF8 } catch {} }
L "ajudante iniciado ($Nonce): v$Version, agente PID $AgentPid, destino '$Dst'"
try { Wait-Process -Id $AgentPid -Timeout 60 -ErrorAction Stop; L "agente fechou" } catch { L "aguardando agente: $($_.Exception.Message)" }
Stop-Process -Id $AgentPid -Force -ErrorAction SilentlyContinue
# qualquer outra instância rodando desta pasta (CLI, bandeja reaberta) segura os arquivos
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Dst, [StringComparison]::OrdinalIgnoreCase) } |
    ForEach-Object { L "encerrando $($_.Name) (PID $($_.Id))"; Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
$ok = $false
for ($i = 1; $i -le 6; $i++) {
    $out = robocopy $Src $Dst /E /R:2 /W:1 /NP /NJH /NJS /NDL
    $code = $LASTEXITCODE
    L "cópia tentativa $i: robocopy $code"
    if ($code -lt 8) { $ok = $true; break }
    L ($out | Select-Object -Last 8 | Out-String)
    Start-Sleep -Seconds 3
}
if ($ok) {
    $key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{EFAF17C3-F513-4AC6-BEF6-172EC2E25FAB}_is1"
    if (Test-Path $key) { Set-ItemProperty $key DisplayVersion $Version -ErrorAction SilentlyContinue }
    L "arquivos trocados para v$Version"
} else {
    L "ERRO: não foi possível trocar os arquivos; reabrindo a versão anterior"
}
try { Start-Process -FilePath (Join-Path $Dst "EinsteinRemoteAgent.exe") -WorkingDirectory $Dst; L "agente reaberto" } catch { L "ERRO ao reabrir: $($_.Exception.Message)" }
if ($Work -like "*einstein-agent-update-*") { Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue }
"""


def _marker() -> Path:
    from .config import config_dir

    return config_dir() / "update-attempt.json"


def check_previous_attempt() -> None:
    """Na inicialização: registra se a última atualização automática pegou (e libera nova tentativa)."""
    import json

    from . import __version__

    m = _marker()
    try:
        data = json.loads(m.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if data.get("version") == __version__:
        log.info("Agente atualizado para %s automaticamente.", __version__)
        m.unlink(missing_ok=True)
    else:
        log.warning("A atualização automática para %s não foi aplicada (agente ainda em %s). Veja update-helper.log.",
                    data.get("version"), __version__)


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
    import json

    try:  # já tentou esta versão e voltou antigo: não fecha de novo (evita o ciclo que deixava o PC offline)
        if json.loads(_marker().read_text(encoding="utf-8")).get("version") == version:
            raise UpdateRefused(f"A atualização para {version} já foi tentada e não pegou — instale o agente novo manualmente "
                                "(motivo em update-helper.log, na pasta de logs do agente).")
    except (OSError, ValueError):
        pass
    work = Path(tempfile.mkdtemp(prefix="einstein-agent-update-"))
    zip_path = work / "agent.zip"
    download(cfg, str(params.get("url", "")), str(params.get("sha256", "")), params.get("size"), zip_path)
    src = extract(zip_path, work / "files")
    zip_path.unlink(missing_ok=True)
    return {"src": src, "dst": dst, "version": version, "work": work}


def start_helper(prep: dict, wait: float = 15.0) -> None:
    """Inicia o ajudante e só retorna depois que ele confirma, no update-helper.log, que está rodando.

    Sem essa confirmação o agente NÃO fecha (no uso real ele fechava, o ajudante não rodava e o PC ficava offline).
    """
    import json
    import secrets
    import time

    from .logs import logs_dir
    from .power import system_exe

    script = prep["work"] / "update-helper.ps1"
    script.write_text(HELPER, encoding="utf-8-sig")
    helper_log = logs_dir() / "update-helper.log"
    helper_log.parent.mkdir(parents=True, exist_ok=True)
    nonce = secrets.token_hex(8)
    console = prep["work"] / "helper-console.log"
    # CREATE_NO_WINDOW (console oculto) | NEW_PROCESS_GROUP | BREAKAWAY_FROM_JOB (sobrevive ao agente)
    flags = 0x08000000 | 0x00000200 | 0x01000000
    args = [system_exe("WindowsPowerShell\\v1.0\\powershell"), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script), "-AgentPid", str(os.getpid()),
            "-Src", str(prep["src"]), "-Dst", str(prep["dst"]), "-Version", prep["version"], "-Work", str(prep["work"]),
            "-Log", str(helper_log), "-Nonce", nonce]
    with open(console, "wb") as out:
        # executável sem console (bandeja): std* explícitos — herdar alças inválidas pode impedir o filho de iniciar
        kw = dict(stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, close_fds=True)
        try:
            proc = subprocess.Popen(args, creationflags=flags, **kw)  # nosec B603 - argumentos fixos
        except OSError as exc:
            log.info("Ajudante sem BREAKAWAY_FROM_JOB (%s)", exc)
            proc = subprocess.Popen(args, creationflags=flags & ~0x01000000, **kw)  # nosec B603
    end = time.time() + wait
    while time.time() < end:
        try:
            if nonce in helper_log.read_text(encoding="utf-8-sig", errors="replace"):
                _marker().write_text(json.dumps({"version": prep["version"], "ts": time.time()}), encoding="utf-8")
                return
        except OSError:
            pass
        if proc.poll() is not None:
            break
        time.sleep(0.3)
    if proc.poll() is None:
        proc.kill()
    detail = ""
    try:
        detail = console.read_text(encoding="utf-8", errors="replace").strip()[-300:]
    except OSError:
        pass
    raise UpdateRefused(f"O ajudante de atualização não iniciou (código {proc.returncode}); o agente continua na versão atual."
                        + (f" Detalhe: {detail}" if detail else ""))


def exit_for_update(version: str) -> None:
    log.warning("Atualizando o agente para %s: fechando para o ajudante trocar os arquivos.", version)
    # sai depois que o resultado chegou ao servidor; o ajudante reabre o ícone da bandeja
    threading.Timer(2.0, os._exit, args=(0,)).start()
