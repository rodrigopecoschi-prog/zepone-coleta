# Site Casa Dê — moda feminina e infantil, com pedido pelo WhatsApp

Site estático (HTML/CSS/JS puro, sem instalação). Abra `index.html` no navegador para ver.

## Como editar

Tudo fica em **`config.js`**:

- `LOJA` — nome, slogan, texto de apresentação, WhatsApp (só números, com 55 + DDD), Instagram, cidade e logo.
- `DEPARTAMENTOS` — Feminino e Infantil (nome, frase de chamada e cor do cartão).
- `PRODUTOS` — a lista de peças. Para cada uma: `departamento` (`"feminino"` ou `"infantil"`), `nome`, `categoria`, `preco`, `tamanhos`,
  `fotos` (ex.: `["img/vestido-floral-1.jpg", "img/vestido-floral-2.jpg"]`),
  e opcionalmente `promo` (preço promocional), `novo: true` e `descricao`.

As fotos vão na pasta `img/`. Proporção ideal: retrato 3:4 (ex.: 900×1200).
As categorias dos filtros são criadas automaticamente a partir das peças.

## Como funciona o pedido

A cliente escolhe peças e tamanhos e vai adicionando à **sacola** (fica salva no navegador).
Em **Finalizar pelo WhatsApp** abre uma conversa com o pedido pronto: cada peça, tamanho,
quantidade, referência e o total. Também dá para pedir uma peça só, direto do detalhe.
`freteGratisAcima` mostra a faixa no topo e quanto falta na sacola (0 desliga).

## Publicar (grátis)

GitHub Pages, Netlify ou Vercel: basta publicar a pasta `denise/` como site estático.
