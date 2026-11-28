"""Limpeza de disco, programas de inicialização, modo de energia e modo jogo.

Princípios (iguais ao resto do agente):
* nada é apagado sem o dono ver antes quanto será liberado e confirmar no painel;
* só categorias fixas, em pastas do próprio usuário; links/junções nunca são seguidos;
* arquivos em uso ou recentes (menos de 1 h) ficam — o Windows ou um programa podem precisar deles;
* o dono pode bloquear tudo isso no PC (bandeja › Permitir limpeza e otimização pelo painel).

Modo jogo: quando um jogo abre, o Windows passa para "Melhor desempenho" (o mesmo controle do
Windows 11 em Configurações › Energia › Modo de energia, que no Acer Nitro acompanha o NitroSense);
quando o jogo fecha, volta ao modo anterior.
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

import psutil

log = logging.getLogger("einstein.maintenance")
WINDOWS = sys.platform == "win32"
MIN_AGE = 3600  # só apaga o que não foi modificado na última hora
REPARSE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT (junções e links do Windows)


class MaintenanceRefused(Exception):
    pass


def _allowed() -> None:
    from .config import AgentConfig

    if not AgentConfig.load().allow_maintenance:
        raise MaintenanceRefused("O dono do computador bloqueou limpeza e otimização pelo painel (menu da bandeja).")


# ================================================================= limpeza de disco
def _local() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))


def _browser_caches() -> list[Path]:
    out: list[Path] = []
    for base in (_local() / "Google/Chrome/User Data", _local() / "Microsoft/Edge/User Data",
                 _local() / "BraveSoftware/Brave-Browser/User Data", _local() / "Opera Software"):
        if not base.is_dir():
            continue
        for profile in base.iterdir():
            for sub in ("Cache/Cache_Data", "Code Cache", "GPUCache"):
                p = profile / sub
                if p.is_dir():
                    out.append(p)
    ff = _local() / "Mozilla/Firefox/Profiles"
    if ff.is_dir():
        out += [p / "cache2" for p in ff.iterdir() if (p / "cache2").is_dir()]
    return out


def categories() -> dict[str, dict]:
    """Categorias fixas. Cada uma: rótulo, explicação e pastas (só do usuário atual)."""
    cats = {
        "temp": {"label": "Arquivos temporários", "desc": "Sobras de instalações e programas (%TEMP%).",
                 "paths": [Path(tempfile.gettempdir())]},
        "browser": {"label": "Cache dos navegadores", "desc": "Chrome, Edge, Brave, Opera e Firefox. Logins e favoritos não são tocados.",
                    "paths": _browser_caches()},
        "crash": {"label": "Relatórios de erro", "desc": "Despejos de memória e relatórios de falhas de programas.",
                  "paths": [_local() / "CrashDumps", _local() / "Microsoft/Windows/WER/ReportArchive",
                            _local() / "Microsoft/Windows/WER/ReportQueue"]},
    }
    if WINDOWS:
        cats["recycle"] = {"label": "Lixeira", "desc": "Arquivos que você já apagou e estão na Lixeira.", "paths": []}
    return cats


def _is_link(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attrs & REPARSE)
    except OSError:
        return True


def _walk(root: Path, cutoff: float):
    """Arquivos apagáveis sob root: sem seguir links/junções, só os modificados antes de cutoff."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if _is_link(e):
                        continue
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(Path(e.path))
                        elif e.is_file(follow_symlinks=False):
                            st = e.stat(follow_symlinks=False)
                            if st.st_mtime < cutoff:
                                yield Path(e.path), st.st_size
                    except OSError:
                        continue
        except OSError:
            continue


def _recycle_bin() -> tuple[int, int]:
    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]

    info = SHQUERYRBINFO(ctypes.sizeof(SHQUERYRBINFO), 0, 0)
    shell32 = ctypes.WinDLL("shell32")
    shell32.SHQueryRecycleBinW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(SHQUERYRBINFO)]
    if shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
        return 0, 0
    return int(info.i64Size), int(info.i64NumItems)


def scan(now: float | None = None) -> dict:
    cutoff = (now or time.time()) - MIN_AGE
    out = []
    for cid, c in categories().items():
        if cid == "recycle":
            size, count = _recycle_bin()
        else:
            size = count = 0
            for p in c["paths"]:
                for _, s in _walk(p, cutoff):
                    size += s
                    count += 1
        out.append({"id": cid, "label": c["label"], "desc": c["desc"], "bytes": size, "files": count})
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        if u.total < 2**30:  # partições de sistema/recuperação minúsculas só poluem a lista
            continue
        disks.append({"mount": part.mountpoint, "total": u.total, "free": u.free, "percent": u.percent})
    return {"categories": out, "disks": disks, "total": sum(c["bytes"] for c in out)}


