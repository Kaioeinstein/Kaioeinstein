"""Fase 4: SYSTEM MONITOR (coleta real, encerramento seguro de processos, políticas do servidor)."""
import asyncio
import json
import os
import subprocess
import sys
import time

import psutil
import pytest
from websockets.asyncio.client import connect

from conftest import XHR
from test_agent import live_server, wait_for  # noqa: F401  (fixture reutilizada)
from test_api import pair_device

WINDOWS = sys.platform == "win32"


@pytest.fixture
def victim():
    """Processo descartável para ser encerrado nos testes."""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    proc = psutil.Process(p.pid)
    yield proc
    if p.poll() is None:
        p.kill()
    p.wait(10)


# ------------------------------------------------------------------ coleta real
def test_processes_snapshot_real():
    from einstein_agent import sysmon

    snap = sysmon.SAMPLER.snapshot()
    me = next(p for p in snap["processes"] if p["pid"] == os.getpid())
    assert me["protected"] is True  # o próprio agente nunca é encerrável
    assert me["memory"] > 0 and me["create_time"] > 0
    assert snap["count"] == len(snap["processes"]) > 3
    assert all(0 <= p["cpu"] <= 100 for p in snap["processes"])
    # ordenado por CPU decrescente
    cpus = [p["cpu"] for p in snap["processes"]]
    assert cpus == sorted(cpus, reverse=True)


def test_network_hardware_services_apps_shapes():
    from einstein_agent import sysmon

    net = sysmon.network()
    assert net["interfaces"] and any(i["loopback"] for i in net["interfaces"])
    hw = sysmon.hardware()
    assert hw["cpu"]["threads"] >= 1 and hw["memory"]["total"] > 0 and hw["os"]["hostname"]
    assert set(hw["system"]) == {"manufacturer", "model", "board", "bios", "bios_date"}
    svc = sysmon.services()
    assert "supported" in svc and isinstance(svc["services"], list)
    apps = sysmon.open_apps()
    assert "supported" in apps and isinstance(apps["apps"], list)


@pytest.mark.skipif(not WINDOWS, reason="somente Windows")
def test_windows_specific_sources():
    from einstein_agent import sysmon

    svc = sysmon.services()
    assert svc["supported"] and len(svc["services"]) > 50
    assert any(s["name"].lower() == "eventlog" for s in svc["services"])
    assert sysmon.open_apps()["supported"] is True
    hw = sysmon.hardware()
    assert hw["os"]["name"].startswith("Windows")
    procs = {p["name"].lower(): p for p in sysmon.SAMPLER.snapshot()["processes"]}
    assert procs["csrss.exe"]["protected"] if "csrss.exe" in procs else True


# ------------------------------------------------------------------ encerramento seguro
def test_kill_guards(victim):
    from einstein_agent.sysmon import KillRefused, kill_process

    name, ct = victim.name(), victim.create_time()
    with pytest.raises(KillRefused, match="desativou"):
        kill_process(victim.pid, name, ct, allowed=False)
    with pytest.raises(KillRefused, match="outro processo"):
        kill_process(victim.pid, name, ct - 100, allowed=True)  # PID reutilizado
    with pytest.raises(KillRefused, match="outro processo"):
        kill_process(victim.pid, "outro.exe", ct, allowed=True)
    with pytest.raises(KillRefused, match="próprio agente"):
        kill_process(os.getpid(), psutil.Process().name(), psutil.Process().create_time(), allowed=True)
    with pytest.raises(KillRefused, match="não existe"):
        kill_process(2**22 + 12345, "x", 0, allowed=True)
    with pytest.raises(KillRefused, match="Parâmetros"):
        kill_process("abc", name, ct, allowed=True)
    assert victim.is_running()


def test_protected_names():
    from einstein_agent.sysmon import is_protected

    for n in ("csrss.exe", "LSASS.EXE", "svchost.exe", "explorer.exe", "System"):
        assert is_protected(1234, n)
    assert is_protected(4, "qualquer")
    assert not is_protected(1234, "chrome.exe")


def test_kill_success(victim):
    from einstein_agent.sysmon import kill_process

    r = kill_process(victim.pid, victim.name(), victim.create_time(), allowed=True)
    assert r["terminated"] and not psutil.pid_exists(victim.pid) or victim.status() == psutil.STATUS_ZOMBIE


