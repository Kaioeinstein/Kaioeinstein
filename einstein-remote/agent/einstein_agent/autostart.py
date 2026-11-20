"""Inicialização automática *visível e opcional*.

Usa a chave padrão ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
com o nome "Einstein Remote Agent" — aparece no Gerenciador de Tarefas >
Inicializar e pode ser desativada pelo próprio Windows. Só é criada quando o
proprietário executa ``einstein-agent autostart enable``.
"""
from __future__ import annotations

import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Einstein Remote Agent"


def _command() -> str:
    if getattr(sys, "frozen", False):  # executável empacotado (PyInstaller)
        return f'"{sys.executable}" run'
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pythonw}" -m einstein_agent run'


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Inicialização automática disponível apenas no Windows.")


def enable() -> str:
    _require_windows()
    import winreg

    cmd = _command()
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
