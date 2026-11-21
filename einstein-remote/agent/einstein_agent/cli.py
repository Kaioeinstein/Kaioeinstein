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
    print(f"Encerrar processos pelo painel: {'permitido' if cfg.allow_process_kill else 'bloqueado'}")
    print(f"Acesso à tela: {({'allow': 'permitido sem perguntar', 'ask': 'perguntar a cada sessão', 'deny': 'bloqueado'}).get(cfg.screen_access, cfg.screen_access)}")
    print(f"Controle de mouse e teclado: {'permitido' if cfg.allow_remote_input else 'bloqueado'}")
    print(f"Controle de energia pelo painel: {'permitido' if cfg.allow_power else 'bloqueado'}")
    print(f"Controle de volume pelo painel: {'permitido' if cfg.allow_volume else 'bloqueado'}")
    print(f"Limpeza e otimização pelo painel: {'permitidas' if cfg.allow_maintenance else 'bloqueadas'}")
    print(f"Modo jogo automático: {'ligado' if cfg.game_mode_auto else 'desligado'}")
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


def cmd_remote_kill(args) -> int:
    cfg = AgentConfig.load()
    cfg.allow_process_kill = args.mode == "enable"
    cfg.save()
    print("Encerramento remoto de processos " + ("PERMITIDO" if cfg.allow_process_kill else "BLOQUEADO") + " neste computador.")
    return 0


def cmd_diag(args) -> int:
    """Testa, neste computador, os recursos usados pelo painel (útil para suporte)."""
    import platform

    from .inputs import create_injector
    from .overlay import ui_available
    from .screen import Capturer

    ok = True
    print(f"EINSTEIN REMOTE AGENT {__version__} · Python {platform.python_version()} · {platform.platform()}")
    try:
        cap = Capturer()
        mons = cap.monitors()
        jpeg, _, size = cap.grab(mons[0], 640, 50, None, None)
        cap.close()
        print(f"Captura de tela: ok ({len(mons)} monitor(es); {', '.join(f'{m['width']}x{m['height']}' for m in mons)}; JPEG {len(jpeg) // 1024} KB)")
    except Exception as e:
        ok = False
        print(f"Captura de tela: FALHOU ({type(e).__name__}: {e})")
    inj = create_injector()
    print("Mouse e teclado remotos: ok" if inj else "Mouse e teclado remotos: indisponível neste sistema")
    if inj:
        print(f"  posição do ponteiro: {inj.cursor()}")
    print("Avisos visíveis (faixa/pedido de permissão): ok" if ui_available() else "Avisos visíveis: indisponível (sem interface gráfica)")
    return 0 if ok else 1


def cmd_screen_access(args) -> int:
    cfg = AgentConfig.load()
    cfg.screen_access = args.mode
    cfg.save()
    labels = {"allow": "PERMITIDO sem perguntar (com faixa de aviso)", "ask": "PERGUNTAR a cada sessão", "deny": "BLOQUEADO"}
    print(f"Acesso remoto à tela: {labels[args.mode]}.")
    return 0


def cmd_remote_input(args) -> int:
    cfg = AgentConfig.load()
    cfg.allow_remote_input = args.mode == "enable"
    cfg.save()
    print("Controle remoto de mouse e teclado " + ("PERMITIDO" if cfg.allow_remote_input else "BLOQUEADO") + " neste computador.")
    return 0


def cmd_folders(args) -> int:
    """Pastas que o painel pode acessar (nada é acessível até o dono liberar)."""
    from . import files

    try:
        if args.op == "add":
            r = files.add_root(args.path, write=args.write)
            print(f"Liberada: {r['path']} ({'leitura e escrita' if r['write'] else 'somente leitura'})")
        elif args.op == "remove":
            print("Removida." if files.remove_root(args.path) else "Pasta não encontrada na lista.")
            return 0
    except files.FileError as e:
        print(e)
        return 1
    roots = files.roots()
    if not roots:
        print("Nenhuma pasta liberada: o painel não acessa nenhum arquivo deste computador.")
        print("Para liberar: einstein-agent folders add \"C:\\Users\\voce\\Documents\" [--write]")
    for i, r in enumerate(roots):
        print(f"[{i}] {r['path']}  ({'leitura e escrita' if r['write'] else 'somente leitura'})")
    return 0


def cmd_power_control(args) -> int:
    cfg = AgentConfig.load()
    cfg.allow_power = args.mode == "enable"
    cfg.save()
    print("Controle de energia pelo painel (desligar, reiniciar, suspender, hibernar, bloquear) "
          + ("PERMITIDO" if cfg.allow_power else "BLOQUEADO") + " neste computador.")
    print("Para cancelar um desligamento/reinício já agendado: ícone na bandeja ou 'shutdown /a'.")
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
    from .screen import enable_dpi_awareness

    enable_dpi_awareness()
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

    sk = sub.add_parser("remote-kill", help="permite/bloqueia encerrar processos pelo painel")
    sk.add_argument("mode", choices=["enable", "disable"])
    sk.set_defaults(fn=cmd_remote_kill)

    sub.add_parser("diag", help="testa captura de tela, mouse/teclado e avisos neste PC").set_defaults(fn=cmd_diag)
    sc = sub.add_parser("screen-access", help="acesso remoto à tela: allow | ask | deny")
    sc.add_argument("mode", choices=["allow", "ask", "deny"])
    sc.set_defaults(fn=cmd_screen_access)
    si = sub.add_parser("remote-input", help="permite/bloqueia controle de mouse e teclado")
    si.add_argument("mode", choices=["enable", "disable"])
    si.set_defaults(fn=cmd_remote_input)

    sf = sub.add_parser("folders", help="pastas liberadas para o painel: list | add <pasta> [--write] | remove <pasta|nº>")
    sf.add_argument("op", choices=["list", "add", "remove"], nargs="?", default="list")
    sf.add_argument("path", nargs="?")
    sf.add_argument("--write", action="store_true", help="permite enviar arquivos e criar pastas (padrão: somente leitura)")
    sf.set_defaults(fn=cmd_folders)

    sp2 = sub.add_parser("power-control", help="permite/bloqueia desligar, reiniciar, suspender pelo painel")
    sp2.add_argument("mode", choices=["enable", "disable"])
    sp2.set_defaults(fn=cmd_power_control)

    su = sub.add_parser("uninstall", help="remove vínculo e configuração")
    su.add_argument("--yes", action="store_true")
    su.set_defaults(fn=cmd_uninstall)

    args = p.parse_args(argv)
    if args.cmd not in ("tray",):
        setup_logging(console=args.cmd == "run")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
