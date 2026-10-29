// ============================================================
//  CONFIGURAÇÃO DA LOJA — edite só este arquivo para trocar
//  nome, contatos e peças. Não precisa mexer no resto.
// ============================================================

const LOJA = {
  nome: "Denise",
  slogan: "Moda feminina com afeto",
  descricao:
    "Peças escolhidas a dedo para mulheres que querem conforto e estilo no dia a dia. Atendimento pessoal pelo WhatsApp.",
  // Só números, com DDI 55 + DDD. Ex.: "5511999998888"
  whatsapp: "5511999999999",
  instagram: "denise.moda", // sem o @
  cidade: "São Paulo · SP",
  // Caminho do logo (ex.: "img/logo.png"). Vazio = usa o nome em texto.
  logo: "",
};

// Cada peça: id único, nome, categoria, preço, tamanhos e fotos.
// "fotos": caminhos de imagens na pasta img/ (ex.: ["img/vestido-1.jpg"]).
// Sem foto, o site mostra um fundo na "cor" indicada.
const PRODUTOS = [
  { id: "v01", nome: "Vestido Midi Floral", categoria: "Vestidos", preco: 189.9, tamanhos: ["P", "M", "G"], cor: "#e8b4a0", fotos: [], novo: true },
  { id: "v02", nome: "Vestido Longo Linho", categoria: "Vestidos", preco: 249.9, tamanhos: ["P", "M", "G", "GG"], cor: "#c9b79c", fotos: [] },
  { id: "b01", nome: "Blusa Ombro a Ombro", categoria: "Blusas", preco: 89.9, tamanhos: ["P", "M", "G"], cor: "#f2d7c9", fotos: [], novo: true },
  { id: "b02", nome: "Camisa de Seda Off-White", categoria: "Blusas", preco: 159.9, tamanhos: ["P", "M", "G"], cor: "#efe6da", fotos: [] },
  { id: "c01", nome: "Calça Pantalona Alfaiataria", categoria: "Calças", preco: 199.9, tamanhos: ["36", "38", "40", "42", "44"], cor: "#7d6a5a", fotos: [] },
  { id: "c02", nome: "Calça Jeans Wide Leg", categoria: "Calças", preco: 179.9, tamanhos: ["36", "38", "40", "42"], cor: "#8fa3b8", fotos: [] },
  { id: "s01", nome: "Saia Midi Plissada", categoria: "Saias", preco: 139.9, tamanhos: ["P", "M", "G"], cor: "#b5838d", fotos: [], promo: 119.9 },
  { id: "a01", nome: "Bolsa de Palha", categoria: "Acessórios", preco: 99.9, tamanhos: ["Único"], cor: "#d9c29a", fotos: [] },
];
