"""Limpeza de disco, inicialização do Windows, modo de energia e modo jogo."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import psutil
import pytest

from einstein_agent import maintenance as m
from einstein_agent.config import AgentConfig
from test_agent import live_server, wait_for  # noqa: F401

WINDOWS = sys.platform == "win32"
OLD = time.time() - 3 * 3600


def _file(p: Path, size: int, mtime: float = OLD) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    os.utime(p, (mtime, mtime))
    return p


@pytest.fixture
def fake_categories(tmp_path, monkeypatch):
    temp, cache, outside = tmp_path / "Temp", tmp_path / "Cache", tmp_path / "Importante"
    old1 = _file(temp / "setup123/a.tmp", 1000)
    old2 = _file(temp / "b.log", 500)
    recent = _file(temp / "em-uso.tmp", 300, mtime=time.time())
    old3 = _file(cache / "data_1", 2000)
    precious = _file(outside / "foto.jpg", 4000)
    link = temp / "atalho-para-fora"
    try:
        link.symlink_to(outside, target_is_directory=True)
        has_link = True
    except OSError:  # Windows sem permissão para links simbólicos
        has_link = False
    cats = {"temp": {"label": "Temporários", "desc": "", "paths": [temp]},
            "browser": {"label": "Cache", "desc": "", "paths": [cache, tmp_path / "nao-existe"]}}
    monkeypatch.setattr(m, "categories", lambda: cats)
    return {"old": [old1, old2, old3], "recent": recent, "precious": precious, "has_link": has_link}


def test_scan_counts_only_old_files_and_never_follows_links(fake_categories):
    r = m.scan()
    by = {c["id"]: c for c in r["categories"]}
    assert by["temp"]["bytes"] == 1500 and by["temp"]["files"] == 2  # recente e o que está do outro lado do link ficam fora
    assert by["browser"]["bytes"] == 2000
    assert r["total"] == 3500 and r["disks"]


def test_clean_deletes_old_files_keeps_recent_and_outside(fake_categories):
    AgentConfig(server_url="http://x", allow_maintenance=True).save()
    r = m.clean(["temp", "browser"])
    assert r["freed"] == 3500 and r["files"] == 3
    assert not any(p.exists() for p in fake_categories["old"])
    assert fake_categories["recent"].exists()  # modificado na última hora: pode estar em uso
    assert fake_categories["precious"].exists()  # link apontando para fora nunca é seguido


def test_clean_refusals(fake_categories):
    AgentConfig(server_url="http://x", allow_maintenance=False).save()
    with pytest.raises(m.MaintenanceRefused, match="bloqueou"):
        m.clean(["temp"])
    assert all(p.exists() for p in fake_categories["old"])
    AgentConfig(server_url="http://x", allow_maintenance=True).save()
    with pytest.raises(m.MaintenanceRefused, match="inválida"):
        m.clean(["../../windows"])
    with pytest.raises(m.MaintenanceRefused):
        m.clean([])


def test_real_categories_are_user_folders():
    cats = m.categories()
    assert {"temp", "browser", "crash"} <= set(cats)
    home = Path.home().resolve()
    import tempfile

    tmp_root = Path(tempfile.gettempdir()).resolve()
    for c in cats.values():
        for p in c["paths"]:
            rp = Path(p).resolve()
            assert rp == tmp_root or home in rp.parents or rp.is_relative_to(Path(os.environ.get("LOCALAPPDATA", home)))


def test_find_game_by_owner_list():
    me = psutil.Process().name()
    assert m.find_game([me.removesuffix(".exe")]) == me
    assert m.find_game(["nenhum-jogo-com-esse-nome"]) is None


def test_game_mode_switches_and_restores():
    AgentConfig(server_url="http://x", game_mode_auto=True).save()
    events, applied = [], []
    running = {"game": None}
    mode = {"now": "equilibrado"}

    def apply(x):
        applied.append(x)
        mode["now"] = x
        return x

    g = m.GameMode(emit=events.append, finder=lambda extra: running["game"], get_mode=lambda: mode["now"], apply_mode=apply)
    g.tick()
    assert not events  # nenhum jogo
    running["game"] = "VALORANT-Win64-Shipping.exe"
    g.tick()
    assert applied == ["desempenho"] and g.state()["active"] and events[-1]["event"] == "started"
    g.tick()
    assert applied == ["desempenho"]  # não repete enquanto o jogo continua aberto
    running["game"] = None
    g.tick()
    assert applied == ["desempenho", "equilibrado"] and events[-1] == {
        "type": "game_event", "event": "stopped", "game": "VALORANT-Win64-Shipping.exe", "error": None, "mode": "equilibrado"}

    # dono desliga o automático com o jogo aberto: volta ao modo anterior na hora
    running["game"] = "game.exe"
    g.tick()
    cfg = AgentConfig.load()
    cfg.game_mode_auto = False
    cfg.save()
    g.tick()
    assert applied[-1] == "equilibrado" and not g.state()["active"]


def test_game_mode_reports_windows_refusal():
    AgentConfig(server_url="http://x", game_mode_auto=True).save()
    events = []

    def refuse(_):
        raise m.MaintenanceRefused("sem modo de energia")

    g = m.GameMode(emit=events.append, finder=lambda e: "jogo.exe", get_mode=lambda: None, apply_mode=refuse)
    g.tick()
    assert events[-1]["error"] == "sem modo de energia"


def test_configure_game_sanitizes():
    AgentConfig(server_url="http://x").save()
    r = m.configure_game({"auto": True, "processes": ["javaw", "Roblox<script>", "", "javaw"]})
    assert r == {"auto": True, "processes": ["Robloxscript", "javaw"]}


def test_guid_roundtrip():
    for v in m.MODES.values():
        assert str(m.GUID.parse(v)) == v


@pytest.mark.skipif(not WINDOWS, reason="API do Windows")
def test_windows_real_apis():
    """Leituras reais no Windows (sem alterar nada): modo de energia, planos, inicialização, Lixeira."""
    assert m.current_mode() in (None, *m.MODES)
    plans = m.power_plans()
    assert plans and sum(p["active"] for p in plans) == 1
    for item in m.startup_items():
        assert item["scope"] in ("user", "machine") and isinstance(item["enabled"], bool)
    size, count = m._recycle_bin()
    assert size >= 0 and count >= 0


def test_server_policies():
    from app.hub import ALLOWED_ACTIONS

    assert ALLOWED_ACTIONS["maint.clean"].confirm and ALLOWED_ACTIONS["maint.clean"].role == "admin"
    assert ALLOWED_ACTIONS["perf.game"].role == "admin" and ALLOWED_ACTIONS["perf.status"].role == "viewer"


def test_end_to_end_scan_clean_and_game_event(live_server, fake_categories):  # noqa: F811
    from websockets.asyncio.client import connect

    from einstein_agent.pairing import pair
    from einstein_agent.runner import AgentRunner

    base, http = live_server
    cfg = pair(base, http.post("/api/pairing-codes", json={"device_name": "PC Jogos"}).json()["code"])
    runner = AgentRunner()
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
                        msg = json.loads(await asyncio.wait_for(ws.recv(), 30))
                        if msg["type"] == "result":
                            return msg
                        if msg["type"] == "error":
                            return {"ok": False, "error": msg.get("message")}

                scan = await call("maint.scan")
                assert scan["ok"] and scan["data"]["total"] == 3500
                refused = await call("maint.clean", {"categories": ["temp"]})  # sem confirmação explícita
                assert not refused["ok"]
                assert all(p.exists() for p in fake_categories["old"])
                done = await call("maint.clean", {"categories": ["temp", "browser"]}, confirm=True)
                assert done["ok"] and done["data"]["freed"] == 3500
                st = await call("perf.status")
                assert st["ok"] and st["data"]["game"]["auto"] is False
                me = psutil.Process().name().removesuffix(".exe")  # "jogo" = o próprio processo de teste
                r = await call("perf.game", {"auto": True, "processes": [me]})
                assert r["ok"] and r["data"]["state"]["active"] is True

        asyncio.run(scenario())
        assert wait_for(lambda: any(n["kind"] == "game_mode" for n in http.get("/api/notifications").json()), 10)
        sent = [a for a in http.get("/api/audit").json() if a["action"] == "command.sent"]
        assert any("maint.clean" in json.dumps(a.get("detail")) for a in sent)  # limpeza registrada na auditoria
    finally:
        runner.stop()
