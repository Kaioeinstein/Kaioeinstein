"""Injeção de mouse e teclado (somente durante uma sessão de controle autorizada).

* Windows: ``SendInput`` (user32), coordenadas absolutas na área de trabalho virtual.
* Linux/X11: extensão XTEST (python-xlib) — usada também nos testes sob Xvfb.

Eventos aceitos (vindos do painel, já validados pelo servidor):
    {"t": "move",   "x": 0..1, "y": 0..1}
    {"t": "button", "b": "left|right|middle", "down": bool, "x": 0..1, "y": 0..1}
    {"t": "wheel",  "dy": int, "dx": int}          # múltiplos de 120 = um "clique" da roda
    {"t": "key",    "code": "KeyA", "down": bool}  # KeyboardEvent.code do navegador
    {"t": "text",   "text": "olá"}                 # digitação Unicode (teclado do celular)
x/y são relativos ao monitor transmitido.
"""
from __future__ import annotations

import logging
import sys
import threading
import time

log = logging.getLogger("einstein.input")
WINDOWS = sys.platform == "win32"

# KeyboardEvent.code → (Windows VK, keysym X11)
KEYMAP: dict[str, tuple[int, str]] = {
    **{f"Key{c}": (ord(c), c.lower()) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    **{f"Digit{d}": (ord(str(d)), str(d)) for d in range(10)},
    **{f"F{i}": (0x6F + i, f"F{i}") for i in range(1, 13)},
    **{f"Numpad{d}": (0x60 + d, f"KP_{d}") for d in range(10)},
    "Enter": (0x0D, "Return"), "NumpadEnter": (0x0D, "KP_Enter"), "Escape": (0x1B, "Escape"),
    "Backspace": (0x08, "BackSpace"), "Tab": (0x09, "Tab"), "Space": (0x20, "space"),
    "ShiftLeft": (0xA0, "Shift_L"), "ShiftRight": (0xA1, "Shift_R"),
    "ControlLeft": (0xA2, "Control_L"), "ControlRight": (0xA3, "Control_R"),
    "AltLeft": (0xA4, "Alt_L"), "AltRight": (0xA5, "ISO_Level3_Shift"),
    "MetaLeft": (0x5B, "Super_L"), "MetaRight": (0x5C, "Super_R"), "ContextMenu": (0x5D, "Menu"),
    "CapsLock": (0x14, "Caps_Lock"), "NumLock": (0x90, "Num_Lock"), "ScrollLock": (0x91, "Scroll_Lock"),
    "ArrowLeft": (0x25, "Left"), "ArrowUp": (0x26, "Up"), "ArrowRight": (0x27, "Right"), "ArrowDown": (0x28, "Down"),
    "Home": (0x24, "Home"), "End": (0x23, "End"), "PageUp": (0x21, "Prior"), "PageDown": (0x22, "Next"),
    "Insert": (0x2D, "Insert"), "Delete": (0x2E, "Delete"), "PrintScreen": (0x2C, "Print"), "Pause": (0x13, "Pause"),
    "Minus": (0xBD, "minus"), "Equal": (0xBB, "equal"), "BracketLeft": (0xDB, "bracketleft"),
    "BracketRight": (0xDD, "bracketright"), "Backslash": (0xDC, "backslash"), "Semicolon": (0xBA, "semicolon"),
    "Quote": (0xDE, "apostrophe"), "Backquote": (0xC0, "grave"), "Comma": (0xBC, "comma"),
    "Period": (0xBE, "period"), "Slash": (0xBF, "slash"), "IntlBackslash": (0xE2, "less"), "IntlRo": (0xC1, "slash"),
    "NumpadAdd": (0x6B, "KP_Add"), "NumpadSubtract": (0x6D, "KP_Subtract"), "NumpadMultiply": (0x6A, "KP_Multiply"),
    "NumpadDivide": (0x6F, "KP_Divide"), "NumpadDecimal": (0x6E, "KP_Decimal"),
    "AudioVolumeMute": (0xAD, "XF86AudioMute"), "AudioVolumeDown": (0xAE, "XF86AudioLowerVolume"),
    "AudioVolumeUp": (0xAF, "XF86AudioRaiseVolume"), "MediaPlayPause": (0xB3, "XF86AudioPlay"),
}
# teclas que precisam da flag "estendida" no Windows
_EXTENDED = {"ControlRight", "AltRight", "ArrowLeft", "ArrowUp", "ArrowRight", "ArrowDown", "Home", "End", "PageUp",
             "PageDown", "Insert", "Delete", "NumpadEnter", "NumpadDivide", "MetaLeft", "MetaRight", "ContextMenu",
             "PrintScreen"}
BUTTONS = {"left", "right", "middle"}
MAX_TEXT = 500


class InputError(Exception):
    pass


def validate(ev: dict) -> dict:
    """Normaliza um evento; levanta InputError se for inválido."""
    if not isinstance(ev, dict):
        raise InputError("evento inválido")
    t = ev.get("t")

    def coord(k):
        v = ev.get(k)
        if not isinstance(v, (int, float)) or v != v:
            raise InputError(f"coordenada {k} inválida")
        return min(1.0, max(0.0, float(v)))

    if t == "move":
        return {"t": t, "x": coord("x"), "y": coord("y")}
    if t == "button":
        if ev.get("b") not in BUTTONS:
            raise InputError("botão inválido")
        out = {"t": t, "b": ev["b"], "down": bool(ev.get("down"))}
        if "x" in ev and "y" in ev:
            out.update(x=coord("x"), y=coord("y"))
        return out
    if t == "wheel":
        dy, dx = ev.get("dy", 0), ev.get("dx", 0)
        if not all(isinstance(v, (int, float)) for v in (dy, dx)):
            raise InputError("rolagem inválida")
        return {"t": t, "dy": int(max(-2400, min(2400, dy))), "dx": int(max(-2400, min(2400, dx)))}
    if t == "key":
        if ev.get("code") not in KEYMAP:
            raise InputError(f"tecla não suportada: {ev.get('code')!r}")
        return {"t": t, "code": ev["code"], "down": bool(ev.get("down"))}
    if t == "text":
        text = ev.get("text")
        if not isinstance(text, str) or not text:
            raise InputError("texto inválido")
        return {"t": t, "text": text[:MAX_TEXT]}
    raise InputError(f"tipo de evento desconhecido: {t!r}")


class Injector:
    """Interface comum; ``pressed`` guarda teclas/botões pressionados para soltar tudo ao encerrar."""

    def __init__(self) -> None:
        self.pressed_keys: set[str] = set()
        self.pressed_buttons: set[str] = set()
        # Toda chamada à plataforma passa por esta trava: a conexão com o servidor X não pode ser
        # usada por duas threads ao mesmo tempo (uma pode consumir a resposta da outra e travar).
        self.lock = threading.RLock()

    # chamadas pelo controlador --------------------------------------------------
    def apply(self, ev: dict, monitor: dict) -> None:
        with self.lock:
            t = ev["t"]
            if t == "move":
                self.move(*self._abs(ev, monitor))
            elif t == "button":
                if "x" in ev:
                    self.move(*self._abs(ev, monitor))
                self.button(ev["b"], ev["down"])
                (self.pressed_buttons.add if ev["down"] else self.pressed_buttons.discard)(ev["b"])
            elif t == "wheel":
                self.wheel(ev["dy"], ev["dx"])
            elif t == "key":
                self.key(ev["code"], ev["down"])
                (self.pressed_keys.add if ev["down"] else self.pressed_keys.discard)(ev["code"])
            elif t == "text":
                self.text(ev["text"])

    def release_all(self) -> None:
        """Solta tudo que ficou pressionado (ex.: conexão caiu com Ctrl apertado)."""
        with self.lock:
            for code in list(self.pressed_keys):
                try:
                    self.key(code, False)
                except Exception:
                    pass
            for b in list(self.pressed_buttons):
                try:
                    self.button(b, False)
                except Exception:
                    pass
            self.pressed_keys.clear()
            self.pressed_buttons.clear()

    @staticmethod
    def _abs(ev: dict, mon: dict) -> tuple[int, int]:
        x = mon["left"] + round(ev["x"] * (mon["width"] - 1))
        y = mon["top"] + round(ev["y"] * (mon["height"] - 1))
        return x, y

    # implementações por plataforma ---------------------------------------------------
    def cursor(self) -> tuple[int, int] | None:
        with self.lock:
            return self._cursor()

    def move(self, x: int, y: int) -> None: raise NotImplementedError
    def button(self, b: str, down: bool) -> None: raise NotImplementedError
    def wheel(self, dy: int, dx: int) -> None: raise NotImplementedError
    def key(self, code: str, down: bool) -> None: raise NotImplementedError
    def text(self, text: str) -> None: raise NotImplementedError
    def _cursor(self) -> tuple[int, int] | None: return None


class WindowsInjector(Injector):
    def __init__(self) -> None:
        super().__init__()
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.user32 = ctypes.windll.user32
        ULONG_PTR = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        self.MOUSEINPUT, self.KEYBDINPUT, self.INPUT = MOUSEINPUT, KEYBDINPUT, INPUT
        self.user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)

    def _send(self, *inputs) -> None:
        arr = (self.INPUT * len(inputs))(*inputs)
        sent = self.user32.SendInput(len(inputs), arr, self.ctypes.sizeof(self.INPUT))
        if sent != len(inputs):
            # Bloqueado pelo Windows (ex.: janela de UAC/tela segura, ou app executando como administrador)
            raise InputError("O Windows bloqueou a entrada (tela segura ou programa elevado).")

    def _mouse(self, flags: int, dx: int = 0, dy: int = 0, data: int = 0):
        inp = self.INPUT(type=0)
        inp.u.mi = self.MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
        return inp

    def move(self, x: int, y: int) -> None:
        # coordenadas normalizadas 0..65535 na área de trabalho virtual (multi-monitor)
        vx, vy = self.user32.GetSystemMetrics(76), self.user32.GetSystemMetrics(77)
        vw, vh = self.user32.GetSystemMetrics(78), self.user32.GetSystemMetrics(79)
        nx = round((x - vx) * 65535 / max(vw - 1, 1))
        ny = round((y - vy) * 65535 / max(vh - 1, 1))
        MOVE, ABSOLUTE, VIRTUALDESK = 0x0001, 0x8000, 0x4000
        self._send(self._mouse(MOVE | ABSOLUTE | VIRTUALDESK, nx, ny))

    def button(self, b: str, down: bool) -> None:
        flags = {("left", True): 0x0002, ("left", False): 0x0004, ("right", True): 0x0008,
                 ("right", False): 0x0010, ("middle", True): 0x0020, ("middle", False): 0x0040}[(b, down)]
        self._send(self._mouse(flags))

    def wheel(self, dy: int, dx: int) -> None:
        if dy:
            self._send(self._mouse(0x0800, data=-dy))  # navegador: dy>0 = rolar para baixo
        if dx:
            self._send(self._mouse(0x01000, data=dx))

    def _key(self, vk: int, scan: int, flags: int):
        inp = self.INPUT(type=1)
        inp.u.ki = self.KEYBDINPUT(vk, scan, flags, 0, 0)
        return inp

    def key(self, code: str, down: bool) -> None:
        vk = KEYMAP[code][0]
        flags = (0x0001 if code in _EXTENDED else 0) | (0 if down else 0x0002)
        scan = self.user32.MapVirtualKeyW(vk, 0)
        self._send(self._key(vk, scan, flags))

    def text(self, text: str) -> None:
        UNICODE, KEYUP = 0x0004, 0x0002
        events = []
        data = text.encode("utf-16-le")
        for i in range(0, len(data), 2):  # unidades UTF-16 (inclui pares substitutos de emoji)
            unit = int.from_bytes(data[i:i + 2], "little")
            if unit == 0x0A:  # quebra de linha = Enter
                events += [self._key(0x0D, 0, 0), self._key(0x0D, 0, KEYUP)]
                continue
            events += [self._key(0, unit, UNICODE), self._key(0, unit, UNICODE | KEYUP)]
        for i in range(0, len(events), 64):
            self._send(*events[i:i + 64])

    def _cursor(self) -> tuple[int, int] | None:
        from ctypes import wintypes

        pt = wintypes.POINT()
        return (pt.x, pt.y) if self.user32.GetCursorPos(self.ctypes.byref(pt)) else None


