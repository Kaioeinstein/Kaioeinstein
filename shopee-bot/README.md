# 🤖 Bot Shopee Vídeo – Afiliados (via USB)

Posta vídeos automaticamente na **Shopee Vídeo** com seu **link de afiliado**,
controlando o seu celular Android pelo cabo USB.

## 1. Preparar o celular
1. **Configurações → Sobre o telefone →** toque 7x em **Número da versão** (ativa o modo desenvolvedor).
2. **Opções do desenvolvedor →** ative **Depuração USB** (e, em Xiaomi, também **Depuração USB (configurações de segurança)** e **Instalar via USB**).
3. Conecte no PC pelo cabo e aceite **"Permitir depuração USB"** no celular.
4. Deixe a Shopee **já logada** na sua conta de afiliado.
5. Recomendo: tela sem senha (ou desbloqueada) e "Tela sempre ligada ao carregar".

## 2. Preparar o PC (Windows / Linux / Mac)
1. Instale o **Python 3.9+** (no Windows marque "Add to PATH").
2. Instale o **ADB** (Android Platform Tools): https://developer.android.com/tools/releases/platform-tools
3. Na pasta do bot:
   ```bash
   pip install -r requirements.txt
   python -m uiautomator2 init      # instala o agente no celular (uma vez)
   python bot.py testar             # deve mostrar "OK! Celular pronto."
   ```

## 3. Colocar os vídeos
- Coloque os `.mp4` na pasta `videos/`.
- Edite `videos.csv` (separado por `;`):

| arquivo | legenda | produto |
|---|---|---|
| fone.mp4 | Fone top por menos de R$50 😱 #shopee | https://s.shopee.com.br/xxxx |

## 4. Ajustar o fluxo para o SEU celular (importante!)
A Shopee muda os botões a cada versão. Abra a Shopee em cada tela do processo
de postar vídeo e rode:
```bash
python bot.py inspecionar
```
Ele mostra os textos, IDs e coordenadas dos botões. Ajuste a lista `fluxo:` no
`config.yaml` com os nomes que aparecerem (ex.: "Próximo", "Postar", "Adicionar produto").

## 5. Usar
```bash
python bot.py postar   # posta 1 vídeo agora (teste)
python bot.py rodar    # modo automático o dia todo
```
- Respeita `limite_por_dia`, horário e intervalo aleatório (`config.yaml`).
- Vídeos postados vão para `videos/postados/` e ficam em `postados.json` (nunca repete).
- Se der erro, um print da tela é salvo em `erros/`. Após 3 falhas seguidas o bot para.

## ⚠️ Avisos
- Automação pode violar os termos da Shopee e gerar restrição na conta. Use limites
  baixos (5–10 vídeos/dia), intervalos grandes e **vídeos originais** (não repostar
  vídeo de outros — isso dá bloqueio por direitos autorais).
- Teste sempre com `postar` antes de deixar no `rodar`.
