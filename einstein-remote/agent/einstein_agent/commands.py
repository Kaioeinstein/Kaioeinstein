"""Comandos que o agente aceita executar.

Lista fechada (allowlist): qualquer ação fora deste registro é recusada,
mesmo que o servidor seja comprometido. Novas fases adicionam handlers aqui.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from . import sysmon
from .config import AgentConfig
from .metrics import system_info

log = logging.getLogger("einstein.commands")


class CommandRefused(Exception):
    """Recusa legítima (política local, processo protegido...), sem stack trace."""


Handler = Callable[[dict], Awaitable[dict]]
REGISTRY: dict[str, Handler] = {}


def command(name: str):
    def deco(fn: Handler) -> Handler:
        REGISTRY[name] = fn
        return fn

    return deco


@command("ping")
async def ping(params: dict) -> dict:
    return {"pong": time.time()}


@command("system.info")
async def info(params: dict) -> dict:
    return system_info()


# ---------------------------------------------------------------- SYSTEM MONITOR (Fase 4)
@command("system.processes")
async def processes(params: dict) -> dict:
    return await asyncio.to_thread(sysmon.SAMPLER.snapshot)


@command("system.apps")
async def apps(params: dict) -> dict:
    return await asyncio.to_thread(sysmon.open_apps)


@command("system.services")
async def services(params: dict) -> dict:
    return await asyncio.to_thread(sysmon.services)


@command("system.hardware")
async def hardware(params: dict) -> dict:
    return await asyncio.to_thread(sysmon.hardware)


@command("system.network")
async def network(params: dict) -> dict:
    return await asyncio.to_thread(sysmon.network)


@command("process.kill")
async def process_kill(params: dict) -> dict:
    allowed = AgentConfig.load().allow_process_kill  # lido a cada pedido: mudança na bandeja vale na hora
    log.warning("Pedido remoto para encerrar processo %s (PID %s)", params.get("name"), params.get("pid"))
    try:
        result = await asyncio.to_thread(
            sysmon.kill_process, params.get("pid"), str(params.get("name", "")), params.get("create_time"), allowed
        )
    except sysmon.KillRefused as exc:
        log.warning("Encerramento recusado: %s", exc)
        raise CommandRefused(str(exc)) from None
    log.warning("Processo encerrado remotamente: %s (PID %s)", result["name"], result["pid"])
    return result


async def execute(action: str, params: dict) -> tuple[bool, dict | None, str | None]:
    handler = REGISTRY.get(action)
    if handler is None:
        return False, None, f"Ação '{action}' não suportada por este agente"
    try:
        return True, await handler(params or {}), None
    except CommandRefused as exc:  # recusa esperada: mensagem limpa para o painel
        return False, None, str(exc)
    except Exception as exc:  # erro nunca derruba o agente
        log.exception("Falha no comando %s", action)
        return False, None, f"{type(exc).__name__}: {exc}"
