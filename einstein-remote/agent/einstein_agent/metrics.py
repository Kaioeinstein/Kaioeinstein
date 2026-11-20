"""Coleta de métricas reais do sistema (psutil + nvidia-smi quando disponível).

Nada aqui é simulado: se um sensor não existe no hardware/SO, o valor é
``None`` e o dashboard exibe "indisponível".
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import time

import psutil

from . import __version__


def _local_ips() -> list[str]:
    ips: list[str] = []
    stats = psutil.net_if_stats()
    for name, addrs in psutil.net_if_addrs().items():
        if name in stats and not stats[name].isup:
            continue
        for a in addrs:
            if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                ips.append(a.address)
    return ips


def _primary_ip() -> str | None:
    """IP da interface usada para a rota padrão (não envia pacotes)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        ips = _local_ips()
        return ips[0] if ips else None


def _os_name() -> str:
    if sys.platform == "win32":
        release, version, _, _ = platform.win32_ver()
        try:
            build = int(version.split(".")[-1])
            if release == "10" and build >= 22000:
                release = "11"
        except ValueError:
            pass
        edition = platform.win32_edition() if hasattr(platform, "win32_edition") else ""
        return f"Windows {release} {edition or ''}".strip() + f" (build {version})"
    return f"{platform.system()} {platform.release()}"


def _cpu_model() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    elif os.path.exists("/proc/cpuinfo"):
        with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    return platform.processor() or "Desconhecido"


def _mac_for(ip: str | None) -> str | None:
    """MAC da placa que tem o IP principal (usado para Wake-on-LAN)."""
    link = getattr(psutil, "AF_LINK", None)
    if not ip or link is None:
        return None
    for addrs in psutil.net_if_addrs().values():
        if any(a.family == socket.AF_INET and a.address == ip for a in addrs):
            mac = next((a.address for a in addrs if a.family == link), None)
            return mac.replace("-", ":").lower() if mac else None
    return None


def system_info() -> dict:
    vm = psutil.virtual_memory()
    ip = _primary_ip()
    return {
        "hostname": socket.gethostname(),
        "os": _os_name(),
        "platform": sys.platform,
        "arch": platform.machine(),
        "cpu_model": _cpu_model(),
        "cpu_cores": psutil.cpu_count(logical=False),
        "cpu_threads": psutil.cpu_count(logical=True),
        "ram_total": vm.total,
        "boot_time": psutil.boot_time(),
        "ip": ip,
        "mac": _mac_for(ip),
        "ip_addresses": _local_ips(),
        "agent_version": __version__,
        "python": platform.python_version(),
    }


