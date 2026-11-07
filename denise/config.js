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
  { id: "feminino", nome: "Feminino", chamada: "Vestidos, blusas e calças para o dia a dia", cor: "#f3e3dc" },
  { id: "infantil", nome: "Infantil", chamada: "Conforto para brincar, do bebê aos 12 anos", cor: "#e1ecf2" },
];

// Cada peça: id único, departamento ("feminino" ou "infantil"), nome,
// categoria, preço, tamanhos e fotos.
// "fotos": caminhos de imagens na pasta img/ (ex.: ["img/vestido-1.jpg"]).
// Sem foto, o site mostra um fundo na "cor" indicada.
// Opcionais: promo (preço promocional), novo: true, descricao.
const PRODUTOS = [
  // ---------- Feminino ----------
  { id: "f01", departamento: "feminino", nome: "Vestido Midi Floral", categoria: "Vestidos", preco: 189.9, tamanhos: ["P", "M", "G"], cor: "#f3dcd3", fotos: ["img/f01.svg"], novo: true },
  { id: "f02", departamento: "feminino", nome: "Vestido Longo Linho", categoria: "Vestidos", preco: 249.9, tamanhos: ["P", "M", "G", "GG"], cor: "#ece4d8", fotos: ["img/f02.svg"] },
  { id: "f03", departamento: "feminino", nome: "Blusa Ombro a Ombro", categoria: "Blusas", preco: 89.9, tamanhos: ["P", "M", "G"], cor: "#f7e8e1", fotos: ["img/f03.svg"], novo: true },
  { id: "f04", departamento: "feminino", nome: "Camisa de Seda Off-White", categoria: "Blusas", preco: 159.9, tamanhos: ["P", "M", "G"], cor: "#f2eee8", fotos: ["img/f04.svg"] },
  { id: "f05", departamento: "feminino", nome: "Calça Pantalona Alfaiataria", categoria: "Calças", preco: 199.9, tamanhos: ["36", "38", "40", "42", "44"], cor: "#e2dbd3", fotos: ["img/f05.svg"] },
  { id: "f06", departamento: "feminino", nome: "Saia Midi Plissada", categoria: "Saias", preco: 139.9, tamanhos: ["P", "M", "G"], cor: "#eed9dd", fotos: ["img/f06.svg"], promo: 119.9 },

  // ---------- Infantil ----------
  { id: "i01", departamento: "infantil", nome: "Body Bebê Algodão", categoria: "Bebê", preco: 49.9, tamanhos: ["RN", "P", "M", "G"], cor: "#fbf1cf", fotos: ["img/i01.svg"], novo: true },
  { id: "i02", departamento: "infantil", nome: "Macacão Plush Ursinho", categoria: "Bebê", preco: 89.9, tamanhos: ["P", "M", "G"], cor: "#efe5d6", fotos: ["img/i02.svg"] },
  { id: "i03", departamento: "infantil", nome: "Vestido Infantil Poá", categoria: "Menina", preco: 99.9, tamanhos: ["2", "4", "6", "8", "10"], cor: "#fadde3", fotos: ["img/i03.svg"], novo: true },
  { id: "i04", departamento: "infantil", nome: "Conjunto Short e Blusa", categoria: "Menina", preco: 79.9, tamanhos: ["2", "4", "6", "8"], cor: "#e2f0de", fotos: ["img/i04.svg"], promo: 64.9 },
  { id: "i05", departamento: "infantil", nome: "Camiseta Estampada Dino", categoria: "Menino", preco: 49.9, tamanhos: ["2", "4", "6", "8", "10", "12"], cor: "#dde9f0", fotos: ["img/i05.svg"] },
  { id: "i06", departamento: "infantil", nome: "Bermuda Sarja", categoria: "Menino", preco: 69.9, tamanhos: ["4", "6", "8", "10", "12"], cor: "#e3e8dc", fotos: ["img/i06.svg"] },
];
