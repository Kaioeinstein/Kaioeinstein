"""Teste de fumaça dos executáveis gerados pelo PyInstaller.

    python installer/smoke_exe.py dist/EinsteinRemoteAgent

Sobe um servidor real numa pasta temporária, cria o proprietário, gera um
código e então, usando SOMENTE os executáveis:
  1. einstein-agent --version / pair / run   → servidor vê o PC online
  2. einstein-agent pause                     → servidor vê "paused"
  3. EinsteinRemoteAgent (bandeja, sem console) → servidor vê o PC online de novo
Sai com código != 0 se qualquer etapa falhar.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ".exe" if os.name == "nt" else ""
XHR = {"X-Requested-With": "EinsteinRemote", "Content-Type": "application/json"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base, path, body=None, token=None, method=None):
    headers = dict(XHR)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method=method or ("POST" if body is not None else "GET"))
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def wait_state(base, token, state, timeout=30):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        devs = call(base, "/api/devices", token=token)
        last = devs[0]["state"] if devs else None
        if last == state:
            return devs[0]
        time.sleep(0.5)
    raise SystemExit(f"FALHA: esperado estado '{state}', obtido '{last}'")


def main() -> int:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "dist" / "EinsteinRemoteAgent").resolve()
    cli = dist / f"einstein-agent{EXE}"
    tray = dist / f"EinsteinRemoteAgent{EXE}"
    assert cli.exists() and tray.exists(), f"executáveis não encontrados em {dist}"

    tmp = Path(tempfile.mkdtemp(prefix="einstein-smoke-"))
    env = dict(os.environ, EINSTEIN_DATA_DIR=str(tmp / "server"), EINSTEIN_AGENT_DIR=str(tmp / "agent"),
               EINSTEIN_AGENT_LOG_DIR=str(tmp / "logs"), PYTHONIOENCODING="utf-8")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT / "server"),
                               "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"], env=env)
    procs = [server]
    try:
        for _ in range(60):
            try:
                call(base, "/api/health")
                break
            except OSError:
                time.sleep(0.5)
        token = call(base, "/api/auth/setup", {"username": "smoke", "password": "Smoke#Test2026"})["access_token"]

        out = subprocess.run([str(cli), "--version"], capture_output=True, encoding="utf-8", errors="replace", env=env, check=True).stdout.strip()
        print(f"[1/5] versão do executável: {out}")

        code = call(base, "/api/pairing-codes", {"device_name": "PC Smoke"}, token)["code"]
        r = subprocess.run([str(cli), "pair", "--server", base, "--code", code], capture_output=True, encoding="utf-8", errors="replace", env=env)
        print(r.stdout.strip())
        assert r.returncode == 0, r.stderr
        print("[2/5] pareamento pelo executável: ok")

        run = subprocess.Popen([str(cli), "run"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(run)
        d = wait_state(base, token, "online")
        assert d["agent_version"] == out, d
        assert d["metrics"] and d["metrics"]["memory"]["total"] > 0
        print(f"[3/5] 'run' online com métricas reais (RAM total {d['metrics']['memory']['total'] // 2**20} MB)")

        subprocess.run([str(cli), "pause"], env=env, check=True, capture_output=True)
        wait_state(base, token, "paused")
        print("[4/5] pausa local refletida no servidor: ok")
        run.terminate()
        run.wait(15)

        subprocess.run([str(cli), "resume"], env=env, check=True, capture_output=True)
        tray_proc = subprocess.Popen([str(tray)], env=env)
        procs.append(tray_proc)
        wait_state(base, token, "online", timeout=40)
        assert tray_proc.poll() is None, "processo da bandeja terminou sozinho"
        print("[5/5] EinsteinRemoteAgent (bandeja) online: ok")
        status = subprocess.run([str(cli), "status"], capture_output=True, encoding="utf-8", errors="replace", env=env).stdout
        assert "Em execução:   sim" in status, status
        print("\nSMOKE TEST OK")
        return 0
    finally:
        for p in reversed(procs):
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()


if __name__ == "__main__":
    sys.exit(main())
