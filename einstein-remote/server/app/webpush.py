"""Web Push (notificações com o app fechado), implementado a partir das RFCs:

* RFC 8291 — criptografia do conteúdo (aes128gcm, ECDH P-256 + HKDF): só o navegador
  do usuário consegue ler a mensagem; o serviço de push (Google/Mozilla/Apple) não.
* RFC 8292 — VAPID: o servidor assina (ES256) cada envio com a própria chave.

Só depende de ``cryptography``. As chaves VAPID são criadas na primeira execução e
guardadas no diretório de dados (permissão 600).
"""
from __future__ import annotations

import base64
import ipaddress
import json
import os
import socket
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import jwt
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

RECORD_SIZE = 4096


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def _raw_public(pub: ec.EllipticCurvePublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# ----------------------------------------------------------------- VAPID
class Vapid:
    def __init__(self, data_dir: Path, subject: str = "mailto:admin@einstein-remote.local"):
        self.subject = subject
        path = data_dir / "vapid_private.pem"
        if path.exists():
            self.key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        else:
            self.key = ec.generate_private_key(ec.SECP256R1())
            path.write_bytes(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()))
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        self.public_b64 = b64u(_raw_public(self.key.public_key()))

    def headers(self, endpoint: str) -> dict:
        u = urlparse(endpoint)
        claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": self.subject}
        token = jwt.encode(claims, self.key, algorithm="ES256")
        return {"Authorization": f"vapid t={token}, k={self.public_b64}"}


# ----------------------------------------------------------------- RFC 8291
def encrypt(payload: bytes, ua_public_b64: str, auth_b64: str, salt: bytes | None = None,
            server_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    ua_public = b64u_dec(ua_public_b64)
    auth = b64u_dec(auth_b64)
    if len(ua_public) != 65 or len(auth) < 16:
        raise ValueError("Chaves da inscrição inválidas")
    salt = salt or os.urandom(16)
    as_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _raw_public(as_key.public_key())
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh = as_key.exchange(ec.ECDH(), ua_key)
    prk_key = _hmac(auth, ecdh)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    if len(payload) > RECORD_SIZE - 17 - 1:
        raise ValueError("Mensagem grande demais para um registro")
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # 0x02 = último registro
    return salt + struct.pack(">I", RECORD_SIZE) + bytes([len(as_public)]) + as_public + ciphertext


def decrypt(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth_b64: str) -> bytes:
    """Inverso de `encrypt` (usado nos testes para provar que a criptografia está correta)."""
    salt, idlen = body[:16], body[20]
    as_public = body[21:21 + idlen]
    ciphertext = body[21 + idlen:]
    ua_public = _raw_public(ua_private.public_key())
    as_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
    ecdh = ua_private.exchange(ec.ECDH(), as_key)
    prk_key = _hmac(b64u_dec(auth_b64), ecdh)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
    return plain.rstrip(b"\x00")[:-1]


# ----------------------------------------------------------------- envio
def check_endpoint(endpoint: str, allow_http: bool = False) -> None:
    """Evita que o servidor seja usado para acessar a rede interna (SSRF)."""
    u = urlparse(endpoint)
    if u.scheme != "https" and not (allow_http and u.scheme == "http"):
        raise ValueError("Endereço de push deve usar HTTPS.")
    if not u.hostname or len(endpoint) > 2048:
        raise ValueError("Endereço de push inválido.")
    if allow_http:
        return
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError("Não foi possível resolver o serviço de push.") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("Endereço de push aponta para a rede interna.")


def send(vapid: Vapid, sub: dict, message: dict, ttl: int = 3600, urgency: str = "normal", timeout: float = 10) -> int:
    """Envia e retorna o status HTTP do serviço de push (201 = aceito; 404/410 = inscrição expirada)."""
    body = encrypt(json.dumps(message, ensure_ascii=False).encode(), sub["p256dh"], sub["auth"])
    headers = {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(ttl),
               "Urgency": urgency, **vapid.headers(sub["endpoint"])}
    req = urllib.request.Request(sub["endpoint"], data=body, headers=headers, method="POST")
    try:
        # endereço validado por check_endpoint() na inscrição E a cada envio (push_api._send_one)
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
