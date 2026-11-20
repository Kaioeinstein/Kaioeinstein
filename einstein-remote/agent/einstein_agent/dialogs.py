"""Caixas de diálogo simples (nativas no Windows, tkinter nos demais sistemas)."""
from __future__ import annotations

import sys

_MB_OK = 0x0
_MB_YESNO = 0x4
_MB_ICONINFO = 0x40
_MB_ICONWARNING = 0x30
_MB_ICONQUESTION = 0x20
_MB_TOPMOST = 0x40000
_MB_SETFOREGROUND = 0x10000
_IDYES = 6


def _win_box(title: str, text: str, flags: int) -> int:
    import ctypes

    return ctypes.windll.user32.MessageBoxW(None, text, title, flags | _MB_TOPMOST | _MB_SETFOREGROUND)


def _tk(kind: str, title: str, text: str):
    import tkinter as tk
    from tkinter import messagebox

    import gc

    root = tk.Tk()
    root.withdraw()
    try:
        return getattr(messagebox, kind)(title, text, parent=root)
    finally:
        root.destroy()
        del root
        gc.collect()  # finaliza objetos Tk nesta thread (ver pair_window.run_pair_window)


def confirm(title: str, text: str) -> bool:
    if sys.platform == "win32":
        return _win_box(title, text, _MB_YESNO | _MB_ICONQUESTION) == _IDYES
    return bool(_tk("askyesno", title, text))


def info(title: str, text: str, warning: bool = False) -> None:
    if sys.platform == "win32":
        _win_box(title, text, _MB_OK | (_MB_ICONWARNING if warning else _MB_ICONINFO))
    else:
        _tk("showwarning" if warning else "showinfo", title, text)
