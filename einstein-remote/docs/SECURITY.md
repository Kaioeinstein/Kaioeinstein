# EINSTEIN REMOTE — Modelo de segurança

## Princípios
1. **Somente acesso autorizado pelo proprietário.** O agente é visível (console/bandeja), pode ser pausado e desinstalado a qualquer momento. Não há persistência furtiva, captura de senhas ou acesso clandestino.
2. **Nada sem autenticação.** Toda rota (exceto `status`, `login`, `setup` inicial, `refresh` e `agent/pair`) exige token.
3. **Defesa em profundidade.** Allowlist de ações no servidor *e* no agente.
4. **Tudo auditado.**

## Controles implementados (Fase 1)

| Ameaça | Controle |
|---|---|
| Senha fraca / vazamento do banco | scrypt (N=16384, r=8) com salt; política mínima de 10 caracteres e 3 classes |
| Força bruta | Bloqueio após 5 falhas por IP **e** por usuário (5 min); notificação crítica "tentativa de acesso não autorizada" |
| Roubo de token | Access JWT de 15 min só em memória; refresh opaco em cookie `httpOnly`, `SameSite=Strict`, `Secure` (com TLS), **rotacionado** a cada uso |
| Sessão comprometida | Revogação imediata de qualquer sessão (validação no banco a cada requisição e a cada comando WS) |
| CSRF | `SameSite=Strict` + cabeçalho obrigatório `X-Requested-With` nas rotas de autenticação |
| XSS | CSP estrita (`script-src 'self'`, sem inline), escape de todo conteúdo dinâmico |
| Clickjacking | `X-Frame-Options: DENY`, `frame-ancestors 'none'` |
| Dispositivo falso | Pareamento por código de uso único (10 min, só hash no banco, rate‑limited); segredo de 48 bytes por dispositivo |
| PC perdido/roubado | Revogação do dispositivo no dashboard encerra a conexão e invalida a credencial |
| Credencial no disco do PC | Segredo cifrado com **Windows DPAPI** (vinculado à conta do usuário) |
| Interceptação | Agente recusa `ws://` fora de localhost; TLS 1.2+; suporte a CA própria (`--ca-file`) |
| Comando malicioso | Allowlist + papel mínimo + acesso ao dispositivo + confirmação explícita para ações críticas + auditoria |
| Agente com credencial roubada/revogada | Conexão recusada, auditada e notificada ("falha de conexão") |
| Credenciais em logs | Autenticação WS na primeira mensagem, não na URL |

## Papéis e permissões
| Papel | Pode |
|---|---|
| **owner** (proprietário, único) | Tudo, inclusive criar/alterar/remover usuários. Não pode ser removido nem rebaixado. |
| **admin** | Vincular/renomear/revogar dispositivos, alterar limites de alerta, ver auditoria, executar comandos |
| **viewer** | Apenas visualizar métricas e notificações dos dispositivos liberados |

**Acesso por dispositivo:** cada usuário (exceto o proprietário) tem acesso a *todos* os computadores ou só a uma lista.
O filtro é aplicado no servidor em **todas** as saídas: REST (`/api/devices`, histórico, notificações), snapshot
WebSocket, métricas em tempo real, eventos de dispositivo e comandos. Alertas globais de segurança (tentativas de login,
agentes recusados) só chegam a administradores.

Quando o proprietário altera papel, acesso ou senha de alguém, as conexões em tempo real desse usuário são encerradas
e reabertas com as novas permissões; redefinição de senha e remoção encerram **todas** as sessões do usuário.
Trocar a própria senha exige a senha atual e encerra as demais sessões.

## Transparência do agente (Fase 3)
- O agente roda com **ícone visível na bandeja**; a cor mostra o estado (verde online, amarelo pausado/conectando, vermelho sem conexão/recusado).
- Avisos do Windows informam quando o agente inicia e quando conecta ("este computador está visível no seu painel").
- Pausar, encerrar e desvincular estão a um clique no próprio PC, sem senha do painel — o dono do computador sempre tem a palavra final.
- Quando pausado ou encerrado no PC, o painel mostra **"Pausado no PC" / "Agente encerrado"** (e não apenas offline), e a ação é auditada.
- O início automático só é ligado por escolha explícita e usa a entrada padrão visível em *Gerenciador de Tarefas › Inicializar*.
- Uma única instância por usuário; o log local nunca contém o segredo do dispositivo (verificado em teste).

