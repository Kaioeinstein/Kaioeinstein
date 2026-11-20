"""Fase 5: tela remota e controle de mouse/teclado.

Os testes com tela real precisam de display: no Linux rode com xvfb-run (o CI faz isso);
no Windows usam a área de trabalho do próprio executor.
"""
import asyncio
import io
import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

from conftest import XHR
from test_api import pair_device

HAS_DISPLAY = sys.platform == "win32" or bool(os.environ.get("DISPLAY"))
needs_display = pytest.mark.skipif(not HAS_DISPLAY, reason="precisa de tela (use xvfb-run)")


# ------------------------------------------------------------------ unidades
def test_frame_packet_roundtrip():
    from einstein_agent.screen import frame_packet, parse_frame

    header = {"seq": 7, "w": 640, "h": 400, "sw": 1920, "sh": 1200, "ts": 1}
    h, body = parse_frame(frame_packet(header, b"\xff\xd8jpeg"))
    assert h == header and body == b"\xff\xd8jpeg"
    with pytest.raises(ValueError):
        parse_frame(b"\x02abc")


def test_input_validation():
    from einstein_agent.inputs import InputError, KEYMAP, validate

    assert validate({"t": "move", "x": 1.7, "y": -3}) == {"t": "move", "x": 1.0, "y": 0.0}  # limitado ao monitor
    assert validate({"t": "button", "b": "left", "down": 1})["down"] is True
    assert validate({"t": "wheel", "dy": 99999})["dy"] == 2400
    assert validate({"t": "key", "code": "KeyA", "down": True})["code"] == "KeyA"
    assert len(validate({"t": "text", "text": "x" * 5000})["text"]) == 500
    for bad in [{"t": "move", "x": "a", "y": 0}, {"t": "button", "b": "x"}, {"t": "key", "code": "Foo"},
                {"t": "text", "text": ""}, {"t": "exec", "cmd": "rm"}, "lixo", {"t": "move", "x": float("nan"), "y": 0}]:
        with pytest.raises(InputError):
            validate(bad)
    for k in ("Enter", "Escape", "ControlLeft", "AltLeft", "MetaLeft", "Tab", "ArrowUp", "F5", "Delete"):
        assert k in KEYMAP


def test_abs_mapping_multi_monitor():
    from einstein_agent.inputs import Injector

    mon = {"left": 1920, "top": -200, "width": 1280, "height": 1024}
    assert Injector._abs({"x": 0, "y": 0}, mon) == (1920, -200)
    assert Injector._abs({"x": 1, "y": 1}, mon) == (1920 + 1279, -200 + 1023)


class FakeOverlay:
    def __init__(self, answer=True):
        self.answer, self.banner, self.asked = answer, None, []

    def show_banner(self, viewers, control, on_stop):
        self.banner = (list(viewers), control, on_stop)
        return True

    def hide_banner(self):
        self.banner = None

    def ask(self, user, control, timeout=30):
        self.asked.append((user, control))
        return self.answer


def make_controller(overlay, sent):
    from einstein_agent.screen import ScreenController

    async def send_bytes(b):
        sent.append(b)

    async def send_json(m):
        sent.append(m)

    return ScreenController(send_bytes, send_json, overlay=overlay, injector_factory=lambda: None)


def test_owner_policies_deny_and_ask():
    from einstein_agent.config import AgentConfig
    from einstein_agent.screen import ScreenRefused

    cfg = AgentConfig.load()
    cfg.screen_access = "deny"
    cfg.save()
    ov = FakeOverlay()
    ctl = make_controller(ov, [])
    with pytest.raises(ScreenRefused, match="bloqueou"):
        asyncio.run(ctl.start({"_user": "kaio"}))

    cfg.screen_access = "ask"
    cfg.save()
    ov = FakeOverlay(answer=False)
    ctl = make_controller(ov, [])
    with pytest.raises(ScreenRefused, match="recusado"):
        asyncio.run(ctl.start({"_user": "kaio", "_control": True}))
    assert ov.asked == [("kaio", True)] and ov.banner is None


