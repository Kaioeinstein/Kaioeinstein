"""Teste ponta a ponta: servidor uvicorn real + agente real + cliente WebSocket."""
import asyncio
import json
import socket
import threading
import time

import httpx
import uvicorn
from websockets.asyncio.client import connect

from app.main import create_app
from conftest import STRONG, XHR, make_settings


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("EINSTEIN_AGENT_DIR", str(tmp_path / "agent"))
    from einstein_agent import cli
    from einstein_agent.client import Agent
    from einstein_agent.config import AgentConfig

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(create_app(make_settings(tmp_path)), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)

    try:
        with httpx.Client(base_url=base) as http:
            token = http.post("/api/auth/setup", json={"username": "kaio", "password": STRONG}, headers=XHR).json()["access_token"]
            auth = {"Authorization": f"Bearer {token}", **XHR}
            code = http.post("/api/pairing-codes", json={"device_name": "PC Gamer"}, headers=auth).json()["code"]

        # pareamento via CLI real do agente
        assert cli.main(["pair", "--server", base, "--code", code]) == 0
        cfg = AgentConfig.load()
        assert cfg.paired and cfg.device_secret.startswith(("plain:", "dpapi:"))
        cfg.interval = 0.5
        cfg.save()

        async def scenario():
            agent = Agent(AgentConfig.load())
            task = asyncio.create_task(agent.run_forever())
            async with connect(f"ws://127.0.0.1:{port}/ws/client") as ui:
                await ui.send(json.dumps({"type": "auth", "token": token}))
                metrics = None
                deadline = time.time() + 15
                while time.time() < deadline and metrics is None:
                    msg = json.loads(await asyncio.wait_for(ui.recv(), 10))
                    if msg["type"] == "metrics":
                        metrics = msg
                assert metrics, "nenhuma métrica recebida"
                data = metrics["data"]
                assert 0 <= data["cpu"]["percent"] <= 100
                assert data["memory"]["total"] > 0
                assert data["disks"] and data["uptime"] > 0

                await ui.send(json.dumps({"type": "command", "request_id": "x", "device_id": cfg.device_id, "action": "system.info"}))
                result = None
                while result is None:
                    msg = json.loads(await asyncio.wait_for(ui.recv(), 10))
                    if msg["type"] == "result":
                        result = msg
                assert result["ok"] and result["data"]["hostname"] == socket.gethostname()

                # pausa local pelo proprietário: agente desconecta
                c = AgentConfig.load(); c.paused = True; c.save()
                offline = False
                deadline = time.time() + 10
                while time.time() < deadline and not offline:
                    msg = json.loads(await asyncio.wait_for(ui.recv(), 10))
                    offline = msg["type"] == "device" and msg["data"]["online"] is False
                assert offline, "agente não desconectou após pausa"
            agent.stop()
            await asyncio.wait_for(task, 10)

        asyncio.run(scenario())
    finally:
        server.should_exit = True
        th.join(5)
