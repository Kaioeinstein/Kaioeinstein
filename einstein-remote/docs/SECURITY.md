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
| Comando malicioso | Allowlist + papel mínimo + confirmação explícita para ações críticas + auditoria |
| Credenciais em logs | Autenticação WS na primeira mensagem, não na URL |

## Papéis
- **owner** – tudo (criado na configuração inicial).
- **admin** – gerencia dispositivos e executa comandos.
- **viewer** – apenas visualiza (sem comandos, sem auditoria).

## Produção — checklist
- Rodar com HTTPS (`scripts/run-server.sh --tls`) e `EINSTEIN_COOKIE_SECURE=1`.
- Atrás de proxy reverso (Caddy/Nginx), encaminhar WebSocket e restringir o acesso ao diretório `data/`.
- Fazer backup de `data/einstein.db` e `data/secret.key` (permissão 600).
- Para acesso pela internet, preferir VPN/Tailscale ou domínio com certificado válido (Let's Encrypt).
