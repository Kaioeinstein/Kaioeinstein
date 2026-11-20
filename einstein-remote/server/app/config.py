"""Configuração do servidor EINSTEIN REMOTE.

Todos os valores podem ser sobrescritos por variáveis de ambiente
(prefixo ``EINSTEIN_``). Nenhum segredo é fixado no código: a chave de
assinatura dos tokens é gerada na primeira execução e salva no diretório
de dados com permissão restrita.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _load_or_create_secret(data_dir: Path) -> str:
    env = os.environ.get("EINSTEIN_SECRET_KEY")
    if env:
        if len(env) < 32:
            raise RuntimeError("EINSTEIN_SECRET_KEY deve ter pelo menos 32 caracteres")
        return env
    path = data_dir / "secret.key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(48)
    path.write_text(key, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


@dataclass
class Settings:
    data_dir: Path
    db_path: Path
    secret_key: str
    web_dir: Path
    access_ttl: int = 15 * 60  # 15 minutos
    refresh_ttl: int = 7 * 24 * 3600  # 7 dias
    pairing_ttl: int = 10 * 60  # 10 minutos
    command_timeout: int = 30
    cookie_secure: bool = False
    max_login_failures: int = 5
    lockout_seconds: int = 5 * 60
    history_persist_interval: int = 60
    allowed_origins: list[str] = field(default_factory=list)


def load_settings() -> Settings:
    root = Path(__file__).resolve().parents[2]
    data_dir = Path(os.environ.get("EINSTEIN_DATA_DIR", root / "data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    origins = [o.strip() for o in os.environ.get("EINSTEIN_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    return Settings(
        data_dir=data_dir,
        db_path=Path(os.environ.get("EINSTEIN_DB_PATH", data_dir / "einstein.db")),
        secret_key=_load_or_create_secret(data_dir),
        web_dir=Path(os.environ.get("EINSTEIN_WEB_DIR", root / "web")).resolve(),
        access_ttl=_env_int("EINSTEIN_ACCESS_TTL", 15 * 60),
        refresh_ttl=_env_int("EINSTEIN_REFRESH_TTL", 7 * 24 * 3600),
        cookie_secure=os.environ.get("EINSTEIN_COOKIE_SECURE", "0") == "1",
        allowed_origins=origins,
    )
