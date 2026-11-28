"""Assistente EINSTEIN no terminal: robô com expressões e comandos executados no próprio PC."""
import unicodedata

import pytest

from einstein_agent import assistant_cli as ac
from einstein_agent import audio, cli
from test_audio import fake_mixer  # noqa: F401


def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def test_robot_faces_are_aligned():
    for face in ac.FACES:
        lines = ac.robot(face, color=False)
        assert len({_width(l) for l in lines[2:8]}) == 1, face  # cabeça: todas as linhas com a mesma largura
        assert all(_width(l) <= 40 for l in lines)


@pytest.fixture
def brain(monkeypatch):
    monkeypatch.setenv("EINSTEIN_POWER_DRY_RUN", "1")
    from einstein_agent import power

    power.DRY_RUN_LOG.clear()
    return ac.Brain()


def test_greeting_help_time_and_unknown(brain):
    assert brain.handle("Oi!")[1].startswith("👋")
    assert "volume" in brain.handle("ajuda")[1]
    assert "🕒" in brain.handle("que horas são?")[1]
    face, text = brain.handle("xablau")
    assert face == "triste" and "ajuda" in text


def test_status_uses_real_metrics(brain):
    face, text = brain.handle("como está o pc?")
    assert face in ("feliz", "alerta") and "CPU" in text and "RAM" in text


def test_volume_commands_run_locally_even_if_panel_blocked(brain, fake_mixer):  # noqa: F811
    from einstein_agent.config import AgentConfig

    cfg = AgentConfig.load()
    cfg.allow_volume = False  # bloqueio vale para o painel remoto, não para o dono no teclado
    cfg.save()
    assert "30%" in brain.handle("volume 30")[1]
    assert "40%" in brain.handle("aumentar o volume")[1]
    assert brain.handle("silenciar")[0] == "dormindo"
    assert fake_mixer["muted"] is True
    assert "50%" in brain.handle("volume 50")[1] and fake_mixer["muted"] is False  # definir volume tira do mudo


def test_power_requires_confirmation(brain):
    from einstein_agent import power

    face, text = brain.handle("desligar em 10 minutos")
    assert face == "alerta" and "sim" in text and not power.DRY_RUN_LOG
    assert "Cancelado" in brain.handle("não")[1] and not power.DRY_RUN_LOG
    brain.handle("reiniciar em 2 minutos")
    assert "✅" in brain.handle("sim")[1]
    assert any(c[1] in ("/r", "-r") for c in power.DRY_RUN_LOG)


def test_cli_command_registered(monkeypatch):
    called = {}
    monkeypatch.setattr(ac, "run", lambda voice=True: called.setdefault("voice", voice) and 0 or 0)
    assert cli.main(["assistente", "--sem-voz"]) == 0 and called["voice"] is False
