"""Volume do PC pelo painel."""
import asyncio
import json
import re
import sys

import pytest

from einstein_agent import audio
from einstein_agent.config import AgentConfig
from test_agent import live_server, wait_for  # noqa: F401


@pytest.fixture
def fake_mixer(monkeypatch):
    """Mixer simulado (pactl) para testar a lógica em qualquer sistema."""
    st = {"volume": 40, "muted": False}

    def pactl(*args):
        if args[0] == "get-sink-volume":
            return f"Volume: front-left: 26214 /  {st['volume']}% / -23.88 dB"
        if args[0] == "get-sink-mute":
            return f"Mute: {'yes' if st['muted'] else 'no'}"
        if args[0] == "set-sink-volume":
            st["volume"] = int(re.match(r"(\d+)%", args[2]).group(1))
        if args[0] == "set-sink-mute":
            st["muted"] = args[2] == "1"
        return ""

    monkeypatch.setattr(audio, "WINDOWS", False)
    monkeypatch.setattr(audio, "_pactl", pactl)
    AgentConfig(server_url="http://x").save()
    return st


def test_change_volume_steps_limits_and_mute(fake_mixer):
    assert audio.status() == {"volume": 40, "muted": False, "available": True}
    assert audio.change({"step": 10})["volume"] == 50
    assert audio.change({"volume": 150})["volume"] == 100  # nunca passa de 100
    assert audio.change({"step": -500})["volume"] == 0
    assert audio.change({"muted": True})["muted"] is True
    assert audio.change({"muted": False})["muted"] is False
    with pytest.raises(audio.AudioError, match="Nada"):
        audio.change({})


def test_owner_can_block_volume(fake_mixer):
    cfg = AgentConfig.load()
    cfg.allow_volume = False
    cfg.save()
    with pytest.raises(audio.AudioError, match="bloqueou"):
        audio.change({"volume": 90})
    assert fake_mixer["volume"] == 40
    assert audio.status()["volume"] == 40  # ler continua permitido


@pytest.mark.skipif(sys.platform != "win32", reason="API Core Audio do Windows")
def test_windows_core_audio_real():
    """Chama a API real; a máquina virtual do CI pode não ter som — então exige a mensagem clara, sem travar."""
    try:
        s = audio.status()
    except audio.AudioError as exc:
        assert "alto-falante" in str(exc) or "recusou" in str(exc)
        return
    assert 0 <= s["volume"] <= 100 and isinstance(s["muted"], bool)
    # volta ao mesmo valor (não muda o som do executor)
    assert audio.change({"volume": s["volume"], "muted": s["muted"]})["volume"] == s["volume"]


def test_server_policy():
    from app.hub import ALLOWED_ACTIONS

    assert ALLOWED_ACTIONS["audio.set"].role == "admin" and ALLOWED_ACTIONS["audio.status"].role == "viewer"


def test_end_to_end_volume_from_panel(live_server, fake_mixer):  # noqa: F811
    from websockets.asyncio.client import connect

    from einstein_agent.pairing import pair
    from einstein_agent.runner import AgentRunner

    base, http = live_server
    cfg = pair(base, http.post("/api/pairing-codes", json={"device_name": "PC Som"}).json()["code"])
    runner = AgentRunner()
    runner.start()
    token = http.headers["Authorization"][7:]
    try:
        assert wait_for(lambda: http.get("/api/devices").json()[0]["online"])

        async def scenario():
            async with connect(base.replace("http", "ws") + "/ws/client") as ws:
                await ws.send(json.dumps({"type": "auth", "token": token}))

                async def call(action, params=None):
                    await ws.send(json.dumps({"type": "command", "request_id": "r", "device_id": cfg.device_id,
                                              "action": action, "params": params or {}}))
                    while True:
                        m = json.loads(await asyncio.wait_for(ws.recv(), 15))
                        if m["type"] == "result":
                            return m

                assert (await call("audio.status"))["data"]["volume"] == 40
                r = await call("audio.set", {"volume": 75})
                assert r["ok"] and r["data"]["volume"] == 75
                r = await call("audio.set", {"muted": True})
                assert r["ok"] and r["data"]["muted"] is True

        asyncio.run(scenario())
        assert fake_mixer == {"volume": 75, "muted": True}
    finally:
        runner.stop()
