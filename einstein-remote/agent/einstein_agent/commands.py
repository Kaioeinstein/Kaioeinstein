"""Comandos que o agente aceita executar.

Lista fechada (allowlist): qualquer ação fora deste registro é recusada,
mesmo que o servidor seja comprometido. Novas fases adicionam handlers aqui.
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable

from .metrics import system_info

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


async def execute(action: str, params: dict) -> tuple[bool, dict | None, str | None]:
    handler = REGISTRY.get(action)
    if handler is None:
        return False, None, f"Ação '{action}' não suportada por este agente"
    try:
        return True, await handler(params or {}), None
    except Exception as exc:  # erro nunca derruba o agente
        return False, None, f"{type(exc).__name__}: {exc}"
