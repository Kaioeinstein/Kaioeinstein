"""Fase 8: 2FA (TOTP), política "exigir 2FA", alerta de novo acesso, Web Push e recuperação."""
import base64
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app import totp, webpush
from conftest import STRONG, XHR


# ------------------------------------------------------------------ TOTP (RFC 6238)
def test_totp_rfc6238_vectors():
    secret = base64.b32encode(b"12345678901234567890").decode()
    # Apêndice B da RFC 6238 (SHA1), últimos 6 dígitos
    for t, expected in [(59, "287082"), (1111111109, "081804"), (1111111111, "050471"), (1234567890, "005924"),
                        (2000000000, "279037"), (20000000000, "353130")]:
        assert totp.code_at(secret, t // 30) == expected


def test_totp_window_and_replay():
    s = totp.new_secret()
    now = 1_800_000_000
    step = now // 30
    assert totp.verify(s, totp.code_at(s, step), 0, now=now) == step
    assert totp.verify(s, totp.code_at(s, step - 1), 0, now=now) == step - 1  # relógio 30 s atrasado
    assert totp.verify(s, totp.code_at(s, step - 3), 0, now=now) is None
    assert totp.verify(s, totp.code_at(s, step), step, now=now) is None  # mesmo código não vale duas vezes
    assert totp.verify(s, "12 34 5", 0, now=now) is None


def test_recovery_codes():
    codes = totp.new_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10 and all(len(c) == 11 and c[5] == "-" for c in codes)
    assert totp.hash_recovery(codes[0].upper().replace("-", " ")) == totp.hash_recovery(codes[0])
    qr = totp.qr_data_uri(totp.otpauth_uri("kaio", "JBSWY3DPEHPK3PXP"))
    assert qr.startswith("data:image/svg+xml") and "xmlns" in qr  # sem xmlns o navegador não exibe


# ------------------------------------------------------------------ Web Push (RFC 8291 / 8292)
def test_webpush_rfc8291_vector():
    as_key = ec.derive_private_key(int.from_bytes(webpush.b64u_dec("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    body = webpush.encrypt(b"When I grow up, I want to be a watermelon",
                           "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                           "BTBZMqHH6r4Tts7J_aSIgg", salt=webpush.b64u_dec("DGv6ra1nlYgDCS1FRnbzlw"), server_key=as_key)
    assert webpush.b64u(body) == ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_"
                                  "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def test_vapid_header(tmp_path):
    v = webpush.Vapid(tmp_path)
    h = v.headers("https://fcm.googleapis.com/fcm/send/abc")["Authorization"]
    token = h.split("t=")[1].split(",")[0]
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.b64u_dec(v.public_b64))
    claims = jwt.decode(token, pub, algorithms=["ES256"], audience="https://fcm.googleapis.com")
    assert claims["sub"].startswith("mailto:") and claims["exp"] > time.time()
    assert webpush.Vapid(tmp_path).public_b64 == v.public_b64  # chave persistida


@pytest.mark.parametrize("url", ["http://push.example.com/x", "https://127.0.0.1/x", "https://localhost/x", "https://10.0.0.5/x",
                                 "https://192.168.1.1/x", "ftp://x"])
def test_push_endpoint_ssrf_guard(url):
    with pytest.raises(ValueError):
        webpush.check_endpoint(url)


# ------------------------------------------------------------------ fluxo de 2FA
def _hdr(token):
    return {"Authorization": f"Bearer {token}", **XHR}


def _enable_2fa(client, headers):
    setup = client.post("/api/auth/2fa/setup", headers=headers).json()
    assert setup["qr"].startswith("data:image/svg+xml") and setup["uri"].startswith("otpauth://totp/")
    step = totp.current_step()
    r = client.post("/api/auth/2fa/enable", headers=headers, json={"code": totp.code_at(setup["secret"], step)})
    assert r.status_code == 200, r.text
    return setup["secret"], r.json()["recovery_codes"], step


def test_two_factor_login_flow(client, owner):
    headers, _ = owner
    secret, codes, used_step = _enable_2fa(client, headers)
    assert client.get("/api/auth/me", headers=headers).json()["totp_enabled"] is True

    client.cookies.clear()
    r = client.post("/api/auth/login", json={"username": "kaio", "password": STRONG}, headers=XHR)
    body = r.json()
    assert body["two_factor"] is True and "access_token" not in body and "er_refresh" not in r.cookies  # sem sessão ainda
    ch = body["challenge"]

    bad = client.post("/api/auth/2fa/verify", json={"challenge": ch, "code": "000000"}, headers=XHR)
    assert bad.status_code == 401
    # o código já usado para ativar não vale de novo (proteção contra reutilização)
    assert client.post("/api/auth/2fa/verify", json={"challenge": ch, "code": totp.code_at(secret, used_step)}, headers=XHR).status_code == 401
    late_step = totp.current_step() + 1  # guardado: o teste de replay abaixo reusa exatamente este código
    ok = client.post("/api/auth/2fa/verify", json={"challenge": ch, "code": totp.code_at(secret, late_step)}, headers=XHR)
    assert ok.status_code == 200 and ok.json()["access_token"]

    # código de recuperação: funciona uma única vez
    ch2 = client.post("/api/auth/login", json={"username": "kaio", "password": STRONG}, headers=XHR).json()["challenge"]
    assert client.post("/api/auth/2fa/verify", json={"challenge": ch2, "code": codes[0]}, headers=XHR).status_code == 200
    ch3 = client.post("/api/auth/login", json={"username": "kaio", "password": STRONG}, headers=XHR).json()["challenge"]
    assert client.post("/api/auth/2fa/verify", json={"challenge": ch3, "code": codes[0]}, headers=XHR).status_code == 401
    assert client.post("/api/auth/2fa/verify", json={"challenge": "lixo" * 5, "code": "123456"}, headers=XHR).status_code == 401

    kinds = [n["kind"] for n in client.get("/api/notifications", headers=headers).json()]
    assert "recovery_code" in kinds and "unauthorized" in kinds
    actions = [a["action"] for a in client.get("/api/audit", headers=headers).json()]
    assert {"auth.2fa_enabled", "auth.2fa_challenge", "auth.2fa_failed"} <= set(actions)

    # desativar exige senha + código
    assert client.post("/api/auth/2fa/disable", headers=headers, json={"password": "errada", "code": "123456"}).status_code == 403
    # códigos desta janela de 30 s já foram usados: reutilizar é recusado (proteção contra replay)…
    assert client.post("/api/auth/2fa/disable", headers=headers,
                       json={"password": STRONG, "code": totp.code_at(secret, late_step)}).status_code == 403
    # …mas um código de recuperação ainda não usado permite desativar
    r = client.post("/api/auth/2fa/disable", headers=headers, json={"password": STRONG, "code": codes[1]})
    assert r.status_code == 200 and r.json()["enabled"] is False


def test_two_factor_brute_force_lock(client, owner):
    headers, _ = owner
    secret, _, _ = _enable_2fa(client, headers)
    ch = client.post("/api/auth/login", json={"username": "kaio", "password": STRONG}, headers=XHR).json()["challenge"]
    for _ in range(5):
        client.post("/api/auth/2fa/verify", json={"challenge": ch, "code": "111111"}, headers=XHR)
    r = client.post("/api/auth/2fa/verify", json={"challenge": ch, "code": totp.code_at(secret, totp.current_step() + 1)}, headers=XHR)
    assert r.status_code == 429  # mesmo o código certo é bloqueado depois de 5 erros


def test_require_2fa_policy(client, owner):
    headers, token = owner
    client.post("/api/users", headers=headers, json={"username": "ana", "password": "Ana#Senha2026x", "role": "admin"})
    ana = _hdr(client.post("/api/auth/login", json={"username": "ana", "password": "Ana#Senha2026x"}, headers=XHR).json()["access_token"])
    assert client.put("/api/settings", headers=ana, json={"require_2fa": True}).status_code == 403  # só o proprietário
    assert client.put("/api/settings", headers=headers, json={"require_2fa": True}).status_code == 200

    # quem não ativou: só consegue o necessário para ativar
    assert client.get("/api/devices", headers=ana).json()["detail"] == "2fa_required"
    assert client.get("/api/auth/me", headers=ana).json()["must_enroll_2fa"] is True
    with client.websocket_connect("/ws/client") as ws:
        ws.send_json({"type": "auth", "token": ana["Authorization"][7:]})
        assert ws.receive_json() == {"type": "auth_error", "error": "2fa_required"}
    _enable_2fa(client, ana)
    assert client.get("/api/devices", headers=ana).status_code == 200


def test_owner_reset_and_server_cli(client, owner, tmp_path, monkeypatch):
    headers, _ = owner
    client.post("/api/users", headers=headers, json={"username": "rui", "password": "Rui#Senha2026x", "role": "viewer"})
    rui = _hdr(client.post("/api/auth/login", json={"username": "rui", "password": "Rui#Senha2026x"}, headers=XHR).json()["access_token"])
    _enable_2fa(client, rui)
    uid = next(u["id"] for u in client.get("/api/users", headers=headers).json() if u["username"] == "rui")
    assert next(u for u in client.get("/api/users", headers=headers).json() if u["username"] == "rui")["totp_enabled"] == 1
    assert client.post(f"/api/users/{uid}/reset-2fa", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=rui).status_code == 401  # sessões encerradas

    # recuperação pelo console do servidor (dono perdeu o celular)
    _enable_2fa(client, headers)
    from app import admin

    monkeypatch.setenv("EINSTEIN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EINSTEIN_DB_PATH", str(tmp_path / "t.db"))
    assert admin.main(["reset-2fa", "kaio"]) == 0
    assert client.post("/api/auth/login", json={"username": "kaio", "password": STRONG}, headers=XHR).json().get("access_token")


def test_new_login_alert_and_revoke_all(client, owner):
    headers, _ = owner
    phone = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36", **XHR}
    r = client.post("/api/auth/login", json={"username": "kaio", "password": STRONG}, headers=phone)
    client.post("/api/auth/login", json={"username": "kaio", "password": STRONG}, headers=phone)  # mesmo aparelho de novo
    alerts = [n["message"] for n in client.get("/api/notifications", headers=headers).json() if n["kind"] == "new_login"]
    assert len(alerts) == 1 and "Chrome em Android" in alerts[0]
    other = _hdr(r.json()["access_token"])
    assert client.delete("/api/sessions", headers=headers).json()["revoked"] >= 2
    assert client.get("/api/auth/me", headers=other).status_code == 401
    assert client.get("/api/auth/me", headers=headers).status_code == 200  # a sessão atual continua


# ------------------------------------------------------------------ Web Push ponta a ponta
class FakePushService(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.received.append((self.path, dict(self.headers), body))
        self.send_response(self.server.status)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def push_service():
    srv = HTTPServer(("127.0.0.1", 0), FakePushService)
    srv.received, srv.status = [], 201
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def _browser_subscription(url):
    key = ec.generate_private_key(ec.SECP256R1())
    auth = webpush.b64u(os.urandom(16))
    return key, {"endpoint": url, "keys": {"p256dh": webpush.b64u(webpush._raw_public(key.public_key())), "auth": auth}}


def _wait(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end and not cond():
        time.sleep(0.05)
    return cond()


def test_web_push_end_to_end(client, owner, push_service, monkeypatch):
    from app import push_api

    monkeypatch.setattr(push_api, "ALLOW_HTTP", True)
    headers, _ = owner
    base = f"http://127.0.0.1:{push_service.server_port}"
    key, sub = _browser_subscription(base + "/push/celular-do-kaio")
    assert client.post("/api/push/subscribe", headers=headers, json={**sub, "min_level": "warning"}).status_code == 200
    assert len(client.get("/api/push/subscriptions", headers=headers).json()) == 1
    vapid_pub = client.get("/api/push/key", headers=headers).json()["key"]

    # evento crítico real (tentativa de login inválida) → push
    client.post("/api/auth/login", json={"username": "kaio", "password": "errada!!"}, headers=XHR)
    assert _wait(lambda: push_service.received)
    path, hdrs, body = push_service.received[0]
    assert path == "/push/celular-do-kaio" and hdrs["Content-Encoding"] == "aes128gcm" and hdrs["Urgency"] == "high"
    token = hdrs["Authorization"].split("t=")[1].split(",")[0]
    assert hdrs["Authorization"].endswith(f"k={vapid_pub}")
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.b64u_dec(vapid_pub))
    jwt.decode(token, pub, algorithms=["ES256"], audience=base)  # assinatura VAPID válida
    msg = json.loads(webpush.decrypt(body, key, sub["keys"]["auth"]))  # só o "navegador" consegue ler
    assert msg["level"] == "critical" and "Tentativa de acesso não autorizada" in msg["body"]

    # nível mínimo: "info" não é enviado para quem escolheu "warning"
    n = len(push_service.received)
    client.put("/api/settings", headers=headers, json={"cpu_alert_percent": 80})
    client.post("/api/auth/2fa/setup", headers=headers)
    client.post("/api/auth/2fa/enable", headers=headers, json={"code": "000000"})  # falha: nenhuma notificação
    time.sleep(0.3)
    assert len(push_service.received) == n

    # botão "Enviar teste" e inscrição expirada (410) é removida
    r = client.post("/api/push/test", headers=headers).json()
    assert r["accepted"] == 1
    push_service.status = 410
    client.post("/api/push/test", headers=headers)
    assert client.get("/api/push/subscriptions", headers=headers).json() == []


def test_push_respects_visibility(client, owner, push_service, monkeypatch):
    from app import push_api

    monkeypatch.setattr(push_api, "ALLOW_HTTP", True)
    headers, _ = owner
    client.post("/api/users", headers=headers, json={"username": "obs", "password": "Obs#Senha2026", "role": "viewer"})
    obs = _hdr(client.post("/api/auth/login", json={"username": "obs", "password": "Obs#Senha2026"}, headers=XHR).json()["access_token"])
    _, sub = _browser_subscription(f"http://127.0.0.1:{push_service.server_port}/push/obs")
    assert client.post("/api/push/subscribe", headers=obs, json={**sub, "min_level": "info"}).status_code == 200
    client.post("/api/auth/login", json={"username": "kaio", "password": "errada!!"}, headers=XHR)  # alerta global de segurança
    time.sleep(0.5)
    assert push_service.received == []  # observador não recebe alertas globais de segurança
    monkeypatch.setattr(push_api, "ALLOW_HTTP", False)  # modo normal: rede interna é bloqueada (SSRF)
    assert client.post("/api/push/subscribe", headers=obs, json={**sub, "endpoint": "https://10.1.2.3/x"}).status_code == 422


def test_push_revalidates_endpoint_on_every_send(client, owner, monkeypatch):
    """DNS rebinding: endereço aceito na inscrição mas que depois aponta para a rede interna não recebe envio."""
    from app import push_api

    headers, _ = owner
    _, sub = _browser_subscription("https://push.exemplo-legitimo.com/abc")
    monkeypatch.setattr(webpush, "check_endpoint", lambda url, allow_http=False: None)  # inscrição aceita
    assert client.post("/api/push/subscribe", headers=headers, json=sub).status_code == 200

    def rebinding(url, allow_http=False):
        raise ValueError("Endereço de push aponta para a rede interna.")

    monkeypatch.setattr(webpush, "check_endpoint", rebinding)  # agora resolve para 10.x
    sent = []
    monkeypatch.setattr(webpush, "send", lambda *a, **k: sent.append(a) or 201)
    r = client.post("/api/push/test", headers=headers).json()
    assert r["accepted"] == 0 and sent == []  # nada foi enviado
