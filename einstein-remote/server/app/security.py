"""Primitivas de segurança: hash de senha, tokens JWT e limitação de tentativas."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

import jwt

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    """Hash scrypt (memory-hard) com salt aleatório de 16 bytes."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, dk_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=len(dk_hex) // 2
        )
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, TypeError):
        return False


def validate_password_strength(password: str) -> str | None:
    """Retorna mensagem de erro, ou None se a senha for aceitável."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"A senha deve ter pelo menos {MIN_PASSWORD_LENGTH} caracteres."
    classes = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    if classes < 3:
        return "A senha deve combinar pelo menos 3 tipos: minúsculas, maiúsculas, números e símbolos."
    return None


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Tokens opacos (refresh, segredo de dispositivo) são guardados apenas como SHA-256."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def new_pairing_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sem caracteres ambíguos (0/O, 1/I)
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_pairing_code(code: str) -> str:
    c = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{c[:4]}-{c[4:]}" if len(c) == 8 else c


def create_access_token(user_id: int, session_id: str, role: str, secret: str, ttl: int) -> str:
    now = int(time.time())
    payload = {"sub": str(user_id), "sid": session_id, "role": role, "iat": now, "exp": now + ttl, "typ": "access"}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> dict:
    payload = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub", "sid"]})
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("tipo de token inválido")
    return payload


class RateLimiter:
    """Contador de falhas em memória com bloqueio temporário (anti força-bruta)."""

    def __init__(self, max_failures: int, lockout_seconds: int):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> list[float]:
        items = [t for t in self._failures.get(key, []) if now - t < self.lockout_seconds]
        self._failures[key] = items
        return items

    def is_locked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.time())) >= self.max_failures

    def fail(self, key: str) -> None:
        with self._lock:
            now = time.time()
            self._prune(key, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
