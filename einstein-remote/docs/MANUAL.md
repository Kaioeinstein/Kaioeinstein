# EINSTEIN REMOTE — Manual de utilização (v0.9, Fase 9)

## 1. Requisitos
- **Servidor:** Python 3.10+ (Windows, Linux ou macOS). Pode ser o próprio PC.
- **Agente:** Windows 10/11 com Python 3.10+ (o instalador .exe chega na Fase 10).
- **Acesso:** qualquer navegador moderno (Chrome, Edge, Safari, Firefox), no PC, tablet ou celular.

## 2. Instalar e iniciar o servidor
```bash
cd einstein-remote
python -m venv .venv
# Windows: .venv\Scripts\activate    |   Linux/macOS: source .venv/bin/activate
pip install -r server/requirements.txt
./scripts/run-server.sh            # Windows: .\scripts\run-server.ps1
```
Abra `http://127.0.0.1:8000` e crie a **conta do proprietário** (só é possível uma vez).

### HTTPS (obrigatório para acesso fora do PC)
```bash
./scripts/gen-cert.sh meu-pc.local 192.168.0.10   # gera certs/ (requer openssl)
./scripts/run-server.sh --tls                       # https://0.0.0.0:8443
```
Instale `certs/ca.crt` como confiável no celular, ou use um domínio com certificado válido.

## 3. Instalar o agente no Windows e vincular

### Opção A — executável (sem Python)
1. Baixe `EinsteinRemoteAgent-<versão>-win64.zip` (artefato do GitHub Actions › workflow **EINSTEIN REMOTE**, ou gere com `installer\build-agent.ps1`).
2. Extraia numa pasta (ex.: `C:\Program Files\EinsteinRemote` ou `%LOCALAPPDATA%\EinsteinRemote\app`).
3. Execute **`EinsteinRemoteAgent.exe`**. Na primeira vez abre a janela **Vincular computador**.

> O executável ainda não tem assinatura digital: o Windows SmartScreen pode avisar "Editor desconhecido" → *Mais informações › Executar assim mesmo*. A assinatura e o instalador `.exe` com desinstalação pelo Windows chegam na Fase 10.

### Opção B — a partir do código-fonte (com Python 3.10+)
```powershell
powershell -ExecutionPolicy Bypass -File installer\install-agent.ps1
```
Cria o atalho **Menu Iniciar › EINSTEIN REMOTE AGENT** e abre o agente na bandeja.

### Vincular
1. No painel: **Dispositivos** → nome do computador → **Gerar código** (vale 10 min, uso único).
2. Na janela do agente: endereço do servidor (ex.: `https://meu-servidor:8443`), o código e, se usar certificado próprio, o arquivo `ca.crt` → **Vincular**.
3. O ícone fica verde e o painel mostra o PC **Online**.

Pela linha de comando: `einstein-agent pair --server https://SEU-SERVIDOR:8443 --code ABCD-EFGH [--ca-file ca.crt]`.
Teste local sem TLS: `--server http://127.0.0.1:8000`.

### Ícone na bandeja
| Cor | Significado |
|---|---|
| 🟢 verde | Online, enviando dados ao painel |
| 🟡 amarelo | Conectando ou **pausado por você** |
| 🟣 roxo | Sua tela está sendo compartilhada |
| 🔴 vermelho | Sem conexão (tenta de novo sozinho) ou vínculo revogado |
| ⚪ cinza | Não vinculado |

Menu (clique com o botão direito): **Abrir painel** · **Pausar/Retomar monitoramento** · **Encerrar compartilhamento de tela** (durante uma sessão) · **Acesso à tela** (permitir / perguntar / bloquear) · **Permitir controle de mouse e teclado** · **Iniciar com o Windows** · **Permitir encerrar processos pelo painel** · **Permitir desligar/reiniciar pelo painel** · **Pastas liberadas para o painel…** · **Cancelar … agendado(a)** (quando houver) · **Ver registro (log)** · **Abrir pasta de configuração** · **Desvincular este computador…** · **Sobre** · **Sair**.
Se o vínculo for revogado no painel, aparece **Vincular este computador…** para refazer.

## 4. Comandos do agente
| Comando | Efeito |
|---|---|
| `einstein-agent tray` | Executa com ícone na bandeja (modo recomendado) |
| `einstein-agent run` | Executa no terminal (diagnóstico) |
| `einstein-agent logs [-n 40]` | Mostra as últimas linhas do registro |
| `einstein-agent status` | Mostra versão, vínculo, servidor, se está em execução, pausa e auto‑início |
| `einstein-agent pause` / `resume` | Pausa/retoma (o agente em execução obedece em até 2 s) |
| `einstein-agent autostart enable` / `disable` | Liga/desliga o início com o Windows (visível em Gerenciador de Tarefas › Inicializar) |
| `einstein-agent remote-kill enable` / `disable` | Permite/bloqueia neste PC o encerramento de processos pelo painel |
| `einstein-agent screen-access allow` / `ask` / `deny` | Acesso remoto à tela: sem perguntar / perguntar a cada sessão / bloqueado |
| `einstein-agent remote-input enable` / `disable` | Permite/bloqueia controle de mouse e teclado |
| `einstein-agent power-control enable` / `disable` | Permite/bloqueia desligar, reiniciar, suspender, hibernar e bloquear pelo painel |
| `einstein-agent folders` · `folders add <pasta> [--write]` · `folders remove <pasta\|nº>` | Pastas que o painel pode acessar (nenhuma por padrão) |
| `einstein-agent uninstall` | Remove vínculo, configuração e auto‑início |

