"""Configurações de alertas/notificações, persistidas no banco e editáveis pelo painel."""
from __future__ import annotations

import threading

from pydantic import BaseModel, Field

from .db import Database

DEFAULTS: dict = {
    "cpu_alert_percent": 90,
    "ram_alert_percent": 90,
    "temp_alert_celsius": 85,
    "alert_sustain_seconds": 10,
    "alert_cooldown_minutes": 10,
    "notify_device_online": True,
    "notify_device_offline": True,
    "notify_high_cpu": True,
    "notify_high_ram": True,
    "notify_high_temp": True,
    "notify_connection_failures": True,
    "notify_commands": False,
    "require_2fa": False,
}


class SettingsUpdate(BaseModel):
    """Todos os campos opcionais; apenas os enviados são alterados."""

    cpu_alert_percent: int | None = Field(None, ge=50, le=100)
    ram_alert_percent: int | None = Field(None, ge=50, le=100)
    temp_alert_celsius: int | None = Field(None, ge=50, le=110)
    alert_sustain_seconds: int | None = Field(None, ge=2, le=600)
    alert_cooldown_minutes: int | None = Field(None, ge=1, le=1440)
    notify_device_online: bool | None = None
    notify_device_offline: bool | None = None
    notify_high_cpu: bool | None = None
    notify_high_ram: bool | None = None
    notify_high_temp: bool | None = None
    notify_connection_failures: bool | None = None
    notify_commands: bool | None = None
    require_2fa: bool | None = None


class Preferences:
    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.Lock()
        stored = db.get_settings()
        self._values = {k: stored.get(k, v) for k, v in DEFAULTS.items()}

    def __getitem__(self, key: str):
        return self._values[key]

    def all(self) -> dict:
        return dict(self._values)

    def update(self, upd: SettingsUpdate) -> dict:
        changes = {k: v for k, v in upd.model_dump(exclude_none=True).items() if self._values.get(k) != v}
        with self._lock:
            self._values.update(changes)
            if changes:
                self.db.save_settings(changes)
        return changes
