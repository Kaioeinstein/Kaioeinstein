"""Fase 6: central de energia (em modo de simulação: nada é desligado de verdade)."""
import asyncio
import json
import sys
import time

import pytest

from conftest import XHR
from test_agent import live_server, wait_for  # noqa: F401
from test_api import pair_device

WINDOWS = sys.platform == "win32"


@pytest.fixture(autouse=True)
def dry_run(monkeypatch):
    monkeypatch.setenv("EINSTEIN_POWER_DRY_RUN", "1")
    from einstein_agent import power

    power.DRY_RUN_LOG.clear()
    yield power.DRY_RUN_LOG


# ------------------------------------------------------------------ agente
def test_native_commands(dry_run):
    from einstein_agent import power

    power.schedule_native("shutdown", 60, "EINSTEIN REMOTE: teste")
    power.schedule_native("restart", 0, "x")
    power.cancel_native()
    if WINDOWS:  # caminho absoluto em System32 (não depende do PATH)
        assert all(c[0].lower().endswith(("\\system32\\shutdown.exe")) for c in dry_run), dry_run
        assert [c[1:] for c in dry_run] == [["/s", "/t", "60", "/c", "EINSTEIN REMOTE: teste"], ["/r", "/t", "0", "/c", "x"], ["/a"]]
    else:
        assert dry_run == [["shutdown", "-h", "+1", "EINSTEIN REMOTE: teste"], ["shutdown", "-r", "now", "x"], ["shutdown", "-c"]]


def test_capabilities_shape():
    from einstein_agent import power

    c = power.capabilities()
    assert set(c["capabilities"]) >= {"shutdown", "restart", "sleep", "hibernate", "lock", "cancel", "dry_run"}
    assert c["capabilities"]["dry_run"] is True


def test_controller_schedule_cancel_and_guards(dry_run):
    from einstein_agent.config import AgentConfig
    from einstein_agent.power import PowerController, PowerError

    events, changes = [], []

    async def send(m):
        events.append(m)

    async def scenario():
        pc = PowerController(on_change=changes.append)
        pc.send_json = send
        r = await pc.request("shutdown", {"delay": 120, "_user": "kaio"})
        assert r["scheduled"] and pc.pending["by"] == "kaio"
        with pytest.raises(PowerError, match="Já existe"):
            await pc.request("restart", {"delay": 5})
        with pytest.raises(PowerError, match="Prazo"):
            pc.pending = None
            await pc.request("restart", {"delay": "x"})
        pc.pending = {"action": "shutdown", "at": time.time() + 100, "delay": 100, "by": "kaio", "native": True}
        c = await pc.cancel("owner")
        assert c == {"canceled": True, "action": "shutdown"} and pc.pending is None

        cfg = AgentConfig.load()
        cfg.allow_power = False
        cfg.save()
        with pytest.raises(PowerError, match="bloqueou"):
            await pc.request("shutdown", {"delay": 60})
        cfg.allow_power = True
        cfg.save()

    asyncio.run(scenario())
    assert [e["event"] for e in events] == ["scheduled", "canceled"]
    assert events[1]["by"] == "owner"
    assert changes[-1] == {"pending": None}


@pytest.mark.skipif(WINDOWS, reason="no Windows suspensão depende do hardware do executor")
def test_suspend_after_delay_and_cancel(dry_run):
    from einstein_agent.power import PowerController

    async def scenario():
        pc = PowerController()
        await pc.request("sleep", {"delay": 0, "_user": "kaio"})
        assert pc.pending["action"] == "sleep"
        await asyncio.sleep(3.8)  # prazo mínimo de 3 s para a resposta chegar ao painel
        assert pc.pending is None
        await pc.request("hibernate", {"delay": 60})
        await pc.cancel("kaio")
        await asyncio.sleep(0.1)

    asyncio.run(scenario())
    assert ["SetSuspendState", "sleep"] in dry_run
    assert ["SetSuspendState", "hibernate"] not in dry_run  # cancelado antes do prazo


# ------------------------------------------------------------------ servidor
def test_server_policies_states_and_audit(client, owner):
    headers, token = owner
    creds, _ = pair_device(client, headers)
    dev = creds["device_id"]
    client.post("/api/users", headers=headers, json={"username": "obs", "password": "Obs#Senha2026", "role": "viewer"})
    vtoken = client.post("/api/auth/login", json={"username": "obs", "password": "Obs#Senha2026"}, headers=XHR).json()["access_token"]
    with client.websocket_connect("/ws/agent") as agent:
        agent.send_json({"type": "auth", "device_id": dev, "secret": creds["device_secret"],
                         "info": {"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.0.50"}})
        agent.receive_json()
        with client.websocket_connect("/ws/client") as v:
            v.send_json({"type": "auth", "token": vtoken})
            v.receive_json()
            v.send_json({"type": "command", "request_id": "1", "device_id": dev, "action": "power.shutdown", "params": {}, "confirm": True})
            assert v.receive_json()["error"] == "Permissão insuficiente"
        with client.websocket_connect("/ws/client") as ui:
            ui.send_json({"type": "auth", "token": token})
            ui.receive_json()
            ui.send_json({"type": "command", "request_id": "2", "device_id": dev, "action": "power.shutdown", "params": {"delay": 60}})
            assert ui.receive_json()["error"] == "Ação crítica exige confirmação"
            ui.send_json({"type": "command", "request_id": "3", "device_id": dev, "action": "power.shutdown",
                          "params": {"delay": 60, "_user": "falso"}, "confirm": True})
            ui.receive_json()
            cmd = agent.receive_json()
            assert cmd["params"] == {"delay": 60, "_user": "kaio"}  # quem pediu é definido pelo servidor
            agent.send_json({"type": "result", "id": cmd["id"], "ok": True, "data": {"scheduled": True}})
            agent.send_json({"type": "power_event", "event": "scheduled", "action": "shutdown", "at": time.time() + 60, "delay": 60, "by": "kaio"})
            seen = {}
            while "device" not in seen:
                m = ui.receive_json()
                seen[m["type"]] = m
            assert seen["device"]["data"]["power"]["action"] == "shutdown"
            # bloquear não exige confirmação
            ui.send_json({"type": "command", "request_id": "4", "device_id": dev, "action": "power.lock"})
            assert ui.receive_json()["type"] == "ack"
            assert agent.receive_json()["action"] == "power.lock"
        # Windows encerra o agente ("stopping") durante o desligamento: estado continua "desligado pelo painel"
        agent.send_json({"type": "status", "state": "stopping"})
    d = client.get("/api/devices", headers=headers).json()[0]
    assert d["state"] == "power_off" and d["mac"] == "aa:bb:cc:dd:ee:ff"
    kinds = [n["kind"] for n in client.get("/api/notifications", headers=headers).json()]
    assert "power" in kinds and "device_power" in kinds and "device_stopped" not in kinds
    audit = [a["action"] for a in client.get("/api/audit", headers=headers).json()]
    assert "power.scheduled" in audit


