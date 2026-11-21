"""Teste do instalador Windows (Inno Setup) — instala, usa, atualiza e desinstala de verdade.

    python installer/smoke_setup.py dist/EinsteinRemoteAgent-<versao>-Setup.exe

Etapas (sai com código != 0 se qualquer uma falhar):
  1. instalação silenciosa com a opção "Iniciar com o Windows"
     → arquivos, atalho no Menu Iniciar, entrada em Aplicativos e valor de início automático corretos
  2. o agente INSTALADO vincula a um servidor real e fica online pela bandeja
  3. atualização (reinstalar) com o agente aberto → o instalador o fecha, mantém o vínculo; volta online
  4. autoatualização: o servidor oferece um pacote novo; o agente INSTALADO baixa, confere o SHA-256, fecha,
     o ajudante troca os arquivos e reabre o agente, que volta online — sem entrar em ciclo
  5. desinstalação silenciosa com /PURGE → agente encerrado (servidor o vê offline) e nada fica para trás:
     programa, atalho, início automático, entrada em Aplicativos e configuração/credencial
Somente Windows; altera o perfil do usuário atual (feito para o executor descartável do CI).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if sys.platform != "win32":
    raise SystemExit("Somente Windows.")

import winreg  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from smoke_exe import ROOT, call, free_port, wait_state  # noqa: E402

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Einstein Remote Agent"
APP_DIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "EinsteinRemote Agent"
CONFIG_DIR = Path(os.environ["APPDATA"]) / "EinsteinRemote"
SHORTCUT = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/EINSTEIN REMOTE AGENT.lnk"
GUID = re.search(r"AppId=\{\{([0-9A-F-]+)\}", (ROOT / "installer" / "EinsteinRemoteAgent.iss").read_text("utf-8-sig")).group(1)
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{{{GUID}}}_is1"


def reg(key: str, name: str) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit(f"FALHA: {msg}")
    print(f"  ok  {msg}")


def run_setup(setup: Path, log: Path, *extra: str) -> None:
    r = subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log}", *extra], timeout=300)
    if r.returncode != 0:
        print(log.read_text("utf-8", errors="replace")[-3000:])
        raise SystemExit(f"FALHA: instalador terminou com código {r.returncode}")


def wait_gone(paths: list[Path], timeout: float = 90) -> None:
    end = time.time() + timeout
    while time.time() < end and any(p.exists() for p in paths):
        time.sleep(0.5)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    setup = Path(sys.argv[1]).resolve()
    version = re.search(r"EinsteinRemoteAgent-(.+)-Setup\.exe$", setup.name).group(1)
    tmp = Path(tempfile.mkdtemp(prefix="einstein-setup-"))
    for p in (CONFIG_DIR, APP_DIR):
        check(not p.exists(), f"perfil limpo antes do teste ({p.name})")

    env = dict(os.environ, EINSTEIN_DATA_DIR=str(tmp / "server"))
    env.pop("EINSTEIN_AGENT_DIR", None)  # o agente instalado usa as pastas reais do usuário
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT / "server"),
                               "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"], env=env)
    tray_procs: list[subprocess.Popen] = []
    try:
        for _ in range(60):
            try:
                call(base, "/api/health")
                break
            except OSError:
                time.sleep(0.5)
        token = call(base, "/api/auth/setup", {"username": "dono", "password": "Instalador#2026"})["access_token"]
        code = call(base, "/api/pairing-codes", {"device_name": "PC instalado"}, token=token)["code"]

        print("1. Instalação silenciosa (com início automático)")
        run_setup(setup, tmp / "install.log", "/TASKS=autostart")
        tray, cli = APP_DIR / "EinsteinRemoteAgent.exe", APP_DIR / "einstein-agent.exe"
        check(tray.exists() and cli.exists(), f"programa instalado em {APP_DIR}")
        check(SHORTCUT.exists(), "atalho no Menu Iniciar")
        run_value = reg(RUN_KEY, RUN_VALUE)
        if run_value != f'"{tray}"':
            log = (tmp / "install.log").read_text("utf-8", errors="replace")
            print("\n".join(l for l in log.splitlines() if "nício automático" in l or "Tasks" in l or "ERRO" in l)[-4000:])
        check(run_value == f'"{tray}"', f"início automático aponta para o agente da bandeja ({run_value!r})")
        check(reg(UNINSTALL_KEY, "DisplayVersion") == version, f"aparece em Aplicativos (versão {version})")
        out = subprocess.run([str(cli), "--version"], capture_output=True, text=True, encoding="utf-8").stdout
        check(version in out, "einstein-agent.exe instalado executa")

        print("2. Vincular e ficar online pela bandeja")
        r = subprocess.run([str(cli), "pair", "--server", base, "--code", code], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        check(r.returncode == 0 and (CONFIG_DIR / "agent.json").exists(), "vinculado (configuração em %APPDATA%)")
        tray_procs.append(subprocess.Popen([str(tray)]))
        wait_state(base, token, "online", need_metrics=True)
        check(True, "agente instalado online com métricas")

        print("3. Atualização com o agente aberto")
        run_setup(setup, tmp / "upgrade.log")
        try:
            tray_procs[0].wait(timeout=30)
        except subprocess.TimeoutExpired:
            raise SystemExit("FALHA: o instalador não fechou o agente em execução") from None
        check(True, "instalador fechou o agente antes de substituir os arquivos")
        wait_state(base, token, "offline")
        check((CONFIG_DIR / "agent.json").exists() and reg(RUN_KEY, RUN_VALUE), "vínculo e início automático mantidos")
        tray_procs.append(subprocess.Popen([str(tray)]))
        wait_state(base, token, "online", need_metrics=True)
        check(True, "online de novo após a atualização")

        print("4. Autoatualização do agente instalado (pacote oferecido pelo servidor)")
        tray_procs[1].kill()
        tray_procs[1].wait(timeout=30)
        wait_state(base, token, "offline")
        pkg_dir = tmp / "server" / "update" / "agent"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        zpath = pkg_dir / "EinsteinRemoteAgent-9.9.9.zip"
        import zipfile

        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in APP_DIR.rglob("*"):
                if f.is_file() and not f.name.lower().startswith("unins"):
                    z.write(f, f.relative_to(APP_DIR))
            z.writestr("update-marker.txt", "pacote 9.9.9")  # prova de que os arquivos foram trocados
        (pkg_dir / "package.json").write_text(json.dumps({
            "version": "9.9.9", "file": zpath.name, "size": zpath.stat().st_size,
            "sha256": hashlib.sha256(zpath.read_bytes()).hexdigest()}), encoding="utf-8")
        helper_log = Path(os.environ["LOCALAPPDATA"]) / "EinsteinRemote" / "logs" / "update-helper.log"
        first = subprocess.Popen([str(tray)])
        end = time.time() + 180
        while time.time() < end and not (APP_DIR / "update-marker.txt").exists():
            time.sleep(1)
        log_text = helper_log.read_text("utf-8-sig", errors="replace") if helper_log.exists() else "(sem update-helper.log)"
        print(log_text)
        check((APP_DIR / "update-marker.txt").exists(), "ajudante trocou os arquivos do programa")
        try:
            first.wait(timeout=30)
        except subprocess.TimeoutExpired:
            raise SystemExit("FALHA: o agente não fechou para ser atualizado") from None
        check(True, "agente fechou sozinho para a troca")
        wait_state(base, token, "online", timeout=90)
        check("agente reaberto" in log_text or "agente reaberto" in helper_log.read_text("utf-8-sig", errors="replace"),
              "ajudante reabriu o agente e ele voltou online")
        time.sleep(20)  # o agente reaberto continua com a versão antiga do executável: não pode entrar em ciclo
        devs = call(base, "/api/devices", token=token)
        check(devs[0]["state"] == "online", "continua online (sem ciclo de atualização)")
        starts = helper_log.read_text("utf-8-sig", errors="replace").count("ajudante iniciado")
        check(starts == 1, f"um único pedido de atualização ({starts})")
        notes = [n["message"] for n in call(base, "/api/notifications", token=token) if n["kind"] == "update"]
        check(any("não foi aplicada" in n for n in notes), "servidor avisou que a versão não mudou e não insistiu")

        print("5. Desinstalação silenciosa com /PURGE")
        uninstaller = APP_DIR / "unins000.exe"
        check(uninstaller.exists(), "desinstalador presente")
        subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/PURGE"], timeout=120)
        wait_gone([APP_DIR, CONFIG_DIR])  # o desinstalador continua a partir de uma cópia temporária
        # o agente reaberto pelo ajudante não é processo filho deste teste: confere pelo servidor e pela lista de processos
        wait_state(base, token, "offline")
        running = subprocess.run(["tasklist", "/FI", "IMAGENAME eq EinsteinRemoteAgent.exe"], capture_output=True, text=True).stdout
        check("EinsteinRemoteAgent.exe" not in running, "nenhum agente rodando após a desinstalação")
        check(True, "agente encerrado (servidor o vê offline)")
        check(not APP_DIR.exists(), "pasta do programa removida")
        check(not SHORTCUT.exists(), "atalho removido")
        check(reg(RUN_KEY, RUN_VALUE) is None, "início automático removido")
        check(reg(UNINSTALL_KEY, "DisplayVersion") is None, "removido de Aplicativos")
        check(not CONFIG_DIR.exists(), "vínculo e configuração apagados (/PURGE)")
        print("\nInstalador OK.")
        return 0
    finally:
        for p in tray_procs:
            if p.poll() is None:
                p.kill()
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    sys.exit(main())
