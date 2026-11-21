"""Configuração local do agente.

Local do arquivo:
* Windows: ``%APPDATA%\\EinsteinRemote\\agent.json``
* Outros:  ``~/.config/einstein-remote/agent.json``

No Windows, o segredo do dispositivo é cifrado com DPAPI (CryptProtectData),
vinculado à conta do usuário: copiar o arquivo para outra máquina/conta não
revela a credencial.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def config_dir() -> Path:
    override = os.environ.get("EINSTEIN_AGENT_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "EinsteinRemote"
    return Path.home() / ".config" / "einstein-remote"


def config_path() -> Path:
    return config_dir() / "agent.json"


# ---------------------------------------------------------------- DPAPI ----
def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise OSError("Falha na operação DPAPI")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def protect_secret(secret: str) -> str:
    if sys.platform == "win32":
        return "dpapi:" + base64.b64encode(_dpapi(secret.encode(), True)).decode()
    return "plain:" + secret


def unprotect_secret(value: str) -> str:
    if value.startswith("dpapi:"):
        return _dpapi(base64.b64decode(value[6:]), False).decode()
    if value.startswith("plain:"):
        return value[6:]
    return value


# --------------------------------------------------------------- config ----
@dataclass
class AgentConfig:
    server_url: str = ""  # ex.: https://meu-servidor:8443
    device_id: str = ""
    device_secret: str = ""  # armazenado cifrado (ver protect_secret)
    device_name: str = ""
    paused: bool = False
    interval: float = 2.0
    ca_file: str = ""  # certificado da CA (para certificados autoassinados)
    allow_insecure: bool = False  # permite ws:// fora de localhost (NÃO recomendado)
    allowed_roots: list[str] = field(default_factory=list)  # pastas liberadas (fase 7)
    allow_process_kill: bool = True  # o dono pode proibir, no próprio PC, o encerramento remoto de processos
    screen_access: str = "allow"  # acesso à tela: "allow" (sem perguntar) | "ask" (perguntar a cada sessão) | "deny"
    allow_remote_input: bool = True
    allow_power: bool = True  # permite desligar/reiniciar/suspender/hibernar/bloquear pelo painel
    allow_volume: bool = True  # controlar o volume do PC pelo painel
    allow_maintenance: bool = True  # limpeza de disco, inicialização, modo de energia e modo jogo pelo painel
    game_mode_auto: bool = False  # modo jogo automático (o dono liga no painel)
    game_processes: list = field(default_factory=list)  # jogos extras escolhidos pelo dono (nome do .exe)

    @property
    def paired(self) -> bool:
        return bool(self.server_url and self.device_id and self.device_secret)

    def secret(self) -> str:
        return unprotect_secret(self.device_secret)

    def ws_url(self) -> str:
        base = self.server_url.rstrip("/")
        if base.startswith("https://"):
            base = "wss://" + base[8:]
        elif base.startswith("http://"):
            base = "ws://" + base[7:]
        return base + "/ws/agent"

    @classmethod
    def load(cls) -> "AgentConfig":
        path = config_path()
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
