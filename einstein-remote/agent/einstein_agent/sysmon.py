"""SYSTEM MONITOR: processos, aplicativos abertos, serviços, hardware e rede.

Tudo é lido do sistema real (psutil, APIs do Windows, registro). Quando algo não
existe no sistema atual, a resposta traz ``"supported": False`` em vez de dados
inventados.
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

WINDOWS = sys.platform == "win32"
CPU_COUNT = psutil.cpu_count(logical=True) or 1

# Processos que nunca podem ser encerrados remotamente (derrubariam o Windows ou o próprio agente).
PROTECTED_NAMES = {
    "system", "system idle process", "registry", "memory compression", "secure system",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "lsaiso.exe",
    "svchost.exe", "fontdrvhost.exe", "dwm.exe", "sihost.exe", "ctfmon.exe", "explorer.exe",
    "msmpeng.exe", "securityhealthservice.exe", "spoolsv.exe", "wudfhost.exe",
    "systemd", "init", "kthreadd", "launchd", "kernel_task",
}
PROTECTED_PIDS = {0, 1, 4}


# ----------------------------------------------------------------- processos
class ProcessSampler:
    """Mantém objetos Process entre leituras para medir CPU por processo como o Gerenciador de Tarefas."""

    def __init__(self) -> None:
        self._procs: dict[int, psutil.Process] = {}
        self._lock = threading.Lock()
        self._last = 0.0

    def _refresh(self) -> list[psutil.Process]:
        alive = []
        for p in psutil.process_iter():
            cached = self._procs.get(p.pid)
            try:
                if cached is None or cached.create_time() != p.create_time():
                    cached = p
                    cached.cpu_percent(None)  # referência inicial
                    self._procs[p.pid] = cached
                alive.append(cached)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        live_pids = {p.pid for p in alive}
        for pid in list(self._procs):
            if pid not in live_pids:
                del self._procs[pid]
        return alive

    def snapshot(self) -> dict:
        with self._lock:
            first = not self._procs or time.time() - self._last > 60
            procs = self._refresh()
            if first:  # primeira leitura: mede num intervalo curto para não devolver tudo 0 %
                time.sleep(0.5)
            self._last = time.time()
            total_mem = psutil.virtual_memory().total
            rows = []
            me = os.getpid()
            for p in procs:
                try:
                    with p.oneshot():
                        cpu = p.cpu_percent(None) / CPU_COUNT
                        mem = p.memory_info()
                        name = p.name()
                        try:
                            user = p.username()
                        except (psutil.AccessDenied, KeyError):
                            user = None
                        rows.append({
                            "pid": p.pid,
                            "name": name,
                            "user": user.split("\\")[-1] if user else None,
                            "cpu": round(cpu, 1),
                            "memory": mem.rss,
                            "memory_percent": round(mem.rss / total_mem * 100, 2) if total_mem else None,
                            "status": p.status(),
                            "threads": p.num_threads(),
                            "create_time": p.create_time(),
                            "protected": is_protected(p.pid, name) or p.pid == me,
                        })
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            rows.sort(key=lambda r: (r["cpu"], r["memory"]), reverse=True)
            return {"ts": time.time(), "cpu_count": CPU_COUNT, "count": len(rows), "processes": rows}


SAMPLER = ProcessSampler()


def is_protected(pid: int, name: str) -> bool:
    return pid in PROTECTED_PIDS or (name or "").lower() in PROTECTED_NAMES


class KillRefused(Exception):
    pass


def kill_process(pid: int, name: str, create_time: float, allowed: bool, timeout: float = 5.0) -> dict:
    """Encerra um processo SOMENTE se nome e hora de criação baterem (evita PID reutilizado)."""
    if not allowed:
        raise KillRefused("O proprietário desativou neste computador o encerramento remoto de processos.")
    try:
        pid = int(pid)
        create_time = float(create_time)
    except (TypeError, ValueError):
        raise KillRefused("Parâmetros inválidos.") from None
    if pid == os.getpid() or pid == os.getppid():
        raise KillRefused("O próprio agente não pode ser encerrado remotamente (use Sair na bandeja).")
    try:
        proc = psutil.Process(pid)
        real_name = proc.name()
        real_ct = proc.create_time()
    except psutil.NoSuchProcess:
        raise KillRefused("O processo não existe mais.") from None
    except psutil.AccessDenied:
        raise KillRefused("Acesso negado a este processo.") from None
    if is_protected(pid, real_name):
        raise KillRefused(f"'{real_name}' é um processo essencial do sistema e não pode ser encerrado.")
    if real_name != name or abs(real_ct - create_time) > 0.01:
        raise KillRefused("O PID agora pertence a outro processo; atualize a lista e tente de novo.")
    try:
        proc.terminate()
        try:
            proc.wait(timeout)
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(3)
    except psutil.NoSuchProcess:
        pass
    except psutil.AccessDenied:
        raise KillRefused("Acesso negado: o processo pertence a outro usuário ou ao sistema.") from None
    except psutil.TimeoutExpired:
        raise KillRefused("O processo não respondeu ao encerramento.") from None
    return {"pid": pid, "name": real_name, "terminated": True}


# ----------------------------------------------------------------- aplicativos abertos
def _windows_apps() -> list[dict]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    DWMWA_CLOAKED = 14
    windows: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        cloaked = wintypes.DWORD()
        if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)) == 0 and cloaked.value:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        windows.append((pid.value, buf.value))
        return True

    user32.EnumWindows(callback, 0)
    return [{"pid": pid, "title": title} for pid, title in windows]


def _linux_apps() -> list[dict] | None:
    if not shutil.which("wmctrl") or not os.environ.get("DISPLAY"):
        return None
    out = subprocess.run(["wmctrl", "-lp"], capture_output=True, text=True, timeout=5).stdout
    apps = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 5 and parts[2].isdigit():
            apps.append({"pid": int(parts[2]), "title": parts[4]})
    return apps


def open_apps() -> dict:
    windows = _windows_apps() if WINDOWS else _linux_apps()
    if windows is None:
        return {"supported": False, "apps": [], "reason": "Listagem de janelas disponível no Windows."}
    stats = {r["pid"]: r for r in SAMPLER.snapshot()["processes"]}
    grouped: dict[int, dict] = {}
    for w in windows:
        p = stats.get(w["pid"])
        app = grouped.setdefault(w["pid"], {
            "pid": w["pid"],
            "name": p["name"] if p else None,
            "cpu": p["cpu"] if p else None,
            "memory": p["memory"] if p else None,
            "create_time": p["create_time"] if p else None,
            "protected": p["protected"] if p else True,
            "windows": [],
        })
        app["windows"].append(w["title"])
    apps = sorted(grouped.values(), key=lambda a: (a["memory"] or 0), reverse=True)
    return {"supported": True, "apps": apps}


# ----------------------------------------------------------------- serviços
def services() -> dict:
    if WINDOWS:
        rows = []
        for s in psutil.win_service_iter():
            try:
                rows.append({
                    "name": s.name(),
                    "display_name": s.display_name(),
                    "status": s.status(),
                    "start_type": s.start_type(),
                    "pid": s.pid(),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        rows.sort(key=lambda r: r["display_name"].lower())
        return {"supported": True, "services": rows}
    if shutil.which("systemctl"):
        r = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain", "--no-pager"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            rows = []
            for line in r.stdout.splitlines():
                parts = line.split(None, 4)
                if len(parts) >= 4:
                    rows.append({
                        "name": parts[0], "display_name": parts[4] if len(parts) > 4 else parts[0],
                        "status": parts[3], "start_type": None, "pid": None,
                    })
            return {"supported": True, "services": rows}
    return {"supported": False, "services": [], "reason": "Gerenciador de serviços não disponível neste sistema."}


# ----------------------------------------------------------------- hardware
def _reg_values(path: str, names: list[str]) -> dict:
    import winreg

    out = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
            for n in names:
                try:
                    out[n] = winreg.QueryValueEx(k, n)[0]
                except OSError:
                    out[n] = None
    except OSError:
        pass
    return out


def _windows_gpus() -> list[dict]:
    import winreg

    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    gpus = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            for i in range(64):
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                if not sub.isdigit():
                    continue
                v = _reg_values(base + "\\" + sub, ["DriverDesc", "DriverVersion", "HardwareInformation.qwMemorySize",
                                                    "HardwareInformation.MemorySize"])
                if not v.get("DriverDesc"):
                    continue
                mem = v.get("HardwareInformation.qwMemorySize") or v.get("HardwareInformation.MemorySize")
                if isinstance(mem, bytes):
                    mem = int.from_bytes(mem[:8], "little")
                gpus.append({"name": v["DriverDesc"], "driver": v.get("DriverVersion"), "memory": mem if isinstance(mem, int) else None})
    except OSError:
        pass
    return gpus


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip() or None
    except OSError:
        return None


def hardware() -> dict:
    from .metrics import _cpu_model, _os_name

    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    freq = psutil.cpu_freq()
    if WINDOWS:
        bios = _reg_values(r"HARDWARE\DESCRIPTION\System\BIOS", [
            "SystemManufacturer", "SystemProductName", "BaseBoardManufacturer", "BaseBoardProduct",
            "BIOSVendor", "BIOSVersion", "BIOSReleaseDate"])
        system = {
            "manufacturer": bios.get("SystemManufacturer"), "model": bios.get("SystemProductName"),
            "board": " ".join(x for x in (bios.get("BaseBoardManufacturer"), bios.get("BaseBoardProduct")) if x) or None,
            "bios": " ".join(x for x in (bios.get("BIOSVendor"), bios.get("BIOSVersion")) if x) or None,
            "bios_date": bios.get("BIOSReleaseDate"),
        }
        gpus = _windows_gpus()
    else:
        dmi = "/sys/class/dmi/id/"
        system = {
            "manufacturer": _read(dmi + "sys_vendor"), "model": _read(dmi + "product_name"),
            "board": " ".join(x for x in (_read(dmi + "board_vendor"), _read(dmi + "board_name")) if x) or None,
            "bios": " ".join(x for x in (_read(dmi + "bios_vendor"), _read(dmi + "bios_version")) if x) or None,
            "bios_date": _read(dmi + "bios_date"),
        }
        gpus = []
    disks = []
    for p in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(p.mountpoint)
        except (PermissionError, OSError):
            continue
        disks.append({"device": p.device, "mount": p.mountpoint, "fstype": p.fstype, "total": u.total, "free": u.free})
    battery = None
    try:
        b = psutil.sensors_battery()
        if b:
            battery = {"percent": b.percent, "plugged": b.power_plugged,
                       "secs_left": b.secsleft if b.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) else None}
    except Exception:
        pass
    return {
        "system": system,
        "os": {"name": _os_name(), "arch": platform.machine(), "hostname": socket.gethostname(), "boot_time": psutil.boot_time()},
        "cpu": {"model": _cpu_model(), "cores": psutil.cpu_count(logical=False), "threads": CPU_COUNT,
                "max_mhz": round(freq.max) if freq and freq.max else None},
        "memory": {"total": vm.total, "swap_total": sw.total},
        "gpus": gpus,
        "disks": disks,
        "battery": battery,
    }


# ----------------------------------------------------------------- rede
def network() -> dict:
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    io = psutil.net_io_counters(pernic=True)
    link = getattr(psutil, "AF_LINK", None)
    interfaces = []
    for name, entries in addrs.items():
        st = stats.get(name)
        c = io.get(name)
        ipv4 = [a.address for a in entries if a.family == socket.AF_INET]
        ipv6 = [a.address.split("%")[0] for a in entries if a.family == socket.AF_INET6]
        mac = next((a.address for a in entries if link is not None and a.family == link), None)
        interfaces.append({
            "name": name,
            "up": bool(st and st.isup),
            "speed_mbps": st.speed if st and st.speed else None,
            "mtu": st.mtu if st else None,
            "ipv4": ipv4, "ipv6": ipv6, "mac": mac,
            "bytes_sent": c.bytes_sent if c else None, "bytes_recv": c.bytes_recv if c else None,
            "loopback": any(ip.startswith("127.") for ip in ipv4) or name.lower().startswith(("lo", "loopback")),
        })
    interfaces.sort(key=lambda i: (not i["up"], i["loopback"], i["name"]))
    connections = None
    try:
        counts: dict[str, int] = {}
        for c in psutil.net_connections(kind="inet"):
            counts[c.status] = counts.get(c.status, 0) + 1
        connections = counts
    except (psutil.AccessDenied, OSError):
        pass
    return {"interfaces": interfaces, "connections": connections}
