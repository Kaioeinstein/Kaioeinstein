# EINSTEIN REMOTE — Arquitetura

## Visão geral

```
 ┌───────────────────────┐        WSS (saída)        ┌──────────────────────────┐        HTTPS/WSS        ┌──────────────────────┐
 │ EINSTEIN REMOTE AGENT │ ────────────────────────► │  EINSTEIN REMOTE SERVER  │ ◄────────────────────── │ DASHBOARD (web/PWA)  │
 │  PC Windows           │   /ws/agent               │  FastAPI + SQLite        │  /api/*  /ws/client     │ desktop · tablet ·   │
 │  psutil · comandos    │ ◄──── comandos ────────── │  auth · relay · auditoria│ ──── métricas ────────► │ celular (MOBILE)     │
 └───────────────────────┘                           └──────────────────────────┘                         └──────────────────────┘
```

**Por que relay?** O agente abre uma conexão de *saída* para o servidor. O PC não precisa
expor portas, abrir o roteador nem ter IP público. O servidor pode rodar no próprio PC (rede
local), num Raspberry Pi, ou numa VPS com HTTPS para acesso de qualquer lugar.

## Componentes

| Componente | Pasta | Tecnologia | Responsabilidade |
|---|---|---|---|
| Servidor | `server/app` | Python 3.10+, FastAPI, Uvicorn, SQLite, PyJWT | Autenticação, sessões, vínculo de dispositivos, relay WebSocket, alertas, auditoria, histórico |
| Agente | `agent/einstein_agent` | Python 3.10+, psutil, websockets (migrável para C#) | Coleta de métricas, execução de comandos da allowlist, pareamento, pausa/auto‑início |
| Dashboard + Mobile | `web/` | HTML, CSS, JavaScript (ES modules), Canvas | Interface responsiva única (PWA instalável no celular) |

### Por que não React?
O painel é um conjunto de telas com atualização em tempo real via WebSocket. JavaScript
puro com módulos ES atende sem build, sem `node_modules` e com uma CSP estrita
(`script-src 'self'`). A versão **MOBILE** é a mesma PWA com layout próprio (navegação
inferior, gauges 2×2), instalável pela opção "Adicionar à tela inicial" — sem duplicar código.

## Módulos do servidor

| Arquivo | Função |
|---|---|
| `config.py` | Configuração por variáveis `EINSTEIN_*`; gera a chave secreta na 1ª execução |
| `security.py` | scrypt, JWT, tokens opacos, códigos de pareamento, rate limiter |
| `db.py` | Única camada que toca o SQLite (facilita migração para PostgreSQL) |
| `auth.py` | Setup inicial, login, refresh com rotação, logout, sessões |
| `devices.py` | Pareamento, dispositivos, histórico, notificações (lidas por usuário), auditoria |
| `users.py` | Usuários, papéis, acesso por dispositivo, configurações de alertas |
| `preferences.py` | Limites e tipos de notificação persistidos no banco |
| `hub.py` | Conexões em tempo real filtradas por permissão, allowlist de ações, alertas configuráveis, comandos pendentes |
| `ws.py` | Endpoints `/ws/agent` e `/ws/client` |
| `main.py` | App, cabeçalhos de segurança, arquivos estáticos, tarefas periódicas |

## Banco de dados (SQLite)

`users`, `user_devices`, `sessions`, `devices`, `pairing_codes`, `audit_log`, `notifications`, `metrics`, `settings`.
Colunas novas são adicionadas automaticamente na inicialização (migração sem perda de dados).
Senhas, refresh tokens, segredos de dispositivo e códigos de pareamento são guardados
**apenas como hash**.

## Protocolo WebSocket

Toda conexão se autentica na **primeira mensagem** (nunca na URL):

```jsonc
// agente → servidor
{"type":"auth","device_id":"…","secret":"…","info":{hostname, os, cpu_model, ip, …}}
{"type":"metrics","data":{ts, cpu:{percent,per_core,freq_mhz}, memory:{…}, disks:[…], network:{down_bps,up_bps}, gpu:[…], temperatures:{max,sensors}, uptime, process_count}}
{"type":"result","id":"<cmd>","ok":true,"data":{…},"error":null}

// servidor → agente
{"type":"command","id":"<cmd>","action":"ping","params":{}}

// cliente → servidor
{"type":"auth","token":"<access JWT>"}
{"type":"command","request_id":"r1","device_id":"…","action":"ping","params":{},"confirm":false}

// servidor → cliente
{"type":"hello","user":{…},"devices":[…]}
{"type":"device"|"device_removed"|"metrics"|"notification"|"ack"|"result", …}
```

### Fluxo de um comando
1. Cliente envia `command` → servidor revalida a sessão no banco.
2. Servidor confere **allowlist** (`hub.ALLOWED_ACTIONS`), **papel** mínimo, **acesso ao dispositivo** e **confirmação** (ações críticas).
3. Registra `command.sent` na auditoria, responde `ack` e encaminha ao agente.
4. Agente confere a **sua própria allowlist** (`commands.REGISTRY`) e executa.
5. Resultado volta ao cliente que pediu; `command.result` é auditado. Timeout: 30 s.

Defesa em profundidade: mesmo com o servidor comprometido, o agente só executa ações registradas localmente.

## Fases

| Fase | Entrega | Status |
|---|---|---|
| 1 | Arquitetura, estrutura, servidor, auth, pareamento, agente, métricas em tempo real, dashboard base, testes | ✅ concluída |
| 2 | Tela inicial com atalhos, usuários e permissões por dispositivo, limites de alerta configuráveis, troca de senha | ✅ concluída |
| 3 | Agente Windows: ícone na bandeja (pausar/retomar/sair), log local, empacotamento .exe | próxima |
| 4 | SYSTEM MONITOR: processos, apps abertos, serviços, hardware, rede; encerrar processo com confirmação | |
| 5 | Tela em tempo real (captura DXGI/mss → JPEG/WebP), mouse e teclado (SendInput), tela cheia, consentimento visível | |
| 6 | Energia: desligar, reiniciar, suspender, hibernar, cancelar (`shutdown /a`) com confirmação | |
| 7 | Arquivos: navegação restrita a pastas autorizadas, upload/download em blocos com verificação SHA‑256 | |
| 8 | Segurança avançada: 2FA TOTP, gestão de usuários, políticas por dispositivo, alertas por e‑mail/push | |
| 9 | Testes de carga, testes no Windows real, correções | |
| 10 | Instalador (.exe com Inno Setup + PyInstaller), servidor como serviço, versão 1.0 | |
