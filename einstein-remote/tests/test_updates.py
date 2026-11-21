"""Atualização automática: servidor (verificação, pedido, pacote do agente) e agente (download, SHA-256, extração)."""
import asyncio
import hashlib
import http.server
import json
import threading
import zipfile
from pathlib import Path

import pytest

from app import updates
from conftest import XHR


def test_versions():
    assert updates.newer("1.2.0", "1.1.9") and updates.newer("2.0.0", "1.10.10")
    assert not updates.newer("1.1.0", "1.1.0") and not updates.newer("lixo", "1.0.0")


@pytest.fixture
def latest(monkeypatch):
    monkeypatch.setenv("EINSTEIN_UPDATE_CHECK", "1")
    monkeypatch.setattr(updates.Updates, "fetch_latest", lambda self: "9.9.9")


def test_status_and_start_rules(client, owner, latest, monkeypatch, tmp_path):
    headers, _ = owner
    s = client.get("/api/update/status?refresh=1", headers=headers).json()
    assert s["latest"] == "9.9.9" and s["available"] is True and s["supported"] is False
    # sem o serviço do Windows (Docker/manual): explica como atualizar
    r = client.post("/api/update/start", json={"confirm": True}, headers=headers)
    assert r.status_code == 409 and "docker compose" in r.json()["detail"]

    monkeypatch.setenv("EINSTEIN_SELF_UPDATE", "1")
    assert client.post("/api/update/start", json={"confirm": False}, headers=headers).status_code == 400
    assert client.post("/api/update/start", json={"confirm": True},
                       headers={"Authorization": headers["Authorization"]}).status_code == 403  # sem X-Requested-With
    exits = []

    class FakeTimer:
        def __init__(self, delay, fn, args=()):
            exits.append((delay, fn, args))

        def start(self):
            pass

    monkeypatch.setattr(updates.threading, "Timer", FakeTimer)
    r = client.post("/api/update/start", json={"confirm": True}, headers=headers)
    assert r.status_code == 200 and r.json()["to"] == "9.9.9"
    req = json.loads((tmp_path / "update" / "requested").read_text())
    assert req["by"] == "kaio" and exits and exits[0][2] == (0,)  # servidor se encerra para o serviço atualizar
    assert any(a["action"] == "server.update_requested" for a in client.get("/api/audit", headers=headers).json())


def test_only_owner_can_update(client, owner, latest, monkeypatch):
    headers, _ = owner
    monkeypatch.setenv("EINSTEIN_SELF_UPDATE", "1")
    client.post("/api/users", headers=headers, json={"username": "admin2", "password": "Admin#Senha2026x", "role": "admin"})
    tok = client.post("/api/auth/login", json={"username": "admin2", "password": "Admin#Senha2026x"}, headers=XHR).json()["access_token"]
    adm = {"Authorization": f"Bearer {tok}", **XHR}
    assert client.get("/api/update/status", headers=adm).status_code == 200
    assert client.post("/api/update/start", json={"confirm": True}, headers=adm).status_code == 403


def _package(data_dir: Path, version="9.9.9") -> tuple[Path, str]:
    d = data_dir / "update" / "agent"
    d.mkdir(parents=True, exist_ok=True)
    z = d / f"EinsteinRemoteAgent-{version}.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("EinsteinRemoteAgent.exe", b"MZ fake")
    sha = hashlib.sha256(z.read_bytes()).hexdigest()
    (d / "package.json").write_text(json.dumps({"version": version, "file": z.name, "size": z.stat().st_size, "sha256": sha}))
    return z, sha


def test_package_single_use_token(client, owner, tmp_path):
    z, _ = _package(tmp_path)
    upd = client.app.state.hub.updates
    token = upd.issue_token()
    r = client.get(f"/api/agent/package?token={token}")
    assert r.status_code == 200 and r.content == z.read_bytes()
    assert client.get(f"/api/agent/package?token={token}").status_code == 403  # uso único
    assert client.get("/api/agent/package?token=inventado").status_code == 403
    # package.json apontando para fora da pasta é ignorado
    (tmp_path / "update" / "agent" / "package.json").write_text(json.dumps({"version": "9.9.9", "file": "../../t.db", "sha256": "x"}))
    assert upd.package() is None


