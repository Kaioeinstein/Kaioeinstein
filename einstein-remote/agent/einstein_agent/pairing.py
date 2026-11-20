"""Vinculação do computador ao servidor (usada pela linha de comando e pela janela gráfica)."""
from __future__ import annotations

import json
import shutil
import ssl
import urllib.error
import urllib.request

from . import autostart
from .client import check_transport, ssl_context
from .config import AgentConfig, config_dir, protect_secret
from .metrics import system_info


class PairingError(Exception):
    pass


def normalize_server(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url:
        raise PairingError("Informe o endereço do servidor.")
    if "://" not in url:
        url = "https://" + url
    if not url.startswith(("http://", "https://")):
        raise PairingError("O endereço deve começar com https://")
    return url


def pair(server: str, code: str, ca_file: str = "", allow_insecure: bool = False) -> AgentConfig:
    """Troca o código de uso único por credenciais do dispositivo e salva a configuração."""
    cfg = AgentConfig.load()
    cfg.server_url = normalize_server(server)
    cfg.ca_file = ca_file or ""
    cfg.allow_insecure = bool(allow_insecure)
    code = code.strip()
    if len("".join(c for c in code if c.isalnum())) != 8:
        raise PairingError("O código tem 8 caracteres (ex.: ABCD-EFGH).")
    try:
        check_transport(cfg)
    except ValueError as e:
        raise PairingError(str(e)) from None

    info = system_info()
    body = json.dumps({"code": code, "hostname": info["hostname"], "os": info["os"]}).encode()
    req = urllib.request.Request(
        cfg.server_url + "/api/agent/pair", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        ctx = ssl_context(cfg) if cfg.server_url.startswith("https://") else None
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}").get("detail", e.reason)
        except ValueError:
            detail = e.reason
        raise PairingError(f"O servidor recusou: {detail}") from None
    except (urllib.error.URLError, ssl.SSLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise PairingError(f"Não foi possível contatar o servidor: {reason}") from None

    cfg.device_id = data["device_id"]
    cfg.device_secret = protect_secret(data["device_secret"])
    cfg.device_name = data["name"]
    cfg.paused = False
    cfg.save()
    return cfg


def unpair() -> None:
    """Remove vínculo, configuração e início automático deste computador."""
    autostart.disable()
    shutil.rmtree(config_dir(), ignore_errors=True)
