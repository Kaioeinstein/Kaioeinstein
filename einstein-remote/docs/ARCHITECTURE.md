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
| Agente | `agent/einstein_agent` | Python 3.10+, psutil, websockets, pystray, tkinter (migrável para C#) | Coleta de métricas, comandos da allowlist, pareamento, ícone na bandeja, pausa, auto‑início, log local |
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
| `transfers.py` / `files_api.py` | Download por link de uso único com streaming e verificação SHA-256; upload em streaming; cópia direta entre dois computadores |
| `wol.py` | Wake-on-LAN: pacote mágico por broadcast (geral e da sub-rede privada do PC) |
| `screens.py` | Sessões de tela: quadros só para quem abriu a sessão, entrada só de quem tem controle (com limite de taxa), auditoria de início/fim |
| `hub.py` | Conexões em tempo real filtradas por permissão, allowlist de ações, alertas configuráveis, comandos pendentes |
| `ws.py` | Endpoints `/ws/agent` e `/ws/client` |
| `main.py` | App, cabeçalhos de segurança, arquivos estáticos, tarefas periódicas |

## Módulos do agente

| Arquivo | Função |
|---|---|
| `client.py` | Conexão WSS, reconexão com espera progressiva, **estados** observáveis (`connecting`, `online`, `offline`, `paused`, `rejected`, `stopped`) e aviso ao servidor do motivo de desconexão |
| `runner.py` | Executa o agente numa thread própria (usado pela bandeja) |
| `tray.py` | Ícone na bandeja: cor = estado; menu pausar/retomar, abrir painel, início com o Windows, log, desvincular, sair |
| `pair_window.py` | Janela de vinculação (servidor, código, CA própria) |
| `pairing.py` | Vinculação/desvinculação compartilhada por CLI e janela |
| `logs.py` | Log rotativo (1 MB × 5) em `%LOCALAPPDATA%\EinsteinRemote\logs` |
| `singleton.py` | Impede dois agentes no mesmo usuário |
| `sysmon.py` | SYSTEM MONITOR: processos (CPU por processo), janelas abertas (EnumWindows), serviços, hardware (registro/DMI), rede; encerramento seguro de processos |
| `screen.py` | Tela remota: captura (mss, thread dedicada), JPEG com cursor desenhado, quadros repetidos não reenviados, controle de fluxo por créditos, DPI-aware |
| `inputs.py` | Mouse e teclado: `SendInput` (Windows, Unicode para acentos) e XTEST (Linux); validação de eventos; solta teclas presas ao encerrar |
| `files.py` | Pastas liberadas pelo dono, validação de nomes/caminhos (`..`, absolutos, `:`, nomes reservados), `realpath` contra links que escapam, listagem, upload em temporário com verificação SHA-256 |
| `transfers.py` | Blocos de 256 KB pelo WebSocket com janela de confirmações (download 0x02, upload 0x03) |
| `folders_window.py` | Janela “Pastas liberadas” da bandeja |
| `power.py` | Energia: `shutdown /s|/r /t N` (agendamento nativo, cancelável com `/a`), `SetSuspendState` com privilégio, `LockWorkStation`, capacidades reais (`GetPwrCapabilities`), modo de simulação para testes |
| `overlay.py` | Faixa "tela compartilhada" sempre visível com botão Encerrar e janela de pedido de permissão (thread Tk dedicada) |
| `icons.py` | Ícones gerados em código (bandeja e `.ico` do executável) |
| `dialogs.py` | Caixas de mensagem nativas do Windows |
| `cli.py` | `tray`, `pair`, `run`, `status`, `pause`, `resume`, `logs`, `autostart`, `uninstall` |

### Distribuição
`installer/agent.spec` (PyInstaller, modo pasta) gera `EinsteinRemoteAgent.exe` (bandeja, sem console) e
`einstein-agent.exe` (linha de comando). O workflow `.github/workflows/einstein-remote.yml` roda os testes em
Linux e Windows, gera o executável no Windows, executa `installer/smoke_exe.py` contra um servidor real e publica
o `.zip` como artefato.

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
{"type":"status","state":"paused"|"stopping"}   // antes de fechar por decisão do proprietário

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
2. Servidor confere a política da ação em `hub.ALLOWED_ACTIONS` (**papel** mínimo, **confirmação** obrigatória e se é **auditada**) e o **acesso ao dispositivo**.
   Leituras atualizadas automaticamente (`system.processes`, `system.services`...) não são auditadas para não inundar o registro; ações que alteram algo sempre são.
3. Registra `command.sent` na auditoria, responde `ack` e encaminha ao agente.
4. Agente confere a **sua própria allowlist** (`commands.REGISTRY`) e executa.
5. Resultado volta ao cliente que pediu; `command.result` é auditado. Timeout: 30 s.

Defesa em profundidade: mesmo com o servidor comprometido, o agente só executa ações registradas localmente.

## Tela remota (Fase 5)

```
agente ──binário: 0x01|len|{seq,w,h,sw,sh}|JPEG──▶ servidor ──"S"+device_id+quadro──▶ navegador (canvas)
agente ◀──{"type":"screen_ack","seq"}───────────── servidor ◀──screen_ack────────────── navegador
agente ◀──{"type":"input","events":[...]}───────── servidor ◀──input (só controladores)── navegador
```
* No máximo 2 quadros sem confirmação: rede lenta recebe menos quadros, sem acumular atraso.
* Coordenadas trafegam normalizadas (0–1) e são convertidas no agente para o monitor transmitido.
* O servidor define quem está assistindo (`_user`, `_viewers`, `_control`); o cliente não consegue falsificar.

## Fases

| Fase | Entrega | Status |
|---|---|---|
| 1 | Arquitetura, estrutura, servidor, auth, pareamento, agente, métricas em tempo real, dashboard base, testes | ✅ concluída |
| 2 | Tela inicial com atalhos, usuários e permissões por dispositivo, limites de alerta configuráveis, troca de senha | ✅ concluída |
| 3 | Agente Windows: bandeja, janela de vinculação, log local, instância única, estados pausado/encerrado no painel, `.exe` + CI Windows | ✅ concluída |
| 4 | SYSTEM MONITOR: processos, aplicativos abertos, serviços, hardware, rede; encerrar processo com confirmação e travas | ✅ concluída |
| 5 | Tela em tempo real, mouse e teclado (desktop e toque), tela cheia, qualidade/resolução, multi-monitor, faixa de aviso e consentimento no PC | ✅ concluída |
| 6 | Energia: desligar, reiniciar, suspender, hibernar, bloquear, cancelar e ligar (Wake-on-LAN), com prazos e confirmação | ✅ concluída |
| 7 | Arquivos: pastas liberadas pelo dono, navegação, download, upload, nova pasta e cópia entre computadores com SHA‑256 | ✅ concluída |
| 8 | Segurança avançada: 2FA TOTP, gestão de usuários, políticas por dispositivo, alertas por e‑mail/push | próxima |
| 9 | Testes de carga, testes no Windows real, correções | |
| 10 | Instalador (.exe com Inno Setup + PyInstaller), servidor como serviço, versão 1.0 | |