def test_kill_command_respects_local_switch(victim):
    from einstein_agent import commands
    from einstein_agent.config import AgentConfig

    cfg = AgentConfig.load()
    cfg.allow_process_kill = False
    cfg.save()
    ok, data, err = asyncio.run(commands.execute(
        "process.kill", {"pid": victim.pid, "name": victim.name(), "create_time": victim.create_time()}))
    assert not ok and "desativou" in err and "Traceback" not in err
    assert victim.is_running()


# ------------------------------------------------------------------ políticas do servidor
def test_server_policies_and_audit(client, owner):
    headers, token = owner
    creds, _ = pair_device(client, headers)
    client.post("/api/users", headers=headers, json={"username": "obs", "password": "Obs#Senha2026", "role": "viewer"})
    vtoken = client.post("/api/auth/login", json={"username": "obs", "password": "Obs#Senha2026"}, headers=XHR).json()["access_token"]

    with client.websocket_connect("/ws/agent") as agent:
        agent.send_json({"type": "auth", "device_id": creds["device_id"], "secret": creds["device_secret"]})
        agent.receive_json()
        with client.websocket_connect("/ws/client") as ui:
            ui.send_json({"type": "auth", "token": vtoken})
            ui.receive_json()
            # observador pode ler processos...
            ui.send_json({"type": "command", "request_id": "r", "device_id": creds["device_id"], "action": "system.processes"})
            assert ui.receive_json()["type"] == "ack"
            cmd = agent.receive_json()
            agent.send_json({"type": "result", "id": cmd["id"], "ok": True, "data": {"processes": []}})
            assert ui.receive_json()["ok"] is True
            # ...mas não encerrar
            ui.send_json({"type": "command", "request_id": "k", "device_id": creds["device_id"], "action": "process.kill",
                          "params": {"pid": 1}, "confirm": True})
            assert ui.receive_json()["error"] == "Permissão insuficiente"

        with client.websocket_connect("/ws/client") as ui:
            ui.send_json({"type": "auth", "token": token})
            ui.receive_json()
            ui.send_json({"type": "command", "request_id": "k", "device_id": creds["device_id"], "action": "process.kill",
                          "params": {"pid": 1, "name": "x", "create_time": 1}})
            assert ui.receive_json()["error"] == "Ação crítica exige confirmação"
            ui.send_json({"type": "command", "request_id": "k2", "device_id": creds["device_id"], "action": "process.kill",
                          "params": {"pid": 77, "name": "jogo.exe", "create_time": 1}, "confirm": True})
            assert ui.receive_json()["type"] == "ack"
            cmd = agent.receive_json()
            assert cmd["params"]["name"] == "jogo.exe"
            agent.send_json({"type": "result", "id": cmd["id"], "ok": True, "data": {"terminated": True}})
            assert ui.receive_json()["ok"] is True

    audit = client.get("/api/audit", headers=headers).json()
    details = [json.loads(a["detail"] or "{}") for a in audit if a["action"].startswith("command.")]
    actions = [d.get("action") for d in details]
    assert "system.processes" not in actions  # leitura automática não polui a auditoria
    kill_result = next(d for a, d in zip([a for a in audit if a["action"].startswith("command.")], details) if a["action"] == "command.result")
    assert kill_result["process"] == "jogo.exe (PID 77)"
    rejected = [d for a, d in zip([a for a in audit if a["action"].startswith("command.")], details) if a["action"] == "command.rejected"]
    assert len(rejected) == 2  # sem permissão + sem confirmação


