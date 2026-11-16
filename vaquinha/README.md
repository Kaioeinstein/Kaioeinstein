# 👶 Vaquinha do Bebê

Site de vaquinha (página única) para ajudar um amigo que vai ser papai. As contribuições são feitas **via PIX direto para a conta dele**, com QR Code e "PIX copia e cola" gerados no próprio navegador, sem intermediário e sem taxa.

## Como personalizar

Abra `index.html` e edite só o bloco `CONFIG` no começo do `<script>`:

| Campo | O que é |
|---|---|
| `papai`, `mamae`, `bebe` | Nomes que aparecem no site |
| `dataPrevista` | Data prevista do parto (`AAAA-MM-DD`) para a contagem regressiva |
| `foto` | Foto do casal (ex.: coloque `foto.jpg` nesta pasta e escreva `"foto.jpg"`) |
| `meta`, `arrecadado`, `apoiadores`, `atualizadoEm` | Barra de progresso. Atualize à mão conforme os PIX chegarem |
| `pix.chave`, `pix.nome`, `pix.cidade` | **Dados PIX do papai.** Confira com ele antes de divulgar! |
| `historia` | Parágrafos da história |
| `valores` | Botões de valor rápido |
| `presentes` | Lista de presentes (cada um preenche o valor no PIX) |
| `recados` | Recadinhos dos amigos |

> ⚠️ Depois de trocar a chave, faça um PIX de teste de R$ 1 lendo o QR Code com o app do banco para garantir que está tudo certo.

## Como publicar (grátis)

- **GitHub Pages:** em *Settings → Pages* do repositório, escolha a branch e a pasta raiz; o site fica em `https://<usuario>.github.io/<repo>/vaquinha/`.
- **Netlify Drop / Vercel:** arraste a pasta `vaquinha` em https://app.netlify.com/drop.

Depois é só usar o botão **Compartilhar no WhatsApp** do próprio site.
