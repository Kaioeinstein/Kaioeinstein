"""Contadores de desempenho do Windows (PDH) — GPU de qualquer fabricante e temperatura.

* GPU: ``\\GPU Engine(*)\\Utilization Percentage`` (Windows 10 1709+, drivers WDDM 2.x —
  NVIDIA, AMD e Intel). Como o Gerenciador de Tarefas: soma os processos por tipo de
  motor (3D, vídeo, cópia…) e mostra o motor mais ocupado. Memória dedicada em uso:
  ``\\GPU Adapter Memory(*)\\Dedicated Usage``.
* Temperatura: ``\\Thermal Zone Information(*)\\Temperature`` (zonas térmicas ACPI, em
  Kelvin; não exige administrador). Se o LibreHardwareMonitor estiver aberto com o
  servidor WMI ativo, usa os sensores dele (CPU/GPU/placa-mãe, mais precisos).

Tudo é opcional: se um contador não existir, o valor fica ``None`` (o painel mostra
"indisponível") — nunca um número inventado.
"""
from __future__ import annotations

import ctypes
import json
import logging
import subprocess
import sys
import threading
import time
from collections import defaultdict

log = logging.getLogger("einstein.winperf")
WINDOWS = sys.platform == "win32"

PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = ctypes.c_long(0x800007D2).value
PDH_CSTATUS_VALID = (0, 1)  # VALID_DATA, NEW_DATA


class _Value(ctypes.Structure):
    # PDH_FMT_COUNTERVALUE: DWORD CStatus + união (double alinhado em 8 bytes)
    _fields_ = [("CStatus", ctypes.c_ulong), ("doubleValue", ctypes.c_double)]


class _Item(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _Value)]


class PdhQuery:
    """Consulta PDH reutilizável. Contadores de taxa precisam de duas coletas (chame collect() a cada ciclo)."""

    def __init__(self) -> None:
        self.pdh = ctypes.WinDLL("pdh.dll")
        self.pdh.PdhOpenQueryW.restype = ctypes.c_long
        self.pdh.PdhAddEnglishCounterW.restype = ctypes.c_long
        self.pdh.PdhCollectQueryData.restype = ctypes.c_long
        self.pdh.PdhGetFormattedCounterArrayW.restype = ctypes.c_long
        self.pdh.PdhGetFormattedCounterValue.restype = ctypes.c_long
        self.handle = ctypes.c_void_p()
        status = self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.handle))
        if status != 0:
            raise OSError(f"PdhOpenQuery falhou ({status:#x})")
        self.counters: dict[str, ctypes.c_void_p] = {}

    def add(self, path: str) -> bool:
        h = ctypes.c_void_p()
        status = self.pdh.PdhAddEnglishCounterW(self.handle, ctypes.c_wchar_p(path), None, ctypes.byref(h))
        if status != 0:
            log.debug("Contador indisponível: %s (%#x)", path, status & 0xFFFFFFFF)
            return False
        self.counters[path] = h
        return True

    def collect(self) -> None:
        self.pdh.PdhCollectQueryData(self.handle)

    def value(self, path: str) -> float | None:
        h = self.counters.get(path)
        if h is None:
            return None
        v = _Value()
        status = self.pdh.PdhGetFormattedCounterValue(h, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100, None, ctypes.byref(v))
        return v.doubleValue if status == 0 and v.CStatus in PDH_CSTATUS_VALID else None

    def array(self, path: str) -> dict[str, float]:
        h = self.counters.get(path)
        if h is None:
            return {}
        size, count = ctypes.c_ulong(0), ctypes.c_ulong(0)
        fmt = PDH_FMT_DOUBLE | PDH_FMT_NOCAP100
        status = self.pdh.PdhGetFormattedCounterArrayW(h, fmt, ctypes.byref(size), ctypes.byref(count), None)
        if status != PDH_MORE_DATA or size.value == 0:
            return {}
        buf = ctypes.create_string_buffer(size.value)
        status = self.pdh.PdhGetFormattedCounterArrayW(h, fmt, ctypes.byref(size), ctypes.byref(count), buf)
        if status != 0:
            return {}
        items = ctypes.cast(buf, ctypes.POINTER(_Item * count.value)).contents
        return {it.szName: it.FmtValue.doubleValue for it in items if it.FmtValue.CStatus in PDH_CSTATUS_VALID}

    def close(self) -> None:
        if self.handle:
            self.pdh.PdhCloseQuery(self.handle)
            self.handle = None


