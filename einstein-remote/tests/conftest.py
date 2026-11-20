import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

STRONG = "Einstein#2026!"
XHR = {"X-Requested-With": "EinsteinRemote"}


def make_settings(tmp_path, **kw):
    web = tmp_path / "web"
    web.mkdir(exist_ok=True)
    (web / "index.html").write_text("<html>ok</html>")
    return Settings(data_dir=tmp_path, db_path=tmp_path / "t.db", secret_key="x" * 48, web_dir=web, **kw)


@pytest.fixture
def client(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def owner(client):
    r = client.post("/api/auth/setup", json={"username": "kaio", "password": STRONG}, headers=XHR)
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}", **XHR}, token


@pytest.fixture(autouse=True)
def isolated_agent_dirs(tmp_path, monkeypatch):
    """Config e log do agente sempre em pastas temporárias (nunca no perfil real)."""
    monkeypatch.setenv("EINSTEIN_AGENT_DIR", str(tmp_path / "agent"))
    monkeypatch.setenv("EINSTEIN_AGENT_LOG_DIR", str(tmp_path / "agent-logs"))
