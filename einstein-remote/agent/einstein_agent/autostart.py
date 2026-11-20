"""Inicialização automática *visível e opcional*.

Usa a chave padrão ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
com o nome "Einstein Remote Agent" — aparece no Gerenciador de Tarefas >
Inicializar e pode ser desativada pelo próprio Windows. Só é criada quando o
proprietário pede (``einstein-agent autostart enable`` ou menu da bandeja).
O programa iniciado é sempre o da bandeja, com ícone visível.
"""
from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Einstein Remote Agent"
TRAY_EXE = "EinsteinRemoteAgent.exe"


def startup_command(tray: bool = True) -> str:
    if getattr(sys, "frozen", False):  # executável empacotado (PyInstaller)
        exe = Path(sys.executable)
        sibling = exe.with_name(TRAY_EXE)
        if sibling.exists():
            return f'"{sibling}"'
        return f'"{exe}" tray'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{interpreter}" -m einstein_agent tray'


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Inicialização automática disponível apenas no Windows.")


def enable(tray: bool = True) -> str:
    _require_windows()
    import winreg

    cmd = startup_command(tray)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, cmd)
    return cmd


def disable() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False