# ------------------------------------------------------------------ servidor
def test_server_screen_rules(client, owner):
    headers, token = owner
    creds, _ = pair_device(client, headers)
    dev = creds["device_id"]
    client.post("/api/users", headers=headers, json={"username": "obs", "password": "Obs#Senha2026", "role": "viewer"})
    vtoken = client.post("/api/auth/login", json={"username": "obs", "password": "Obs#Senha2026"}, headers=XHR).json()["access_token"]

    with client.websocket_connect("/ws/agent") as agent:
        agent.send_json({"type": "auth", "device_id": dev, "secret": creds["device_secret"]})
        agent.receive_json()
        with client.websocket_connect("/ws/client") as viewer, client.websocket_connect("/ws/client") as ui:
            viewer.send_json({"type": "auth", "token": vtoken})
            viewer.receive_json()
            # observador não acessa a tela
            viewer.send_json({"type": "command", "request_id": "v", "device_id": dev, "action": "screen.start"})
            assert viewer.receive_json()["error"] == "Permissão insuficiente"

            ui.send_json({"type": "auth", "token": token})
            ui.receive_json()
            # campos internos enviados pelo cliente são ignorados; servidor define quem assiste
            ui.send_json({"type": "command", "request_id": "s", "device_id": dev, "action": "screen.start",
                          "params": {"control": True, "_user": "hacker", "_viewers": ["x"], "max_width": 800}})
            ack = ui.receive_json()
            cmd = agent.receive_json()
            assert cmd["params"]["_user"] == "kaio" and cmd["params"]["_viewers"] == ["kaio"] and cmd["params"]["_control"] is True
            agent.send_json({"type": "result", "id": cmd["id"], "ok": True, "data": {"control": True, "monitor": 1}})
            assert ui.receive_json()["id"] == ack["id"]
            note = ui.receive_json()
            assert note["type"] == "notification" and "com controle" in note["data"]["message"]

            # quadro do agente chega só a quem está na sessão
            agent.send_bytes(b"\x01\x00\x02{}JPEG")
            pkt = ui.receive_bytes()
            assert pkt == b"S" + dev.encode() + b"\x01\x00\x02{}JPEG"

            # confirmação e entrada são repassadas ao agente
            ui.send_json({"type": "screen_ack", "device_id": dev, "seq": 1})
            assert agent.receive_json() == {"type": "screen_ack", "seq": 1}
            ui.send_json({"type": "input", "device_id": dev, "events": [{"t": "move", "x": .5, "y": .5}] * 100})
            fwd = agent.receive_json()
            assert fwd["type"] == "input" and len(fwd["events"]) == 64  # limite por mensagem

            # observador tentando injetar entrada: ignorado (não está na sessão)
            viewer.send_json({"type": "input", "device_id": dev, "events": [{"t": "text", "text": "x"}]})
            # dono encerra pela faixa no PC → painel avisado
            agent.send_json({"type": "screen_event", "event": "stopped", "reason": "owner"})
            msg = ui.receive_json()
            while msg["type"] != "screen_stopped":
                msg = ui.receive_json()
            assert msg["reason"] == "owner"

            # nova sessão; fechar o painel derruba a sessão no agente
            ui.send_json({"type": "command", "request_id": "s2", "device_id": dev, "action": "screen.start", "params": {}})
            ui.receive_json()
            cmd = agent.receive_json()
            assert cmd["params"]["_control"] is False
            agent.send_json({"type": "result", "id": cmd["id"], "ok": True, "data": {"control": False}})
        stop = agent.receive_json()
        while stop.get("action") != "screen.stop":
            stop = agent.receive_json()
        assert stop["params"] == {"_viewers": [], "_control": False}

    audit = [a["action"] for a in client.get("/api/audit", headers=headers).json()]
    assert audit.count("screen.started") == 2 and audit.count("screen.ended") == 2


# ------------------------------------------------------------------ ponta a ponta com tela real
TK_APP = textwrap.dedent('''
    import sys, tkinter as tk
    out = sys.argv[1]
    root = tk.Tk()
    root.geometry("%dx%d+0+0" % (root.winfo_screenwidth(), root.winfo_screenheight()))
    entry = tk.Entry(root, font=("Arial", 28))
    entry.pack(fill="both", expand=True)
    root.bind("<Button-1>", lambda e: open(out + ".click", "w").write("%d,%d" % (e.x_root, e.y_root)))
    def done(e=None):
        open(out, "w", encoding="utf-8").write(entry.get())
        root.destroy()
    entry.bind("<Return>", done)
    root.after(200, entry.focus_force)
    root.after(60000, root.destroy)
    open(out + ".ready", "w").write("1")
    root.mainloop()
''')


