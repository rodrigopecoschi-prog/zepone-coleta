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
      <a href="#${escapar(d.id)}" class="dep-card" data-dep="${escapar(d.id)}" style="--cor:${escapar(d.cor)}">
        <span class="dep-nome">${escapar(d.nome)}</span>
        <span class="dep-chamada">${escapar(d.chamada)}</span>
        <span class="dep-seta" aria-hidden="true">→</span>
      </a>`)
    .join("");
}

// ---------- Vitrine ----------

let depAtual = "todos";
let catAtual = "Todas";
let busca = "";
const semAcento = (t) => t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

// Página inicial ("todos") ou página de um departamento
function mostrarPagina(dep) {
  const mudou = dep !== depAtual;
  depAtual = dep;
  if (mudou) {
    catAtual = "Todas";
    busca = "";
    $("busca").value = "";
  }
  const naInicial = dep === "todos";
  $("hero").hidden = !naInicial;
  $("pagina-dep").hidden = naInicial;
  if (!naInicial) {
    const d = DEPARTAMENTOS.find((x) => x.id === dep);
    $("dep-trilha").textContent = d.nome;
    $("dep-titulo").textContent = d.nome;
    $("dep-chamada").textContent = d.chamada;
    $("dep-banner").style.setProperty("--cor", d.cor);
    const vitrine = PRODUTOS.find((p) => p.departamento === dep && p.destaque && p.fotos && p.fotos.length);
    $("dep-img").hidden = !vitrine;
    if (vitrine) $("dep-img").src = vitrine.fotos[0];
  }
  document.title = naInicial ? `${LOJA.nome} · Moda feminina e infantil` : `${nomeDep(dep)} · ${LOJA.nome}`;
  montarVitrine();
  if (mudou) window.scrollTo(0, 0);
}

function imagem(p, classe) {
  if (p.fotos && p.fotos.length) {
    return `<img class="${classe}" src="${escapar(p.fotos[0])}" alt="${escapar(p.nome)}" loading="lazy">`;
  }
  return `<div class="${classe} sem-foto" style="--cor:${escapar(p.cor || "#ddd")}"><span>${escapar(p.nome[0])}</span></div>`;
}

const VISTAS = ["Frente", "Costas", "Lado"];
const nomeVista = (p, i) => (p.vistas || VISTAS)[i] || `Foto ${i + 1}`;

function galeria(p) {
  if (!p.fotos || !p.fotos.length) return imagem(p, "foto");
  const fotos = p.fotos
    .map((f, i) => `<img class="foto" src="${escapar(f)}" alt="${escapar(p.nome)} — ${escapar(nomeVista(p, i))}">`)
    .join("");
  const botoes = p.fotos.length < 2 ? "" : `<div class="vistas">${p.fotos
    .map((f, i) => `<button data-i="${i}" aria-pressed="${i === 0}"><img src="${escapar(f)}" alt=""><span>${escapar(nomeVista(p, i))}</span></button>`)
    .join("")}</div>`;
  return `<div class="galeria">${fotos}</div>${botoes}`;
}

function preco(p) {
  return p.promo
    ? `<s>${brl(p.preco)}</s><span class="promo">${brl(p.promo)}</span>`
    : brl(p.preco);
}

function montarVitrine() {
  const abas = [{ id: "todos", nome: "Início" }, ...DEPARTAMENTOS];
  $("abas-dep").innerHTML = abas
    .map((d) => `<a href="#${d.id === "todos" ? "" : escapar(d.id)}" data-dep="${escapar(d.id)}"${d.id === depAtual ? ' aria-current="page"' : ""}>${escapar(d.nome)}</a>`)
    .join("");

  const doDep = PRODUTOS.filter((p) => depAtual === "todos" || p.departamento === depAtual);
  const cats = ["Todas", ...new Set(doDep.map((p) => p.categoria))];
  $("filtros").innerHTML = cats
    .map((c) => `<button aria-pressed="${c === catAtual}" data-cat="${escapar(c)}">${escapar(c)}</button>`)
    .join("");

  const termo = semAcento(busca.trim());
  const lista = doDep.filter((p) =>
    (catAtual === "Todas" || p.categoria === catAtual) &&
    (!termo || semAcento(`${p.nome} ${p.categoria}`).includes(termo)));
  $("titulo-colecao").textContent = depAtual === "todos"
    ? "Coleção"
    : `${lista.length} ${lista.length === 1 ? "peça" : "peças"}`;
  if (!lista.length) {
    $("grade").innerHTML = `<p class="vazio">Nenhuma peça encontrada.</p>`;
    return;
  }
  $("grade").innerHTML = lista
    .map((p) => `
      <button class="card" data-id="${escapar(p.id)}">
        <div class="card-img">
          ${imagem(p, "foto")}
          ${p.fotos && p.fotos[1] ? `<img class="foto foto-2" src="${escapar(p.fotos[1])}" alt="" loading="lazy">` : ""}
          ${p.novo ? '<span class="selo">Novo</span>' : ""}
          ${p.promo && !p.novo ? '<span class="selo selo-promo">Promo</span>' : ""}
        </div>
        <span class="card-nome">${escapar(p.nome)}</span>
        <span class="card-preco">${preco(p)}</span>
      </button>`)
    .join("");
}

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
  if (c) location.hash = `peca-${c.dataset.id}`;
};