class MetricsCollector:
    def __init__(self) -> None:
        self._last_net = psutil.net_io_counters()
        self._last_ts = time.time()
        self._gpu_cache: list[dict] = []
        self._gpu_ts = 0.0
        self._nvidia_smi = shutil.which("nvidia-smi")
        self._win = None  # contadores do Windows (GPU de qualquer fabricante, zonas térmicas)
        self._win_gpu_names: list[dict] = []
        if sys.platform == "win32":
            from . import win_perf
            from .sysmon import _windows_gpus

            self._win = win_perf.create()
            try:
                self._win_gpu_names = [g for g in _windows_gpus() if "basic" not in g["name"].lower()]
            except Exception:
                self._win_gpu_names = []
        self._system_drive = (os.environ.get("SystemDrive", "C:") + "\\") if sys.platform == "win32" else "/"
        psutil.cpu_percent(None)  # primeira leitura descartada (referência)
        psutil.cpu_percent(None, percpu=True)

    def _gpu(self) -> list[dict]:
        if not self._nvidia_smi:
            return []
        if time.time() - self._gpu_ts < 4:
            return self._gpu_cache
        self._gpu_ts = time.time()
        try:
            out = subprocess.run(
                [
                    self._nvidia_smi,
                    "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
            gpus = []
            for line in out.strip().splitlines():
                name, util, used, total, temp = [p.strip() for p in line.split(",")]
                gpus.append(
                    {
                        "name": name,
                        "util": float(util),
                        "mem_used": float(used) * 1024 * 1024,
                        "mem_total": float(total) * 1024 * 1024,
                        "temp": float(temp),
                    }
                )
            self._gpu_cache = gpus
        except (OSError, ValueError, subprocess.SubprocessError):
            self._gpu_cache = []
        return self._gpu_cache

    def _temperatures(self, gpus: list[dict], extra: list[dict] | None = None) -> dict:
        sensors: list[dict] = list(extra or [])
        fn = getattr(psutil, "sensors_temperatures", None)  # não existe no Windows
        if fn:
            try:
                for chip, entries in fn().items():
                    for e in entries:
                        if e.current and e.current > 0:
                            sensors.append({"label": f"{chip} {e.label}".strip(), "current": e.current})
            except Exception:
                pass
        for g in gpus:
            if g.get("temp") is not None:  # GPU sem sensor de temperatura (ex.: dados do PDH)
                sensors.append({"label": f"GPU {g['name']}", "current": g["temp"]})
        return {"max": max((s["current"] for s in sensors), default=None), "sensors": sensors[:16]}

    def _disks(self) -> list[dict]:
        disks = []
        for p in psutil.disk_partitions(all=False):
            if sys.platform == "win32" and ("cdrom" in p.opts or not p.fstype):
                continue
            if sys.platform != "win32" and (p.fstype in ("squashfs", "tmpfs", "overlay") or p.mountpoint.startswith("/snap")):
                continue
            try:
                u = psutil.disk_usage(p.mountpoint)
            except (PermissionError, OSError):
                continue
            disks.append(
                {
                    "mount": p.mountpoint,
                    "fstype": p.fstype,
                    "total": u.total,
                    "used": u.used,
                    "free": u.free,
                    "percent": u.percent,
                    "system": p.mountpoint.rstrip("\\/").upper() == self._system_drive.rstrip("\\/").upper()
                    or p.mountpoint == self._system_drive,
                }
            )
        if not disks:  # ambientes sem partições listadas (ex.: contêineres)
            try:
                u = psutil.disk_usage(self._system_drive)
                disks.append(
                    {"mount": self._system_drive, "fstype": "", "total": u.total, "used": u.used, "free": u.free,
                     "percent": u.percent, "system": True}
                )
            except OSError:
                pass
        return disks

    def collect(self) -> dict:
        now = time.time()
        net = psutil.net_io_counters()
        dt = max(now - self._last_ts, 1e-3)
        down = max(net.bytes_recv - self._last_net.bytes_recv, 0) / dt
        up = max(net.bytes_sent - self._last_net.bytes_sent, 0) / dt
        self._last_net, self._last_ts = net, now

        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        freq = psutil.cpu_freq()
        gpus = self._gpu()
        extra_temps: list[dict] = []
        if self._win:
            try:
                win = self._win.sample(self._win_gpu_names)
                extra_temps = win["sensors"]
                if not gpus:  # sem nvidia-smi: AMD, Intel ou NVIDIA sem a ferramenta
                    gpus = win["gpus"]
            except Exception:
                pass
        return {
            "ts": now,
            "cpu": {
                "percent": psutil.cpu_percent(None),
                "per_core": psutil.cpu_percent(None, percpu=True),
                "freq_mhz": round(freq.current) if freq else None,
            },
            "memory": {
                "total": vm.total,
                "used": vm.total - vm.available,
                "available": vm.available,
                "percent": vm.percent,
                "swap_percent": sw.percent,
            },
            "disks": self._disks(),
            "network": {"down_bps": down, "up_bps": up, "total_recv": net.bytes_recv, "total_sent": net.bytes_sent},
            "gpu": gpus,
            "temperatures": self._temperatures(gpus, extra_temps),
            "uptime": now - psutil.boot_time(),
            "process_count": len(psutil.pids()),
        }