def clean(ids: list[str], now: float | None = None, local: bool = False) -> dict:
    if not local:  # local = o dono no teclado do próprio PC (assistente no terminal)
        _allowed()
    cats = categories()
    unknown = [i for i in ids if i not in cats]
    if unknown or not ids:
        raise MaintenanceRefused(f"Categoria inválida: {', '.join(unknown) or '(nenhuma)'}")
    cutoff = (now or time.time()) - MIN_AGE
    freed = files = skipped = 0
    for cid in ids:
        if cid == "recycle":
            size, count = _recycle_bin()
            shell32 = ctypes.WinDLL("shell32")
            shell32.SHEmptyRecycleBinW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
            # 0x7 = sem confirmação, sem barra de progresso, sem som (a confirmação foi feita no painel)
            if shell32.SHEmptyRecycleBinW(None, None, 0x7) in (0, -2147418113):  # S_OK ou "já vazia"
                freed += size
                files += count
            continue
        for root in cats[cid]["paths"]:
            root_real = os.path.normcase(os.path.realpath(root))
            for path, size in list(_walk(root, cutoff)):
                # defesa extra: o arquivo tem de continuar dentro da pasta da categoria
                if not os.path.normcase(os.path.realpath(path)).startswith(root_real + os.sep):
                    skipped += 1
                    continue
                try:
                    os.chmod(path, stat.S_IWRITE)
                    os.remove(path)
                    freed += size
                    files += 1
                except OSError:  # em uso por algum programa: fica para a próxima
                    skipped += 1
            _remove_empty_dirs(root)
    log.warning("Limpeza pelo painel: %s — %d arquivo(s), %.1f MB liberados, %d em uso", ",".join(ids), files,
                freed / 2**20, skipped)
    return {"freed": freed, "files": files, "skipped": skipped}


def _remove_empty_dirs(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root, topdown=False, followlinks=False):
        if Path(dirpath) == root or filenames or dirnames:
            continue
        try:
            if os.lstat(dirpath).st_mtime < time.time() - MIN_AGE:
                os.rmdir(dirpath)
        except OSError:
            pass


# ================================================================= programas que iniciam com o Windows
RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN32 = r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"


def _approved(hive, sub: str, name: str) -> bool:
    import winreg

    try:
        with winreg.OpenKey(hive, f"{APPROVED}\\{sub}") as k:
            data = winreg.QueryValueEx(k, name)[0]
            return not (isinstance(data, bytes) and data[:1] and data[0] & 1)  # bit 0 ligado = desativado
    except OSError:
        return True


def startup_items() -> list[dict]:
    if not WINDOWS:
        return []
    import winreg

    items = []
    for hive, key, scope, sub in ((winreg.HKEY_CURRENT_USER, RUN, "user", "Run"),
                                  (winreg.HKEY_LOCAL_MACHINE, RUN, "machine", "Run"),
                                  (winreg.HKEY_LOCAL_MACHINE, RUN32, "machine", "Run32")):
        try:
            with winreg.OpenKey(hive, key) as k:
                i = 0
                while True:
                    try:
                        name, cmd, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    i += 1
                    items.append({"id": f"{scope}:{sub}:{name}", "name": name, "command": str(cmd)[:300], "scope": scope,
                                  "enabled": _approved(hive, sub, name), "editable": scope == "user"})
        except OSError:
            continue
    folder = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
    if folder.is_dir():
        for f in folder.iterdir():
            if f.suffix.lower() in (".lnk", ".exe", ".bat", ".cmd", ".url"):
                items.append({"id": f"user:StartupFolder:{f.name}", "name": f.stem, "command": str(f)[:300], "scope": "user",
                              "enabled": _approved(winreg.HKEY_CURRENT_USER, "StartupFolder", f.name), "editable": True})
    return sorted(items, key=lambda x: x["name"].lower())


