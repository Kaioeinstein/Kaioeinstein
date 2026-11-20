# EINSTEIN REMOTE — Manual de utilização (v0.5, Fase 5)

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

Menu (clique com o botão direito): **Abrir painel** · **Pausar/Retomar monitoramento** · **Encerrar compartilhamento de tela** (durante uma sessão) · **Acesso à tela** (permitir / perguntar / bloquear) · **Permitir controle de mouse e teclado** · **Iniciar com o Windows** · **Permitir encerrar processos pelo painel** · **Ver registro (log)** · **Abrir pasta de configuração** · **Desvincular este computador…** · **Sobre** · **Sair**.
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
- **Temperatura no Windows:** o Windows não expõe sensores de CPU via psutil; o painel mostra a temperatura da GPU NVIDIA quando houver. Integração com LibreHardwareMonitor está prevista na Fase 4.
- **GPU:** uso medido via `nvidia-smi` (NVIDIA). AMD/Intel serão tratadas na Fase 4 (contadores de desempenho do Windows).
- Tela remota, mouse/teclado, energia, processos e arquivos chegam nas Fases 4–7 e **ainda não aparecem** no painel.
- **Tela remota:** `Ctrl+Alt+Del`, janelas de UAC ("Deseja permitir...") e a tela de bloqueio do Windows não aceitam mouse/teclado remotos — proteção do próprio Windows. Programas executados como administrador também ignoram a entrada se o agente não estiver elevado.
- **Desempenho da tela:** ~10–15 quadros/s em rede boa; em rede lenta a taxa cai automaticamente (sem atraso acumulado). Telas paradas quase não consomem banda.
- Só um agente roda por usuário do Windows; abrir de novo avisa que já está em execução.
- Registro local: `%LOCALAPPDATA%\EinsteinRemote\logs\agent.log` (rotativo, até ~6 MB).
- As notificações aparecem no painel e como alerta nativo do navegador (com a página aberta em segundo plano). Push com o app fechado chega na Fase 8.

## 7. Variáveis de ambiente do servidor
| Variável | Padrão | Descrição |
|---|---|---|
| `EINSTEIN_DATA_DIR` | `./data` | Banco e chave secreta |
| `EINSTEIN_SECRET_KEY` | gerada | Chave de assinatura (≥ 32 caracteres) |
| `EINSTEIN_ACCESS_TTL` | `900` | Validade do access token (s) |
| `EINSTEIN_REFRESH_TTL` | `604800` | Validade da sessão (s) |
| `EINSTEIN_COOKIE_SECURE` | `0` | `1` com HTTPS |

## 8. Testes
```bash
pip install -r requirements-dev.txt
python -m pytest -q
```
