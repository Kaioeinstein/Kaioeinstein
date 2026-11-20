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
├── installer/         # Instalador PowerShell, build .exe (PyInstaller), smoke test
├── scripts/           # Iniciar servidor, gerar certificados TLS
├── tests/             # Testes unitários, de API e ponta a ponta
└── docs/              # Arquitetura, segurança, manual
```

## Início rápido
```bash
pip install -r requirements-dev.txt
./scripts/run-server.sh                   # http://127.0.0.1:8000 → criar conta → Dispositivos → Gerar código
cd agent && pip install . && einstein-agent pair --server http://127.0.0.1:8000 --code XXXX-XXXX && einstein-agent run
```

- [Arquitetura e fases](docs/ARCHITECTURE.md)
- [Modelo de segurança](docs/SECURITY.md)
- [Manual de utilização](docs/MANUAL.md)

**Status:** Fase 7 concluída — arquivos (pastas liberadas pelo dono, download/upload/cópia entre PCs com SHA-256); central de energia; tela remota com controle; SYSTEM MONITOR; agente na bandeja; executável Windows testado no CI.
