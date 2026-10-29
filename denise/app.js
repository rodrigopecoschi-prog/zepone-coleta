const $ = (id) => document.getElementById(id);
const brl = (v) => v.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const linkWhats = (texto) =>
  `https://wa.me/${LOJA.whatsapp}?text=${encodeURIComponent(texto)}`;
const valor = (p) => p.promo || p.preco;
const porId = (id) => PRODUTOS.find((p) => p.id === id);
const nomeDep = (id) => (DEPARTAMENTOS.find((d) => d.id === id) || {}).nome || id;

function escapar(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// ---------- Loja ----------

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
  if (LOJA.freteGratisAcima) {
    $("faixa").textContent = `Frete grátis em compras acima de ${brl(LOJA.freteGratisAcima)}`;
    $("faixa").hidden = false;
  }

  $("departamentos").innerHTML = DEPARTAMENTOS
    .map((d) => `
      <a href="#colecao" class="dep-card" data-dep="${escapar(d.id)}" style="--cor:${escapar(d.cor)}">
        <span class="dep-nome">${escapar(d.nome)}</span>
        <span class="dep-chamada">${escapar(d.chamada)}</span>
        <span class="dep-seta" aria-hidden="true">→</span>
      </a>`)
    .join("");
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-dep]");
    if (a) escolherDep(a.dataset.dep);
  });
}

// ---------- Vitrine ----------

let depAtual = "todos";
let catAtual = "Todas";
let busca = "";
const semAcento = (t) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

function escolherDep(dep) {
  depAtual = dep;
  catAtual = "Todas";
  montarVitrine();
}

function imagem(p, classe) {
  if (p.fotos && p.fotos.length) {
    return `<img class="${classe}" src="${escapar(p.fotos[0])}" alt="${escapar(p.nome)}" loading="lazy">`;
  }
  return `<div class="${classe} sem-foto" style="--cor:${escapar(p.cor || "#ddd")}"><span>${escapar(p.nome[0])}</span></div>`;
}

function preco(p) {
  return p.promo
    ? `<s>${brl(p.preco)}</s><span class="promo">${brl(p.promo)}</span>`
    : brl(p.preco);
}

function montarVitrine() {
  const abas = [{ id: "todos", nome: "Tudo" }, ...DEPARTAMENTOS];
  $("abas-dep").innerHTML = abas
    .map((d) => `<button aria-selected="${d.id === depAtual}" data-dep="${escapar(d.id)}">${escapar(d.nome)}</button>`)
    .join("");
  $("titulo-colecao").textContent = depAtual === "todos" ? "Coleção" : nomeDep(depAtual);

  const doDep = PRODUTOS.filter((p) => depAtual === "todos" || p.departamento === depAtual);
  const cats = ["Todas", ...new Set(doDep.map((p) => p.categoria))];
  $("filtros").innerHTML = cats
    .map((c) => `<button aria-pressed="${c === catAtual}" data-cat="${escapar(c)}">${escapar(c)}</button>`)
    .join("");

  const termo = semAcento(busca.trim());
  const lista = doDep.filter((p) =>
    (catAtual === "Todas" || p.categoria === catAtual) &&
    (!termo || semAcento(`${p.nome} ${p.categoria}`).includes(termo)));
  if (!lista.length) {
    $("grade").innerHTML = `<p class="vazio">Nenhuma peça encontrada.</p>`;
    return;
  }
  $("grade").innerHTML = lista
    .map((p) => `
      <button class="card" data-id="${escapar(p.id)}">
        <div class="card-img">
          ${imagem(p, "foto")}
          ${p.novo ? '<span class="selo">Novo</span>' : ""}
          ${p.promo && !p.novo ? '<span class="selo selo-promo">Promo</span>' : ""}
        </div>
        <span class="card-nome">${escapar(p.nome)}</span>
        <span class="card-preco">${preco(p)}</span>
      </button>`)
    .join("");
}

$("abas-dep").onclick = (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  escolherDep(b.dataset.dep);
  $("colecao").scrollIntoView();
};
$("busca").oninput = (e) => {
  busca = e.target.value;
  montarVitrine();
};
$("filtros").onclick = (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  catAtual = b.dataset.cat;
  montarVitrine();
};
$("grade").onclick = (e) => {
  const c = e.target.closest(".card");
  if (c) abrir(porId(c.dataset.id));
};

// ---------- Detalhe da peça ----------

