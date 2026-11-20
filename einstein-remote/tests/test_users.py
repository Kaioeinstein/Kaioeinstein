"""Fase 2: usuários, permissões por dispositivo, configurações de alertas, troca de senha."""
import time

from conftest import STRONG, XHR
from test_api import pair_device

PW2 = "Outra#Senha2026"


def login(client, username, password=PW2):
    r = client.post("/api/auth/login", json={"username": username, "password": password}, headers=XHR)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}", **XHR}, r.json()["access_token"]


def make_user(client, headers, username, role="viewer", all_devices=True, device_ids=()):
    r = client.post("/api/users", headers=headers, json={
        "username": username, "password": PW2, "role": role, "all_devices": all_devices, "device_ids": list(device_ids)})
    assert r.status_code == 201, r.text
    return r.json()


def test_only_owner_manages_users(client, owner):
    headers, _ = owner
    make_user(client, headers, "ana", role="admin")
    ana, _ = login(client, "ana")
    assert client.get("/api/users", headers=ana).status_code == 403
    assert client.post("/api/users", headers=ana, json={"username": "x1x", "password": PW2, "role": "viewer"}).status_code == 403
    users = client.get("/api/users", headers=headers).json()
    assert [u["username"] for u in users] == ["kaio", "ana"]
    assert "password_hash" not in users[0]


def test_user_validation(client, owner):
    headers, _ = owner
    assert client.post("/api/users", headers=headers, json={"username": "bia", "password": "fraca", "role": "viewer"}).status_code == 422
    assert client.post("/api/users", headers=headers, json={"username": "bia", "password": PW2, "role": "owner"}).status_code == 422
    make_user(client, headers, "bia")
    assert client.post("/api/users", headers=headers, json={"username": "BIA", "password": PW2, "role": "viewer"}).status_code == 409
    assert client.post("/api/users", headers=headers, json={"username": "caio", "password": PW2, "role": "viewer",
                                                            "all_devices": False, "device_ids": ["naoexiste"]}).status_code == 422


def test_owner_is_protected(client, owner):
    headers, _ = owner
    owner_id = client.get("/api/users", headers=headers).json()[0]["id"]
    assert client.patch(f"/api/users/{owner_id}", headers=headers, json={"role": "viewer"}).status_code == 403
    assert client.delete(f"/api/users/{owner_id}", headers=headers).status_code == 403


def test_device_scoped_access(client, owner):
    headers, owner_token = owner
    a, _ = pair_device(client, headers, "PC A")
    b, _ = pair_device(client, headers, "PC B")
    make_user(client, headers, "visit", all_devices=False, device_ids=[a["device_id"]])
    vh, vtoken = login(client, "visit")

    assert [d["name"] for d in client.get("/api/devices", headers=vh).json()] == ["PC A"]
    assert client.get(f"/api/devices/{b['device_id']}/history", headers=vh).status_code == 404
    assert client.get(f"/api/devices/{a['device_id']}/history", headers=vh).status_code == 200
    # viewer não gerencia dispositivos nem vê auditoria
    assert client.delete(f"/api/devices/{a['device_id']}", headers=vh).status_code == 403
    assert client.get("/api/audit", headers=vh).status_code == 403

    with client.websocket_connect("/ws/client") as ui:
        ui.send_json({"type": "auth", "token": vtoken})
        hello = ui.receive_json()
        assert [d["name"] for d in hello["devices"]] == ["PC A"]

        # agente B conecta e envia métricas: o visitante NÃO pode receber nada de B
        with client.websocket_connect("/ws/agent") as agent_b:
            agent_b.send_json({"type": "auth", "device_id": b["device_id"], "secret": b["device_secret"]})
            assert agent_b.receive_json()["type"] == "auth_ok"
            agent_b.send_json({"type": "metrics", "data": {"ts": 1, "cpu": {"percent": 1}, "memory": {"percent": 1}}})
            # comando para B é recusado
            ui.send_json({"type": "command", "request_id": "q", "device_id": b["device_id"], "action": "ping"})
            r = ui.receive_json()
            assert r["type"] == "result" and r["error"] == "Sem acesso a este dispositivo"

        # agente A conecta: o visitante recebe
        with client.websocket_connect("/ws/agent") as agent_a:
            agent_a.send_json({"type": "auth", "device_id": a["device_id"], "secret": a["device_secret"]})
            agent_a.receive_json()
            msg = ui.receive_json()
            assert msg["type"] == "device" and msg["data"]["id"] == a["device_id"]

    # notificações: só as do PC A, nenhuma global de segurança
    notes = client.get("/api/notifications", headers=vh).json()
    assert notes and all(n["device_id"] == a["device_id"] for n in notes)


