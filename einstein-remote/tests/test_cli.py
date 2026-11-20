"""Linha de comando do agente: todos os comandos que o dono usa no próprio PC."""
import sys

import pytest

from einstein_agent import cli
from einstein_agent.config import AgentConfig, config_path


def run(capsys, *args):
    code = cli.main(list(args))
    return code, capsys.readouterr().out


def test_status_unpaired(capsys):
    code, out = run(capsys, "status")
    assert code == 0 and "Vinculado:     não" in out and "Em execução:   não" in out


def test_pause_requires_pairing(capsys):
    code, out = run(capsys, "pause")
    assert code == 1 and "não está vinculado" in out


def test_pause_resume_and_switches(capsys):
    cfg = AgentConfig(server_url="http://127.0.0.1:1", device_id="d", device_secret="plain:s", device_name="PC")
    cfg.save()
    assert run(capsys, "pause")[0] == 0 and AgentConfig.load().paused is True
    assert run(capsys, "resume")[0] == 0 and AgentConfig.load().paused is False

    assert "BLOQUEADO" in run(capsys, "remote-kill", "disable")[1] and AgentConfig.load().allow_process_kill is False
    assert "PERMITIDO" in run(capsys, "remote-kill", "enable")[1]
    assert "BLOQUEADO" in run(capsys, "remote-input", "disable")[1] and AgentConfig.load().allow_remote_input is False
    assert "BLOQUEADO" in run(capsys, "power-control", "disable")[1] and AgentConfig.load().allow_power is False
    assert "PERGUNTAR" in run(capsys, "screen-access", "ask")[1] and AgentConfig.load().screen_access == "ask"
    _, out = run(capsys, "status")
    assert "Acesso à tela: perguntar a cada sessão" in out and "Controle de energia pelo painel: bloqueado" in out
    with pytest.raises(SystemExit):  # valor inválido é recusado pelo argparse
        cli.main(["screen-access", "sempre"])


def test_folders_commands(tmp_path, capsys):
    docs = tmp_path / "Docs"
    docs.mkdir()
    _, out = run(capsys, "folders")
    assert "Nenhuma pasta liberada" in out
    code, out = run(capsys, "folders", "add", str(docs), "--write")
    assert code == 0 and "leitura e escrita" in out
    code, out = run(capsys, "folders", "add", str(tmp_path / "naoexiste"))
    assert code == 1 and "não existe" in out
    _, out = run(capsys, "folders", "remove", "0")
    assert "Removida" in out


def test_logs_and_uninstall(capsys):
    AgentConfig(server_url="http://127.0.0.1:1", device_id="d", device_secret="plain:s").save()
    run(capsys, "status")  # gera algo no registro
    code, out = run(capsys, "logs", "-n", "5")
    assert code == 0
    code, out = run(capsys, "uninstall", "--yes")
    assert code == 0 and "Configuração removida" in out and not config_path().exists()


def test_autostart_outside_windows(capsys):
    if sys.platform == "win32":
        pytest.skip("no Windows o teste alteraria o registro do executor")
    code, out = run(capsys, "autostart", "enable")
    assert code == 1 and "apenas no Windows" in out


def test_run_unpaired(capsys):
    code, out = run(capsys, "run")
    assert code == 1 and "ainda não foi vinculado" in out
