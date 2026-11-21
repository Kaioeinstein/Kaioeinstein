# Changelog — EINSTEIN REMOTE

Formato: [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) · versões [SemVer](https://semver.org/lang/pt-BR/).

## [1.3.1] — 2026-11-21
### Corrigido
- **PC ficava offline em ciclo após a atualização automática do agente** (encontrado no uso real): o agente fechava
  para ser trocado, voltava na versão antiga e o servidor pedia de novo a cada conexão. Agora o servidor faz um
  único pedido por versão e, se o agente voltar antigo, avisa e não insiste; o agente também recusa repetir uma
  tentativa que não pegou.
- Ajudante de atualização: console oculto (o PowerShell sem console podia não rodar), espera o agente fechar,
  encerra qualquer cópia aberta da pasta, confere a cópia (robocopy) e grava `update-helper.log` na pasta de logs.
- CI: autoatualização real do agente instalado pelo instalador (baixa, confere SHA-256, troca, reabre, sem ciclo).

## [1.3.0] — 2026-11-21
### Adicionado
- **Volume do PC** pelo painel (celular e computador): controle deslizante, − / + (10%) e silenciar, com o ícone
  mostrando o estado atual. Usa a API de áudio do Windows (Core Audio) — o mesmo controle do ícone de som.
  Na bandeja do PC: **Permitir controlar o volume pelo painel** (o dono pode bloquear).

## [1.2.0] — 2026-11-21
### Adicionado
- **Atualização automática.** Ajustes › **Atualizações** mostra a versão instalada e a publicada; o servidor
  verifica o repositório do projeto a cada 30 min e avisa quando há versão nova. **Atualizar agora**
  (proprietário, com confirmação): o servidor baixa a versão nova, guarda uma cópia da atual, troca os arquivos
  (dados, chaves e certificados ficam), monta o novo agente e religa. **Se a versão nova não responder em 90 s,
  a anterior volta sozinha.**
- **Agentes se atualizam sozinhos**: ao conectar com versão antiga, o PC recebe o pacote do seu servidor (link de
  uso único), confere tamanho e SHA-256, troca os arquivos e reabre o ícone da bandeja — vínculo e permissões
  mantidos. Pode ser desligado em Ajustes › Atualizações.

### Corrigido
- Painel guardado pelo navegador depois de uma atualização (agora sempre revalidado).

## [1.1.0] — 2026-11-21
### Adicionado
- **Otimizar** (nova página do painel, também no celular):
  - **Modo jogo automático**: quando um jogo abre (Steam, Epic, Riot, Xbox, EA, Ubisoft, GOG, Rockstar ou
    programas escolhidos pelo dono), o Windows passa para *Melhor desempenho*; quando fecha, volta ao modo
    anterior. Aviso no painel e no celular. No Acer Nitro, o modo de energia do Windows acompanha o NitroSense.
  - **Modo de energia** (Economia / Equilibrado / Melhor desempenho) e **plano de energia** pelo painel.
  - **Limpeza de disco**: mostra antes quanto cada categoria libera (temporários, cache dos navegadores,
    relatórios de erro, Lixeira) e só apaga após confirmação; nunca segue links, só pastas do usuário,
    preserva arquivos em uso ou alterados na última hora. Espaço livre de cada disco.
  - **Programas que iniciam com o Windows**: ativar/desativar como o Gerenciador de Tarefas (sem desinstalar).
  - Na bandeja do PC: **Permitir limpeza e otimização pelo painel** (o dono pode bloquear tudo).
- `setup-windows-server.ps1`: na tomada, o notebook não suspende mais nem com a **tampa fechada**
  (o servidor ficava fora do ar).

### Corrigido
- **Painel preso em "Suspensão em andamento…"** e Cancelar sem efeito: o agente agora avisa quando a
  suspensão termina (ou não acontece) e, sem nada pendente, o Cancelar faz o servidor descartar o aviso —
  inclusive com agentes antigos.
- PCs com **Modern Standby** (sem suspensão clássica S3): Suspender desliga a tela, que é como o Windows
  entra nesse modo (o pedido clássico era ignorado).
- Barra de navegação do celular com 7 botões cabe numa linha.

## [1.0.1] — 2026-11-21
### Corrigido
- **Suspender e hibernar pelo painel falhavam no Windows 64 bits** com "Não foi possível abrir o token do
  processo": o identificador do processo era truncado ao chamar a API do Windows. Teste no Windows real (CI).
- `scripts/gen-cert.sh` não funcionava no Git Bash do Windows.

### Adicionado
- `scripts/setup-windows-server.ps1`: o PC Windows vira servidor com um comando — Tailscale (acesso pelo 4G),
  certificado HTTPS, firewall restrito, início automático com o PC (religa se cair) e renovação do certificado.

## [1.0.0] — 2026-11-20
Primeira versão final.

### Adicionado
- **Instalador Windows** (`EinsteinRemoteAgent-1.0.0-Setup.exe`, Inno Setup): por usuário, sem administrador, em
  português; tela explicando o que o agente faz; início com o Windows **opcional e desmarcado**; atalho no Menu
  Iniciar; aparece em Configurações › Aplicativos; fecha o agente aberto para atualizar sem perder o vínculo.
  Desinstalação completa (programa, atalhos, início automático e, se o usuário quiser, vínculo e configuração).
  Modo silencioso `/VERYSILENT [/TASKS="autostart"]` e `unins000.exe /VERYSILENT [/PURGE]`.
- **Servidor em produção:** imagem Docker (usuário sem privilégios, verificação de saúde), `docker-compose.yml` com
  Caddy e HTTPS automático (Let's Encrypt, ou CA local para uso só em casa), serviço systemd com isolamento.
- **IP real atrás do proxy** (`--proxy-headers`): bloqueio de força bruta e auditoria por visitante.
- `python -m app.admin backup <pasta>`: backup consistente com o servidor em uso (banco + chaves); restauração documentada.
- **Release automático** ao criar a tag `vX.Y.Z`: testes, executáveis, instalador, testes de fumaça, assinatura
  de código opcional e Release com `SHA256SUMS.txt`.
- CI: testes do instalador (instalar → vincular → online → atualizar com o agente aberto → desinstalar sem resíduos)
  e do conjunto Docker + Caddy com um agente real via `wss://`.
- Guia rápido no manual e guia de implantação (`deploy/README.md`) com lista de verificação de segurança.

## [0.9.0] — Fase 9: testes e correções
- Teste de carga (500 computadores, 10 painéis), estabilidade (reinício do servidor, painéis travados).
- Fila de envio por painel: um celular com rede ruim não atrasa os demais.
- GPU de qualquer fabricante e temperatura no Windows (contadores PDH; LibreHardwareMonitor opcional).
- Correções: limitador de tentativas, alertas de força bruta (1/min por IP), DNS rebinding no push,
  programas do Windows por caminho absoluto. Análise estática (bandit) sem pendências.

## [0.8.0] — Fase 8: segurança
- 2FA (TOTP, RFC 6238) com QR code e códigos de recuperação; opção "exigir 2FA" para todos.
- Alerta de acesso por aparelho/rede novo; encerrar todas as outras sessões.
- Notificações push com o app fechado (Web Push RFC 8291/8292, sem serviços de terceiros além do push do navegador).

## [0.7.0] — Fase 7: arquivos
- Pastas liberadas pelo dono (somente leitura por padrão), navegação, download/upload com SHA-256, cópia entre PCs.

## [0.6.0] — Fase 6: energia
- Desligar, reiniciar, suspender, hibernar e bloquear, com contagem regressiva e cancelamento; Wake-on-LAN.

## [0.5.0] — Fase 5: tela, mouse e teclado
- Tela remota com aviso permanente "Tela compartilhada" e botão Encerrar; modos permitir/perguntar/bloquear.
- Mouse e teclado remotos (acentos incluídos), desativáveis pelo dono.

## [0.4.0] — Fase 4: SYSTEM MONITOR
- Processos (CPU por processo como o Gerenciador de Tarefas), aplicativos, serviços, hardware, rede; histórico e alertas.
- Encerrar processo com confirmação, verificação de identidade e lista de processos protegidos.

## [0.3.0] — Fase 3: agente Windows
- Agente na bandeja (estado visível, pausar, desvincular), vínculo por código, credencial protegida por DPAPI,
  início automático somente quando o dono ativa, executável PyInstaller.

## [0.2.0] — Fase 2: interface
- Painel e versão celular (PWA) com tema escuro, gráficos em tempo real e notificações.

## [0.1.0] — Fase 1: arquitetura
- Servidor FastAPI, autenticação (senha scrypt, JWT + sessão rotativa), WebSocket, SQLite, auditoria.
