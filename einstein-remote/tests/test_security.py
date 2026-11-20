import time

import jwt
import pytest

from app import security


def test_password_hash_roundtrip():
    h = security.hash_password("Einstein#2026!")
    assert h.startswith("scrypt$")
    assert security.verify_password("Einstein#2026!", h)
    assert not security.verify_password("errada", h)
    assert not security.verify_password("x", "lixo")


def test_hashes_are_salted():
    assert security.hash_password("a") != security.hash_password("a")


@pytest.mark.parametrize("pw,ok", [("curta", False), ("somenteminusculas", False), ("Senha1234!", True), ("Abcdefghij1", True)])
def test_password_strength(pw, ok):
    assert (security.validate_password_strength(pw) is None) == ok


def test_access_token_expiry():
    t = security.create_access_token(1, "sid", "owner", "k" * 40, ttl=-1)
    with pytest.raises(jwt.ExpiredSignatureError):
        security.decode_access_token(t, "k" * 40)


def test_access_token_wrong_secret():
    t = security.create_access_token(1, "sid", "owner", "k" * 40, ttl=60)
    with pytest.raises(jwt.InvalidSignatureError):
        security.decode_access_token(t, "z" * 40)
    assert security.decode_access_token(t, "k" * 40)["sid"] == "sid"


def test_pairing_code_format():
    c = security.new_pairing_code()
    assert len(c) == 9 and c[4] == "-"
    assert security.normalize_pairing_code(c.lower().replace("-", " ")) == c


def test_rate_limiter():
    rl = security.RateLimiter(3, 60)
    for _ in range(3):
        assert not rl.is_locked("k")
        rl.fail("k")
    assert rl.is_locked("k")
    rl.reset("k")
    assert not rl.is_locked("k")
