# EINSTEIN REMOTE — Manual de utilização (v0.1, Fase 1)

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

## 3. Vincular o computador Windows
1. Dashboard → **Dispositivos** → digite um nome → **Gerar código** (vale 10 min, uso único).
2. No PC Windows (PowerShell, na pasta do projeto):
   ```powershell
   powershell -ExecutionPolicy Bypass -File installer\install-agent.ps1
   einstein-agent pair --server https://SEU-SERVIDOR:8443 --code ABCD-EFGH --ca-file certs\ca.crt
   einstein-agent run
   ```
   Teste local sem TLS: `einstein-agent pair --server http://127.0.0.1:8000 --code ABCD-EFGH`.
3. O painel mostra o PC **Online** com métricas em tempo real.

## 4. Comandos do agente
| Comando | Efeito |
|---|---|
| `einstein-agent run` | Executa o agente (visível no terminal) |
| `einstein-agent status` | Mostra vínculo, servidor, pausa e auto‑início |
| `einstein-agent pause` / `resume` | Pausa/retoma (o agente em execução obedece em até 2 s) |
| `einstein-agent autostart enable` / `disable` | Liga/desliga o início com o Windows (visível em Gerenciador de Tarefas › Inicializar) |
| `einstein-agent uninstall` | Remove vínculo, configuração e auto‑início |

## 5. Painel
- **Painel:** status, sistema, hostname, IP, tempo ligado, CPU, RAM, GPU (NVIDIA), temperatura (quando o SO expõe sensores), discos, rede ↓/↑, núcleos, gráficos em tempo real, histórico (1h/6h/24h/7d), últimas atividades, teste de latência.
- **Dispositivos:** gerar código, renomear, revogar.
- **Alertas:** conexão/desconexão, CPU alta, RAM no limite, temperatura alta, tentativas de acesso; opção de alertas nativos do navegador.
- **Segurança:** sessões ativas (revogar) e registro de auditoria.

### Celular
Abra o endereço do servidor no navegador e use **Adicionar à tela inicial** — o EINSTEIN REMOTE MOBILE abre como app, com navegação inferior.

## 6. Limitações conhecidas (honestas)
- **Temperatura no Windows:** o Windows não expõe sensores de CPU via psutil; o painel mostra a temperatura da GPU NVIDIA quando houver. Integração com LibreHardwareMonitor está prevista na Fase 4.
- **GPU:** uso medido via `nvidia-smi` (NVIDIA). AMD/Intel serão tratadas na Fase 4 (contadores de desempenho do Windows).
- Tela remota, mouse/teclado, energia, processos e arquivos chegam nas Fases 4–7 e **ainda não aparecem** no painel.

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
