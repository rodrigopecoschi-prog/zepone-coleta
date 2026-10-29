const $ = (id) => document.getElementById(id);
const brl = (v) => v.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const linkWhats = (texto) =>
  `https://wa.me/${LOJA.whatsapp}?text=${encodeURIComponent(texto)}`;

function escapar(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function preencherLoja() {
  const marca = LOJA.logo
    ? `<img src="${escapar(LOJA.logo)}" alt="${escapar(LOJA.nome)}">`
    : escapar(LOJA.nome);
  $("marca").innerHTML = marca;
  $("rodape-marca").innerHTML = marca;
  $("slogan").textContent = LOJA.slogan;
  $("descricao").textContent = LOJA.descricao;
  $("cidade").textContent = LOJA.cidade;
  $("nome-rodape").textContent = LOJA.nome;
  $("ano").textContent = new Date().getFullYear();
  const oi = `Olá, ${LOJA.nome}! Vim pelo site e gostaria de ver as peças.`;
  $("link-whats").href = linkWhats(oi);
  $("whats-flutuante").href = linkWhats(oi);
  $("link-insta").href = `https://instagram.com/${LOJA.instagram}`;
  document.title = `${LOJA.nome} · ${LOJA.slogan}`;
}

function imagem(p, classe) {
  if (p.fotos && p.fotos.length) {
    return `<img class="${classe}" src="${escapar(p.fotos[0])}" alt="${escapar(p.nome)}" loading="lazy">`;
  }
  return `<div class="${classe} sem-foto" style="--cor:${escapar(p.cor || "#ddd")}"><span>${escapar(p.nome[0])}</span></div>`;
}

function preco(p) {
  return p.promo
    ? `<s>${brl(p.preco)}</s> <strong>${brl(p.promo)}</strong>`
    : `<strong>${brl(p.preco)}</strong>`;
}

let filtroAtual = "Todas";

function montarFiltros() {
  const cats = ["Todas", ...new Set(PRODUTOS.map((p) => p.categoria))];
  $("filtros").innerHTML = cats
    .map((c) => `<button role="tab" aria-selected="${c === filtroAtual}" data-cat="${escapar(c)}">${escapar(c)}</button>`)
    .join("");
  $("filtros").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    filtroAtual = b.dataset.cat;
    montarFiltros();
    montarGrade();
  };
}

function montarGrade() {
  const lista = PRODUTOS.filter((p) => filtroAtual === "Todas" || p.categoria === filtroAtual);
  $("grade").innerHTML = lista
    .map(
      (p) => `
      <button class="card" data-id="${escapar(p.id)}">
        <div class="card-img">
          ${imagem(p, "foto")}
          ${p.novo ? '<span class="selo">Novo</span>' : ""}
          ${p.promo ? '<span class="selo selo-promo">Promo</span>' : ""}
        </div>
        <span class="card-cat">${escapar(p.categoria)}</span>
        <span class="card-nome">${escapar(p.nome)}</span>
        <span class="card-preco">${preco(p)}</span>
      </button>`
    )
    .join("");
  $("grade").onclick = (e) => {
    const c = e.target.closest(".card");
    if (c) abrir(PRODUTOS.find((p) => p.id === c.dataset.id));
  };
}

function abrir(p) {
  let tamanho = p.tamanhos.length === 1 ? p.tamanhos[0] : "";
  const fotos = p.fotos && p.fotos.length > 1
    ? `<div class="miniaturas">${p.fotos.map((f) => `<img src="${escapar(f)}" alt="">`).join("")}</div>`
    : "";
  $("detalhe-corpo").innerHTML = `
    <div class="detalhe-img">${imagem(p, "foto")}${fotos}</div>
    <div class="detalhe-info">
      <span class="card-cat">${escapar(p.categoria)}</span>
      <h3>${escapar(p.nome)}</h3>
      <p class="detalhe-preco">${preco(p)}</p>
      ${p.descricao ? `<p>${escapar(p.descricao)}</p>` : ""}
      <p class="rotulo">Tamanho</p>
      <div class="tamanhos">${p.tamanhos
        .map((t) => `<button data-t="${escapar(t)}" aria-pressed="${t === tamanho}">${escapar(t)}</button>`)
        .join("")}</div>
      <a class="botao botao-whats" id="pedir" target="_blank" rel="noopener">Pedir pelo WhatsApp</a>
      <p class="aviso" id="aviso"></p>
    </div>`;

  const atualizar = () => {
    const valor = brl(p.promo || p.preco);
    const msg = `Olá! Tenho interesse na peça *${p.nome}* (${valor})` +
      (tamanho ? `, tamanho *${tamanho}*` : "") + `. Ref: ${p.id}`;
    $("pedir").href = linkWhats(msg);
  };
  atualizar();

  const det = $("detalhe-corpo");
  det.querySelector(".tamanhos").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    tamanho = b.dataset.t;
    det.querySelectorAll(".tamanhos button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    $("aviso").textContent = "";
    atualizar();
  };
  $("pedir").onclick = (e) => {
    if (!tamanho) {
      e.preventDefault();
      $("aviso").textContent = "Escolha um tamanho antes de pedir.";
    }
  };
  const mini = det.querySelector(".miniaturas");
  if (mini) mini.onclick = (e) => {
    if (e.target.tagName === "IMG") det.querySelector(".detalhe-img .foto").src = e.target.src;
  };
  $("detalhe").showModal();
}

$("fechar").onclick = () => $("detalhe").close();
$("detalhe").onclick = (e) => { if (e.target === $("detalhe")) $("detalhe").close(); };

preencherLoja();
montarFiltros();
montarGrade();