## Encerramento remoto de processos (Fase 4)
Camadas, todas obrigatórias:
1. **Servidor:** papel *administrador* ou superior, acesso ao computador e `confirm: true` (o painel exige confirmação explícita).
2. **Agente — política local:** o dono do PC pode bloquear a função (bandeja › *Permitir encerrar processos pelo painel*, ou `einstein-agent remote-kill disable`). Lida a cada pedido.
3. **Agente — identidade do processo:** o pedido traz PID, nome **e** hora de criação; se não baterem (PID reaproveitado por outro programa), é recusado.
4. **Agente — processos protegidos:** núcleo do Windows (`csrss`, `lsass`, `wininit`, `winlogon`, `services`, `svchost`, `dwm`, `explorer`, antivírus...), PIDs 0/4 e o próprio agente nunca são encerrados.
5. **Auditoria:** quem, quando, qual processo e o resultado; opcionalmente uma notificação.

## Tela remota e controle (Fase 5)
- **Quem pode:** administradores/proprietário com acesso ao computador. Observadores não veem a tela.
- **No PC, sempre visível:** faixa no topo "Tela compartilhada com *fulano* · mouse e teclado remotos ATIVOS", com botão **Encerrar**; ícone da bandeja muda de cor; aviso do Windows ao iniciar.
- **Escolha do dono (bandeja ou CLI):**
  - *Acesso à tela:* **Permitir sem perguntar** (para acessar o próprio PC à distância) · **Perguntar a cada sessão** (janela Permitir/Recusar; sem resposta em 30 s = recusado) · **Bloquear**.
  - *Permitir controle de mouse e teclado:* pode ser desligado mantendo só a visualização; vale na hora, inclusive no meio de uma sessão.
- **Servidor:** quadros vão apenas para quem abriu a sessão; eventos de entrada só de quem tem controle, no máximo 64 por mensagem e 400/s; eventos são validados no agente (tipos, teclas e coordenadas permitidas).
- **Auditoria e alertas:** início (quem, com/sem controle), fim (motivo, duração, quadros); notificação a cada acesso.
- **Segurança do Windows preservada:** `Ctrl+Alt+Del`, tela de UAC e a tela de bloqueio não aceitam entrada simulada — por design do Windows.
- Ao encerrar ou se a conexão cair, teclas e botões pressionados são soltos automaticamente.

## Energia (Fase 6)
- Desligar, reiniciar, suspender e hibernar: **administrador + confirmação**; no painel, desligar/reiniciar exigem marcar “Entendo que programas abertos serão fechados”.
- Quem pediu é definido pelo servidor e aparece no aviso do próprio Windows (“EINSTEIN REMOTE: desligamento solicitado por …”).
- Prazos de até 24 h; enquanto pendente, nenhuma outra ação de energia é aceita; **cancelar** pelo painel, pelo ícone na bandeja ou com `shutdown /a`.
- O dono pode bloquear todo o controle de energia no PC (bandeja › *Permitir desligar/reiniciar pelo painel*, ou `einstein-agent power-control disable`).
- Agendamento, cancelamento (inclusive no próprio PC), falha e Wake-on-LAN são auditados e notificados.
- Wake-on-LAN só faz broadcast para a rede geral e para a sub-rede **privada** onde o PC foi visto; nunca para endereços públicos.

## Produção — checklist
- Rodar com HTTPS (`scripts/run-server.sh --tls`) e `EINSTEIN_COOKIE_SECURE=1`.
- Atrás de proxy reverso (Caddy/Nginx), encaminhar WebSocket e restringir o acesso ao diretório `data/`.
- Fazer backup de `data/einstein.db` e `data/secret.key` (permissão 600).
- Para acesso pela internet, preferir VPN/Tailscale ou domínio com certificado válido (Let's Encrypt).
