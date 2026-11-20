"""Teste da janela gráfica de vinculação (Tk real). Requer tela: rode com xvfb-run no Linux.

    xvfb-run -a python -m pytest tests/test_pair_window_gui.py
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

tk = pytest.importorskip("tkinter")
if not os.environ.get("DISPLAY") and os.name != "nt":
    pytest.skip("sem tela (use xvfb-run)", allow_module_level=True)


class FakePairServer(BaseHTTPRequestHandler):
    """Emula /api/agent/pair: aceita somente o código ABCD-EFGH."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        normalized = "".join(c for c in body["code"].upper() if c.isalnum())  # igual ao servidor real
        ok = normalized == "ABCDEFGH"
        payload = {"device_id": "dev123", "device_secret": "s" * 64, "name": "PC Gráfico"} if ok else {"detail": "Código inválido ou expirado"}
        data = json.dumps(payload).encode()
        self.send_response(200 if ok else 401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_server():
    srv = HTTPServer(("127.0.0.1", 0), FakePairServer)
    srv.requests = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


def drive_window(monkeypatch, steps):
    """Executa `steps(root)` dentro do mainloop da janela real."""
    orig = tk.Tk.mainloop

    def mainloop(self, n=0):
        self.after(300, lambda: steps(self))
        self.after(15000, self.destroy)  # segurança: o teste nunca fica preso
        orig(self, n)

    monkeypatch.setattr(tk.Tk, "mainloop", mainloop)


def test_pair_window_success_and_error(fake_server, monkeypatch):
    from einstein_agent.config import AgentConfig
    from einstein_agent.pair_window import run_pair_window

    url = f"http://127.0.0.1:{fake_server.server_port}"
    seen = {}

    def steps(root):
        entries = [w for w in walk(root) if isinstance(w, tk.Entry)]
        button = next(w for w in walk(root) if isinstance(w, tk.Button) and w["text"] == "Vincular")
        status = [w for w in walk(root) if isinstance(w, tk.Label)]
        server, code = entries[0], entries[1]
        server.delete(0, "end"); server.insert(0, url)  # noqa: E702
        code.insert(0, "ZZZZ-ZZZZ")  # primeiro um código errado
        button.invoke()

        def after_error():
            seen["error"] = next((s["text"] for s in status if "recusou" in s["text"]), None)
            seen["button_state_after_error"] = str(button["state"])
            code.delete(0, "end"); code.insert(0, "abcd efgh")  # noqa: E702  (formato livre é aceito)
            button.invoke()

        root.after(1500, after_error)

    drive_window(monkeypatch, steps)
    assert run_pair_window() is True
    assert seen["error"] and "Código inválido" in seen["error"]
    assert seen["button_state_after_error"] == "normal"
    cfg = AgentConfig.load()
    assert cfg.paired and cfg.device_name == "PC Gráfico" and cfg.secret() == "s" * 64
    assert [r["code"] for r in fake_server.requests] == ["ZZZZ-ZZZZ", "abcd efgh"]


def test_pair_window_cancel(monkeypatch):
    from einstein_agent.config import AgentConfig
    from einstein_agent.pair_window import run_pair_window

    def steps(root):
        next(w for w in walk(root) if isinstance(w, tk.Button) and w["text"] == "Cancelar").invoke()

    drive_window(monkeypatch, steps)
    assert run_pair_window() is False
    assert not AgentConfig.load().paired
