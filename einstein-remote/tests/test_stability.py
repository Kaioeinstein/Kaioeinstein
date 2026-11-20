"""Fase 9: estabilidade — reinício do servidor e painéis travados."""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "load"))

XHR = {"X-Requested-With": "EinsteinRemote", "Content-Type": "application/json"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base, path, body=None, token=None):
    h = dict(XHR)
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=h, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def start_server(port, data_dir):
    env = dict(os.environ, EINSTEIN_DATA_DIR=str(data_dir))
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT / "server"),
                          "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"], env=env)
    for _ in range(100):
        try:
            call(f"http://127.0.0.1:{port}", "/api/health")
            return p
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("servidor não subiu")


def wait_for(cond, timeout):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if cond():
                return True
        except OSError:
            pass
        time.sleep(0.2)
    return False


def test_agent_reconnects_after_server_restart(tmp_path):
    from einstein_agent.pairing import pair
    from einstein_agent.runner import AgentRunner

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    server = start_server(port, tmp_path / "srv")
    states = []
    runner = None
    try:
        token = call(base, "/api/auth/setup", {"username": "kaio", "password": "Einstein#2026!"})["access_token"]
        pair(base, call(base, "/api/pairing-codes", {"device_name": "PC"}, token)["code"])
        runner = AgentRunner(on_status=lambda s: states.append(s.state))
        runner.start()
        online = lambda: call(base, "/api/devices", token=token)[0]["online"]  # noqa: E731
        assert wait_for(online, 15)

        server.terminate()  # servidor cai (atualização, queda de energia...)
        server.wait(10)
        assert wait_for(lambda: states and states[-1] == "offline", 15), states
        time.sleep(2)
        server = start_server(port, tmp_path / "srv")  # volta com o mesmo banco
        assert wait_for(online, 40), states  # agente reconecta sozinho, sem intervenção
        assert runner.running
    finally:
        if runner:
            runner.stop()
        server.terminate()
        server.wait(10)


def test_stalled_panel_does_not_slow_others():
    """Regressão: 1 painel que para de ler não pode atrasar os demais nem os agentes."""
    import loadtest

    r = asyncio.run(loadtest.main(n_agents=30, n_clients=2, seconds=8, stalled=1))
    assert r["command_errors"] == 0 and r["commands"] >= 20
    assert r["metrics_delivered_per_s"] >= 0.85 * r["expected_delivered_per_s"], r
    assert r["metrics_sent_per_s"] >= 0.75 * r["agents"], r
    assert r["delivery_lag_ms_p95"] < 500, r