def test_offer_agent_update_once(client, owner, tmp_path):
    _package(tmp_path)
    hub = client.app.state.hub
    calls = []

    async def fake_call(device_id, action, params, timeout=30):
        calls.append((action, params))
        raise RuntimeError("Agente rodando pelo Python")

    hub.server_call = fake_call

    class Conn:
        device_id, name = "dev1", "PC"

    async def scenario():
        orig = asyncio.sleep
        updates.asyncio.sleep = lambda s: orig(0)
        try:
            await updates.offer_agent_update(hub, Conn, "1.0.0")
            await updates.offer_agent_update(hub, Conn, "1.0.0")  # não insiste depois de uma recusa
            await updates.offer_agent_update(hub, Conn, "9.9.9")  # já atualizado
        finally:
            updates.asyncio.sleep = orig

    asyncio.run(scenario())
    assert len(calls) == 1 and calls[0][0] == "agent.update"
    p = calls[0][1]
    assert p["version"] == "9.9.9" and p["url"].startswith("/api/agent/package?token=") and len(p["sha256"]) == 64


# ------------------------------------------------------------------ agente
@pytest.fixture
def package_server(tmp_path):
    z = tmp_path / "pkg.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("EinsteinRemoteAgent.exe", b"MZ novo")
        f.writestr("_internal/base_library.zip", b"x" * 1000)
    body = z.read_bytes()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", body
    srv.shutdown()


def test_agent_download_verifies_sha(package_server, tmp_path):
    from einstein_agent import updater
    from einstein_agent.config import AgentConfig

    base, body = package_server
    cfg = AgentConfig(server_url=base, allow_insecure=True)
    good = hashlib.sha256(body).hexdigest()
    dest = tmp_path / "a.zip"
    updater.download(cfg, "/api/agent/package?token=t", good, len(body), dest)
    root = updater.extract(dest, tmp_path / "files")
    assert (root / "EinsteinRemoteAgent.exe").read_bytes() == b"MZ novo"
    with pytest.raises(updater.UpdateRefused, match="SHA-256"):
        updater.download(cfg, "/api/agent/package?token=t", "0" * 64, len(body), tmp_path / "b.zip")
    with pytest.raises(updater.UpdateRefused, match="incompleto"):
        updater.download(cfg, "/api/agent/package?token=t", good, len(body) + 1, tmp_path / "c.zip")
    with pytest.raises(updater.UpdateRefused, match="inválido"):
        updater.download(cfg, "https://outro-site/malware.zip", good, None, tmp_path / "d.zip")


def test_agent_extract_blocks_zip_slip(tmp_path):
    from einstein_agent import updater

    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("EinsteinRemoteAgent.exe", b"MZ")
        f.writestr("../../fora.txt", b"x")
    with pytest.raises(updater.UpdateRefused, match="inválidos"):
        updater.extract(z, tmp_path / "files")
    assert not (tmp_path / "fora.txt").exists()


def test_agent_refuses_outside_installed_windows():
    from einstein_agent import updater
    from einstein_agent.config import AgentConfig

    with pytest.raises(updater.UpdateRefused):
        updater.prepare({"version": "9.9.9"}, AgentConfig(server_url="http://x"))


def test_offer_does_not_loop_when_update_not_applied(client, owner, tmp_path):
    """Regressão (uso real): o agente aceitava, fechava, voltava na versão antiga e recebia o pedido de novo — em ciclo,
    deixando o PC offline. Agora: um pedido por versão; se voltar antigo, avisa e não insiste."""
    _package(tmp_path)
    hub = client.app.state.hub
    calls = []

    async def ok_call(device_id, action, params, timeout=30):
        calls.append(action)
        return {"version": params["version"], "restarting": True}

    hub.server_call = ok_call

    class Conn:
        device_id, name = "dev2", "PC"

    async def scenario():
        orig = asyncio.sleep
        updates.asyncio.sleep = lambda s: orig(0)
        try:
            for _ in range(4):  # reconecta várias vezes ainda na versão antiga
                await updates.offer_agent_update(hub, Conn, "1.2.0")
        finally:
            updates.asyncio.sleep = orig

    asyncio.run(scenario())
    assert calls == ["agent.update"]
    notes = [n["message"] for n in client.get("/api/notifications", headers=owner[0]).json() if n["kind"] == "update"]
    assert any("não foi aplicada" in n for n in notes)


def test_helper_script_has_no_drive_qualified_variables():
    """Regressão (CI Windows): '"$i: ..."' é lido pelo PowerShell como variável de unidade e o script inteiro
    não roda. Variáveis seguidas de ':' precisam de ${...}."""
    import re

    from einstein_agent import updater

    bad = [m.group(0) for m in re.finditer(r"\$(?!env:|\{)[A-Za-z_]\w*:", updater.HELPER)]
    assert not bad, bad