## 5. Painel
- **Tela inicial:** marca, status do computador selecionado, monitor com mini‑gráfico e indicadores ao vivo, e atalhos (testar latência, histórico, vincular PC, alertas, sessões, ajustes).
- **Painel:** status, sistema, hostname, IP, tempo ligado, CPU, RAM, GPU (NVIDIA), temperatura (quando o SO expõe sensores), discos, rede ↓/↑, núcleos, gráficos em tempo real, histórico (1h/6h/24h/7d), últimas atividades, teste de latência.
- **Monitor (SYSTEM MONITOR):** consulta o computador selecionado em tempo real, com atualização automática (desligável) e busca:
  - *Processos* — CPU (como no Gerenciador de Tarefas), memória, usuário, threads; ordenação por coluna; **Encerrar** (administradores, com confirmação).
  - *Aplicativos* — programas com janela aberta e seus títulos.
  - *Serviços* — serviços do Windows, status e tipo de inicialização (filtro em execução/parados).
  - *Hardware* — fabricante/modelo, placa-mãe, BIOS, processador, memória, placas de vídeo, discos, bateria.
  - *Rede* — velocidade atual, totais, conexões por estado e cada interface (IPv4/IPv6/MAC/velocidade).
  Processos essenciais do Windows e o próprio agente aparecem como *protegido*.
- **Tela remota** (*Ver tela* na tela inicial ou *Acessar tela* no cartão do computador — administradores):
  - Imagem ao vivo; qualidade **Econômica / Equilibrada / Alta / Máxima** (resolução e compressão da transmissão); seleção de **monitor**; **Tela cheia**.
  - **Controle ligado/desligado.** Com controle: mouse e teclado do seu computador vão para o PC remoto.
  - **Teclas**: atalhos que o seu navegador não deixa enviar (Win, Alt+Tab, Ctrl+Shift+Esc, Alt+F4, Win+D...).
  - **Celular/tablet:** toque = clique · toque longo = botão direito · arrastar = mover o ponteiro · dois dedos = rolar · botão **Teclado** abre o teclado do celular (acentos funcionam).
  - No PC aparece a faixa *“Tela compartilhada com …”* com botão **Encerrar**.
- **Central de energia** (no Painel, administradores): **Desligar** e **Reiniciar** (agora, 30 s, 1 min… até 1 h), **Suspender**, **Hibernar** (agora ou com prazo), **Bloquear** a tela e **Ligar** (Wake-on-LAN, quando o PC está offline).
  - Enquanto há uma ação agendada aparece a contagem regressiva com **Cancelar**; no PC, o Windows mostra o aviso e o ícone do agente oferece *Cancelar desligamento*.
  - Botões ficam desativados quando o hardware não suporta (ex.: hibernação desligada — ative com `powercfg /hibernate on` como administrador).
  - Depois do desligamento o computador aparece como **Desligado pelo painel** (ou *Reiniciando…*, *Suspenso*, *Hibernando*), não como “caiu”.
  - **Ligar (Wake-on-LAN)** só funciona se o servidor estiver na mesma rede local do PC e o Wake-on-LAN estiver ativo na BIOS/UEFI e na placa de rede (Gerenciador de Dispositivos › placa de rede › Gerenciamento de energia). Pela internet, use um servidor na sua rede (ex.: Raspberry Pi) ou VPN.
- **Arquivos** (administradores):
  - Primeiro, **no PC**, libere as pastas: ícone do agente › *Pastas liberadas para o painel…* › *Adicionar pasta…* (marque “Permitir enviar arquivos e criar pastas” se quiser escrita). Pelo terminal: `einstein-agent folders add "C:\Users\voce\Documents" --write`.
  - No painel: escolha a pasta liberada, navegue pelas subpastas, **Baixar** (usa o download normal do navegador, inclusive no celular), **Enviar arquivos** (ou arraste e solte), **Nova pasta** e **Copiar para…** outro computador online.
  - Cada transferência é conferida com SHA-256; o painel mostra “Enviado e verificado”. Se já existir um arquivo com o mesmo nome, o novo recebe “nome (1).ext”.
  - Limite de upload do servidor: 4 GB por arquivo (ajustável com `EINSTEIN_MAX_UPLOAD_MB`).
