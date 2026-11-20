# EINSTEIN REMOTE — Testes, carga e estabilidade (Fase 9)

## Como rodar
```bash
pip install -r requirements-dev.txt
python -m pytest -q                       # suíte completa (Linux: use xvfb-run -a para a janela Tk e a tela remota)
python tests/load/loadtest.py --agents 500 --clients 10 --seconds 25          # carga
python tests/load/loadtest.py --agents 200 --clients 3 --stalled 2 --seconds 30 # painéis travados
python -m pytest --cov=server/app --cov=agent/einstein_agent                   # cobertura
bandit -r server/app agent/einstein_agent --severity-level medium              # análise estática de segurança
```
O CI (`.github/workflows/einstein-remote.yml`) roda tudo em **Linux e Windows** a cada envio, gera o `.exe` no
Windows e executa o teste de fumaça dele contra um servidor real.

## O que é testado de verdade (sem simulação)
| Área | Como |
|---|---|
| Métricas | `psutil` real; executável PyInstaller online contra servidor real (Linux e Windows) |
| Tela remota | captura real sob Xvfb; clique e digitação (com acentos) chegando a um aplicativo Tk; navegador real (desktop e celular) |
| Arquivos | download/upload/cópia entre **dois agentes reais**; conteúdo e SHA-256 idênticos; links que escapam bloqueados |
| Energia | modo de simulação registra o comando exato (`shutdown /s /t 60`, `shutdown /a`) — nada é desligado |
| 2FA | vetores oficiais da RFC 6238; fluxo completo no navegador |
| Web Push | saída idêntica byte a byte ao exemplo da RFC 8291; serviço de push local decifra com a chave do “navegador” |
| Windows | DPAPI, registro, Tk, `SendInput`/cursor, captura, serviços, janelas, API PDH — no executor Windows do CI |
| Estabilidade | servidor reiniciado com agente real conectado → agente volta sozinho; painel travado não afeta os outros |

## Resultados de carga (4 vCPUs, servidor e clientes na mesma máquina)
Cada computador simulado envia a amostra completa (8 núcleos, 3 discos, rede) **a cada 1 s** — o agente real usa 2 s.

| Computadores | Painéis | Mensagens entregues/s | Comando p50 / p95 | CPU do servidor | Memória |
|---|---|---|---|---|---|
| 100 | 5 | 479 de 500 | 4,3 / 8,2 ms | 6,7 % | 76 MB |
| 500 | 10 | 4 790 de 5 000 | 6,7 / 15,5 ms | 32,9 % (antes: 39,4 %) | 117 MB |
| 200 + **2 painéis travados** | 3 | **582 de 600** (antes da correção: 234) | 3,2 / 6,8 ms | 9,1 % | 88 MB |

Um servidor doméstico (até um Raspberry Pi 4) atende com folga dezenas de computadores.

## Problemas encontrados e corrigidos nesta fase
1. **Painel travado atrasava todos** (celular com rede ruim): o servidor enviava para cada painel em sequência.
   Agora cada painel tem fila e tarefa de envio próprias; atrasado, descarta só métricas (reenviadas em ~1 s);
   travado, é desconectado e reconecta. Entrega aos demais: 39 % → 97 %.
2. **Serialização repetida**: a mesma mensagem virava JSON uma vez por painel; agora uma vez para todos (−17 % de CPU).
3. **Força bruta gerava um alerta (e um push) por tentativa** → no máximo 1 por minuto por IP; a auditoria continua registrando todas.
4. **Limitador de tentativas guardava IPs antigos para sempre** → entradas vencidas são descartadas (e a primeira falha
   continua contando — um erro introduzido e pego pelos testes durante a correção).
5. **DNS rebinding no push**: o endereço era validado só na inscrição; agora também a cada envio.
6. **Programas do Windows chamados pelo PATH** (`shutdown`, `powershell`) → caminho absoluto em `System32`.
7. **Temperatura de GPU sem sensor** quebraria o cálculo do máximo → ignorada.
8. Nome do computador consultado no banco a cada amostra → guardado na conexão.

## Cobertura
~75 % no total; módulos de segurança do servidor entre 90 % e 97 % (`auth` 97 %, `security` 96 %, `users` 95 %,
`webpush` 93 %). Os trechos menos cobertos aqui são exclusivos do Windows (início automático, PDH) — exercitados no
executor Windows do CI — e janelas gráficas verificadas por captura de tela.

## Análise estática (bandit)
0 itens de severidade média ou alta. Os 3 itens médios iniciais foram analisados: 2 falsos positivos documentados no
código (esquema da URL já validado; nomes de coluna de lista fixa) e 1 risco real corrigido (DNS rebinding no push).
Itens baixos remanescentes: fechamentos “melhor esforço” (`try/except: pass` ao fechar conexões) e chamadas de
`subprocess` sem shell, com argumentos fixos.