# ----------------------------------------------------------------- cálculos (testáveis em qualquer sistema)
def gpu_usage_from_engines(engines: dict[str, float]) -> dict[str, float]:
    """Instâncias 'pid_1234_luid_0x0_0x1A2B_phys_0_eng_3_engtype_3D' → % por placa (luid).

    Igual ao Gerenciador de Tarefas: soma por (placa, tipo de motor) e pega o maior.
    """
    per = defaultdict(float)
    for name, val in engines.items():
        try:
            luid = name.split("_luid_")[1].split("_phys_")[0]
            engtype = name.split("_engtype_")[1]
        except IndexError:
            continue
        per[(luid, engtype)] += max(0.0, val)
    out: dict[str, float] = {}
    for (luid, _), val in per.items():
        out[luid] = min(100.0, max(out.get(luid, 0.0), val))
    return out


def adapter_memory(usage: dict[str, float]) -> dict[str, float]:
    """Instâncias 'luid_0x0_0x1A2B_phys_0' → bytes de memória dedicada em uso por placa."""
    out = defaultdict(float)
    for name, val in usage.items():
        if "luid_" in name:
            out[name.split("luid_")[1].split("_phys_")[0]] += val
    return dict(out)


def kelvin_to_celsius(k: float) -> float | None:
    if k is None or k <= 0:
        return None
    c = k - 273.15 if k > 200 else k  # algumas zonas já informam em °C
    return round(c, 1) if -20 < c < 150 else None


# ----------------------------------------------------------------- coletor
class WindowsSensors:
    GPU_ENGINE = r"\GPU Engine(*)\Utilization Percentage"
    GPU_MEM = r"\GPU Adapter Memory(*)\Dedicated Usage"
    THERMAL = r"\Thermal Zone Information(*)\Temperature"

    def __init__(self) -> None:
        self.q = PdhQuery()
        self.has_gpu = self.q.add(self.GPU_ENGINE)
        self.has_gpu_mem = self.q.add(self.GPU_MEM)
        self.has_thermal = self.q.add(self.THERMAL)
        self.q.collect()  # primeira amostra de referência
        self._lhm: list[dict] = []
        self._lhm_ok: bool | None = None
        self._lhm_thread: threading.Thread | None = None
        self._lhm_last = 0.0

    def sample(self, gpu_names: list[dict]) -> dict:
        self.q.collect()
        gpus = []
        if self.has_gpu:
            usage = gpu_usage_from_engines(self.q.array(self.GPU_ENGINE))
            mem = adapter_memory(self.q.array(self.GPU_MEM)) if self.has_gpu_mem else {}
            # placas reais primeiro (o adaptador de vídeo básico/remoto costuma ter uso 0)
            ordered = sorted(usage.items(), key=lambda kv: -mem.get(kv[0], 0))
            for i, (luid, util) in enumerate(ordered):
                info = gpu_names[i] if i < len(gpu_names) else {}
                gpus.append({"name": info.get("name") or "GPU", "util": round(util, 1), "mem_used": mem.get(luid),
                             "mem_total": info.get("memory"), "temp": None, "source": "Windows (PDH)"})
        sensors = []
        if self.has_thermal:
            for name, val in self.q.array(self.THERMAL).items():
                c = kelvin_to_celsius(val)
                if c is not None:
                    sensors.append({"label": f"Zona térmica {name.split('.')[-1]}", "current": c})
        self._refresh_lhm()
        sensors.extend(self._lhm)
        return {"gpus": gpus, "sensors": sensors}

    # LibreHardwareMonitor (opcional): consulta lenta, feita em segundo plano a cada 15 s
    def _refresh_lhm(self) -> None:
        if self._lhm_ok is False or time.time() - self._lhm_last < 15:
            return
        if self._lhm_thread and self._lhm_thread.is_alive():
            return
        self._lhm_last = time.time()
        self._lhm_thread = threading.Thread(target=self._query_lhm, daemon=True)
        self._lhm_thread.start()

    def _query_lhm(self) -> None:
        ps = ("Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor -ErrorAction Stop | "
              "Where-Object SensorType -eq 'Temperature' | Select-Object Name, Value, Parent | ConvertTo-Json -Compress")
        try:
            from .power import system_exe

            exe = system_exe("WindowsPowerShell\\v1.0\\powershell")
            r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                               timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0 or not r.stdout.strip():
                if self._lhm_ok is None:
                    log.info("LibreHardwareMonitor não encontrado (opcional, para temperaturas detalhadas).")
                self._lhm_ok = False
                return
            data = json.loads(r.stdout)
            data = data if isinstance(data, list) else [data]
            self._lhm = [{"label": f"{d.get('Name')} (LHM)", "current": round(float(d["Value"]), 1)}
                         for d in data if d.get("Value") is not None and 0 < float(d["Value"]) < 150][:16]
            self._lhm_ok = True
        except (OSError, ValueError, subprocess.SubprocessError):
            self._lhm_ok = False


def create() -> WindowsSensors | None:
    if not WINDOWS:
        return None
    try:
        return WindowsSensors()
    except Exception as exc:
        log.info("Contadores de desempenho do Windows indisponíveis: %s", exc)
        return None