@needs_display
@pytest.mark.skipif(sys.platform == "win32", reason="digitação real validada no Linux/Xvfb; no Windows o CI valida o cursor")
def test_end_to_end_view_and_control(live_server, tmp_path):  # noqa: F811
    from PIL import Image
    from websockets.asyncio.client import connect

    from einstein_agent.overlay import OVERLAY
    from einstein_agent.pairing import pair
    from einstein_agent.runner import AgentRunner
    from einstein_agent.screen import parse_frame

    base, http = live_server
    code = http.post("/api/pairing-codes", json={"device_name": "PC Tela"}).json()["code"]
    cfg = pair(base, code)
    out = tmp_path / "typed.txt"
    app_py = tmp_path / "app.py"
    app_py.write_text(TK_APP)
    app = subprocess.Popen([sys.executable, str(app_py), str(out)])
    runner = AgentRunner()
    runner.start()
    token = http.headers["Authorization"][7:]
    try:
        assert wait_for(lambda: (tmp_path / "typed.txt.ready").exists(), 15)
        assert wait_for(lambda: http.get("/api/devices").json()[0]["online"])

        async def scenario():
            async with connect(base.replace("http", "ws") + "/ws/client", max_size=16 * 2**20) as ws:
                await ws.send(json.dumps({"type": "auth", "token": token}))
                await ws.send(json.dumps({"type": "command", "request_id": "s", "device_id": cfg.device_id,
                                          "action": "screen.start", "params": {"control": True, "max_width": 640, "fps": 15}}))
                frames, result = [], None
                end = time.time() + 20
                while (len(frames) < 3 or result is None) and time.time() < end:
                    msg = await asyncio.wait_for(ws.recv(), 10)
                    if isinstance(msg, bytes):
                        assert msg[:1] == b"S" and msg[1:33].decode() == cfg.device_id
                        header, jpeg = parse_frame(msg[33:])
                        frames.append((header, jpeg))
                        await ws.send(json.dumps({"type": "screen_ack", "device_id": cfg.device_id, "seq": header["seq"]}))
                    elif json.loads(msg).get("type") == "result":
                        result = json.loads(msg)
                assert result and result["ok"] and result["data"]["control"] is True, result
                assert len(frames) >= 1
                header, jpeg = frames[-1]
                img = Image.open(io.BytesIO(jpeg))
                assert img.format == "JPEG" and img.size == (header["w"], header["h"]) and header["w"] == 640
                assert OVERLAY.banner_visible()  # aviso visível no PC durante a sessão

                events = [{"t": "button", "b": "left", "down": True, "x": 0.5, "y": 0.5},
                          {"t": "button", "b": "left", "down": False, "x": 0.5, "y": 0.5},
                          {"t": "text", "text": "Olá Einstein, ação ÇÃ"},
                          {"t": "key", "code": "Enter", "down": True}, {"t": "key", "code": "Enter", "down": False}]
                await ws.send(json.dumps({"type": "input", "device_id": cfg.device_id, "events": events}))
                end = time.time() + 15
                while not out.exists() and time.time() < end:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), 0.5)
                        if isinstance(msg, bytes):
                            h, _ = parse_frame(msg[33:])
                            await ws.send(json.dumps({"type": "screen_ack", "device_id": cfg.device_id, "seq": h["seq"]}))
                    except asyncio.TimeoutError:
                        pass
                await ws.send(json.dumps({"type": "command", "request_id": "x", "device_id": cfg.device_id, "action": "screen.stop"}))
                await asyncio.sleep(1.5)

        asyncio.run(scenario())
        assert out.read_text(encoding="utf-8") == "Olá Einstein, ação ÇÃ"
        w, h = map(int, (tmp_path / "typed.txt.click").read_text().split(","))
        assert w > 0 and h > 0  # o clique remoto chegou ao aplicativo
        assert not OVERLAY.banner_visible()  # faixa some ao encerrar
    finally:
        runner.stop()
        app.kill()
        app.wait(5)


@needs_display
def test_real_capture_and_cursor():
    """Captura real do monitor e leitura/movimento do ponteiro (Windows: SendInput; Linux: XTEST)."""
    from einstein_agent.inputs import create_injector
    from einstein_agent.screen import Capturer

    cap = Capturer()
    try:
        mons = cap.monitors()
        assert mons and mons[0]["width"] > 0
        inj = create_injector()
        assert inj is not None
        mon = mons[0]
        inj.apply({"t": "move", "x": 0.25, "y": 0.5}, mon)
        time.sleep(0.2)
        x, y = inj.cursor()
        ex, ey = inj._abs({"x": 0.25, "y": 0.5}, mon)
        assert abs(x - ex) <= 2 and abs(y - ey) <= 2
        jpeg, crc, size = cap.grab(mon, 800, 60, (x, y), None)
        assert jpeg[:2] == b"\xff\xd8" and size[0] == min(800, mon["width"])
        same, crc2, _ = cap.grab(mon, 800, 60, (x, y), crc)
        assert same is None or crc2 != crc  # tela parada não é reenviada
    finally:
        cap.close()


def wait_for(cond, timeout=10, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


from test_agent import live_server  # noqa: E402,F401  (fixture reutilizada)
