"""Fase 3: núcleo do agente (estados, pausa/encerramento informados ao servidor, log, instância única, pareamento, bandeja)."""
import asyncio
import os
import socket
import threading
import time

os.environ.setdefault("PYSTRAY_BACKEND", "dummy")  # bandeja testável sem tela

import httpx
import pytest
import uvicorn

from app.main import create_app
from conftest import STRONG, XHR, make_settings


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(tmp_path):
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(make_settings(tmp_path)), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}"
    http = httpx.Client(base_url=base)
    token = http.post("/api/auth/setup", json={"username": "kaio", "password": STRONG}, headers=XHR).json()["access_token"]
    http.headers.update({"Authorization": f"Bearer {token}", **XHR})
    yield base, http
    http.close()
    server.should_exit = True
    th.join(5)


def wait_for(cond, timeout=10, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


# ------------------------------------------------------------------ unidades
def test_icons():
    from einstein_agent.icons import STATE_COLORS, make_icon

    for state in [None, *STATE_COLORS]:
        img = make_icon(state, 64)
        assert img.size == (64, 64) and img.mode == "RGBA"
    online = make_icon("online").getpixel((57, 57))
    offline = make_icon("offline").getpixel((57, 57))
    assert online != offline  # o ponto de status muda de cor


def test_save_ico(tmp_path):
    from PIL import Image

    from einstein_agent.icons import save_ico

    path = tmp_path / "icon.ico"
    save_ico(str(path))
    assert (256, 256) in Image.open(path).info["sizes"]


def test_single_instance():
    from einstein_agent.singleton import SingleInstance

    a, b = SingleInstance("agent"), SingleInstance("agent")
    assert a.acquire()
    assert not b.acquire()
    a.release()
    assert b.acquire()
    b.release()


def test_pairing_validation():
    from einstein_agent.pairing import PairingError, normalize_server, pair

    assert normalize_server(" meu-pc.local:8443/ ") == "https://meu-pc.local:8443"
    with pytest.raises(PairingError, match="8 caracteres"):
        pair("https://x", "ABC")
    with pytest.raises(PairingError, match="sem criptografia"):
        pair("http://192.168.0.10:8000", "ABCD-EFGH")
    with pytest.raises(PairingError, match="contatar"):
        pair(f"http://127.0.0.1:{free_port()}", "ABCD-EFGH")


def test_autostart_command_uses_tray():
    from einstein_agent import autostart

    assert autostart.startup_command().endswith("-m einstein_agent tray")


def test_offline_status_when_server_down():
    from einstein_agent.client import Agent
    from einstein_agent.config import AgentConfig

    cfg = AgentConfig(server_url=f"http://127.0.0.1:{free_port()}", device_id="x", device_secret="plain:y")
    states = []

    async def scenario():
        agent = Agent(cfg, on_status=lambda s: states.append(s.state))
        task = asyncio.create_task(agent.run_forever())
        for _ in range(300):  # no Windows a recusa de conexão leva ~2 s
            if "offline" in states:
                break
            await asyncio.sleep(0.05)
        agent.stop()
        await asyncio.wait_for(task, 5)

    asyncio.run(scenario())
    assert states[:2] == ["connecting", "offline"] and states[-1] == "stopped"


# ------------------------------------------------------------------ integração
def test_pause_resume_stop_reported_to_server(live_server):
    from einstein_agent import cli
    from einstein_agent.config import AgentConfig
    from einstein_agent.logs import log_file
    from einstein_agent.runner import AgentRunner

    base, http = live_server
    code = http.post("/api/pairing-codes", json={"device_name": "PC Kaio"}).json()["code"]
    assert cli.main(["pair", "--server", base, "--code", code]) == 0
    cfg = AgentConfig.load()
    cfg.interval = 0.3
    cfg.save()

    states = []
    runner = AgentRunner(on_status=lambda s: states.append(s.state))
    assert runner.start()
    device = lambda: http.get("/api/devices").json()[0]  # noqa: E731
    try:
        assert wait_for(lambda: device()["state"] == "online")
        assert device()["agent_version"] == "0.3.0"

        runner.set_paused(True)
        assert wait_for(lambda: device()["state"] == "paused")
        assert "paused" in states

        runner.set_paused(False)
        assert wait_for(lambda: device()["state"] == "online")
    finally:
        runner.stop()
    assert wait_for(lambda: device()["state"] == "stopped")
    assert states[-1] == "stopped"

    kinds = [n["kind"] for n in http.get("/api/notifications").json()]
    assert "device_paused" in kinds and "device_stopped" in kinds and "device_offline" not in kinds
    # o segredo do dispositivo nunca aparece no log
    secret = cfg.secret()
    assert log_file().exists() and secret not in log_file().read_text()


def test_revoked_device_goes_to_rejected(live_server):
    from einstein_agent.pairing import pair
    from einstein_agent.runner import AgentRunner

    base, http = live_server
    code = http.post("/api/pairing-codes", json={"device_name": "PC X"}).json()["code"]
    cfg = pair(base, code)
    assert http.delete(f"/api/devices/{cfg.device_id}").status_code == 200
    states = []
    runner = AgentRunner(on_status=lambda s: states.append(s.state))
    runner.start()
    assert wait_for(lambda: "rejected" in states)
    assert wait_for(lambda: not runner.running)  # para de tentar
    kinds = [n["kind"] for n in http.get("/api/notifications").json()]
    assert "connection_failure" in kinds


def test_cli_run_refuses_second_instance(live_server, capsys):
    from einstein_agent import cli
    from einstein_agent.singleton import SingleInstance

    base, http = live_server
    code = http.post("/api/pairing-codes", json={"device_name": "PC Y"}).json()["code"]
    cli.main(["pair", "--server", base, "--code", code])
    held = SingleInstance("agent")
    assert held.acquire()
    try:
        assert cli.main(["run"]) == 3
        assert "já está em execução" in capsys.readouterr().out
    finally:
        held.release()


def test_tray_controller(live_server):
    pytest.importorskip("pystray")
    from einstein_agent.client import Status
    from einstein_agent.config import AgentConfig
    from einstein_agent.pairing import pair
    from einstein_agent.tray import TrayApp

    class FakeIcon:
        """Substitui o ícone nativo: registra imagem, dica e menu."""
        HAS_NOTIFICATION = True

        def __init__(self, name, icon, title, menu):
            self.name, self.icon, self.title, self.menu = name, icon, title, menu
            self.visible = False
            self.stopped = False

        def update_menu(self):
            pass

        def notify(self, message, title=None):
            pass

        def stop(self):
            self.stopped = True

    base, http = live_server
    tray = TrayApp(backend_icon_cls=FakeIcon)
    notes = []
    tray.notify = notes.append

    labels = lambda: [i.text for i in tray.icon.menu.items if i.visible]  # noqa: E731
    assert "Vincular este computador…" in labels()
    assert "Desvincular este computador…" not in labels()

    code = http.post("/api/pairing-codes", json={"device_name": "PC Tray"}).json()["code"]
    pair(base, code)
    cfg = AgentConfig.load(); cfg.interval = 0.3; cfg.save()  # noqa: E702
    tray._setup(tray.icon)
    try:
        assert wait_for(lambda: tray.status.state == "online")
        assert tray.icon.title == "EINSTEIN REMOTE · Online — PC Tray"
        assert any("Conectado" in n for n in notes)
        assert "Pausar monitoramento" in labels() and "Desvincular este computador…" in labels()
        tray.toggle_pause()
        assert wait_for(lambda: tray.status.state == "paused")
        assert "Retomar monitoramento" in labels()
        tray.toggle_pause()
        assert wait_for(lambda: tray.status.state == "online")
        tray._on_status(Status("offline", "x"))
        assert any("perdida" in n for n in notes)
        tray.quit()
        assert tray.icon.stopped and not tray.runner.running
    finally:
        tray.runner.stop()
