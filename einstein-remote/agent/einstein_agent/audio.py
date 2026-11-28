"""Volume do PC (alto-falantes/fones padrão do Windows) pelo painel.

Usa a API Core Audio do Windows (IAudioEndpointVolume) por ctypes/COM — o mesmo controle do ícone de som
da barra de tarefas. Sem dependências extras. No Linux (testes e uso opcional) usa o `pactl` do PulseAudio/PipeWire.
O dono pode bloquear no PC: bandeja › Permitir controlar o volume pelo painel.
"""
from __future__ import annotations

import ctypes
import logging
import re
import shutil
import subprocess
import sys

log = logging.getLogger("einstein.audio")
WINDOWS = sys.platform == "win32"


class AudioError(Exception):
    pass


# ================================================================= Windows (Core Audio via COM)
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, s: str) -> "_GUID":
        h = s.replace("-", "")
        b = bytes.fromhex(h)
        return cls(int(h[:8], 16), int(h[8:12], 16), int(h[12:16], 16), (ctypes.c_ubyte * 8)(*b[8:]))


CLSID_MMDeviceEnumerator = "BCDE0395-E52F-467C-8E3D-C4579291692E"
IID_IMMDeviceEnumerator = "A95664D2-9614-4F35-A746-DE8DB63617E6"
IID_IAudioEndpointVolume = "5CDF2C82-841E-4546-9722-0CF74078229A"
E_NOTFOUND = 0x80070490


def _method(obj: ctypes.c_void_p, index: int, restype, *argtypes):
    """Função na posição `index` da vtable da interface COM `obj`."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return lambda *args: proto(vtable[index])(obj, *args)


def _check(hr: int, what: str) -> None:
    hr &= 0xFFFFFFFF
    if hr == E_NOTFOUND:
        raise AudioError("Nenhum alto-falante/fone de ouvido ativo neste PC.")
    if hr & 0x80000000:
        raise AudioError(f"O Windows recusou {what} (código {hr:#010x}).")


class _Endpoint:
    """IAudioEndpointVolume do dispositivo de saída padrão. Use com `with`."""

    def __enter__(self):
        ole32 = ctypes.WinDLL("ole32")
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self._uninit = ole32.CoInitializeEx(None, 0) in (0, 1)  # S_OK / S_FALSE: chamar CoUninitialize no fim
        self._ole32 = ole32
        self.enum = ctypes.c_void_p()
        self.dev = ctypes.c_void_p()
        self.vol = ctypes.c_void_p()
        try:
            ole32.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_ulong,
                                               ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
            _check(ole32.CoCreateInstance(ctypes.byref(_GUID.parse(CLSID_MMDeviceEnumerator)), None, 1,
                                          ctypes.byref(_GUID.parse(IID_IMMDeviceEnumerator)), ctypes.byref(self.enum)),
                   "abrir o áudio")
            # IMMDeviceEnumerator::GetDefaultAudioEndpoint(eRender=0, eMultimedia=1, &device)
            get_default = _method(self.enum, 4, ctypes.c_long, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))
            _check(get_default(0, 1, ctypes.byref(self.dev)), "localizar o dispositivo de som")
            # IMMDevice::Activate(IID_IAudioEndpointVolume, CLSCTX_ALL, NULL, &volume)
            activate = _method(self.dev, 3, ctypes.c_long, ctypes.POINTER(_GUID), ctypes.c_ulong, ctypes.c_void_p,
                               ctypes.POINTER(ctypes.c_void_p))
            _check(activate(ctypes.byref(_GUID.parse(IID_IAudioEndpointVolume)), 0x17, None, ctypes.byref(self.vol)),
                   "abrir o controle de volume")
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        for obj in (self.vol, self.dev, self.enum):
            if obj and obj.value:
                _method(obj, 2, ctypes.c_ulong)()  # IUnknown::Release
        if self._uninit:
            self._ole32.CoUninitialize()

    def get(self) -> dict:
        level = ctypes.c_float()
        _check(_method(self.vol, 9, ctypes.c_long, ctypes.POINTER(ctypes.c_float))(ctypes.byref(level)), "ler o volume")
        mute = ctypes.c_int()
        _check(_method(self.vol, 15, ctypes.c_long, ctypes.POINTER(ctypes.c_int))(ctypes.byref(mute)), "ler o mudo")
        return {"volume": round(level.value * 100), "muted": bool(mute.value)}

    def set_volume(self, percent: int) -> None:
        _check(_method(self.vol, 7, ctypes.c_long, ctypes.c_float, ctypes.c_void_p)(percent / 100.0, None), "mudar o volume")

    def set_mute(self, muted: bool) -> None:
        _check(_method(self.vol, 14, ctypes.c_long, ctypes.c_int, ctypes.c_void_p)(int(muted), None), "silenciar")


# ================================================================= Linux (pactl)
def _pactl(*args: str) -> str:
    exe = shutil.which("pactl")
    if not exe:
        raise AudioError("Controle de volume indisponível (pactl não encontrado).")
    r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=5)  # nosec B603 - argumentos fixos
    if r.returncode != 0:
        raise AudioError((r.stderr or "falha no pactl").strip()[:200])
    return r.stdout


# ================================================================= interface usada pelo agente
def status() -> dict:
    if WINDOWS:
        with _Endpoint() as e:
            return {**e.get(), "available": True}
    vol = re.search(r"(\d+)%", _pactl("get-sink-volume", "@DEFAULT_SINK@"))
    mute = "yes" in _pactl("get-sink-mute", "@DEFAULT_SINK@")
    return {"volume": int(vol.group(1)) if vol else 0, "muted": mute, "available": True}


def _allowed() -> None:
    from .config import AgentConfig

    if not AgentConfig.load().allow_volume:
        raise AudioError("O dono do computador bloqueou o controle de volume pelo painel (menu da bandeja).")


def change(params: dict, local: bool = False) -> dict:
    """{"volume": 0..100} e/ou {"muted": bool} e/ou {"step": -100..100}. local = dono no teclado do próprio PC."""
    if not local:
        _allowed()
    target = params.get("volume")
    step = params.get("step")
    muted = params.get("muted")
    if target is None and step is None and muted is None:
        raise AudioError("Nada para alterar.")
    if WINDOWS:
        with _Endpoint() as e:
            if step is not None:
                target = e.get()["volume"] + int(step)
            if target is not None:
                e.set_volume(max(0, min(100, int(target))))
                if muted is None and int(target) > 0:
                    e.set_mute(False)  # subir o volume tira do mudo, como no Windows
            if muted is not None:
                e.set_mute(bool(muted))
            return {**e.get(), "available": True}
    if step is not None:
        target = status()["volume"] + int(step)
    if target is not None:
        _pactl("set-sink-volume", "@DEFAULT_SINK@", f"{max(0, min(100, int(target)))}%")
        if muted is None and int(target) > 0:
            _pactl("set-sink-mute", "@DEFAULT_SINK@", "0")  # como no Windows: mexer no volume tira do mudo
    if muted is not None:
        _pactl("set-sink-mute", "@DEFAULT_SINK@", "1" if muted else "0")
    return status()