class X11Injector(Injector):
    def __init__(self) -> None:
        super().__init__()
        from Xlib import X, XK, display
        from Xlib.ext import xtest

        self.X, self.XK, self.xtest = X, XK, xtest
        self.d = display.Display()
        if not self.d.has_extension("XTEST"):
            raise InputError("Servidor X sem extensão XTEST")
        self.root = self.d.screen().root

    def _sync(self):
        self.d.sync()

    def move(self, x: int, y: int) -> None:
        self.xtest.fake_input(self.d, self.X.MotionNotify, x=x, y=y)
        self._sync()

    def button(self, b: str, down: bool) -> None:
        num = {"left": 1, "middle": 2, "right": 3}[b]
        self.xtest.fake_input(self.d, self.X.ButtonPress if down else self.X.ButtonRelease, num)
        self._sync()

    def wheel(self, dy: int, dx: int) -> None:
        for amount, neg, pos in ((dy, 4, 5), (dx, 6, 7)):
            steps = round(abs(amount) / 120) or (1 if amount else 0)
            for _ in range(steps):
                btn = pos if amount > 0 else neg
                self.xtest.fake_input(self.d, self.X.ButtonPress, btn)
                self.xtest.fake_input(self.d, self.X.ButtonRelease, btn)
        self._sync()

    def _keycode(self, keysym_name: str) -> int:
        ks = self.XK.string_to_keysym(keysym_name)
        kc = self.d.keysym_to_keycode(ks)
        if not kc:
            raise InputError(f"tecla sem mapeamento no X: {keysym_name}")
        return kc

    def key(self, code: str, down: bool) -> None:
        kc = self._keycode(KEYMAP[code][1])
        self.xtest.fake_input(self.d, self.X.KeyPress if down else self.X.KeyRelease, kc)
        self._sync()

    @staticmethod
    def _keysym_for(ch: str) -> int:
        if ch == "\n":
            return 0xFF0D  # Return
        cp = ord(ch)
        if 0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF:
            return cp  # Latin-1: keysym = código do caractere
        return 0x01000000 | cp  # demais Unicode (convenção X11)

    def _scratch_keycode(self) -> int:
        """Tecla sem uso no mapa atual, para mapear temporariamente caracteres ausentes (ex.: 'á')."""
        if getattr(self, "_scratch", None):
            return self._scratch
        first = self.d.display.info.min_keycode
        count = self.d.display.info.max_keycode - first + 1
        mapping = self.d.get_keyboard_mapping(first, count)
        for i in range(count - 1, -1, -1):
            if not any(mapping[i]):
                self._scratch = first + i
                return self._scratch
        raise InputError("Sem tecla livre para digitar caracteres especiais")

    def text(self, text: str) -> None:
        shift = self._keycode("Shift_L")
        for ch in text:
            ks = self._keysym_for(ch)
            kc = self.d.keysym_to_keycode(ks)
            scratch = False
            if not kc:  # caractere sem tecla no layout atual: mapeia numa tecla livre
                kc = self._scratch_keycode()
                self.d.change_keyboard_mapping(kc, [(ks, ks)])
                self.d.sync()
                scratch = True
            needs_shift = not scratch and self.d.keycode_to_keysym(kc, 0) != ks
            if needs_shift:
                self.xtest.fake_input(self.d, self.X.KeyPress, shift)
            self.xtest.fake_input(self.d, self.X.KeyPress, kc)
            self.xtest.fake_input(self.d, self.X.KeyRelease, kc)
            if needs_shift:
                self.xtest.fake_input(self.d, self.X.KeyRelease, shift)
            self.d.sync()
            if scratch:
                time.sleep(0.01)  # o aplicativo precisa ler o mapa antes de restaurá-lo
                self.d.change_keyboard_mapping(kc, [(0, 0)])
                self.d.sync()

    def _cursor(self) -> tuple[int, int] | None:
        p = self.root.query_pointer()
        return p.root_x, p.root_y


def create_injector() -> Injector | None:
    try:
        if WINDOWS:
            return WindowsInjector()
        import os

        if os.environ.get("DISPLAY"):
            return X11Injector()
    except Exception as exc:
        log.warning("Controle de mouse/teclado indisponível: %s", exc)
    return None
