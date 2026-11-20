"""Central de energia: desligar, reiniciar, suspender, hibernar, bloquear e cancelar.

* Desligar/reiniciar usam o agendador nativo do Windows (``shutdown /s|/r /t N``):
  o Windows mostra o próprio aviso e o agendamento continua mesmo se o agente cair.
  ``shutdown /a`` cancela.
* Suspender/hibernar não têm agendamento nativo: o agente conta o prazo e chama
  ``SetSuspendState``; cancelar = interromper a contagem.
* O que o hardware suporta é consultado de verdade (``GetPwrCapabilities``).

Modo de simulação (``EINSTEIN_POWER_DRY_RUN=1``): nada é executado; o comando exato
fica registrado em ``DRY_RUN_LOG`` e no log — usado nos testes automatizados.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger("einstein.power")
WINDOWS = sys.platform == "win32"
ACTIONS = ("shutdown", "restart", "sleep", "hibernate", "lock")
MAX_DELAY = 24 * 3600
DRY_RUN_LOG: list[list[str]] = []


class PowerError(Exception):
    pass


def system_exe(name: str) -> str:
    """Caminho absoluto de utilitários do Windows (evita executar um 'shutdown.exe' falso colocado no PATH)."""
    if WINDOWS:
        path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", f"{name}.exe")
        if os.path.exists(path):
            return path
    return name


def dry_run() -> bool:
    return os.environ.get("EINSTEIN_POWER_DRY_RUN") == "1"


def _run(cmd: list[str]) -> subprocess.CompletedProcess | None:
    if dry_run():
        DRY_RUN_LOG.append(cmd)
        log.warning("[simulação] executaria: %s", " ".join(cmd))
        return None
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


# ----------------------------------------------------------------- capacidades
def _windows_caps() -> dict:
    import ctypes

    buf = (ctypes.c_ubyte * 512)()  # SYSTEM_POWER_CAPABILITIES (campos iniciais são BOOLEAN de 1 byte)
    ok = ctypes.windll.powrprof.GetPwrCapabilities(ctypes.byref(buf))
    if not ok:
        return {"sleep": True, "hibernate": False}
    s3, s4, hiberfile, aoac = buf[5], buf[6], buf[8], buf[21]
    return {"sleep": bool(s3 or aoac), "hibernate": bool(s4 and hiberfile), "modern_standby": bool(aoac)}


def capabilities() -> dict:
    caps = {"shutdown": True, "restart": True, "lock": True, "cancel": True, "sleep": False, "hibernate": False}
    reasons = {}
    if WINDOWS:
        try:
            caps.update(_windows_caps())
        except Exception as exc:
            log.debug("GetPwrCapabilities falhou: %s", exc)
        if not caps["hibernate"]:
            reasons["hibernate"] = "Hibernação desativada neste PC (ative com: powercfg /hibernate on, como administrador)."
        if not caps["sleep"]:
            reasons["sleep"] = "O hardware/driver de vídeo não oferece suspensão."
    else:
        has_systemctl = bool(shutil.which("systemctl"))
        caps.update(sleep=has_systemctl, hibernate=has_systemctl, lock=bool(shutil.which("loginctl")))
        caps["shutdown"] = caps["restart"] = caps["cancel"] = bool(shutil.which("shutdown")) or has_systemctl
        if not has_systemctl:
            reasons["sleep"] = reasons["hibernate"] = "systemd não disponível."
    if dry_run() and not WINDOWS:  # ambiente de teste Linux: simula um PC com todos os recursos
        caps.update(shutdown=True, restart=True, sleep=True, hibernate=True, lock=True, cancel=True)
        reasons = {}
    caps["dry_run"] = dry_run()
    return {"capabilities": caps, "reasons": reasons}


# ----------------------------------------------------------------- privilégio (Windows)
def _enable_shutdown_privilege() -> None:
    """SetSuspendState exige o privilégio SeShutdownPrivilege habilitado no processo."""
    import ctypes
    from ctypes import wintypes

    advapi32, kernel32 = ctypes.windll.advapi32, ctypes.windll.kernel32

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]

    token = wintypes.HANDLE()
    TOKEN_ADJUST_PRIVILEGES, TOKEN_QUERY, SE_PRIVILEGE_ENABLED = 0x20, 0x8, 0x2
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(token)):
        raise PowerError("Não foi possível abrir o token do processo.")
    try:
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, "SeShutdownPrivilege", ctypes.byref(luid)):
            raise PowerError("Privilégio de desligamento indisponível.")
        tp = TOKEN_PRIVILEGES(1, (LUID_AND_ATTRIBUTES * 1)(LUID_AND_ATTRIBUTES(luid, SE_PRIVILEGE_ENABLED)))
        advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
        if kernel32.GetLastError() != 0:
            raise PowerError("O Windows não concedeu o privilégio de desligamento a este usuário.")
    finally:
        kernel32.CloseHandle(token)


# ----------------------------------------------------------------- ações
def schedule_native(action: str, delay: int, comment: str) -> None:
    """Desligar/reiniciar com prazo, pelo agendador do sistema."""
    if WINDOWS:
        flag = "/s" if action == "shutdown" else "/r"
        r = _run([system_exe("shutdown"), flag, "/t", str(delay), "/c", comment[:500]])
        if r is not None and r.returncode != 0:
            # 1190 = já existe um desligamento agendado
            raise PowerError(f"O Windows recusou: {(r.stderr or r.stdout).strip() or r.returncode}")
    else:
        minutes = "now" if delay < 60 else f"+{max(1, round(delay / 60))}"
        r = _run(["shutdown", "-h" if action == "shutdown" else "-r", minutes, comment])
        if r is not None and r.returncode != 0:
            raise PowerError((r.stderr or r.stdout).strip() or "falha no shutdown")


def cancel_native() -> bool:
    r = _run([system_exe("shutdown"), "/a"] if WINDOWS else ["shutdown", "-c"])
    return r is None or r.returncode == 0


def suspend(hibernate: bool) -> None:
    if dry_run():
        _run(["SetSuspendState", "hibernate" if hibernate else "sleep"])
        return
    if WINDOWS:
        import ctypes

        _enable_shutdown_privilege()
        if not ctypes.windll.powrprof.SetSuspendState(bool(hibernate), False, False):
            raise PowerError("O Windows recusou a suspensão/hibernação.")
    else:
        r = _run(["systemctl", "hibernate" if hibernate else "suspend"])
        if r is not None and r.returncode != 0:
            raise PowerError((r.stderr or r.stdout).strip())


def lock() -> None:
    if dry_run():
        _run(["LockWorkStation"])
        return
    if WINDOWS:
        import ctypes

        if not ctypes.windll.user32.LockWorkStation():
            raise PowerError("Não foi possível bloquear (sem sessão interativa).")
    else:
        r = _run(["loginctl", "lock-sessions"])
        if r is not None and r.returncode != 0:
            raise PowerError((r.stderr or r.stdout).strip())


# ----------------------------------------------------------------- controlador (prazos e cancelamento)
LABELS = {"shutdown": "desligamento", "restart": "reinício", "sleep": "suspensão", "hibernate": "hibernação"}


class PowerController:
    """Mantém a ação de energia pendente e avisa servidor (send_json) e bandeja (on_change)."""

    def __init__(self, on_change=None):
        import asyncio  # noqa: F401  (garantia de disponibilidade no executável)

        self.on_change = on_change
        self.send_json = None  # definido a cada conexão
        self.pending: dict | None = None
        self._task = None

    def state(self) -> dict:
        return {"pending": dict(self.pending) if self.pending else None}

    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change(self.state())
            except Exception:
                log.exception("Falha ao notificar mudança de energia")

    async def _emit(self, msg: dict) -> None:
        if self.send_json:
            try:
                await self.send_json({"type": "power_event", **msg})
            except Exception:
                pass

    async def request(self, action: str, params: dict) -> dict:
        import asyncio
        import time

        from .config import AgentConfig

        if action not in ACTIONS:
            raise PowerError("Ação de energia desconhecida.")
        if not AgentConfig.load().allow_power:
            raise PowerError("O proprietário bloqueou neste computador o controle de energia pelo painel.")
        caps = capabilities()
        if not caps["capabilities"].get(action):
            raise PowerError(caps["reasons"].get(action) or "Ação não suportada neste computador.")
        by = str(params.get("_user") or "painel")[:64]

        if action == "lock":
            await asyncio.to_thread(lock)
            log.warning("Tela bloqueada a pedido de %s", by)
            return {"action": "lock", "done": True}

        if self.pending:
            raise PowerError(f"Já existe um(a) {LABELS[self.pending['action']]} agendado(a). Cancele antes de pedir outra ação.")
        try:
            delay = max(0, min(MAX_DELAY, int(params.get("delay", 60))))
        except (TypeError, ValueError):
            raise PowerError("Prazo inválido.") from None

        at = time.time() + delay
        if action in ("shutdown", "restart"):
            comment = f"EINSTEIN REMOTE: {LABELS[action]} solicitado por {by} pelo painel."
            await asyncio.to_thread(schedule_native, action, delay, comment)
            self.pending = {"action": action, "at": at, "delay": delay, "by": by, "native": True}
            self._task = asyncio.create_task(self._expire(delay + 120))
        else:
            self.pending = {"action": action, "at": at, "delay": delay, "by": by, "native": False}
            self._task = asyncio.create_task(self._suspend_after(action, max(delay, 3)))
        log.warning("%s agendado(a) para daqui a %ss a pedido de %s", LABELS[action].capitalize(), delay, by)
        await self._emit({"event": "scheduled", **self.pending})
        self._changed()
        return {"action": action, "scheduled": True, "at": at, "delay": delay}

    async def _expire(self, seconds: float) -> None:
        """Se o PC continuar ligado bem depois do prazo, alguém cancelou fora do agente."""
        import asyncio

        await asyncio.sleep(seconds)
        if self.pending and self.pending.get("native"):
            log.info("Desligamento/reinício não ocorreu; estado pendente descartado.")
            self.pending = None
            await self._emit({"event": "canceled", "by": "sistema"})
            self._changed()

    async def _suspend_after(self, action: str, delay: float) -> None:
        import asyncio

        await asyncio.sleep(delay)
        await self._emit({"event": "executing", "action": action})
        await asyncio.sleep(0.5)  # deixa a mensagem sair antes de a rede adormecer
        try:
            await asyncio.to_thread(suspend, action == "hibernate")
        except PowerError as exc:
            log.error("Falha ao %s: %s", LABELS[action], exc)
            await self._emit({"event": "failed", "action": action, "error": str(exc)})
        finally:
            self.pending = None
            self._task = None
            self._changed()

    async def cancel(self, by: str) -> dict:
        import asyncio

        pending = self.pending
        if pending and not pending["native"]:
            if self._task:
                self._task.cancel()
            ok = True
        else:
            ok = await asyncio.to_thread(cancel_native)
            if self._task:
                self._task.cancel()
        self._task = None
        self.pending = None
        if pending:
            log.warning("%s cancelado(a) por %s", LABELS[pending["action"]].capitalize(), by)
            await self._emit({"event": "canceled", "by": by, "action": pending["action"]})
        self._changed()
        return {"canceled": bool(pending) and ok, "action": pending["action"] if pending else None}