def set_startup(item_id: str, enabled: bool) -> dict:
    """Ativa/desativa como o Gerenciador de Tarefas (StartupApproved): o programa NÃO é apagado."""
    _allowed()
    if not WINDOWS:
        raise MaintenanceRefused("Disponível apenas no Windows.")
    import winreg

    item = next((i for i in startup_items() if i["id"] == item_id), None)
    if not item:
        raise MaintenanceRefused("Programa de inicialização não encontrado.")
    if not item["editable"]:
        raise MaintenanceRefused("Este item é do sistema (todos os usuários) e só pode ser alterado como administrador no PC.")
    _, sub, name = item_id.split(":", 2)
    if enabled:
        data = b"\x02" + b"\x00" * 11
    else:
        ft = int((time.time() + 11644473600) * 10**7)  # FILETIME do momento (o Gerenciador mostra "desativado em")
        data = b"\x03\x00\x00\x00" + ft.to_bytes(8, "little")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{APPROVED}\\{sub}") as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_BINARY, data)
    log.warning("Inicialização com o Windows %s pelo painel: %s", "ativada" if enabled else "desativada", name)
    return {"id": item_id, "enabled": enabled}


# ================================================================= modo de energia (Windows 10/11)
MODES = {  # GUIDs do "Modo de energia" (overlay) do Windows
    "economia": "961cc777-2547-4f9d-8174-7d86181b8a7a",
    "equilibrado": "00000000-0000-0000-0000-000000000000",
    "desempenho": "ded574b5-45a0-4f42-8737-46345c09c238",
}
MODE_LABELS = {"economia": "Economia de energia", "equilibrado": "Equilibrado", "desempenho": "Melhor desempenho"}


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, s: str) -> "GUID":
        h = s.replace("-", "")
        b = bytes.fromhex(h)
        return cls(int(h[:8], 16), int(h[8:12], 16), int(h[12:16], 16), (ctypes.c_ubyte * 8)(*b[8:]))

    def __str__(self) -> str:
        d4 = bytes(self.Data4).hex()
        return f"{self.Data1:08x}-{self.Data2:04x}-{self.Data3:04x}-{d4[:4]}-{d4[4:]}"


def _powrprof():
    p = ctypes.WinDLL("powrprof")
    p.PowerGetEffectiveOverlayScheme.argtypes = [ctypes.POINTER(GUID)]
    p.PowerGetEffectiveOverlayScheme.restype = ctypes.c_ulong
    p.PowerSetActiveOverlayScheme.argtypes = [GUID]
    p.PowerSetActiveOverlayScheme.restype = ctypes.c_ulong
    return p


def current_mode() -> str | None:
    if not WINDOWS:
        return None
    try:
        g = GUID()
        if _powrprof().PowerGetEffectiveOverlayScheme(ctypes.byref(g)) != 0:
            return None
        return next((k for k, v in MODES.items() if v == str(g)), None)
    except (OSError, AttributeError):  # Windows antigo sem modo de energia
        return None


def set_mode(mode: str) -> str:
    if mode not in MODES:
        raise MaintenanceRefused("Modo de energia inválido.")
    if not WINDOWS:
        raise MaintenanceRefused("Disponível apenas no Windows.")
    try:
        r = _powrprof().PowerSetActiveOverlayScheme(GUID.parse(MODES[mode]))
    except (OSError, AttributeError):
        raise MaintenanceRefused("Este Windows não tem o controle de modo de energia.") from None
    if r != 0:
        raise MaintenanceRefused(f"O Windows recusou o modo de energia (código {r}). Use o plano 'Equilibrado'.")
    return mode


