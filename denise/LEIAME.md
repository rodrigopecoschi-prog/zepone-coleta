# Site Denise — vitrine de roupas com pedido pelo WhatsApp

Site estático (HTML/CSS/JS puro, sem instalação). Abra `index.html` no navegador para ver.

## Como editar

Tudo fica em **`config.js`**:

- `LOJA` — nome, slogan, texto de apresentação, WhatsApp (só números, com 55 + DDD), Instagram, cidade e logo.
- `PRODUTOS` — a lista de peças. Para cada uma: `nome`, `categoria`, `preco`, `tamanhos`,
  `fotos` (ex.: `["img/vestido-floral-1.jpg", "img/vestido-floral-2.jpg"]`),
  e opcionalmente `promo` (preço promocional), `novo: true` e `descricao`.

As fotos vão na pasta `img/`. Proporção ideal: retrato 3:4 (ex.: 900×1200).
As categorias dos filtros são criadas automaticamente a partir das peças.

## Como funciona o pedido

A cliente escolhe a peça e o tamanho e toca em **Pedir pelo WhatsApp**. Abre uma conversa
com a mensagem pronta: nome da peça, preço, tamanho e referência.

## Publicar (grátis)

GitHub Pages, Netlify ou Vercel: basta publicar a pasta `denise/` como site estático.
