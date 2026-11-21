"""Diagnóstico com um clique (bandeja): relatório legível sem precisar do PowerShell."""
from einstein_agent import __version__, diagnostics
from einstein_agent.config import AgentConfig
from test_agent import live_server  # noqa: F401


def test_report_unpaired_does_not_crash():
    text = diagnostics.report()
    assert "== Agente ==" in text and __version__ in text and "não vinculado" in text


def test_report_with_real_server(live_server):  # noqa: F811
    from einstein_agent.pairing import pair

    base, http = live_server
    pair(base, http.post("/api/pairing-codes", json={"device_name": "PC Diag"}).json()["code"])
    text = diagnostics.report()
    assert "vinculado: sim — PC Diag" in text
    assert "conexão com o servidor agora: OK" in text and "servidor v" in text
    path = diagnostics.save_report()
    assert path.name == "diagnostico.txt" and "== Registro do agente (fim) ==" in path.read_text(encoding="utf-8-sig")


def test_report_shows_unreachable_server():
    AgentConfig(server_url="http://127.0.0.1:9", device_id="d", device_secret="plain:s", device_name="PC").save()
    assert "conexão com o servidor agora: FALHOU" in diagnostics.report()


def test_repair_without_local_server_is_harmless():
    assert diagnostics.repair_server() is False  # sem servidor neste PC: nada acontece
