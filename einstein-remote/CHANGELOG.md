# Changelog — EINSTEIN REMOTE

Formato: [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) · versões [SemVer](https://semver.org/lang/pt-BR/).

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
