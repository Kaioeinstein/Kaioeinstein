"""Registro local do agente (arquivo rotativo, nunca contém segredos).

Local:
* Windows: ``%LOCALAPPDATA%\\EinsteinRemote\\logs\\agent.log``
* Outros:  ``~/.local/state/einstein-remote/logs/agent.log``

Fica fora da pasta de configuração para que "desvincular" não precise apagar
um arquivo que ainda está aberto.
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s  %(levelname)-7s %(name)s: %(message)s"


def logs_dir() -> Path:
    override = os.environ.get("EINSTEIN_AGENT_LOG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "EinsteinRemote" / "logs"
    return Path.home() / ".local" / "state" / "einstein-remote" / "logs"


def log_file() -> Path:
    return logs_dir() / "agent.log"


def setup_logging(console: bool = True, level: int = logging.INFO) -> Path:
    """Configura log em arquivo (1 MB × 5) e, opcionalmente, no console."""
    path = log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    fh = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(fh)
    if console and sys.stderr is not None:  # executável sem console tem stderr = None
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S"))
        root.addHandler(ch)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    return path


def tail(lines: int = 40) -> str:
    try:
        with open(log_file(), encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return ""
