"""Interface de linha de comando do agente.

    einstein-agent pair --server https://host:8443 --code ABCD-EFGH
    einstein-agent run
    einstein-agent status | pause | resume
    einstein-agent autostart enable|disable
    einstein-agent uninstall
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import signal
import ssl
import sys
import urllib.error
import urllib.request

from . import __version__, autostart
from .client import Agent, AuthRejected, check_transport, ssl_context
from .config import AgentConfig, config_dir, config_path, protect_secret
from .metrics import system_info

BANNER = r"""
  ███████╗██╗███╗   ██╗███████╗████████╗███████╗██╗███╗   ██╗
  ██╔════╝██║████╗  ██║██╔════╝╚══██╔══╝██╔════╝██║████╗  ██║
  █████╗  ██║██╔██╗ ██║███████╗   ██║   █████╗  ██║██╔██╗ ██║
  ██╔══╝  ██║██║╚██╗██║╚════██║   ██║   ██╔══╝  ██║██║╚██╗██║
  ███████╗██║██║ ╚████║███████║   ██║   ███████╗██║██║ ╚████║
  ╚══════╝╚═╝╚═╝  ╚═══╝╚══════╝   ╚═╝   ╚══════╝╚═╝╚═╝  ╚═══╝  REMOTE AGENT v{v}
"""


def cmd_pair(args) -> int:
    cfg = AgentConfig.load()
    cfg.server_url = args.server.rstrip("/")
    cfg.ca_file = args.ca_file or cfg.ca_file
    cfg.allow_insecure = args.allow_insecure or cfg.allow_insecure
    check_transport(cfg)
    info = system_info()
    body = json.dumps({"code": args.code, "hostname": info["hostname"], "os": info["os"]}).encode()
    req = urllib.request.Request(
        cfg.server_url + "/api/agent/pair", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    ctx = ssl_context(cfg) if cfg.server_url.startswith("https://") else None
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = json.loads(e.read() or b"{}").get("detail", e.reason)
        print(f"Falha no pareamento: {detail}")
        return 1
    except (urllib.error.URLError, ssl.SSLError) as e:
        print(f"Não foi possível contatar o servidor: {e}")
        return 1
    cfg.device_id = data["device_id"]
    cfg.device_secret = protect_secret(data["device_secret"])
    cfg.device_name = data["name"]
    cfg.paused = False
    cfg.save()
    print(f"✔ Dispositivo vinculado como '{data['name']}'. Configuração salva em {config_path()}")
    print("  Inicie com: einstein-agent run")
    return 0


def cmd_run(args) -> int:
    print(BANNER.format(v=__version__))
    cfg = AgentConfig.load()
    if not cfg.paired:
        print("Este computador ainda não foi vinculado. Execute 'einstein-agent pair' primeiro.")
        return 1
    print("O EINSTEIN REMOTE AGENT está ATIVO e visível. O proprietário pode pausá-lo a qualquer momento")
    print("com 'einstein-agent pause' ou encerrá-lo com Ctrl+C.\n")
    agent = Agent(cfg)

    async def main():
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, agent.stop)
            except (NotImplementedError, RuntimeError):
                pass  # Windows: Ctrl+C gera KeyboardInterrupt
        await agent.run_forever()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except AuthRejected:
        return 2
    except ValueError as e:
        print(e)
        return 1
    print("Agente encerrado.")
    return 0


def cmd_status(args) -> int:
    cfg = AgentConfig.load()
    print(f"Configuração:  {config_path()}")
    print(f"Vinculado:     {'sim' if cfg.paired else 'não'}")
    if cfg.paired:
        print(f"Servidor:      {cfg.server_url}")
        print(f"Nome:          {cfg.device_name}")
        print(f"ID:            {cfg.device_id}")
    print(f"Pausado:       {'sim' if cfg.paused else 'não'}")
    print(f"Auto-início:   {'ativado' if autostart.is_enabled() else 'desativado'}")
    return 0


def _set_paused(value: bool) -> int:
    cfg = AgentConfig.load()
    cfg.paused = value
    cfg.save()
    print("Agente pausado: nenhuma métrica ou comando será processado." if value else "Agente retomado.")
    return 0


def cmd_autostart(args) -> int:
    try:
        if args.mode == "enable":
            print(f"Inicialização automática ativada (visível em Gerenciador de Tarefas > Inicializar):\n  {autostart.enable()}")
        else:
            print("Inicialização automática desativada." if autostart.disable() else "Já estava desativada.")
    except RuntimeError as e:
        print(e)
        return 1
    return 0


def cmd_uninstall(args) -> int:
    if not args.yes:
        answer = input("Remover vínculo, configuração e auto-início deste computador? [s/N] ")
        if answer.strip().lower() not in ("s", "sim", "y", "yes"):
            print("Cancelado.")
            return 1
    autostart.disable()
    shutil.rmtree(config_dir(), ignore_errors=True)
    print("Configuração removida. Revogue também o dispositivo no dashboard (Dispositivos > Revogar).")
    print("Para remover o programa: 'pip uninstall einstein-remote-agent' ou pelo desinstalador do Windows.")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser(prog="einstein-agent", description="EINSTEIN REMOTE AGENT")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("pair", help="vincula este computador ao servidor")
    sp.add_argument("--server", required=True, help="URL do servidor, ex.: https://meu-servidor:8443")
    sp.add_argument("--code", required=True, help="código gerado no dashboard")
    sp.add_argument("--ca-file", help="certificado da CA (para certificado autoassinado)")
    sp.add_argument("--allow-insecure", action="store_true", help="permite http:// fora de localhost (somente testes)")
    sp.set_defaults(fn=cmd_pair)

    sub.add_parser("run", help="executa o agente").set_defaults(fn=cmd_run)
    sub.add_parser("status", help="mostra o estado do agente").set_defaults(fn=cmd_status)
    sub.add_parser("pause", help="pausa o agente").set_defaults(fn=lambda a: _set_paused(True))
    sub.add_parser("resume", help="retoma o agente").set_defaults(fn=lambda a: _set_paused(False))

    sa = sub.add_parser("autostart", help="ativa/desativa início automático com o Windows")
    sa.add_argument("mode", choices=["enable", "disable"])
    sa.set_defaults(fn=cmd_autostart)

    su = sub.add_parser("uninstall", help="remove vínculo e configuração")
    su.add_argument("--yes", action="store_true")
    su.set_defaults(fn=cmd_uninstall)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
