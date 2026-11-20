"""Camada de persistência (SQLite).

O acesso é encapsulado nesta classe para facilitar a migração futura para
PostgreSQL: o restante do código nunca usa ``sqlite3`` diretamente.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner','admin','viewer')),
    created_at REAL NOT NULL,
    all_devices INTEGER NOT NULL DEFAULT 1,
    notif_read_upto INTEGER NOT NULL DEFAULT 0,
    last_login REAL
);

CREATE TABLE IF NOT EXISTS user_devices (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, device_id)
);

CREATE TABLE IF NOT EXISTS known_logins (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL,
    label TEXT,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    PRIMARY KEY (user_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    min_level TEXT NOT NULL DEFAULT 'warning',
    user_agent TEXT,
    created_at REAL NOT NULL,
    last_ok REAL,
    failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    last_seen REAL NOT NULL,
    ip TEXT,
    user_agent TEXT,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_refresh ON sessions(refresh_hash);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    secret_hash TEXT NOT NULL,
    hostname TEXT,
    os TEXT,
    created_at REAL NOT NULL,
    last_seen REAL,
    revoked INTEGER NOT NULL DEFAULT 0,
    last_state TEXT,
    agent_version TEXT,
    mac TEXT,
    last_ip TEXT
);

CREATE TABLE IF NOT EXISTS pairing_codes (
    code_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    device_name TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    user_id INTEGER,
    username TEXT,
    device_id TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    ip TEXT,
    success INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    device_id TEXT,
    level TEXT NOT NULL CHECK (level IN ('info','warning','critical')),
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notifications_ts ON notifications(ts);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    ts REAL NOT NULL,
    cpu REAL, ram REAL, disk REAL, net_down REAL, net_up REAL, gpu REAL, temp REAL
);
CREATE INDEX IF NOT EXISTS idx_metrics_device_ts ON metrics(device_id, ts);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()

    # Migrações incrementais (bancos criados em versões anteriores) -----------
    _USER_COLUMNS = {
        "all_devices": "INTEGER NOT NULL DEFAULT 1",
        "notif_read_upto": "INTEGER NOT NULL DEFAULT 0",
        "last_login": "REAL",
        "totp_secret": "TEXT",
        "totp_enabled": "INTEGER NOT NULL DEFAULT 0",
        "totp_last_step": "INTEGER NOT NULL DEFAULT 0",
        "recovery_hashes": "TEXT",
    }

    _DEVICE_COLUMNS = {"last_state": "TEXT", "agent_version": "TEXT", "mac": "TEXT", "last_ip": "TEXT"}

    def _migrate(self) -> None:
        for table, columns in (("users", self._USER_COLUMNS), ("devices", self._DEVICE_COLUMNS)):
            cols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for name, ddl in columns.items():
                if name not in cols:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- helpers genéricos ------------------------------------------------
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def all(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # -- usuários ----------------------------------------------------------
    def count_users(self) -> int:
        return self.one("SELECT COUNT(*) AS n FROM users")["n"]

    def create_user(self, username: str, password_hash: str, role: str) -> int:
        cur = self.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
            (username, password_hash, role, time.time()),
        )
        return cur.lastrowid

    def get_user_by_name(self, username: str) -> dict | None:
        return self.one("SELECT * FROM users WHERE username = ?", (username,))

    def get_user(self, user_id: int) -> dict | None:
        return self.one("SELECT * FROM users WHERE id = ?", (user_id,))

    def get_owner(self) -> dict | None:
        return self.one("SELECT * FROM users WHERE role = 'owner' ORDER BY id LIMIT 1")

    def list_users(self) -> list[dict]:
        users = self.all("SELECT id, username, role, created_at, all_devices, last_login, totp_enabled FROM users ORDER BY id")
        for u in users:
            u["device_ids"] = self.user_device_ids(u["id"])
        return users

    def update_user(self, user_id: int, **fields) -> None:
        allowed = {"role", "all_devices", "password_hash", "notif_read_upto", "last_login", "totp_secret",
                   "totp_enabled", "totp_last_step", "recovery_hashes"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        # nomes de coluna vêm só da lista fixa `allowed`; valores vão como parâmetros
        self.execute(f"UPDATE users SET {cols} WHERE id = ?", (*fields.values(), user_id))  # nosec B608

    def delete_user(self, user_id: int) -> None:
        """Remove o usuário. Dispositivos vinculados por ele passam ao proprietário."""
        with self._lock:
            owner = self.get_owner()
            self.execute("UPDATE devices SET owner_id = ? WHERE owner_id = ?", (owner["id"], user_id))
            self.execute("DELETE FROM pairing_codes WHERE user_id = ?", (user_id,))
            self.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def user_device_ids(self, user_id: int) -> list[str]:
        return [r["device_id"] for r in self.all("SELECT device_id FROM user_devices WHERE user_id = ?", (user_id,))]

    def set_user_devices(self, user_id: int, device_ids: list[str]) -> None:
        with self._lock:
            self.execute("DELETE FROM user_devices WHERE user_id = ?", (user_id,))
            for d in device_ids:
                self.execute("INSERT OR IGNORE INTO user_devices (user_id, device_id) VALUES (?,?)", (user_id, d))

    def grant_device(self, user_id: int, device_id: str) -> None:
        self.execute("INSERT OR IGNORE INTO user_devices (user_id, device_id) VALUES (?,?)", (user_id, device_id))

    def accessible_device_ids(self, user: dict) -> set[str] | None:
        """None = acesso a todos os dispositivos."""
        if user["role"] == "owner" or user.get("all_devices"):
            return None
        return set(self.user_device_ids(user["id"]))

    # -- acessos conhecidos (alerta de novo acesso) ---------------------------
    def seen_login(self, user_id: int, fingerprint: str, label: str) -> tuple[bool, int]:
        """Registra o acesso. Retorna (é_novo, quantos_acessos_conhecidos_antes)."""
        with self._lock:
            known = self.one("SELECT COUNT(*) AS n FROM known_logins WHERE user_id = ?", (user_id,))["n"]
            row = self.one("SELECT 1 FROM known_logins WHERE user_id = ? AND fingerprint = ?", (user_id, fingerprint))
            now = time.time()
            if row:
                self.execute("UPDATE known_logins SET last_seen = ? WHERE user_id = ? AND fingerprint = ?", (now, user_id, fingerprint))
                return False, known
            self.execute("INSERT INTO known_logins (user_id, fingerprint, label, first_seen, last_seen) VALUES (?,?,?,?,?)",
                         (user_id, fingerprint, label, now, now))
            return True, known

    # -- inscrições de push ---------------------------------------------------
    def save_push(self, user_id: int, endpoint: str, p256dh: str, auth: str, min_level: str, ua: str | None) -> None:
        self.execute(
            "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, min_level, user_agent, created_at) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(endpoint) DO UPDATE SET user_id = excluded.user_id, p256dh = excluded.p256dh, auth = excluded.auth,"
            " min_level = excluded.min_level, user_agent = excluded.user_agent, failures = 0",
            (user_id, endpoint, p256dh, auth, min_level, (ua or "")[:300], time.time()),
        )

    def push_subscriptions(self, user_id: int | None = None) -> list[dict]:
        if user_id is None:
            return self.all("SELECT * FROM push_subscriptions")
        return self.all("SELECT id, endpoint, min_level, user_agent, created_at, last_ok, failures FROM push_subscriptions WHERE user_id = ?", (user_id,))

    def delete_push(self, endpoint: str, user_id: int | None = None) -> bool:
        if user_id is None:
            return self.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).rowcount > 0
        return self.execute("DELETE FROM push_subscriptions WHERE endpoint = ? AND user_id = ?", (endpoint, user_id)).rowcount > 0

    def push_result(self, endpoint: str, ok: bool) -> None:
        if ok:
            self.execute("UPDATE push_subscriptions SET last_ok = ?, failures = 0 WHERE endpoint = ?", (time.time(), endpoint))
        else:
            self.execute("UPDATE push_subscriptions SET failures = failures + 1 WHERE endpoint = ?", (endpoint,))

    # -- configurações --------------------------------------------------------
    def get_settings(self) -> dict:
        return {r["key"]: json.loads(r["value"]) for r in self.all("SELECT key, value FROM settings")}

    def save_settings(self, values: dict) -> None:
        with self._lock:
            for k, v in values.items():
                self.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (k, json.dumps(v)),
                )

    # -- sessões -----------------------------------------------------------
    def create_session(self, sid: str, user_id: int, refresh_hash: str, ttl: int, ip: str | None, ua: str | None) -> None:
        now = time.time()
        self.execute(
            "INSERT INTO sessions (id, user_id, refresh_hash, created_at, expires_at, last_seen, ip, user_agent)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (sid, user_id, refresh_hash, now, now + ttl, now, ip, (ua or "")[:300]),
        )

    def get_session(self, sid: str) -> dict | None:
        return self.one("SELECT * FROM sessions WHERE id = ?", (sid,))

    def get_session_by_refresh(self, refresh_hash: str) -> dict | None:
        return self.one("SELECT * FROM sessions WHERE refresh_hash = ?", (refresh_hash,))

    def rotate_refresh(self, sid: str, refresh_hash: str) -> None:
        self.execute("UPDATE sessions SET refresh_hash = ?, last_seen = ? WHERE id = ?", (refresh_hash, time.time(), sid))

    def touch_session(self, sid: str) -> None:
        self.execute("UPDATE sessions SET last_seen = ? WHERE id = ?", (time.time(), sid))

    def revoke_session(self, sid: str) -> None:
        self.execute("UPDATE sessions SET revoked = 1 WHERE id = ?", (sid,))

    def revoke_user_sessions(self, user_id: int, except_sid: str | None = None) -> list[str]:
        rows = self.all("SELECT id FROM sessions WHERE user_id = ? AND revoked = 0", (user_id,))
        ids = [r["id"] for r in rows if r["id"] != except_sid]
        for sid in ids:
            self.revoke_session(sid)
        return ids

    def list_sessions(self, user_id: int) -> list[dict]:
        return self.all(
            "SELECT id, created_at, expires_at, last_seen, ip, user_agent, revoked FROM sessions"
            " WHERE user_id = ? AND revoked = 0 AND expires_at > ? ORDER BY last_seen DESC",
            (user_id, time.time()),
        )

    # -- dispositivos ------------------------------------------------------
    def create_pairing_code(self, code_hash: str, user_id: int, device_name: str, ttl: int) -> float:
        now = time.time()
        self.execute(
            "INSERT INTO pairing_codes (code_hash, user_id, device_name, created_at, expires_at) VALUES (?,?,?,?,?)",
            (code_hash, user_id, device_name, now, now + ttl),
        )
        return now + ttl

    def consume_pairing_code(self, code_hash: str) -> dict | None:
        """Marca o código como usado de forma atômica. Retorna None se inválido/expirado/já usado."""
        with self._lock:
            row = self.one(
                "SELECT * FROM pairing_codes WHERE code_hash = ? AND used = 0 AND expires_at > ?",
                (code_hash, time.time()),
            )
            if not row:
                return None
            self.execute("UPDATE pairing_codes SET used = 1 WHERE code_hash = ?", (code_hash,))
            return row

    def create_device(self, device_id: str, name: str, owner_id: int, secret_hash: str, hostname: str, os_name: str) -> None:
        self.execute(
            "INSERT INTO devices (id, name, owner_id, secret_hash, hostname, os, created_at) VALUES (?,?,?,?,?,?,?)",
            (device_id, name, owner_id, secret_hash, hostname, os_name, time.time()),
        )

    def get_device(self, device_id: str) -> dict | None:
        return self.one("SELECT * FROM devices WHERE id = ?", (device_id,))

    def get_device_public(self, device_id: str) -> dict | None:
        return self.one(
            "SELECT id, name, hostname, os, created_at, last_seen, revoked, last_state, agent_version, mac FROM devices WHERE id = ?",
            (device_id,),
        )

    def list_devices(self) -> list[dict]:
        return self.all(
            "SELECT id, name, hostname, os, created_at, last_seen, revoked, last_state, agent_version, mac FROM devices"
            " WHERE revoked = 0 ORDER BY created_at"
        )

    def update_device_seen(self, device_id: str, hostname: str | None = None, os_name: str | None = None) -> None:
        self.execute(
            "UPDATE devices SET last_seen = ?, hostname = COALESCE(?, hostname), os = COALESCE(?, os) WHERE id = ?",
            (time.time(), hostname, os_name, device_id),
        )

    def set_device_state(self, device_id: str, state: str, agent_version: str | None = None) -> None:
        self.execute(
            "UPDATE devices SET last_state = ?, agent_version = COALESCE(?, agent_version) WHERE id = ?",
            (state, agent_version, device_id),
        )

    def set_device_network(self, device_id: str, mac: str | None, ip: str | None) -> None:
        self.execute("UPDATE devices SET mac = COALESCE(?, mac), last_ip = COALESCE(?, last_ip) WHERE id = ?", (mac, ip, device_id))

    def rename_device(self, device_id: str, name: str) -> None:
        self.execute("UPDATE devices SET name = ? WHERE id = ?", (name, device_id))

    def revoke_device(self, device_id: str) -> None:
        self.execute("UPDATE devices SET revoked = 1 WHERE id = ?", (device_id,))

    # -- auditoria e notificações -----------------------------------------
    def audit(
        self,
        action: str,
        *,
        user: dict | None = None,
        device_id: str | None = None,
        detail: Any = None,
        ip: str | None = None,
        success: bool = True,
    ) -> None:
        if detail is not None and not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=False)[:2000]
        self.execute(
            "INSERT INTO audit_log (ts, user_id, username, device_id, action, detail, ip, success) VALUES (?,?,?,?,?,?,?,?)",
            (
                time.time(),
                user["id"] if user else None,
                user["username"] if user else None,
                device_id,
                action,
                detail,
                ip,
                1 if success else 0,
            ),
        )

    def list_audit(self, limit: int = 100) -> list[dict]:
        return self.all("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))

    def add_notification(self, level: str, kind: str, message: str, device_id: str | None = None) -> dict:
        ts = time.time()
        cur = self.execute(
            "INSERT INTO notifications (ts, device_id, level, kind, message) VALUES (?,?,?,?,?)",
            (ts, device_id, level, kind, message),
        )
        return {"id": cur.lastrowid, "ts": ts, "device_id": device_id, "level": level, "kind": kind, "message": message, "read": 0}

    def list_notifications(self, limit: int = 50) -> list[dict]:
        return self.all("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))

    def last_notification_id(self) -> int:
        return self.one("SELECT COALESCE(MAX(id), 0) AS n FROM notifications")["n"]

    # -- métricas ----------------------------------------------------------
    def add_metric(self, device_id: str, sample: dict) -> None:
        self.execute(
            "INSERT INTO metrics (device_id, ts, cpu, ram, disk, net_down, net_up, gpu, temp) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                device_id,
                sample["ts"],
                sample.get("cpu"),
                sample.get("ram"),
                sample.get("disk"),
                sample.get("net_down"),
                sample.get("net_up"),
                sample.get("gpu"),
                sample.get("temp"),
            ),
        )

    def metric_history(self, device_id: str, since: float) -> list[dict]:
        return self.all(
            "SELECT ts, cpu, ram, disk, net_down, net_up, gpu, temp FROM metrics WHERE device_id = ? AND ts >= ? ORDER BY ts",
            (device_id, since),
        )

    def prune_metrics(self, older_than: float) -> None:
        self.execute("DELETE FROM metrics WHERE ts < ?", (older_than,))
