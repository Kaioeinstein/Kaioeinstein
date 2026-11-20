"""Interface de linha de comando do agente.

    einstein-agent tray                       ícone na bandeja (modo recomendado)
    einstein-agent pair --server URL --code ABCD-EFGH
    einstein-agent run                        modo console (servidor/diagnóstico)
    einstein-agent status | pause | resume | logs
    einstein-agent autostart enable|disable
    einstein-agent uninstall
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from . import __version__, autostart
from .client import Agent, AuthRejected
from .config import AgentConfig, config_path
from .logs import log_file, setup_logging, tail
from .pairing import PairingError, pair, unpair
from .singleton import SingleInstance

BANNER = r"""
  ███████╗██╗███╗   ██╗███████╗████████╗███████╗██╗███╗   ██╗
  ██╔════╝██║████╗  ██║██╔════╝╚══██╔══╝██╔════╝██║████╗  ██║
  █████╗  ██║██╔██╗ ██║███████╗   ██║   █████╗  ██║██╔██╗ ██║
  ██╔══╝  ██║██║╚██╗██║╚════██║   ██║   ██╔══╝  ██║██║╚██╗██║
  ███████╗██║██║ ╚████║███████║   ██║   ███████╗██║██║ ╚████║
  ╚══════╝╚═╝╚═╝  ╚═══╝╚══════╝   ╚═╝   ╚══════╝╚═╝╚═╝  ╚═══╝  REMOTE AGENT v{v}
"""


def cmd_pair(args) -> int:
    try:
        cfg = pair(args.server, args.code, args.ca_file or "", args.allow_insecure)
    except PairingError as e:
        print(f"Falha no pareamento: {e}")
        return 1
    print(f"✔ Dispositivo vinculado como '{cfg.device_name}'. Configuração salva em {config_path()}")
    print("  Inicie com: einstein-agent tray   (ou 'einstein-agent run' no console)")
    return 0


def cmd_run(args) -> int:
    print(BANNER.format(v=__version__))
    cfg = AgentConfig.load()
    if not cfg.paired:
        print("Este computador ainda não foi vinculado. Execute 'einstein-agent pair' primeiro.")
        return 1
    lock = SingleInstance("agent")
    if not lock.acquire():
        print("O agente já está em execução neste computador (bandeja ou outro terminal).")
        return 3
    print("O EINSTEIN REMOTE AGENT está ATIVO e visível. O proprietário pode pausá-lo a qualquer momento")
    print(f"com 'einstein-agent pause' ou encerrá-lo com Ctrl+C. Registro: {log_file()}\n")
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
    finally:
        lock.release()
    print("Agente encerrado.")
    return 0


def cmd_tray(args) -> int:
    try:
        from .tray import main as tray_main
    except ImportError as e:
        print(f"Interface da bandeja indisponível ({e}). Instale: pip install pystray pillow")
        return 1
    return tray_main()


def cmd_status(args) -> int:
    cfg = AgentConfig.load()
    probe = SingleInstance("agent")
    running = not probe.acquire()
    probe.release()
    print(f"Versão:        {__version__}")
    print(f"Configuração:  {config_path()}")
    print(f"Registro:      {log_file()}")
    print(f"Vinculado:     {'sim' if cfg.paired else 'não'}")
    if cfg.paired:
        print(f"Servidor:      {cfg.server_url}")
        print(f"Nome:          {cfg.device_name}")
        print(f"ID:            {cfg.device_id}")
    print(f"Em execução:   {'sim' if running else 'não'}")
    print(f"Pausado:       {'sim' if cfg.paused else 'não'}")
    print(f"Auto-início:   {'ativado' if autostart.is_enabled() else 'desativado'}")
    return 0


def _set_paused(value: bool) -> int:
    cfg = AgentConfig.load()
    if not cfg.paired:
        print("Este computador não está vinculado.")
        return 1
    cfg.paused = value
    cfg.save()
    print("Agente pausado: nenhuma métrica ou comando será processado." if value else "Agente retomado.")
    return 0


def cmd_logs(args) -> int:
    text = tail(args.lines)
    print(text if text else f"(sem registros em {log_file()})")
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
    unpair()
    print("Configuração removida. Revogue também o dispositivo no dashboard (Dispositivos > Revogar).")
    print("Para remover o programa: Configurações do Windows > Aplicativos > EINSTEIN REMOTE AGENT,")
    print("ou 'pip uninstall einstein-remote-agent' se instalou pelo Python.")
    return 0


def _safe_console() -> None:
    """Saída consistente em qualquer terminal.

    * Redirecionada (arquivo, pipe, outro programa): UTF-8 — no Windows o padrão
      seria a página de código ANSI/ASCII e os acentos virariam "?".
    * Console interativo: mantém a codificação do console, apenas trocando o que
      não tiver representação em vez de falhar.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _safe_console()
    p = argparse.ArgumentParser(prog="einstein-agent", description="EINSTEIN REMOTE AGENT")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("pair", help="vincula este computador ao servidor")
    sp.add_argument("--server", required=True, help="URL do servidor, ex.: https://meu-servidor:8443")
    sp.add_argument("--code", required=True, help="código gerado no dashboard")
    sp.add_argument("--ca-file", help="certificado da CA (para certificado autoassinado)")
    sp.add_argument("--allow-insecure", action="store_true", help="permite http:// fora de localhost (somente testes)")
    sp.set_defaults(fn=cmd_pair)

    sub.add_parser("tray", help="executa com ícone na bandeja (recomendado)").set_defaults(fn=cmd_tray)
    sub.add_parser("run", help="executa no console").set_defaults(fn=cmd_run)
    sub.add_parser("status", help="mostra o estado do agente").set_defaults(fn=cmd_status)
    sub.add_parser("pause", help="pausa o agente").set_defaults(fn=lambda a: _set_paused(True))
    sub.add_parser("resume", help="retoma o agente").set_defaults(fn=lambda a: _set_paused(False))
    sl = sub.add_parser("logs", help="mostra as últimas linhas do registro")
    sl.add_argument("-n", "--lines", type=int, default=40)
    sl.set_defaults(fn=cmd_logs)

    sa = sub.add_parser("autostart", help="ativa/desativa início automático com o Windows")
    sa.add_argument("mode", choices=["enable", "disable"])
    sa.set_defaults(fn=cmd_autostart)

    su = sub.add_parser("uninstall", help="remove vínculo e configuração")
    su.add_argument("--yes", action="store_true")
    su.set_defaults(fn=cmd_uninstall)

    args = p.parse_args(argv)
    if args.cmd not in ("tray",):
        setup_logging(console=args.cmd == "run")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
