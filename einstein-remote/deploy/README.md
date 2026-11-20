# EINSTEIN REMOTE — Servidor em produção

O servidor é o único ponto exposto à internet: painel web, API e o canal em tempo real com os agentes.
Ele precisa de **HTTPS** (senhas, tokens e a sua tela trafegam por ele). As três formas abaixo foram testadas
de ponta a ponta com um agente real (`deploy/smoke_compose.sh`, também executado no CI).

| Opção | Para quem | HTTPS |
|---|---|---|
| [A. Docker + Caddy](#a-docker--caddy-recomendado) | VPS, mini PC, Raspberry Pi, NAS | Let's Encrypt automático |
| [B. Rede local sem domínio](#b-somente-na-rede-local) | uso só dentro de casa | CA local do Caddy |
| [C. Serviço systemd](#c-linux-sem-docker-systemd) | Linux sem Docker | Caddy instalado na máquina |
| [D. Windows](#d-servidor-no-windows) | o próprio PC Windows | certificado próprio |

## A. Docker + Caddy (recomendado)
1. Um domínio (ou subdomínio gratuito, ex.: DuckDNS) apontando para o IP público da máquina.
2. Portas **80 e 443** liberadas/encaminhadas no roteador para essa máquina.
3. Na pasta `einstein-remote`:
   ```bash
   EINSTEIN_DOMAIN=remote.seudominio.com docker compose up -d
   docker compose ps          # server "healthy" e caddy "running"
   ```
4. Abra `https://remote.seudominio.com` e crie a conta do proprietário **imediatamente** (o primeiro a abrir cria o dono).

O que esta configuração já faz:
- certificado Let's Encrypt emitido e renovado sozinho; HTTP redireciona para HTTPS; HTTP/2; compressão;
- servidor roda como usuário sem privilégios, sem porta publicada (só o Caddy o alcança);
- `--proxy-headers`: o servidor vê o **IP real** de quem acessa — o bloqueio de força bruta e a auditoria
  funcionam por visitante, não pelo IP do proxy;
- cookies de sessão marcados `Secure`; dados (banco, chaves) no volume `einstein-data`;
- reinicia sozinho com a máquina (`restart: unless-stopped`) e tem verificação de saúde.

Atualizar: `git pull && docker compose up -d --build` (o banco é migrado automaticamente na inicialização).
Registros: `docker compose logs -f server`.

## B. Somente na rede local
```bash
EINSTEIN_DOMAIN=192.168.0.10 EINSTEIN_TLS=internal docker compose up -d
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./einstein-ca.crt
```
- No agente, informe `einstein-ca.crt` no campo **Certificado da CA** (ou `--ca-file einstein-ca.crt`).
- No celular, instale `einstein-ca.crt` como certificado confiável (Android: Configurações › Segurança ›
  Criptografia › Instalar certificado › CA; iPhone: abrir o arquivo, instalar o perfil e ativá-lo em
  Ajustes › Geral › Sobre › Ajustes de Confiança de Certificados).
- Para acessar de fora sem abrir portas, use uma VPN como WireGuard ou Tailscale até a sua rede.

## C. Linux sem Docker (systemd)
```bash
sudo useradd --system --home /var/lib/einstein-remote --shell /usr/sbin/nologin einstein
sudo mkdir -p /opt/einstein-remote && sudo cp -r server web /opt/einstein-remote/
sudo python3 -m venv /opt/einstein-remote/venv
sudo /opt/einstein-remote/venv/bin/pip install -r /opt/einstein-remote/server/requirements.txt
sudo cp deploy/einstein-remote.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now einstein-remote
systemctl status einstein-remote
```
O serviço escuta só em `127.0.0.1:8000`, com sistema de arquivos somente leitura exceto `/var/lib/einstein-remote`.
Instale o [Caddy](https://caddyserver.com/docs/install) e use `deploy/Caddyfile.host` (troque o domínio).

## D. Servidor no Windows (com acesso pelo 4G)
Um script faz tudo: instala Python e Tailscale se faltarem, prepara o servidor, gera o certificado HTTPS do
Tailscale (o celular não precisa instalar certificado), libera o firewall **só** para a rede Tailscale e a rede
local, cria o início automático com o PC (antes mesmo de entrar no Windows, religando se cair), renova o
certificado todo dia e mostra o endereço para abrir no celular:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-windows-server.ps1
```
Durante a execução, entre na sua conta Tailscale quando pedido (e no celular, no app Tailscale, com a mesma conta).
Parar: `... -Stop` · ligar de novo: rode o script sem opções · remover tudo: `... -Uninstall` ·
sem Tailscale (certificado próprio em `certs\`): `... -SkipTailscale` · não mexer na suspensão: `... -KeepSleep`.
Registro do servidor: `data\server.log`. O PC precisa ficar ligado (o script desativa a suspensão na tomada).

## Variáveis de ambiente
| Variável | Padrão | Uso |
|---|---|---|
| `EINSTEIN_DATA_DIR` | `./data` (`/data` no Docker) | banco, chave de assinatura, chave VAPID |
| `EINSTEIN_COOKIE_SECURE` | `0` (`1` no Docker) | cookie de sessão só por HTTPS — use `1` em produção |
| `EINSTEIN_SECRET_KEY` | gerada e salva em `secret.key` | fixar a chave de assinatura (≥ 32 caracteres) |
| `EINSTEIN_ACCESS_TTL` / `EINSTEIN_REFRESH_TTL` | 900 / 604800 s | duração do token e da sessão |
| `EINSTEIN_ALLOWED_ORIGINS` | (mesma origem) | origens extras aceitas no WebSocket |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | de quais proxies aceitar `X-Forwarded-For` (uvicorn) |

## Backup e restauração
```bash
# Docker
docker compose exec server python -m app.admin backup /data/backups
docker compose cp server:/data/backups ./backups
# systemd / manual
sudo -u einstein env EINSTEIN_DATA_DIR=/var/lib/einstein-remote \
  /opt/einstein-remote/venv/bin/python -m app.admin backup /var/lib/einstein-remote/backups   # rodar em /opt/einstein-remote/server
```
O backup é consistente mesmo com o servidor em uso e contém `einstein.db`, `secret.key` e `vapid_private.pem`
(guarde em local seguro: são as chaves do servidor). **Restaurar:** pare o servidor, copie os três arquivos para o
diretório de dados e inicie — contas, 2FA, dispositivos vinculados e inscrições de push continuam valendo.

Recuperação de acesso pelo console do servidor: `python -m app.admin reset-password <usuário>` e
`python -m app.admin reset-2fa <usuário>` (veja `python -m app.admin --help`).

## Lista de verificação de segurança
- [ ] HTTPS ativo (cadeado no navegador) e `EINSTEIN_COOKIE_SECURE=1`.
- [ ] Conta do proprietário criada logo após subir o servidor; senha forte; **2FA ativo** e códigos de recuperação guardados.
- [ ] Em Ajustes, **Exigir 2FA** para todos os usuários.
- [ ] Notificações push ativadas no celular (alerta de novo acesso e de tentativas de invasão).
- [ ] Somente as portas 80/443 expostas; nada aponta diretamente para a porta 8000.
- [ ] Backup periódico (ex.: `cron` diário com o comando acima) guardado fora da máquina.
- [ ] Sistema e imagem atualizados (`docker compose pull && docker compose up -d --build`).

## Publicar uma versão
Ao enviar uma tag `vX.Y.Z` (`git tag v1.0.0 && git push origin v1.0.0`), o workflow **EINSTEIN REMOTE — Release**:
1. roda os testes, gera os executáveis e o instalador e executa os testes de fumaça (executável e instalação real);
2. assina `EinsteinRemoteAgent.exe`, `einstein-agent.exe` e o instalador **se** os segredos do repositório
   `WINDOWS_CERT_PFX_BASE64` (o `.pfx` em base64) e `WINDOWS_CERT_PASSWORD` existirem;
3. cria o Release com `…-Setup.exe`, `…-win64.zip` e `SHA256SUMS.txt`, com as notas do `CHANGELOG.md`.
A tag precisa ser igual à versão do código (`server/app/__init__.py`, `agent/einstein_agent/__init__.py`).