def test_permission_change_disconnects_user(client, owner):
    headers, _ = owner
    a, _ = pair_device(client, headers, "PC A")
    u = make_user(client, headers, "rui", all_devices=True)
    _, token = login(client, "rui")
    with client.websocket_connect("/ws/client") as ui:
        ui.send_json({"type": "auth", "token": token})
        assert len(ui.receive_json()["devices"]) == 1
        r = client.patch(f"/api/users/{u['id']}", headers=headers, json={"all_devices": False, "device_ids": []})
        assert r.status_code == 200 and r.json()["all_devices"] == 0
        try:
            ui.receive_json()
            closed = False
        except Exception:
            closed = True
        assert closed
    # nova conexão já respeita a permissão
    with client.websocket_connect("/ws/client") as ui:
        ui.send_json({"type": "auth", "token": token})
        assert ui.receive_json()["devices"] == []


def test_password_reset_and_delete_revoke_sessions(client, owner):
    headers, _ = owner
    u = make_user(client, headers, "leo", role="admin")
    lh, _ = login(client, "leo")
    assert client.patch(f"/api/users/{u['id']}", headers=headers, json={"new_password": "Nova#Senha2026"}).status_code == 200
    assert client.get("/api/auth/me", headers=lh).status_code == 401
    lh, _ = login(client, "leo", "Nova#Senha2026")
    # dispositivo vinculado por admin restrito -> ganha acesso; ao remover o admin, passa ao proprietário
    client.patch(f"/api/users/{u['id']}", headers=headers, json={"all_devices": False})
    lh, _ = login(client, "leo", "Nova#Senha2026")
    code = client.post("/api/pairing-codes", json={"device_name": "PC Leo"}, headers=lh).json()["code"]
    client.post("/api/agent/pair", json={"code": code})
    assert [d["name"] for d in client.get("/api/devices", headers=lh).json()] == ["PC Leo"]
    assert client.delete(f"/api/users/{u['id']}", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=lh).status_code == 401
    assert [d["name"] for d in client.get("/api/devices", headers=headers).json()] == ["PC Leo"]
    actions = [a["action"] for a in client.get("/api/audit", headers=headers).json()]
    assert {"user.created", "user.updated", "user.deleted"} <= set(actions)


def test_change_own_password(client, owner):
    headers, _ = owner
    other, _ = login(client, "kaio", STRONG)
    assert client.post("/api/auth/password", headers=headers, json={"current_password": "errada", "new_password": PW2}).status_code == 403
    assert client.post("/api/auth/password", headers=headers, json={"current_password": STRONG, "new_password": "fraca"}).status_code == 422
    r = client.post("/api/auth/password", headers=headers, json={"current_password": STRONG, "new_password": PW2})
    assert r.status_code == 200 and r.json()["sessions_revoked"] == 1
    assert client.get("/api/auth/me", headers=headers).status_code == 200  # sessão atual continua
    assert client.get("/api/auth/me", headers=other).status_code == 401    # outras encerradas
    login(client, "kaio", PW2)


def test_settings_validation_and_permissions(client, owner):
    headers, _ = owner
    s = client.get("/api/settings", headers=headers).json()
    assert s["cpu_alert_percent"] == 90 and s["notify_commands"] is False
    assert client.put("/api/settings", headers=headers, json={"cpu_alert_percent": 20}).status_code == 422
    r = client.put("/api/settings", headers=headers, json={"cpu_alert_percent": 70, "notify_device_online": False})
    assert r.status_code == 200 and r.json()["cpu_alert_percent"] == 70 and r.json()["ram_alert_percent"] == 90
    make_user(client, headers, "zeca")
    zh, _ = login(client, "zeca")
    assert client.get("/api/settings", headers=zh).status_code == 200
    assert client.put("/api/settings", headers=zh, json={"cpu_alert_percent": 80}).status_code == 403


