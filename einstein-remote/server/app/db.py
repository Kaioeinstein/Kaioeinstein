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
    created_at REAL NOT NULL
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
    revoked INTEGER NOT NULL DEFAULT 0
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
            "SELECT id, name, hostname, os, created_at, last_seen, revoked FROM devices WHERE id = ?", (device_id,)
        )

    def list_devices(self) -> list[dict]:
        return self.all(
            "SELECT id, name, hostname, os, created_at, last_seen, revoked FROM devices WHERE revoked = 0 ORDER BY created_at"
        )

    def update_device_seen(self, device_id: str, hostname: str | None = None, os_name: str | None = None) -> None:
        self.execute(
            "UPDATE devices SET last_seen = ?, hostname = COALESCE(?, hostname), os = COALESCE(?, os) WHERE id = ?",
            (time.time(), hostname, os_name, device_id),
        )

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

    def mark_notifications_read(self) -> None:
        self.execute("UPDATE notifications SET read = 1 WHERE read = 0")

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
