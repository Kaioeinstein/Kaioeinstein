"""Verificação em duas etapas: TOTP (RFC 6238, HMAC-SHA1, 6 dígitos, 30 s) e códigos de recuperação.

Compatível com Google Authenticator, Microsoft Authenticator, Authy, 1Password, Bitwarden.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30
DIGITS = 6
ISSUER = "EINSTEIN REMOTE"
RECOVERY_COUNT = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // STEP)


def verify(secret: str, code: str, last_step: int = 0, window: int = 1, now: float | None = None) -> int | None:
    """Retorna o passo aceito (para impedir reutilização) ou None.

    Aceita ±`window` passos (relógio do celular um pouco adiantado/atrasado), mas nunca
    um passo <= `last_step` (o mesmo código não serve duas vezes).
    """
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != DIGITS:
        return None
    now_step = current_step(now)
    for step in range(now_step - window, now_step + window + 1):
        if step > last_step and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def otpauth_uri(username: str, secret: str) -> str:
    label = quote(f"{ISSUER}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


def qr_data_uri(uri: str) -> str:
    """QR code como data URI SVG completo (com xmlns — sem ele o navegador não exibe a imagem)."""
    import segno

    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2, dark="#000000", light="#ffffff")


# ----------------------------------------------------------------- códigos de recuperação
def _norm(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum())


def new_recovery_codes() -> list[str]:
    codes = []
    for _ in range(RECOVERY_COUNT):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def hash_recovery(code: str) -> str:
    return hashlib.sha256(_norm(code).encode()).hexdigest()


def is_recovery_format(code: str) -> bool:
    return len(_norm(code)) == 10
