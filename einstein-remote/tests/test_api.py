from conftest import STRONG, XHR


def pair_device(client, headers, name="PC Teste"):
    code = client.post("/api/pairing-codes", json={"device_name": name}, headers=headers).json()["code"]
    r = client.post("/api/agent/pair", json={"code": code, "hostname": "host1", "os": "Windows 11"})
    assert r.status_code == 200, r.text
    return r.json(), code


def test_setup_only_once(client):
    assert client.get("/api/auth/status").json() == {"setup_required": True}
    assert client.post("/api/auth/setup", json={"username": "kaio", "password": "fraca"}, headers=XHR).status_code == 422
    assert client.post("/api/auth/setup", json={"username": "kaio", "password": STRONG}, headers=XHR).status_code == 200
    assert client.post("/api/auth/setup", json={"username": "outro", "password": STRONG}, headers=XHR).status_code == 409
    assert client.get("/api/auth/status").json() == {"setup_required": False}


def test_requires_xhr_header(client):
    assert client.post("/api/auth/setup", json={"username": "kaio", "password": STRONG}).status_code == 400


def test_protected_routes_require_auth(client, owner):
    for path in ("/api/devices", "/api/notifications", "/api/audit", "/api/sessions", "/api/auth/me"):
        assert client.get(path).status_code == 401, path
    assert client.get("/api/devices", headers={"Authorization": "Bearer lixo"}).status_code == 401


def test_login_lockout_and_audit(client, owner):
    headers, _ = owner
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "kaio", "password": "errada123"}, headers=XHR).status_code == 401
    # mesmo com a senha correta, fica bloqueado
    assert client.post("/api/auth/login", json={"username": "kaio", "password": STRONG}, headers=XHR).status_code == 429
    actions = [a["action"] for a in client.get("/api/audit", headers=headers).json()]
    assert actions.count("auth.login_failed") == 5 and "auth.login_blocked" in actions
    kinds = [n["kind"] for n in client.get("/api/notifications", headers=headers).json()]
    assert "unauthorized" in kinds


def test_refresh_rotation_and_logout(client, owner):
    headers, _ = owner
    old_cookie = client.cookies.get("er_refresh")
    r = client.post("/api/auth/refresh", headers=XHR)
    assert r.status_code == 200
    new_cookie = client.cookies.get("er_refresh")
    assert new_cookie and new_cookie != old_cookie
    # refresh antigo não vale mais
    client.cookies.set("er_refresh", old_cookie, path="/api/auth")
    assert client.post("/api/auth/refresh", headers=XHR).status_code == 401
    client.cookies.set("er_refresh", new_cookie, path="/api/auth")
    new_headers = {"Authorization": f"Bearer {r.json()['access_token']}", **XHR}
    assert client.post("/api/auth/logout", headers=new_headers).status_code == 200
    # access token da sessão revogada deixa de valer imediatamente
    assert client.get("/api/auth/me", headers=new_headers).status_code == 401


def test_session_revocation(client, owner):
    headers, _ = owner
    r = client.post("/api/auth/login", json={"username": "kaio", "password": STRONG}, headers=XHR)
    other = {"Authorization": f"Bearer {r.json()['access_token']}", **XHR}
    sessions = client.get("/api/sessions", headers=headers).json()
    assert len(sessions) == 2
    target = next(s for s in sessions if not s["current"])
    assert client.delete(f"/api/sessions/{target['id']}", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=other).status_code == 401


def test_pairing_code_single_use(client, owner):
    headers, _ = owner
    creds, code = pair_device(client, headers)
    assert creds["device_id"] and len(creds["device_secret"]) > 40
    assert client.post("/api/agent/pair", json={"code": code}).status_code == 401
    devices = client.get("/api/devices", headers=headers).json()
    assert len(devices) == 1 and devices[0]["online"] is False and "secret_hash" not in devices[0]


def test_agent_ws_auth_and_command_flow(client, owner):
    headers, token = owner
    creds, _ = pair_device(client, headers)
    with client.websocket_connect("/ws/agent") as bad:
        bad.send_json({"type": "auth", "device_id": creds["device_id"], "secret": "errado"})
        assert bad.receive_json()["type"] == "auth_error"

    with client.websocket_connect("/ws/client") as ui:
        ui.send_json({"type": "auth", "token": token})
        hello = ui.receive_json()
        assert hello["type"] == "hello" and hello["devices"][0]["online"] is False

        with client.websocket_connect("/ws/agent") as agent:
            agent.send_json({"type": "auth", "device_id": creds["device_id"], "secret": creds["device_secret"], "info": {"hostname": "host1"}})
            assert agent.receive_json()["type"] == "auth_ok"
            assert ui.receive_json()["type"] == "device"
            assert ui.receive_json()["data"]["kind"] == "device_online"

            agent.send_json({"type": "metrics", "data": {"ts": 1, "cpu": {"percent": 12.5}, "memory": {"percent": 40}, "disks": [], "network": {"down_bps": 10, "up_bps": 5}}})
            m = ui.receive_json()
            assert m["type"] == "metrics" and m["summary"]["cpu"] == 12.5 and m["summary"]["net_down"] == 10

            # ação fora da allowlist é recusada pelo servidor
            ui.send_json({"type": "command", "request_id": "r1", "device_id": creds["device_id"], "action": "shell.exec"})
            r = ui.receive_json()
            assert r["ok"] is False and r["request_id"] == "r1"

            ui.send_json({"type": "command", "request_id": "r2", "device_id": creds["device_id"], "action": "ping"})
            ack = ui.receive_json()
            assert ack["type"] == "ack"
            cmd = agent.receive_json()
            assert cmd["type"] == "command" and cmd["action"] == "ping" and cmd["id"] == ack["id"]
            agent.send_json({"type": "result", "id": cmd["id"], "ok": True, "data": {"pong": 1}})
            res = ui.receive_json()
            assert res == {"type": "result", "id": ack["id"], "ok": True, "data": {"pong": 1}, "error": None}

    actions = [a["action"] for a in client.get("/api/audit", headers=headers).json()]
    for a in ("agent.auth_failed", "agent.connected", "command.rejected", "command.sent", "command.result"):
        assert a in actions, a


def test_device_revocation(client, owner):
    headers, _ = owner
    creds, _ = pair_device(client, headers)
    assert client.delete(f"/api/devices/{creds['device_id']}", headers=headers).status_code == 200
    assert client.get("/api/devices", headers=headers).json() == []
    with client.websocket_connect("/ws/agent") as agent:
        agent.send_json({"type": "auth", "device_id": creds["device_id"], "secret": creds["device_secret"]})
        assert agent.receive_json()["type"] == "auth_error"


def test_security_headers_and_spa(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert client.get("/api/nao-existe").status_code == 404