def test_alerts_use_configured_thresholds(client, owner):
    headers, token = owner
    a, _ = pair_device(client, headers, "PC A")
    client.put("/api/settings", headers=headers, json={
        "cpu_alert_percent": 60, "alert_sustain_seconds": 2, "notify_device_online": False, "notify_commands": True})
    with client.websocket_connect("/ws/client") as ui:
        ui.send_json({"type": "auth", "token": token})
        ui.receive_json()
        with client.websocket_connect("/ws/agent") as agent:
            agent.send_json({"type": "auth", "device_id": a["device_id"], "secret": a["device_secret"]})
            agent.receive_json()
            assert ui.receive_json()["type"] == "device"  # sem notificação de 'online' (desativada)
            sample = {"cpu": {"percent": 65}, "memory": {"percent": 10}}
            agent.send_json({"type": "metrics", "data": sample})
            assert ui.receive_json()["type"] == "metrics"
            time.sleep(2.1)
            agent.send_json({"type": "metrics", "data": sample})
            assert ui.receive_json()["type"] == "metrics"
            note = ui.receive_json()
            assert note["type"] == "notification" and note["data"]["kind"] == "high_cpu" and "65%" in note["data"]["message"]
            # comando executado gera notificação (opção ativada)
            ui.send_json({"type": "command", "request_id": "c", "device_id": a["device_id"], "action": "ping"})
            ack = ui.receive_json()
            cmd = agent.receive_json()
            agent.send_json({"type": "result", "id": cmd["id"], "ok": True, "data": {}})
            assert ui.receive_json()["type"] == "result"
            n = ui.receive_json()
            assert n["data"]["kind"] == "command" and "ping" in n["data"]["message"]


def test_agent_auth_failure_notifies(client, owner):
    headers, _ = owner
    with client.websocket_connect("/ws/agent") as bad:
        bad.send_json({"type": "auth", "device_id": "x", "secret": "y"})
        bad.receive_json()
    kinds = [n["kind"] for n in client.get("/api/notifications", headers=headers).json()]
    assert "connection_failure" in kinds


def test_notification_read_state_is_per_user(client, owner):
    headers, _ = owner
    pair_device(client, headers)
    client.post("/api/auth/login", json={"username": "kaio", "password": "errada!!"}, headers=XHR)  # gera alerta global
    make_user(client, headers, "ivo", role="admin")
    ih, _ = login(client, "ivo")
    with client.websocket_connect("/ws/agent") as bad:  # outro alerta global (agente com credencial inválida)
        bad.send_json({"type": "auth", "device_id": "x", "secret": "y"})
        bad.receive_json()
    assert client.post("/api/notifications/read-all", headers=headers).status_code == 200
    assert all(n["read"] for n in client.get("/api/notifications", headers=headers).json())
    ivo_notes = client.get("/api/notifications", headers=ih).json()
    assert any(not n["read"] for n in ivo_notes)  # leitura do dono não afeta o Ivo


def test_migrates_phase1_database(tmp_path):
    import sqlite3

    from app.db import Database

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,"
                " password_hash TEXT NOT NULL, role TEXT NOT NULL, created_at REAL NOT NULL)")
    con.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES ('kaio', 'h', 'owner', 1)")
    con.commit()
    con.close()
    db = Database(path)
    u = db.get_user_by_name("kaio")
    assert u["all_devices"] == 1 and u["notif_read_upto"] == 0 and db.accessible_device_ids(u) is None


def test_restricted_admin_audit_is_scoped(client, owner):
    headers, _ = owner
    a, _ = pair_device(client, headers, "PC A")
    b, _ = pair_device(client, headers, "PC B")
    make_user(client, headers, "adm", role="admin", all_devices=False, device_ids=[a["device_id"]])
    ah, _ = login(client, "adm")
    rows = client.get("/api/audit", headers=ah).json()
    assert any(r["device_id"] == a["device_id"] for r in rows)
    assert not any(r["device_id"] == b["device_id"] for r in rows)
    assert client.delete(f"/api/devices/{b['device_id']}", headers=ah).status_code == 404
