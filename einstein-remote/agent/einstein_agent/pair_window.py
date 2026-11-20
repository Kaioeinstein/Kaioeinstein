"""Janela gráfica de vinculação (para quem instala o .exe e não usa o terminal)."""
from __future__ import annotations

import gc
import queue
import threading

from .config import AgentConfig
from .pairing import PairingError, pair

BG = "#0b0b12"
PANEL = "#14141f"
FG = "#ececf4"
MUTED = "#8a8aa0"
PURPLE = "#a855f7"
GREEN = "#22e3a0"
RED = "#ff4d6d"
FONT = ("Segoe UI", 10)


def run_pair_window(on_done=None) -> bool:
    """Mostra a janela e bloqueia até fechar. Retorna True se o computador foi vinculado.

    Todos os objetos Tk vivem dentro de ``_window`` e são coletados AQUI, na mesma
    thread que os criou: se o coletor de lixo os finalizar em outra thread, o Tcl
    aborta o processo inteiro (Tcl_AsyncDelete). A bandeja abre esta janela numa
    thread própria, então isso é essencial.
    """
    try:
        ok = _window()
    finally:
        gc.collect()
    if on_done:
        on_done(ok)
    return ok


def _window() -> bool:
    import tkinter as tk
    from tkinter import filedialog

    cfg = AgentConfig.load()
    result = {"ok": False}
    jobs: queue.Queue = queue.Queue()

    root = tk.Tk()
    root.title("EINSTEIN REMOTE — Vincular computador")
    root.configure(bg=BG)
    root.resizable(False, False)
    try:
        from PIL import ImageTk

        from .icons import make_icon

        root._icon = ImageTk.PhotoImage(make_icon(None, 64))  # mantém referência
        root.iconphoto(True, root._icon)
    except Exception:
        pass

    frame = tk.Frame(root, bg=BG, padx=28, pady=24)
    frame.pack(fill="both", expand=True)

    brand = tk.Frame(frame, bg=BG)
    brand.grid(row=0, column=0, columnspan=3, sticky="w")
    tk.Label(brand, text="EINSTEIN", font=("Segoe UI", 20, "bold"), fg=FG, bg=BG).pack(side="left")
    tk.Label(brand, text="REMOTE", font=("Segoe UI", 20, "bold"), fg=PURPLE, bg=BG).pack(side="left", padx=(8, 0))
    tk.Label(
        frame,
        text="Vincule este computador ao seu painel.\nNo painel: Dispositivos › Gerar código (válido por 10 minutos).",
        font=FONT, fg=MUTED, bg=BG, justify="left",
    ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 16))

    def field(row: int, label: str, value: str = "", width: int = 42):
        tk.Label(frame, text=label, font=FONT, fg=MUTED, bg=BG).grid(row=row, column=0, columnspan=3, sticky="w")
        var = tk.StringVar(value=value)
        entry = tk.Entry(
            frame, textvariable=var, width=width, font=("Segoe UI", 11), bg=PANEL, fg=FG, insertbackground=FG,
            relief="flat", highlightthickness=1, highlightbackground="#2a2a3a", highlightcolor=PURPLE,
        )
        entry.grid(row=row + 1, column=0, columnspan=2, sticky="we", pady=(2, 12), ipady=6)
        return var, entry

    server_var, server_entry = field(2, "Endereço do servidor", cfg.server_url or "https://")
    code_var, code_entry = field(4, "Código de vinculação", "")
    ca_var, _ = field(6, "Certificado da CA (opcional, para certificado próprio)", cfg.ca_file)

    def browse():
        path = filedialog.askopenfilename(title="Certificado da CA", filetypes=[("Certificado", "*.crt *.pem *.cer"), ("Todos", "*.*")])
        if path:
            ca_var.set(path)

    tk.Button(frame, text="Procurar…", command=browse, font=FONT, bg=PANEL, fg=FG, relief="flat", activebackground="#22222f",
              activeforeground=FG, padx=10).grid(row=7, column=2, sticky="w", padx=(8, 0), pady=(2, 12))

    insecure_var = tk.BooleanVar(value=cfg.allow_insecure)
    tk.Checkbutton(
        frame, text="Permitir conexão sem criptografia (somente rede de testes)", variable=insecure_var, font=FONT,
        fg=MUTED, bg=BG, selectcolor=PANEL, activebackground=BG, activeforeground=FG,
    ).grid(row=8, column=0, columnspan=3, sticky="w")

    status = tk.Label(frame, text="", font=FONT, fg=MUTED, bg=BG, wraplength=420, justify="left")
    status.grid(row=9, column=0, columnspan=3, sticky="w", pady=(12, 8))

    buttons = tk.Frame(frame, bg=BG)
    buttons.grid(row=10, column=0, columnspan=3, sticky="e")
    cancel_btn = tk.Button(buttons, text="Cancelar", command=root.destroy, font=FONT, bg=PANEL, fg=FG, relief="flat",
                           padx=14, pady=6, activebackground="#22222f", activeforeground=FG)
    cancel_btn.pack(side="left", padx=(0, 8))
    pair_btn = tk.Button(buttons, text="Vincular", font=("Segoe UI", 10, "bold"), bg=PURPLE, fg="white", relief="flat",
                         padx=18, pady=6, activebackground="#7c3aed", activeforeground="white")
    pair_btn.pack(side="left")

    def worker(server, code, ca, insecure):
        try:
            c = pair(server, code, ca, insecure)
            jobs.put(("ok", c.device_name))
        except PairingError as e:
            jobs.put(("error", str(e)))
        except Exception as e:  # nunca deixa a janela travada
            jobs.put(("error", f"Erro inesperado: {e}"))

    def poll():
        try:
            kind, msg = jobs.get_nowait()
        except queue.Empty:
            root.after(100, poll)
            return
        if kind == "ok":
            result["ok"] = True
            status.config(text=f"✔ Vinculado como “{msg}”. O agente será iniciado.", fg=GREEN)
            root.after(1200, root.destroy)
        else:
            status.config(text=msg, fg=RED)
            pair_btn.config(state="normal")
            server_entry.config(state="normal")
            code_entry.config(state="normal")

    def submit(_event=None):
        if str(pair_btn["state"]) == "disabled":
            return
        status.config(text="Vinculando…", fg=MUTED)
        pair_btn.config(state="disabled")
        server_entry.config(state="disabled")
        code_entry.config(state="disabled")
        threading.Thread(
            target=worker, args=(server_var.get(), code_var.get(), ca_var.get(), insecure_var.get()), daemon=True
        ).start()
        root.after(100, poll)

    pair_btn.config(command=submit)
    root.bind("<Return>", submit)
    (code_entry if cfg.server_url else server_entry).focus_set()
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 3}")
    root.lift()
    root.attributes("-topmost", True)
    root.after(500, lambda: root.attributes("-topmost", False))
    root.mainloop()
    return result["ok"]