function abrir(p) {
  let tamanho = p.tamanhos.length === 1 ? p.tamanhos[0] : "";
  const fotos = p.fotos && p.fotos.length > 1
    ? `<div class="miniaturas">${p.fotos.map((f) => `<img src="${escapar(f)}" alt="">`).join("")}</div>`
    : "";
  const det = $("detalhe-corpo");
  det.innerHTML = `
    <div class="detalhe-img">${imagem(p, "foto")}${fotos}</div>
    <div class="detalhe-info">
      <span class="detalhe-cat">${escapar(nomeDep(p.departamento))} · ${escapar(p.categoria)}</span>
      <h3>${escapar(p.nome)}</h3>
      <p class="detalhe-preco">${preco(p)}</p>
      ${p.descricao ? `<p>${escapar(p.descricao)}</p>` : ""}
      <p class="rotulo">Tamanho${p.departamento === "infantil" ? " (idade)" : ""}</p>
      <div class="tamanhos">${p.tamanhos
        .map((t) => `<button data-t="${escapar(t)}" aria-pressed="${t === tamanho}">${escapar(t)}</button>`)
        .join("")}</div>
      <button class="botao" id="adicionar">Adicionar à sacola</button>
      <a class="botao botao-contorno" id="pedir" target="_blank" rel="noopener">Pedir só esta no WhatsApp</a>
      <p class="aviso" id="aviso"></p>
    </div>`;

  const atualizar = () => {
    const msg = `Olá! Tenho interesse na peça *${p.nome}* (${brl(valor(p))})` +
      (tamanho ? `, tamanho *${tamanho}*` : "") + `. Ref: ${p.id}`;
    $("pedir").href = linkWhats(msg);
  };
  const exigirTamanho = () => {
    if (tamanho) return true;
    $("aviso").textContent = "Escolha um tamanho primeiro.";
    return false;
  };
  atualizar();

  det.querySelector(".tamanhos").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    tamanho = b.dataset.t;
    det.querySelectorAll(".tamanhos button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    $("aviso").textContent = "";
    atualizar();
  };
  $("pedir").onclick = (e) => { if (!exigirTamanho()) e.preventDefault(); };
  $("adicionar").onclick = () => {
    if (!exigirTamanho()) return;
    adicionar(p.id, tamanho);
    $("detalhe").close();
    avisar(`${p.nome} (${tamanho}) foi para a sacola`);
  };
  const mini = det.querySelector(".miniaturas");
  if (mini) mini.onclick = (e) => {
    if (e.target.tagName === "IMG") det.querySelector(".detalhe-img .foto").src = e.target.src;
  };
  $("detalhe").showModal();
}

// ---------- Sacola ----------

const CHAVE = "denise-sacola";
let sacola = [];
try { sacola = JSON.parse(localStorage.getItem(CHAVE)) || []; } catch { sacola = []; }
sacola = sacola.filter((i) => porId(i.id));

function salvar() {
  try { localStorage.setItem(CHAVE, JSON.stringify(sacola)); } catch {}
  const qtd = sacola.reduce((s, i) => s + i.qtd, 0);
  $("contador").textContent = qtd;
  $("contador").hidden = qtd === 0;
}

function adicionar(id, tamanho) {
  const item = sacola.find((i) => i.id === id && i.tamanho === tamanho);
  if (item) item.qtd++;
  else sacola.push({ id, tamanho, qtd: 1 });
  salvar();
}

function montarSacola() {
  if (!sacola.length) {
    $("sacola-itens").innerHTML = `<p class="vazia">Sua sacola está vazia.</p>`;
    $("sacola-rodape").innerHTML = "";
    return;
  }
  $("sacola-itens").innerHTML = sacola
    .map((i, n) => {
      const p = porId(i.id);
      return `
        <div class="item">
          ${imagem(p, "item-foto")}
          <div class="item-info">
            <strong>${escapar(p.nome)}</strong>
            <span>Tamanho ${escapar(i.tamanho)} · ${brl(valor(p))}</span>
            <div class="qtd">
              <button data-n="${n}" data-acao="-1" aria-label="Diminuir">−</button>
              <span>${i.qtd}</span>
              <button data-n="${n}" data-acao="1" aria-label="Aumentar">+</button>
              <button class="remover" data-n="${n}" data-acao="x">Remover</button>
            </div>
          </div>
        </div>`;
    })
    .join("");

  const total = sacola.reduce((s, i) => s + valor(porId(i.id)) * i.qtd, 0);
  const falta = LOJA.freteGratisAcima - total;
  const progresso = Math.min(100, (total / LOJA.freteGratisAcima) * 100);
  const frete = !LOJA.freteGratisAcima ? ""
    : (falta > 0 ? `Faltam <strong>${brl(falta)}</strong> para o frete grátis` : "Frete grátis garantido ✓") +
      `<div class="barra-frete"><i style="width:${progresso}%"></i></div>`;
  const linhas = sacola.map((i) => {
    const p = porId(i.id);
    return `• ${i.qtd}x ${p.nome} — tam. ${i.tamanho} — ${brl(valor(p) * i.qtd)} (ref ${p.id})`;
  });
  const msg = `Olá, ${LOJA.nome}! Quero fazer este pedido pelo site:\n\n${linhas.join("\n")}\n\n*Total: ${brl(total)}*`;
  $("sacola-rodape").innerHTML = `
    ${frete ? `<div class="frete">${frete}</div>` : ""}
    <p class="total"><span>Total</span><strong>${brl(total)}</strong></p>
    <a class="botao botao-whats" href="${linkWhats(msg)}" target="_blank" rel="noopener">Finalizar pelo WhatsApp</a>`;
}

$("sacola-itens").onclick = (e) => {
  const b = e.target.closest("button[data-acao]");
  if (!b) return;
  const i = sacola[b.dataset.n];
  if (b.dataset.acao === "x") sacola.splice(b.dataset.n, 1);
  else {
    i.qtd += Number(b.dataset.acao);
    if (i.qtd < 1) sacola.splice(b.dataset.n, 1);
  }
  salvar();
  montarSacola();
};
$("abrir-sacola").onclick = () => { montarSacola(); $("sacola").showModal(); };

// ---------- Geral ----------

let tempoToast;
function avisar(texto) {
  $("toast").textContent = texto;
  $("toast").classList.add("visivel");
  clearTimeout(tempoToast);
  tempoToast = setTimeout(() => $("toast").classList.remove("visivel"), 2500);
}

document.querySelectorAll("dialog").forEach((d) => {
  d.addEventListener("click", (e) => {
    if (e.target === d || e.target.closest("[data-fechar]")) d.close();
  });
});

preencherLoja();
montarVitrine();
salvar();
