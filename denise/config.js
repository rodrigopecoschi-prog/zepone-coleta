// ============================================================
//  CONFIGURAÇÃO DA LOJA — edite só este arquivo para trocar
//  nome, contatos e peças. Não precisa mexer no resto.
// ============================================================

const LOJA = {
  nome: "Casa Dê",
  slogan: "Moda para você e para os pequenos",
  descricao:
    "Roupas femininas e infantis escolhidas a dedo, com conforto e estilo para toda a família. Monte sua sacola e finalize pelo WhatsApp.",
  // Só números, com DDI 55 + DDD. Ex.: "5511999998888"
  whatsapp: "5511954509791",
  instagram: "casade.moda", // sem o @ (provisório)
  cidade: "São Paulo · SP",
  // Caminho do logo (ex.: "img/logo.png"). Vazio = usa o nome em texto.
  logo: "",
  // Frete grátis acima deste valor (0 = não mostrar o aviso)
  freteGratisAcima: 299,
};

// Departamentos que aparecem no topo e nos cartões de destaque.
const DEPARTAMENTOS = [
  { id: "feminino", nome: "Feminino", chamada: "Vestidos, blusas, calças e sapatos", cor: "#f3e3dc" },
  { id: "infantil", nome: "Infantil", chamada: "Conforto para brincar, do bebê aos 12 anos", cor: "#e1ecf2" },
];

// Cada peça: id único, departamento ("feminino" ou "infantil"), nome,
// categoria, preço, tamanhos e fotos.
// "fotos": imagens na pasta img/, nesta ordem: frente, costas, lado
//   (ex.: ["img/vestido-frente.jpg", "img/vestido-costas.jpg", "img/vestido-lado.jpg"]).
//   Pode ter só uma, ou mais de três. Sem foto, o site mostra a "cor".
// Opcionais: promo (preço promocional), novo: true, descricao,
//   destaque: true (aparece no carrossel da página inicial),
//   vistas: nomes das fotos, se não forem frente/costas/lado
//   (ex.: sapatos usam ["Lado", "Frente", "Costas"]).
const PRODUTOS = [
  // ---------- Feminino ----------
  { id: "f01", departamento: "feminino", nome: "Vestido Midi Floral", categoria: "Vestidos", preco: 189.9, tamanhos: ["P", "M", "G"], cor: "#f3dcd3", fotos: ["img/f01-frente.svg", "img/f01-costas.svg", "img/f01-lado.svg"], novo: true, destaque: true },
  { id: "f02", departamento: "feminino", nome: "Vestido Longo Linho", categoria: "Vestidos", preco: 249.9, tamanhos: ["P", "M", "G", "GG"], cor: "#ece4d8", fotos: ["img/f02-frente.svg", "img/f02-costas.svg", "img/f02-lado.svg"] },
  { id: "f03", departamento: "feminino", nome: "Blusa Ombro a Ombro", categoria: "Blusas", preco: 89.9, tamanhos: ["P", "M", "G"], cor: "#f7e8e1", fotos: ["img/f03-frente.svg", "img/f03-costas.svg", "img/f03-lado.svg"], novo: true },
  { id: "f04", departamento: "feminino", nome: "Camisa de Seda Off-White", categoria: "Blusas", preco: 159.9, tamanhos: ["P", "M", "G"], cor: "#f2eee8", fotos: ["img/f04-frente.svg", "img/f04-costas.svg", "img/f04-lado.svg"], destaque: true },
  { id: "f05", departamento: "feminino", nome: "Calça Pantalona Alfaiataria", categoria: "Calças", preco: 199.9, tamanhos: ["36", "38", "40", "42", "44"], cor: "#e2dbd3", fotos: ["img/f05-frente.svg", "img/f05-costas.svg", "img/f05-lado.svg"] },
  { id: "f06", departamento: "feminino", nome: "Saia Midi Plissada", categoria: "Saias", preco: 139.9, tamanhos: ["P", "M", "G"], cor: "#eed9dd", fotos: ["img/f06-frente.svg", "img/f06-costas.svg", "img/f06-lado.svg"], promo: 119.9, destaque: true },
  { id: "f07", departamento: "feminino", nome: "Scarpin Nude Salto Fino", categoria: "Sapatos", preco: 229.9, tamanhos: ["34", "35", "36", "37", "38", "39"], cor: "#f4e6dc", fotos: ["img/f07-lado.svg", "img/f07-frente.svg", "img/f07-costas.svg"], vistas: ["Lado", "Frente", "Costas"], novo: true, destaque: true },
  { id: "f08", departamento: "feminino", nome: "Sandália Rasteira Caramelo", categoria: "Sapatos", preco: 119.9, tamanhos: ["34", "35", "36", "37", "38", "39"], cor: "#f6ecd9", fotos: ["img/f08-lado.svg", "img/f08-frente.svg", "img/f08-costas.svg"], vistas: ["Lado", "Frente", "Costas"], promo: 99.9 },
  { id: "f09", departamento: "feminino", nome: "Tênis Casual Branco", categoria: "Sapatos", preco: 199.9, tamanhos: ["34", "35", "36", "37", "38", "39"], cor: "#e9eef0", fotos: ["img/f09-lado.svg", "img/f09-frente.svg", "img/f09-costas.svg"], vistas: ["Lado", "Frente", "Costas"] },

  // ---------- Infantil ----------
  { id: "i01", departamento: "infantil", nome: "Body Bebê Algodão", categoria: "Bebê", preco: 49.9, tamanhos: ["RN", "P", "M", "G"], cor: "#fbf1cf", fotos: ["img/i01-frente.svg", "img/i01-costas.svg", "img/i01-lado.svg"], novo: true },
  { id: "i02", departamento: "infantil", nome: "Macacão Plush Ursinho", categoria: "Bebê", preco: 89.9, tamanhos: ["P", "M", "G"], cor: "#efe5d6", fotos: ["img/i02-frente.svg", "img/i02-costas.svg", "img/i02-lado.svg"], destaque: true },
  { id: "i03", departamento: "infantil", nome: "Vestido Infantil Poá", categoria: "Menina", preco: 99.9, tamanhos: ["2", "4", "6", "8", "10"], cor: "#fadde3", fotos: ["img/i03-frente.svg", "img/i03-costas.svg", "img/i03-lado.svg"], novo: true, destaque: true },
  { id: "i04", departamento: "infantil", nome: "Conjunto Short e Blusa", categoria: "Menina", preco: 79.9, tamanhos: ["2", "4", "6", "8"], cor: "#e2f0de", fotos: ["img/i04-frente.svg", "img/i04-costas.svg", "img/i04-lado.svg"], promo: 64.9 },
  { id: "i05", departamento: "infantil", nome: "Camiseta Estampada Dino", categoria: "Menino", preco: 49.9, tamanhos: ["2", "4", "6", "8", "10", "12"], cor: "#dde9f0", fotos: ["img/i05-frente.svg", "img/i05-costas.svg", "img/i05-lado.svg"] },
  { id: "i06", departamento: "infantil", nome: "Bermuda Sarja", categoria: "Menino", preco: 69.9, tamanhos: ["4", "6", "8", "10", "12"], cor: "#e3e8dc", fotos: ["img/i06-frente.svg", "img/i06-costas.svg", "img/i06-lado.svg"] },
];
