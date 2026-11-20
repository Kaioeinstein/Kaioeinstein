"""Avisos visíveis no próprio computador durante o acesso à tela.

* **Faixa** sempre no topo: "Tela compartilhada com <usuário>" (+ "controle ativo"),
  com botão **Encerrar** — o dono interrompe a sessão a qualquer momento.
* **Pedido de permissão** (modo "perguntar"): Permitir / Recusar, recusa sozinho em 30 s.

Tudo roda numa única thread dedicada ao Tk (objetos Tk nunca saem dela — ver
pair_window.run_pair_window sobre o crash Tcl_AsyncDelete).
"""
from __future__ import annotations

import concurrent.futures
import logging
import os
import queue
import sys
import threading
from typing import Callable

log = logging.getLogger("einstein.overlay")

BG = "#16081f"
ACCENT = "#a855f7"
RED = "#ff4d6d"
FG = "#ffffff"


def ui_available() -> bool:
    if os.environ.get("EINSTEIN_NO_OVERLAY") == "1":
        return False
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        return False
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return False
    return True


class OverlayUI:
    def __init__(self) -> None:
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._ok = False
        self._banner = None
        self._lock = threading.Lock()

    # -------------------------------------------------------------- thread do Tk
    def _ensure(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self._ok
            if not ui_available():
                return False
            self._ready.clear()
            self._thread = threading.Thread(target=self._run, name="einstein-overlay", daemon=True)
            self._thread.start()
        self._ready.wait(10)
        return self._ok

    def _run(self) -> None:
        import gc
        import tkinter as tk

        try:
            root = tk.Tk()
            root.withdraw()
            self._ok = True
        except Exception as exc:
            log.warning("Interface de avisos indisponível: %s", exc)
            self._ok = False
            self._ready.set()
            return
        self._ready.set()

        def poll():
            while True:
                try:
                    fn, fut = self._jobs.get_nowait()
                except queue.Empty:
                    break
                if fn is None:
                    root.quit()
                    return
                try:
                    fut.set_result(fn(root))
                except Exception as exc:
                    fut.set_exception(exc)
            root.after(50, poll)

        root.after(50, poll)
        root.mainloop()
        self._banner = None
        root.destroy()
        del root
        gc.collect()

    def _call(self, fn: Callable) -> concurrent.futures.Future:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        if not self._ensure():
            fut.set_exception(RuntimeError("interface indisponível"))
            return fut
        self._jobs.put((fn, fut))
        return fut

    def shutdown(self) -> None:
        if self._thread and self._thread.is_alive():
            self._jobs.put((None, None))
            self._thread.join(5)

    # -------------------------------------------------------------- faixa
    def show_banner(self, viewers: list[str], control: bool, on_stop: Callable[[], None]) -> bool:
        text = f"Tela compartilhada com {', '.join(viewers) or 'o painel'}"
        if control:
            text += " · mouse e teclado remotos ATIVOS"

        def build(root):
            import tkinter as tk

            if self._banner is not None:
                self._banner.label.config(text="●  EINSTEIN REMOTE — " + text)
                self._banner.stop_cb = on_stop
                return True
            win = tk.Toplevel(root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            try:
                win.attributes("-alpha", 0.94)
            except tk.TclError:
                pass
            frame = tk.Frame(win, bg=BG, highlightthickness=2, highlightbackground=RED, padx=12, pady=6)
            frame.pack()
            win.label = tk.Label(frame, text="●  EINSTEIN REMOTE — " + text, bg=BG, fg=FG, font=("Segoe UI", 10, "bold"))
            win.label.pack(side="left")
            win.stop_cb = on_stop

            def stop():
                cb = win.stop_cb
                threading.Thread(target=cb, daemon=True).start()  # nunca bloqueia a thread do Tk

            tk.Button(frame, text="Encerrar", command=stop, bg=RED, fg=FG, relief="flat", padx=10,
                      activebackground="#d93a57", activeforeground=FG, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(12, 0))

            # arrastável, para não cobrir algo importante
            def press(e):
                win._drag = (e.x_root - win.winfo_x(), e.y_root - win.winfo_y())

            def drag(e):
                dx, dy = win._drag
                win.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

            for w in (frame, win.label):
                w.bind("<ButtonPress-1>", press)
                w.bind("<B1-Motion>", drag)
            win.update_idletasks()
            win.geometry(f"+{(win.winfo_screenwidth() - win.winfo_width()) // 2}+8")
            self._banner = win
            return True

        try:
            return self._call(build).result(10)
        except Exception as exc:
            log.warning("Não foi possível exibir a faixa de compartilhamento: %s", exc)
            return False

    def hide_banner(self) -> None:
        def close(root):
            if self._banner is not None:
                self._banner.destroy()
                self._banner = None

        if self._thread and self._thread.is_alive():
            try:
                self._call(close).result(5)
            except Exception:
                pass

    def banner_visible(self) -> bool:
        return self._banner is not None

    # -------------------------------------------------------------- consentimento
    def ask(self, user: str, control: bool, timeout: int = 30) -> bool:
        """Pergunta ao dono do PC. Sem resposta em `timeout` segundos = recusado."""
        result: concurrent.futures.Future = concurrent.futures.Future()
        what = "VER e CONTROLAR (mouse e teclado)" if control else "VER"

        def build(root):
            import tkinter as tk

            win = tk.Toplevel(root)
            win.title("EINSTEIN REMOTE — pedido de acesso")
            win.configure(bg=BG)
            win.attributes("-topmost", True)
            win.resizable(False, False)
            tk.Label(win, text="Pedido de acesso à tela", bg=BG, fg=ACCENT, font=("Segoe UI", 13, "bold")).pack(padx=24, pady=(18, 6))
            tk.Label(win, text=f"“{user}” quer {what} a tela deste computador pelo painel EINSTEIN REMOTE.",
                     bg=BG, fg=FG, wraplength=360, justify="center", font=("Segoe UI", 10)).pack(padx=24)
            countdown = tk.Label(win, bg=BG, fg="#8a8aa0", font=("Segoe UI", 9))
            countdown.pack(pady=(8, 10))
            buttons = tk.Frame(win, bg=BG)
            buttons.pack(pady=(0, 18))
            state = {"left": timeout}

            def done(ok: bool):
                if not result.done():
                    result.set_result(ok)
                win.destroy()

            tk.Button(buttons, text="Recusar", command=lambda: done(False), bg="#2a2a3a", fg=FG, relief="flat", padx=16, pady=6).pack(side="left", padx=6)
            tk.Button(buttons, text="Permitir", command=lambda: done(True), bg=ACCENT, fg=FG, relief="flat", padx=16, pady=6,
                      font=("Segoe UI", 10, "bold")).pack(side="left", padx=6)
            win.protocol("WM_DELETE_WINDOW", lambda: done(False))

            def tick():
                if result.done():
                    return
                if state["left"] <= 0:
                    done(False)
                    return
                countdown.config(text=f"Recusa automática em {state['left']} s")
                state["left"] -= 1
                win.after(1000, tick)

            tick()
            win.update_idletasks()
            win.geometry(f"+{(win.winfo_screenwidth() - win.winfo_width()) // 2}+{(win.winfo_screenheight() - win.winfo_height()) // 3}")
            win.lift()
            win.focus_force()
            return True

        try:
            self._call(build).result(10)
            return bool(result.result(timeout + 5))
        except Exception as exc:
            log.warning("Não foi possível perguntar ao dono do computador: %s", exc)
            return False


OVERLAY = OverlayUI()