# ------------------------------------------------------------------ ponta a ponta com agente real
def test_end_to_end_list_and_kill(live_server, victim):  # noqa: F811
    from einstein_agent.config import AgentConfig
    from einstein_agent.pairing import pair
    from einstein_agent.runner import AgentRunner

    base, http = live_server
    code = http.post("/api/pairing-codes", json={"device_name": "PC Monitor"}).json()["code"]
    cfg = pair(base, code)
    runner = AgentRunner()
    runner.start()
    token = http.headers["Authorization"][7:]
    try:
        assert wait_for(lambda: http.get("/api/devices").json()[0]["online"])

        async def scenario():
            async with connect(base.replace("http", "ws") + "/ws/client") as ws:
                await ws.send(json.dumps({"type": "auth", "token": token}))

                async def call(action, params=None, confirm=False, rid="x"):
                    await ws.send(json.dumps({"type": "command", "request_id": rid, "device_id": cfg.device_id,
                                              "action": action, "params": params or {}, "confirm": confirm}))
                    while True:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), 20))
                        if msg["type"] == "result":
                            return msg

                res = await call("system.processes")
                assert res["ok"]
                row = next(p for p in res["data"]["processes"] if p["pid"] == victim.pid)
                assert not row["protected"]
                for action in ("system.network", "system.hardware", "system.services", "system.apps"):
                    assert (await call(action))["ok"], action
                res = await call("process.kill", {"pid": row["pid"], "name": row["name"], "create_time": row["create_time"]}, True)
                assert res["ok"] and res["data"]["terminated"], res
                res = await call("process.kill", {"pid": os.getpid(), "name": psutil.Process().name(),
                                                  "create_time": psutil.Process().create_time()}, True)
                assert not res["ok"]

        asyncio.run(scenario())
        assert wait_for(lambda: not victim.is_running() or victim.status() == psutil.STATUS_ZOMBIE)
    finally:
        runner.stop()


# ------------------------------------------------------------------ Fase 9: GPU/temperatura no Windows
def test_gpu_engine_aggregation_like_task_manager():
    from einstein_agent.win_perf import adapter_memory, gpu_usage_from_engines, kelvin_to_celsius

    engines = {
        "pid_100_luid_0x00000000_0x0000D1A2_phys_0_eng_0_engtype_3D": 30.0,
        "pid_200_luid_0x00000000_0x0000D1A2_phys_0_eng_0_engtype_3D": 25.0,   # 3D somado entre processos = 55
        "pid_200_luid_0x00000000_0x0000D1A2_phys_0_eng_4_engtype_VideoDecode": 40.0,
        "pid_300_luid_0x00000000_0x0000BEEF_phys_0_eng_0_engtype_3D": 5.0,
        "lixo": 99.0,
    }
    assert gpu_usage_from_engines(engines) == {"0x00000000_0x0000D1A2": 55.0, "0x00000000_0x0000BEEF": 5.0}
    assert gpu_usage_from_engines({"pid_1_luid_a_phys_0_eng_0_engtype_3D": 80.0, "pid_2_luid_a_phys_0_eng_0_engtype_3D": 70.0}) == {"a": 100.0}
    assert adapter_memory({"luid_0x0_0xD1A2_phys_0": 2e9, "luid_0x0_0xD1A2_phys_1": 1e9}) == {"0x0_0xD1A2": 3e9}
    assert kelvin_to_celsius(318.15) == 45.0 and kelvin_to_celsius(0) is None and kelvin_to_celsius(5000) is None


def test_temperatures_ignore_gpus_without_sensor():
    from einstein_agent.metrics import MetricsCollector

    t = MetricsCollector()._temperatures([{"name": "Radeon", "temp": None}], [{"label": "Zona", "current": 51.0}])
    assert t["max"] == 51.0 and len(t["sensors"]) >= 1


@pytest.mark.skipif(not WINDOWS, reason="somente Windows")
def test_windows_pdh_counters():
    """Valida o acesso à API PDH com um contador que sempre existe; GPU/temperatura podem não existir na VM."""
    from einstein_agent.win_perf import PdhQuery, create

    q = PdhQuery()
    assert q.add(r"\Processor(_Total)\% Processor Time") and q.add(r"\Processor(*)\% Processor Time")
    q.collect()
    time.sleep(0.6)
    q.collect()
    total = q.value(r"\Processor(_Total)\% Processor Time")
    per_core = q.array(r"\Processor(*)\% Processor Time")
    assert total is not None and 0 <= total <= 100
    assert "_Total" in per_core and len(per_core) >= 2
    q.close()
    sensors = create()
    assert sensors is not None
    s = sensors.sample([])
    assert isinstance(s["gpus"], list) and isinstance(s["sensors"], list)
