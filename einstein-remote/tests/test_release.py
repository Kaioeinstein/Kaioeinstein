"""Versão final: backup/restauração, versões coerentes e arquivos de produção/instalador."""
import re
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from conftest import STRONG, XHR, make_settings

ROOT = Path(__file__).resolve().parents[1]


def test_backup_while_running_and_restore(client, owner, tmp_path, monkeypatch):
    headers, token = owner
    code = client.post("/api/pairing-codes", json={"device_name": "PC"}, headers=headers).json()["code"]
    assert client.post("/api/agent/pair", json={"code": code, "hostname": "pc"}).status_code == 200
    (tmp_path / "secret.key").write_text("x" * 48)  # a chave VAPID real já foi criada pelo servidor

    from app import admin

    monkeypatch.setenv("EINSTEIN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EINSTEIN_DB_PATH", str(tmp_path / "t.db"))
    assert admin.main(["backup", str(tmp_path / "bk")]) == 0  # servidor (client) continua aberto e gravando
    out = next((tmp_path / "bk").iterdir())
    assert {p.name for p in out.iterdir()} == {"einstein.db", "secret.key", "vapid_private.pem"}
    assert sqlite3.connect(out / "einstein.db").execute("pragma integrity_check").fetchone()[0] == "ok"

    # restauração: nova máquina com os arquivos do backup → mesma conta, mesmo dispositivo, mesmo token válido
    new = tmp_path / "restaurado"
    new.mkdir()
    for f in out.iterdir():
        (new / ("t.db" if f.name == "einstein.db" else f.name)).write_bytes(f.read_bytes())
    with TestClient(create_app(make_settings(new))) as c2:
        assert c2.get("/api/auth/me", headers=headers).status_code == 200
        assert [d["name"] for d in c2.get("/api/devices", headers=headers).json()] == ["PC"]
        r = c2.post("/api/auth/login", json={"username": "kaio", "password": STRONG}, headers=XHR)
        assert r.status_code == 200


def test_versions_are_consistent():
    from app import __version__ as server_v
    from einstein_agent import __version__ as agent_v

    pyproject = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "agent" / "pyproject.toml").read_text(), re.M).group(1)
    compose_tag = re.search(r"image:\s*einstein-remote:(\S+)", (ROOT / "docker-compose.yml").read_text()).group(1)
    assert server_v == agent_v == pyproject == compose_tag
    assert re.fullmatch(r"\d+\.\d+\.\d+", server_v)
    assert f"## [{server_v}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_installer_script_respects_owner_consent():
    iss = (ROOT / "installer" / "EinsteinRemoteAgent.iss").read_text(encoding="utf-8-sig")
    assert "PrivilegesRequired=lowest" in iss  # sem administrador
    # início automático: opção visível, desmarcada por padrão e removida na desinstalação
    assert re.search(r'Name: "autostart";.*Flags: unchecked', iss)
    assert re.search(r"RegDeleteValue\(HKEY_CURRENT_USER, '\{#RunKey\}', '\{#RunValue\}'\)", iss)
    assert "InfoBeforeFile=LEIA-ANTES.txt" in iss and (ROOT / "installer" / "LEIA-ANTES.txt").exists()
    # mesmo nome de valor que o agente usa no menu "Iniciar com o Windows"
    from einstein_agent import autostart

    assert f'#define RunValue "{autostart.VALUE_NAME}"' in iss and f'#define TrayExe "{autostart.TRAY_EXE}"' in iss
    assert re.search(r"AppId=\{\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}", iss)


def test_production_files():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "USER einstein" in dockerfile and "--proxy-headers" in dockerfile and "HEALTHCHECK" in dockerfile
    compose = (ROOT / "docker-compose.yml").read_text()
    assert 'EINSTEIN_COOKIE_SECURE: "1"' in compose
    assert '"8000:8000"' not in compose  # servidor só acessível pelo proxy HTTPS
    unit = (ROOT / "deploy" / "einstein-remote.service").read_text()
    assert "--forwarded-allow-ips 127.0.0.1" in unit and "--host 127.0.0.1" in unit and "NoNewPrivileges=yes" in unit
    ignore = (ROOT / ".dockerignore").read_text().split()
    assert "data/" in ignore and "*.db" in ignore  # nunca copia banco/chaves locais para a imagem


def test_proxy_headers_give_real_client_ip(tmp_path):
    """Com o uvicorn em --proxy-headers, o IP vem do X-Forwarded-For: o limitador de tentativas usa o visitante."""
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app = create_app(make_settings(tmp_path))
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts="*")
    with TestClient(wrapped) as c:
        c.post("/api/auth/setup", json={"username": "kaio", "password": STRONG}, headers=XHR)
        for i in range(5):  # usuários diferentes: só o bloqueio por IP entra em ação
            c.post("/api/auth/login", json={"username": f"visitante{i}", "password": "errada-123456"},
                   headers={**XHR, "X-Forwarded-For": "203.0.113.7"})
        blocked = c.post("/api/auth/login", json={"username": "kaio", "password": STRONG}, headers={**XHR, "X-Forwarded-For": "203.0.113.7"})
        other = c.post("/api/auth/login", json={"username": "kaio", "password": STRONG}, headers={**XHR, "X-Forwarded-For": "198.51.100.9"})
        assert blocked.status_code == 429 and other.status_code == 200
