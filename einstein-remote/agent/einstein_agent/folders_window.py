"""Janela "Pastas liberadas": o dono escolhe quais pastas o painel pode acessar."""
from __future__ import annotations

import gc
import os

from . import files

BG, PANEL, FG, MUTED, PURPLE, RED = "#0b0b12", "#14141f", "#ececf4", "#8a8aa0", "#a855f7", "#ff4d6d"
FONT = ("Segoe UI", 10)


def run_folders_window() -> None:
    """Bloqueia até fechar. Objetos Tk são coletados nesta mesma thread (ver pair_window)."""
    try:
        _window()
    finally:
        gc.collect()


def _window() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.title("EINSTEIN REMOTE — Pastas liberadas")
    root.configure(bg=BG)
    root.minsize(560, 320)
    frame = tk.Frame(root, bg=BG, padx=22, pady=18)
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text="Pastas liberadas para o painel", font=("Segoe UI", 14, "bold"), fg=FG, bg=BG).pack(anchor="w")
    tk.Label(frame, text="O painel só enxerga o que estiver aqui. Fora destas pastas, nenhum arquivo é acessível.",
             font=FONT, fg=MUTED, bg=BG, wraplength=520, justify="left").pack(anchor="w", pady=(4, 12))

    lst = tk.Listbox(frame, bg=PANEL, fg=FG, selectbackground=PURPLE, relief="flat", font=FONT, height=8,
                     highlightthickness=1, highlightbackground="#2a2a3a", activestyle="none")
    lst.pack(fill="both", expand=True)
    write_var = tk.BooleanVar(value=False)

    def refresh():
        lst.delete(0, "end")
        for r in files.roots():
            name = os.path.basename(r["path"].rstrip("\\/")) or r["path"]
            mode = "leitura e escrita" if r["write"] else "somente leitura"
            lst.insert("end", f"{'✎' if r['write'] else '👁'}  {name} — {mode}    ({r['path']})")
        if not files.roots():
            lst.insert("end", "(nenhuma pasta liberada)")

    def add():
        path = filedialog.askdirectory(title="Escolha a pasta a liberar", parent=root, mustexist=True)
        if not path:
            return
        try:
            files.add_root(path, write=write_var.get())
        except files.FileError as e:
            messagebox.showwarning("EINSTEIN REMOTE", str(e), parent=root)
        refresh()

    def remove():
        sel = lst.curselection()
        if not sel or not files.roots():
            return
        files.remove_root(sel[0])
        refresh()

    bar = tk.Frame(frame, bg=BG)
    bar.pack(fill="x", pady=(12, 0))
    tk.Checkbutton(bar, text="Permitir enviar arquivos e criar pastas (escrita)", variable=write_var, font=FONT, fg=FG, bg=BG,
                   selectcolor=PANEL, activebackground=BG, activeforeground=FG).pack(side="left")
    tk.Button(bar, text="Fechar", command=root.destroy, font=FONT, bg=PANEL, fg=FG, relief="flat", padx=12, pady=4).pack(side="right")
    tk.Button(bar, text="Remover", command=remove, font=FONT, bg=PANEL, fg=RED, relief="flat", padx=12, pady=4).pack(side="right", padx=6)
    tk.Button(bar, text="Adicionar pasta…", command=add, font=("Segoe UI", 10, "bold"), bg=PURPLE, fg="white", relief="flat",
              padx=12, pady=4).pack(side="right")
    refresh()
    root.lift()
    root.mainloop()
