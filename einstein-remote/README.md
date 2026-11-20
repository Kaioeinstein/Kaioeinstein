# EINSTEIN REMOTE

> **Seu computador. Seu controle. Em qualquer lugar.**

Plataforma para monitorar e controlar **o seu próprio** computador Windows a partir do celular,
do tablet ou de outro computador — com autenticação forte, vínculo de dispositivos e auditoria completa.

```
einstein-remote/
├── server/            # Servidor FastAPI (auth, relay WebSocket, SQLite, auditoria)
│   ├── app/
│   └── requirements.txt
├── agent/             # EINSTEIN REMOTE AGENT (Windows; Python, migrável p/ C#)
│   ├── einstein_agent/
│   └── pyproject.toml
├── web/               # EINSTEIN REMOTE DASHBOARD + MOBILE (PWA, HTML/CSS/JS)
├── installer/         # Instalador Windows (Inno Setup), build .exe (PyInstaller), testes de fumaça
├── deploy/            # Produção: Caddy (HTTPS), systemd, teste do docker compose
├── Dockerfile, docker-compose.yml
├── scripts/           # Iniciar servidor, gerar certificados TLS
├── tests/             # Testes unitários, de API e ponta a ponta
└── docs/              # Arquitetura, segurança, manual
```

## Início rápido
**Uso real:** servidor com `EINSTEIN_DOMAIN=remote.seudominio.com docker compose up -d` ([guia](deploy/README.md)) e,
no PC, o instalador `EinsteinRemoteAgent-1.0.0-Setup.exe` ([manual](docs/MANUAL.md#guia-rápido-15-minutos)).

Desenvolvimento:
```bash
pip install -r requirements-dev.txt
./scripts/run-server.sh                   # http://127.0.0.1:8000 → criar conta → Dispositivos → Gerar código
cd agent && pip install . && einstein-agent pair --server http://127.0.0.1:8000 --code XXXX-XXXX && einstein-agent run
```

- [Arquitetura e fases](docs/ARCHITECTURE.md)
- [Modelo de segurança](docs/SECURITY.md)
- [Manual de utilização](docs/MANUAL.md)
- [Testes, carga e estabilidade](docs/TESTING.md)
- [Servidor em produção e publicação de versões](deploy/README.md)
- [Histórico de versões](CHANGELOG.md)

**Status:** versão **1.0.0** — as 10 fases concluídas. Instalador Windows testado no CI (instalar, vincular, atualizar, desinstalar sem resíduos); servidor em Docker + HTTPS testado com agente real; 500 computadores simultâneos ([testes](docs/TESTING.md)).
