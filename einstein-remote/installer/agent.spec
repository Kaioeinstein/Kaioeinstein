# -*- mode: python ; coding: utf-8 -*-
"""Build do EINSTEIN REMOTE AGENT com PyInstaller (modo pasta, dois executáveis).

    pyinstaller installer/agent.spec --noconfirm

Resultado em dist/EinsteinRemoteAgent/:
    EinsteinRemoteAgent.exe   bandeja do sistema (sem janela de console)
    einstein-agent.exe        linha de comando (pair, status, logs, ...)

Modo pasta (e não arquivo único) porque inicia mais rápido e gera menos falsos
positivos em antivírus do que executáveis autoextraíveis.
"""
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
AGENT = ROOT / "agent"
BUILD = ROOT / "installer" / "build"
BUILD.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(AGENT))

from einstein_agent import __version__  # noqa: E402
from einstein_agent.icons import save_ico  # noqa: E402

ICON = str(BUILD / "einstein.ico")
save_ico(ICON)

VERSION_FILE = None
if sys.platform == "win32":
    nums = tuple(int(x) for x in __version__.split(".")) + (0,)
    VERSION_FILE = str(BUILD / "version_info.txt")
    Path(VERSION_FILE).write_text(f"""
VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041604B0', [
      StringStruct('CompanyName', 'EINSTEIN REMOTE'),
      StringStruct('FileDescription', 'EINSTEIN REMOTE AGENT - monitoramento autorizado pelo proprietário'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('ProductName', 'EINSTEIN REMOTE AGENT'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1046, 1200])])
  ]
)
""", encoding="utf-8")

# módulos carregados dinamicamente (bandeja, captura de tela, entrada no X11)
hidden = ["pystray._win32", "mss.windows"] if sys.platform == "win32" else ["pystray._xorg", "mss.linux", "Xlib.ext.xtest"]
common = dict(
    pathex=[str(AGENT)],
    hiddenimports=hidden + ["PIL.ImageTk"],
    excludes=["pytest", "fastapi", "uvicorn", "starlette", "pydantic", "numpy"],
)

a_tray = Analysis([str(ROOT / "installer" / "entry_tray.py")], **common)
a_cli = Analysis([str(ROOT / "installer" / "entry_cli.py")], **common)

exe_tray = EXE(
    PYZ(a_tray.pure), a_tray.scripts, [], exclude_binaries=True, name="EinsteinRemoteAgent",
    console=False, icon=ICON, version=VERSION_FILE, upx=False,
)
exe_cli = EXE(
    PYZ(a_cli.pure), a_cli.scripts, [], exclude_binaries=True, name="einstein-agent",
    console=True, icon=ICON, version=VERSION_FILE, upx=False,
)
coll = COLLECT(
    exe_tray, a_tray.binaries, a_tray.datas,
    exe_cli, a_cli.binaries, a_cli.datas,
    name="EinsteinRemoteAgent", upx=False,
)