// ---------- Detalhe da peça ----------

function abrir(p) {
  let tamanho = p.tamanhos.length === 1 ? p.tamanhos[0] : "";
  const det = $("detalhe-corpo");
  det.innerHTML = `
    <div class="detalhe-img">${galeria(p)}</div>
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
  const gal = det.querySelector(".galeria");
  const vistas = det.querySelector(".vistas");
  if (gal && vistas) {
    vistas.onclick = (e) => {
      const b = e.target.closest("button");
      if (b) gal.scrollTo({ left: gal.clientWidth * b.dataset.i, behavior: "smooth" });
    };
    gal.onscroll = () => {
      const i = Math.round(gal.scrollLeft / gal.clientWidth);
      vistas.querySelectorAll("button").forEach((b, n) => b.setAttribute("aria-pressed", n === i));
    };
  }
  if (!$("detalhe").open) $("detalhe").showModal();
}

// ---------- Endereço próprio de cada peça (#peca-f01) ----------

// #feminino e #infantil abrem a página do departamento; #peca-id abre a peça
// por cima da página em que a pessoa está.
function lerEndereco() {
  const h = location.hash.slice(1);
  const m = h.match(/^peca-([\w-]+)$/);
  const p = m && porId(m[1]);
  if (p) return abrir(p);
  if ($("detalhe").open) $("detalhe").close();
  mostrarPagina(DEPARTAMENTOS.some((d) => d.id === h) ? h : "todos");
}
window.addEventListener("hashchange", lerEndereco);
$("detalhe").addEventListener("close", () => {
  if (!location.hash.startsWith("#peca-")) return;
  const volta = depAtual === "todos" ? location.pathname + location.search : `#${depAtual}`;
  try { history.replaceState(null, "", volta); }
  catch { location.hash = depAtual === "todos" ? "" : depAtual; }
});

// ---------- Carrossel de destaques ----------

function montarCarrossel() {
  const lista = PRODUTOS.filter((p) => p.destaque);
  if (!lista.length) return;
  $("carrossel").hidden = false;
  $("trilho").innerHTML = lista
    .map((p) => `
      <a class="slide" href="#peca-${escapar(p.id)}" style="--cor:${escapar(p.cor || "#eee")}">
        <div class="slide-img">${imagem(p, "foto")}</div>
        <div class="slide-info">
          <span class="slide-dep">${escapar(nomeDep(p.departamento))}${p.novo ? " · Novidade" : p.promo ? " · Promoção" : ""}</span>
          <strong class="slide-nome">${escapar(p.nome)}</strong>
          <span class="slide-preco">${preco(p)}</span>
          <span class="slide-ver">Ver peça →</span>
        </div>
      </a>`)
    .join("");
  $("pontos").innerHTML = lista
    .map((_, i) => `<button data-i="${i}" aria-label="Destaque ${i + 1}"></button>`)
    .join("");

  const trilho = $("trilho");
  const atual = () => Math.round(trilho.scrollLeft / trilho.clientWidth);
  const ir = (i) => trilho.scrollTo({ left: trilho.clientWidth * ((i + lista.length) % lista.length), behavior: "smooth" });
  const marcar = () => $("pontos").querySelectorAll("button")
    .forEach((b, n) => b.setAttribute("aria-current", n === atual()));
  trilho.addEventListener("scroll", marcar, { passive: true });
  marcar();
  $("seta-ant").onclick = () => ir(atual() - 1);
  $("seta-prox").onclick = () => ir(atual() + 1);
  $("pontos").onclick = (e) => {
    const b = e.target.closest("button");
    if (b) ir(Number(b.dataset.i));
  };

  // Avança sozinho a cada 5 s e pausa enquanto a pessoa mexe nele
  let pausado = false;
  const c = $("carrossel");
  ["mouseenter", "touchstart", "focusin"].forEach((ev) => c.addEventListener(ev, () => (pausado = true), { passive: true }));
  ["mouseleave", "touchend", "focusout"].forEach((ev) => c.addEventListener(ev, () => (pausado = false), { passive: true }));
  if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
    setInterval(() => {
      if (!pausado && !document.hidden && !$("detalhe").open && !$("hero").hidden) ir(atual() + 1);
    }, 5000);
  }
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
montarCarrossel();
salvar();
lerEndereco();
if ($("grade").innerHTML === "") montarVitrine();