def test_wake_on_lan(client, owner, monkeypatch):
    from app import wol

    assert wol.magic_packet("AA:BB:CC:DD:EE:FF") == b"\xff" * 6 + bytes.fromhex("aabbccddeeff") * 16
    with pytest.raises(ValueError):
        wol.magic_packet("zz:bb")
    assert wol.targets("192.168.1.77") == ["255.255.255.255", "192.168.1.255"]
    assert wol.targets("8.8.8.8") == ["255.255.255.255"]  # nunca faz broadcast para rede pública

    headers, _ = owner
    creds, _ = pair_device(client, headers)
    dev = creds["device_id"]
    assert client.post(f"/api/devices/{dev}/wake", headers=headers).status_code == 409  # MAC desconhecido
    with client.websocket_connect("/ws/agent") as agent:
        agent.send_json({"type": "auth", "device_id": dev, "secret": creds["device_secret"],
                         "info": {"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.77"}})
        agent.receive_json()
        assert client.post(f"/api/devices/{dev}/wake", headers=headers).status_code == 409  # já ligado
    sent = []
    monkeypatch.setattr(wol, "send", lambda mac, ip: sent.append((mac, ip)) or ["255.255.255.255:9"])
    r = client.post(f"/api/devices/{dev}/wake", headers=headers)
    assert r.status_code == 200 and sent == [("aa:bb:cc:dd:ee:ff", "192.168.1.77")]
    assert "power.wake" in [a["action"] for a in client.get("/api/audit", headers=headers).json()]


# ------------------------------------------------------------------ ponta a ponta (agente real, simulação)
def test_end_to_end_schedule_and_cancel(live_server, dry_run):  # noqa: F811
    from websockets.asyncio.client import connect

    from einstein_agent.pairing import pair
    from einstein_agent.runner import AgentRunner

    base, http = live_server
    cfg = pair(base, http.post("/api/pairing-codes", json={"device_name": "PC Energia"}).json()["code"])
    owner_events = []
    runner = AgentRunner(on_power=owner_events.append)
    runner.start()
    token = http.headers["Authorization"][7:]
    try:
        assert wait_for(lambda: http.get("/api/devices").json()[0]["online"])

        async def scenario():
            async with connect(base.replace("http", "ws") + "/ws/client") as ws:
                await ws.send(json.dumps({"type": "auth", "token": token}))

                async def call(action, params=None, confirm=False):
                    await ws.send(json.dumps({"type": "command", "request_id": "r", "device_id": cfg.device_id,
                                              "action": action, "params": params or {}, "confirm": confirm}))
                    while True:
                        m = json.loads(await asyncio.wait_for(ws.recv(), 15))
                        if m["type"] == "result":
                            return m

                st = await call("power.status")
                assert st["ok"] and st["data"]["capabilities"]["dry_run"] is True
                r = await call("power.restart", {"delay": 300}, confirm=True)
                assert r["ok"] and r["data"]["delay"] == 300
                r = await call("power.cancel")
                assert r["ok"] and r["data"]["canceled"] is True

        asyncio.run(scenario())
        flag = "/r" if WINDOWS else "-r"
        assert any(c[0].lower().endswith(("shutdown", "shutdown.exe")) and c[1] == flag for c in dry_run)
        assert any(c[0].lower().endswith(("shutdown", "shutdown.exe")) and c[1:] == (["/a"] if WINDOWS else ["-c"]) for c in dry_run)
        assert owner_events[0]["pending"]["by"] == "kaio" and owner_events[-1] == {"pending": None}
        kinds = [n["message"] for n in http.get("/api/notifications").json() if n["kind"] == "power"]
        assert any("cancelado" in k for k in kinds) and any("reinício" in k for k in kinds)
    finally:
        runner.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="API do Windows")
def test_windows_shutdown_privilege_real():
    """Regressão: no Windows 64 bits o handle do processo era truncado e a suspensão falhava com
    'Não foi possível abrir o token do processo'. Habilitar o privilégio não desliga nada."""
    from einstein_agent.power import _enable_shutdown_privilege

    _enable_shutdown_privilege()  # não pode lançar PowerError