- **Verificação em duas etapas** (Ajustes › Verificação em duas etapas): *Ativar* → escaneie o QR code no Google/Microsoft Authenticator, Authy ou 1Password → digite o código → **guarde os 10 códigos de recuperação** (botão *Baixar*). A partir daí o login pede o código de 6 dígitos. O proprietário pode *Exigir 2FA de todos* (em Usuários e permissões) e *Redefinir 2FA* de quem perdeu o celular.
- **Notificações push** (Alertas › Notificações push neste aparelho): *Ativar* em cada celular/computador, escolhendo o nível (todas, avisos e críticas, só críticas), e *Enviar teste*. Exigem **HTTPS** (ou localhost). No iPhone/iPad: Compartilhar › *Adicionar à Tela de Início* e abrir por lá (iOS 16.4+). O servidor precisa de acesso à internet para entregar ao serviço de push do navegador.
- **Dispositivos:** gerar código, renomear, revogar.
- **Alertas:** conexão/desconexão, CPU alta, RAM no limite, temperatura alta, tentativas de acesso; opção de alertas nativos do navegador.
- **Segurança:** sessões ativas (revogar) e registro de auditoria.
- **Ajustes:**
  - *Limites de alerta* — CPU, RAM e temperatura; por quanto tempo o valor precisa se manter alto; intervalo mínimo entre alertas repetidos; quais eventos notificar (inclui "comandos executados"). Tentativas de acesso não autorizadas são sempre notificadas.
  - *Minha conta* — troca de senha (encerra suas outras sessões).
  - *Usuários e permissões* (só o proprietário) — criar Administradores e Observadores, escolher se acessam todos os computadores ou só alguns, redefinir senha, remover. As mudanças valem na hora, sem o usuário precisar sair.

### Celular
Abra o endereço do servidor no navegador e use **Adicionar à tela inicial** — o EINSTEIN REMOTE MOBILE abre como app, com navegação inferior.

## 6. Limitações conhecidas (honestas)
- **Temperatura no Windows:** o painel usa as zonas térmicas ACPI do Windows (quando o fabricante as expõe) e a temperatura da GPU NVIDIA. Para temperaturas detalhadas de CPU/GPU/placa-mãe, deixe o [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) aberto (ele precisa rodar como administrador para ler os sensores; o agente lê os valores que ele publica no WMI, a cada 15 s). Sem nenhuma dessas fontes, o painel mostra “Sensor indisponível” em vez de inventar um valor.
- **GPU:** uso de qualquer placa (NVIDIA, AMD, Intel) pelos contadores do Windows 10/11 — o mesmo número do Gerenciador de Tarefas; com `nvidia-smi` instalado, também a temperatura da NVIDIA.
- Tela remota, mouse/teclado, energia, processos e arquivos chegam nas Fases 4–7 e **ainda não aparecem** no painel.
- **Tela remota:** `Ctrl+Alt+Del`, janelas de UAC ("Deseja permitir...") e a tela de bloqueio do Windows não aceitam mouse/teclado remotos — proteção do próprio Windows. Programas executados como administrador também ignoram a entrada se o agente não estiver elevado.
- **Desempenho da tela:** ~10–15 quadros/s em rede boa; em rede lenta a taxa cai automaticamente (sem atraso acumulado). Telas paradas quase não consomem banda.
- Só um agente roda por usuário do Windows; abrir de novo avisa que já está em execução.
- Registro local: `%LOCALAPPDATA%\EinsteinRemote\logs\agent.log` (rotativo, até ~6 MB).
- Notificações push com o app fechado exigem HTTPS e um navegador compatível (Chrome, Edge, Firefox, Safari 16.4+ instalado na Tela de Início).

## 7. Variáveis de ambiente do servidor
| Variável | Padrão | Descrição |
|---|---|---|
| `EINSTEIN_DATA_DIR` | `./data` | Banco e chave secreta |
| `EINSTEIN_SECRET_KEY` | gerada | Chave de assinatura (≥ 32 caracteres) |
| `EINSTEIN_ACCESS_TTL` | `900` | Validade do access token (s) |
| `EINSTEIN_REFRESH_TTL` | `604800` | Validade da sessão (s) |
| `EINSTEIN_COOKIE_SECURE` | `0` | `1` com HTTPS |

## 8. Recuperação no servidor
Se o **proprietário** perder o celular e os códigos de recuperação, no computador do servidor (pasta `einstein-remote`):
```bash
PYTHONPATH=server python -m app.admin reset-2fa kaio        # desativa o 2FA e encerra as sessões
PYTHONPATH=server python -m app.admin reset-password kaio   # define uma nova senha
```
(No Windows: `set PYTHONPATH=server` antes.) Exige acesso local à máquina do servidor — não é possível pela internet.

## 9. Testes
```bash
pip install -r requirements-dev.txt
python -m pytest -q
```
