"""Fase 7: arquivos — pastas liberadas, segurança de caminhos e transferências reais com SHA-256."""
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import XHR
from test_agent import live_server, wait_for  # noqa: F401

WINDOWS = sys.platform == "win32"


@pytest.fixture
def roots(tmp_path):
    from einstein_agent import files

    rw = tmp_path / "Documentos"
    ro = tmp_path / "Fotos"
    (rw / "sub").mkdir(parents=True)
    ro.mkdir()
    (rw / "sub" / "relatorio.bin").write_bytes(os.urandom(1_500_000 + 123))
    (ro / "foto.jpg").write_bytes(b"jpeg" * 1000)
    files.add_root(str(rw), write=True)
    files.add_root(str(ro), write=False)
    return rw, ro


# ------------------------------------------------------------------ segurança de caminhos
@pytest.mark.parametrize("bad", ["../x", "sub/../../x", "/etc", "C:\\Windows", "\\\\srv\\share", "a:stream", "CON", "nul.txt",
                                  "pasta/ponto.", "x\x00y", "a*b"])
def test_rejects_dangerous_paths(roots, bad):
    from einstein_agent import files

    with pytest.raises(files.FileError):
        files.resolve(0, bad)


def test_root_rules(tmp_path, roots):
    from einstein_agent import files

    with pytest.raises(files.FileError, match="raiz"):
        files.add_root(os.path.abspath(os.sep), write=False)
    with pytest.raises(files.FileError, match="usuário inteira"):
        files.add_root(str(Path.home()), write=False)
    with pytest.raises(files.FileError, match="não existe"):
        files.add_root(str(tmp_path / "nada"), write=False)
    with pytest.raises(files.FileError, match="não está"):
        files.resolve(7, "")
    r = files.list_roots()["roots"]
    assert [x["write"] for x in r] == [True, False] and r[0]["name"] == "Documentos"
    with pytest.raises(files.FileError, match="somente para leitura"):
        files.mkdir(1, "", "nova")
    assert files.mkdir(0, "sub", "nova")["path"] == "sub/nova"
    assert files.remove_root(1) and len(files.list_roots()["roots"]) == 1


@pytest.mark.skipif(WINDOWS, reason="links simbólicos exigem privilégio no Windows")
def test_symlink_escape_blocked(tmp_path, roots):
    from einstein_agent import files

    rw, _ = roots
    secret = tmp_path / "segredo"
    secret.mkdir()
    (secret / "senhas.txt").write_text("x")
    os.symlink(secret, rw / "atalho")
    names = [e["name"] for e in files.list_dir(0, "")["entries"]]
    assert "atalho" not in names and "sub" in names  # link para fora nem aparece
    with pytest.raises(files.FileError, match="sai da pasta"):
        files.resolve(0, "atalho/senhas.txt")


def test_list_dir(roots):
    from einstein_agent import files

    d = files.list_dir(0, "sub")
    assert d["entries"][0]["name"] == "relatorio.bin" and d["entries"][0]["size"] == 1_500_123 and d["write"] is True


# ------------------------------------------------------------------ ponta a ponta com agente real
def _ws_call(base, token, device_id, action, params):
    from websockets.asyncio.client import connect

    async def run():
        async with connect(base.replace("http", "ws") + "/ws/client") as ws:
            await ws.send(json.dumps({"type": "auth", "token": token}))
            await ws.send(json.dumps({"type": "command", "request_id": "r", "device_id": device_id, "action": action, "params": params}))
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), 15))
                if m["type"] == "result":
                    return m

    return asyncio.run(run())