def power_plans() -> list[dict]:
    if not WINDOWS:
        return []
    from .power import system_exe

    cp = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    r = subprocess.run([system_exe("powercfg"), "/list"], capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    text = r.stdout.decode(cp, errors="replace")
    return [{"guid": m.group(1), "name": m.group(2).strip(), "active": bool(m.group(3))}
            for m in re.finditer(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s+\(([^)]*)\)\s*(\*)?", text, re.I)]


def set_plan(guid: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", guid or ""):
        raise MaintenanceRefused("Plano inválido.")
    if not any(p["guid"].lower() == guid.lower() for p in power_plans()):
        raise MaintenanceRefused("Plano de energia não encontrado neste PC.")
    from .power import system_exe

    r = subprocess.run([system_exe("powercfg"), "/setactive", guid], capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise MaintenanceRefused("O Windows recusou a troca de plano de energia.")
    return guid


# ================================================================= modo jogo
GAME_DIRS = ("\\steamapps\\common\\", "\\epic games\\", "\\riot games\\", "\\xboxgames\\", "\\ea games\\",
             "\\ubisoft game launcher\\games\\", "\\gog galaxy\\games\\", "\\rockstar games\\")
LAUNCHERS = {"steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "epicwebhelper.exe", "riotclientservices.exe",
             "riotclientux.exe", "riotclientuxrender.exe", "riotclientcrashhandler.exe", "battle.net.exe", "agent.exe",
             "eadesktop.exe", "ealauncher.exe", "upc.exe", "uplay.exe", "galaxyclient.exe", "rockstarservice.exe",
             "launcher.exe", "crashreporter.exe", "unitycrashhandler64.exe", "vgc.exe", "vgtray.exe", "socialclubhelper.exe",
             # ferramentas instaladas pela Steam que ficam abertas o tempo todo (não são jogos)
             "wallpaper32.exe", "wallpaper64.exe", "webwallpaper32.exe", "ui32.exe", "soundpad.exe"}


def find_game(extra_names: list[str]) -> str | None:
    """Um jogo em execução: pasta de jogos das lojas (Steam, Epic, Riot, Xbox...) ou nome escolhido pelo dono."""
    extra = {n.lower() if n.lower().endswith(".exe") else f"{n.lower()}.exe" for n in extra_names if n}
    for p in psutil.process_iter(["name", "exe"]):
        name = (p.info.get("name") or "").lower()
        if not name or name in LAUNCHERS:
            continue
        if name in extra or name.removesuffix(".exe") in {e.removesuffix(".exe") for e in extra}:
            return p.info["name"]
        exe = (p.info.get("exe") or "").lower()
        if exe and any(d in exe for d in GAME_DIRS):
            return p.info["name"]
    return None


class GameMode:
    """Verifica a cada poucos segundos se um jogo abriu ou fechou e troca o modo de energia."""

    def __init__(self, emit: Callable[[dict], None] | None = None, interval: float = 5.0,
                 finder: Callable[[list[str]], str | None] = find_game,
                 get_mode: Callable[[], str | None] = current_mode, apply_mode: Callable[[str], str] = set_mode):
        self.emit = emit
        self.interval = interval
        self.finder, self.get_mode, self.apply_mode = finder, get_mode, apply_mode
        self.game: str | None = None
        self.previous: str | None = None
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="game-mode", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.end()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:
                log.debug("modo jogo: falha na verificação", exc_info=True)

    def tick(self) -> None:
        from .config import AgentConfig

        cfg = AgentConfig.load()
        if not cfg.game_mode_auto:
            self.end()
            return
        game = self.finder(cfg.game_processes)
        if game and not self.game:
            self.begin(game)
        elif not game and self.game:
            self.end()

    def begin(self, game: str) -> None:
        self.game = game
        self.previous = self.get_mode() or "equilibrado"
        try:
            self.apply_mode("desempenho")
            self.error = None
            log.info("Modo jogo ATIVADO (%s): Melhor desempenho", game)
        except MaintenanceRefused as exc:
            self.error = str(exc)
            log.warning("Modo jogo: %s", exc)
        self._notify("started")

    def end(self) -> None:
        if not self.game:
            return
        game, self.game = self.game, None
        if not self.error and self.previous and self.previous != "desempenho":
            try:
                self.apply_mode(self.previous)
            except MaintenanceRefused as exc:
                log.warning("Modo jogo: não voltou ao modo anterior: %s", exc)
        log.info("Modo jogo DESATIVADO (%s fechado): %s", game, MODE_LABELS.get(self.previous or "", self.previous))
        self._notify("stopped", game)

    def _notify(self, event: str, game: str | None = None) -> None:
        if self.emit:
            try:
                self.emit({"type": "game_event", "event": event, "game": game or self.game, "error": self.error,
                           "mode": "desempenho" if event == "started" else self.previous})
            except Exception:
                pass

    def state(self) -> dict:
        from .config import AgentConfig

        cfg = AgentConfig.load()
        return {"auto": cfg.game_mode_auto, "processes": cfg.game_processes, "active": bool(self.game), "game": self.game,
                "error": self.error}


def perf_status(game: GameMode) -> dict:
    mode = current_mode()
    return {"mode": mode, "modes": [{"id": k, "label": v} for k, v in MODE_LABELS.items()],
            "plans": power_plans(), "game": game.state(), "windows": WINDOWS}


def configure_game(params: dict, local: bool = False) -> dict:
    if not local:
        _allowed()
    from .config import AgentConfig

    cfg = AgentConfig.load()
    if "auto" in params:
        cfg.game_mode_auto = bool(params["auto"])
    if "processes" in params:
        names = [re.sub(r"[^\w .\-]", "", str(n)).strip()[:64] for n in params["processes"] or []]
        cfg.game_processes = sorted({n for n in names if n})[:30]
    cfg.save()
    return {"auto": cfg.game_mode_auto, "processes": cfg.game_processes}

