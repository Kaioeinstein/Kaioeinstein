"""Diagnóstico e reparo com um clique (menu da bandeja) — sem precisar abrir o PowerShell.

* ``report()``: monta um relatório em texto — agente, conexão com o servidor, servidor local (se este PC for o
  servidor), atualização automática, Tailscale e as últimas linhas dos registros. Só lê; não muda nada.
* ``repair_server()``: se este PC é o servidor, roda o ``setup-windows-server.ps1`` dele (o Windows pede
  permissão de administrador) — confere tudo, religa o servidor e mostra o resultado numa janela.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import __version__
from .config import AgentConfig, config_path
from .logs import logs_dir

WINDOWS = sys.platform == "win32"
TASK = "EINSTEIN REMOTE Servidor"
NO_WINDOW = 0x08000000


def _run(args: list[str], timeout: float = 10) -> str:
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout,  # nosec B603 - argumentos fixos
                           creationflags=NO_WINDOW if WINDOWS else 0)
        out = r.stdout or r.stderr
        enc = f"cp{ctypes.windll.kernel32.GetOEMCP()}" if WINDOWS else "utf-8"
        return out.decode(enc, errors="replace").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"(não foi possível executar: {exc})"


def _tail(path: Path, n: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        return "\n".join(lines[-n:]) or "(vazio)"
    except OSError:
        return "(não existe)"


def server_dir() -> Path | None:
    """Pasta do servidor, se este PC for o servidor instalado pelo setup-windows-server.ps1."""
    if not WINDOWS:
        return None
    xml = _run(["schtasks", "/query", "/tn", TASK, "/xml"])
    m = re.search(r"<WorkingDirectory>([^<]+)</WorkingDirectory>", xml)
    if m and Path(m.group(1)).is_dir():
        return Path(m.group(1))
    # sem permissão para ler a tarefa: o setup-windows-server.ps1 deixa a pasta anotada aqui
    hint = Path(os.environ.get("LOCALAPPDATA", "")) / "EinsteinRemote" / "server-dir.txt"
    try:
        d = Path(hint.read_text(encoding="utf-8-sig").strip())
        return d if (d / "scripts" / "setup-windows-server.ps1").exists() else None
    except OSError:
        return None


def check_server(cfg: AgentConfig) -> str:
    from .client import ssl_context

    if not cfg.server_url:
        return "agente não vinculado"
    url = cfg.server_url.rstrip("/") + "/api/health"
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=8, context=ssl_context(cfg)) as r:  # nosec B310 - servidor do vínculo
            body = json.loads(r.read(2048))
        return f"OK em {int((time.time() - t0) * 1000)} ms — servidor v{body.get('version')}, {body.get('agents_online')} PC(s) online"
    except Exception as exc:
        return f"FALHOU: {type(exc).__name__}: {exc}"


def report() -> str:
    cfg = AgentConfig.load() if config_path().exists() else AgentConfig()
    logs = logs_dir()
    parts = [
        f"EINSTEIN REMOTE — diagnóstico ({time.strftime('%d/%m/%Y %H:%M:%S')})",
        "",
        "== Agente ==",
        f"versão: {__version__}",
        f"vinculado: {'sim — ' + cfg.device_name if cfg.device_id else 'não'}",
        f"servidor configurado: {cfg.server_url or '—'}",
        f"pausado: {'sim' if cfg.paused else 'não'}",
        f"conexão com o servidor agora: {check_server(cfg)}",
    ]
    sdir = server_dir()
    if sdir:
        data = sdir / "data"
        local = "não consultado"
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False  # só o teste de saúde do próprio PC (127.0.0.1), que não bate com o nome do certificado
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen("https://127.0.0.1:8443/api/health", timeout=5, context=ctx) as r:  # nosec B310
                local = r.read(300).decode(errors="replace")
        except Exception as exc:
            local = f"não respondeu ({type(exc).__name__})"
        parts += [
            "", "== Servidor neste PC ==",
            f"pasta: {sdir}",
            f"tarefa: {_run(['schtasks', '/query', '/tn', TASK, '/fo', 'list']).replace(chr(13), '')}",
            f"responde localmente: {local}",
            f"atualização: {_tail(data / 'update' / 'state.json', 3)}",
            "-- server.log (fim) --", _tail(data / "server.log", 15),
        ]
    if WINDOWS:
        ts = Path(r"C:\Program Files\Tailscale\tailscale.exe")
        parts += ["", "== Tailscale ==", _run([str(ts), "status"])[:1200] if ts.exists() else "não instalado"]
    parts += [
        "", "== Registro do agente (fim) ==", _tail(logs / "agent.log", 25),
        "", "== Atualização automática do agente (fim) ==", _tail(logs / "update-helper.log", 10),
    ]
    return "\n".join(parts)


def save_report() -> Path:
    path = logs_dir() / "diagnostico.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report(), encoding="utf-8-sig")
    return path


def repair_server() -> bool:
    """Roda o setup do servidor deste PC como administrador (o Windows pede permissão). False se não há servidor aqui."""
    sdir = server_dir()
    if not sdir or not WINDOWS:
        return False
    script = sdir / "scripts" / "setup-windows-server.ps1"
    if not script.exists():
        return False
    params = f'-NoProfile -ExecutionPolicy Bypass -NoExit -File "{script}"'
    # ShellExecute "runas": pedido de administrador do Windows; a janela mostra cada passo e o resultado
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", params, str(sdir), 1)
    return r > 32