def test_end_to_end_transfers(live_server, roots, tmp_path):  # noqa: F811
    from einstein_agent.pairing import pair
    from einstein_agent.runner import AgentRunner

    rw, ro = roots
    base, http = live_server
    token = http.headers["Authorization"][7:]
    cfg = pair(base, http.post("/api/pairing-codes", json={"device_name": "PC A"}).json()["code"])
    runner = AgentRunner()
    runner.start()
    try:
        assert wait_for(lambda: http.get("/api/devices").json()[0]["online"])
        dev = cfg.device_id

        listing = _ws_call(base, token, dev, "files.list", {"root": 0, "path": "sub"})
        assert listing["ok"] and listing["data"]["entries"][0]["name"] == "relatorio.bin"
        assert not _ws_call(base, token, dev, "files.list", {"root": 0, "path": "../.."})["ok"]
        # o cliente não consegue abrir transferência diretamente (só o servidor)
        assert _ws_call(base, token, dev, "files.download", {"root": 0, "path": "sub/relatorio.bin", "_tid": "x"})["error"] == "Ação não permitida"

        # download: link de uso único, conteúdo idêntico
        original = (rw / "sub" / "relatorio.bin").read_bytes()
        r = http.post(f"/api/devices/{dev}/files/download", json={"root": 0, "path": "sub/relatorio.bin"})
        assert r.status_code == 200, r.text
        url = r.json()["url"]
        got = http.get(url, timeout=30)
        assert got.status_code == 200 and got.content == original
        assert "relatorio.bin" in got.headers["content-disposition"]
        assert http.get(url).status_code == 404  # uso único
        assert http.post(f"/api/devices/{dev}/files/download", json={"root": 0, "path": "../segredo"}).status_code == 409

        # upload com SHA do navegador
        data = os.urandom(3 * 1024 * 1024 + 7)
        sha = hashlib.sha256(data).hexdigest()
        up = http.post(f"/api/devices/{dev}/files/upload", params={"root": 0, "path": "sub", "name": "envio.bin"},
                       content=data, headers={"X-Content-SHA256": sha}, timeout=60)
        assert up.status_code == 200, up.text
        assert up.json()["verified"] and up.json()["sha256"] == sha
        assert (rw / "sub" / "envio.bin").read_bytes() == data
        # mesmo nome: não sobrescreve
        up2 = http.post(f"/api/devices/{dev}/files/upload", params={"root": 0, "path": "sub", "name": "envio.bin"},
                        content=b"novo", timeout=30)
        assert up2.json()["name"] == "envio (1).bin" and (rw / "sub" / "envio.bin").read_bytes() == data
        # SHA errado: descartado, sem sobra de temporário
        bad = http.post(f"/api/devices/{dev}/files/upload", params={"root": 0, "path": "", "name": "ruim.bin"},
                        content=b"abc" * 1000, headers={"X-Content-SHA256": "0" * 64}, timeout=30)
        assert bad.status_code == 409
        assert wait_for(lambda: not any(".einstein-part-" in p.name for p in rw.rglob("*")), 5)
        assert not (rw / "ruim.bin").exists()
        # pasta somente leitura
        ro_up = http.post(f"/api/devices/{dev}/files/upload", params={"root": 1, "path": "", "name": "x.txt"}, content=b"x")
        assert ro_up.status_code == 409 and "somente para leitura" in ro_up.json()["detail"]

        actions = [a["action"] for a in http.get("/api/audit").json()]
        assert {"file.download", "file.upload", "file.upload_failed"} <= set(actions)
    finally:
        runner.stop()


def test_copy_between_two_real_agents(live_server, roots, tmp_path):  # noqa: F811
    """PC A (agente nesta thread) → PC B (agente em outro processo, outra pasta de config)."""
    from einstein_agent.pairing import pair
    from einstein_agent.runner import AgentRunner

    rw, _ = roots
    base, http = live_server
    cfg_a = pair(base, http.post("/api/pairing-codes", json={"device_name": "PC A"}).json()["code"])

    b_dir, b_root = tmp_path / "agentB", tmp_path / "DestinoB"
    b_root.mkdir()
    env_b = dict(os.environ, EINSTEIN_AGENT_DIR=str(b_dir), EINSTEIN_AGENT_LOG_DIR=str(b_dir / "logs"),
                 PYTHONPATH=os.pathsep.join([str(Path(__file__).parents[1] / "agent"), os.environ.get("PYTHONPATH", "")]))
    code_b = http.post("/api/pairing-codes", json={"device_name": "PC B"}).json()["code"]
    run = lambda *a: subprocess.run([sys.executable, "-m", "einstein_agent", *a], env=env_b, capture_output=True, text=True)  # noqa: E731
    assert run("pair", "--server", base, "--code", code_b).returncode == 0
    assert run("folders", "add", str(b_root), "--write").returncode == 0
    proc_b = subprocess.Popen([sys.executable, "-m", "einstein_agent", "run"], env=env_b,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    runner = AgentRunner()
    runner.start()
    try:
        assert wait_for(lambda: sum(d["online"] for d in http.get("/api/devices").json()) == 2, 20)
        dev_b = next(d["id"] for d in http.get("/api/devices").json() if d["name"] == "PC B")
        r = http.post("/api/files/copy", json={"from_device": cfg_a.device_id, "from_root": 0, "from_path": "sub/relatorio.bin",
                                               "to_device": dev_b, "to_root": 0, "to_path": ""}, timeout=60)
        assert r.status_code == 200, r.text
        assert r.json()["verified"] is True
        assert (b_root / "relatorio.bin").read_bytes() == (rw / "sub" / "relatorio.bin").read_bytes()
        assert "file.copy" in [a["action"] for a in http.get("/api/audit").json()]
    finally:
        runner.stop()
        proc_b.terminate()
        proc_b.wait(10)


def test_viewer_cannot_transfer(client, owner):
    from test_api import pair_device

    headers, _ = owner
    creds, _ = pair_device(client, headers)
    client.post("/api/users", headers=headers, json={"username": "obs", "password": "Obs#Senha2026", "role": "viewer"})
    t = client.post("/api/auth/login", json={"username": "obs", "password": "Obs#Senha2026"}, headers=XHR).json()["access_token"]
    vh = {"Authorization": f"Bearer {t}", **XHR}
    assert client.post(f"/api/devices/{creds['device_id']}/files/download", headers=vh, json={"root": 0, "path": "x"}).status_code == 403
    assert client.post(f"/api/devices/{creds['device_id']}/files/upload?root=0&name=x", headers=vh, content=b"x").status_code == 403
    assert client.get("/api/transfers/qualquer").status_code == 404
