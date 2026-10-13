# -*- coding: utf-8 -*-
"""
exportar_zepone.py -- gera os arquivos de dados do painel ZepOne publicado.

Le o painel.html que o Atualizar_painel.bat acabou de gerar (nao chama nenhuma
API), guarda so os ultimos 60 dias e grava um arquivo por site na pasta
zepone_dados:

    zepone_dados/zepone__geral.json      (lista de sites, deals, anotacoes)
    zepone_dados/zepone_<site>.json      (um por site)

Depois, no painel ZepOne, clique em "Atualizar dados" e selecione todos os
arquivos dessa pasta.

Como usar: python exportar_zepone.py   (na pasta painelgamauto)
"""
import datetime
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

DIAS = 60  # quantos dias de historico vao para o painel publicado
# Dados que o painel publicado nao usa (abas retiradas) e por isso ficam fora.
FORA = ("demandaFmtD", "demandaFmtWin")


def ler_data(caminho):
    texto = open(caminho, encoding="utf-8", errors="replace").read()
    marca = "const DATA = "
    i = texto.index(marca) + len(marca)
    data, _ = json.JSONDecoder().raw_decode(texto, i)
    return data


def _e_data(k):
    return isinstance(k, str) and len(k) == 10 and k[4] == "-" and k[7] == "-"


def cortar(x, corte):
    """Remove, em qualquer nivel, as chaves que sao datas anteriores ao corte."""
    if isinstance(x, dict):
        if x and all(_e_data(k) for k in x):
            return {k: cortar(v, corte) for k, v in x.items() if k >= corte}
        return {k: cortar(v, corte) for k, v in x.items()}
    if isinstance(x, list):
        return [cortar(v, corte) for v in x]
    return x


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    entrada = sys.argv[1] if len(sys.argv) > 1 else os.path.join(base, "painel.html")
    saida = os.path.join(base, "zepone_dados")
    print(f"Lendo: {entrada}")
    data = ler_data(entrada)
    sites = data.get("sites") or {}
    ultimo = max((s["dias"][-1] for s in sites.values() if s.get("dias")), default=None)
    if not ultimo:
        print("[ERRO] O painel.html nao tem dados de nenhum site.")
        sys.exit(1)
    corte = (datetime.date.fromisoformat(ultimo) - datetime.timedelta(days=DIAS - 1)).isoformat()
    os.makedirs(saida, exist_ok=True)
    for velho in os.listdir(saida):
        if velho.startswith("zepone_") and velho.endswith(".json"):
            os.remove(os.path.join(saida, velho))
    geral = {k: v for k, v in data.items() if k != "sites"}
    geral["janela_dias"] = DIAS
    geral["order"] = [sid for sid in (data.get("order") or list(sites)) if sid in sites]
    total = 0

    def gravar(nome, obj):
        caminho = os.path.join(saida, nome)
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        return os.path.getsize(caminho)

    total += gravar("zepone__geral.json", geral)
    for sid, s in sites.items():
        idx = [i for i, d in enumerate(s.get("dias") or []) if d >= corte]
        novo = {}
        for k, v in s.items():
            if k in FORA:
                continue
            if k == "dias":
                novo[k] = [v[i] for i in idx]
            elif k == "seriesD":
                novo[k] = {f: {dv: [linhas[i] for i in idx if i < len(linhas)] for dv, linhas in devs.items()} for f, devs in v.items()}
            elif k == "intraday":
                w = cortar(v, corte)
                if isinstance(w, dict) and isinstance(w.get("datasHora"), list):
                    w["datasHora"] = [d for d in w["datasHora"] if d >= corte]
                novo[k] = w
            else:
                novo[k] = cortar(v, corte)
        tam = gravar(f"zepone_{sid}.json", novo)
        total += tam
        aviso = "  <-- ACIMA DE 20 MB: nao vai subir" if tam > 20 * 1024 * 1024 else ""
        print(f"  {sid}: {tam / 1e6:.1f} MB{aviso}")
    # marcador: gravado por ultimo, avisa a atualizacao automatica de que os arquivos estao completos
    gravar("_pronto.json", {"gerado_em": data.get("gerado_em"), "de": corte, "ate": ultimo,
                            "arquivos": sorted(n for n in os.listdir(saida) if n.startswith("zepone_") and n.endswith(".json")),
                            "terminou": datetime.datetime.now().strftime("%d/%m/%Y %H:%M")})
    print(f"Painel gerado em: {data.get('gerado_em')} | dados de {corte} a {ultimo}")
    print(f"Pasta: {saida} ({len(sites) + 1} arquivos, {total / 1e6:.0f} MB)")
    print('Agora abra o ZepOne, clique em "Atualizar dados" e selecione todos os arquivos dessa pasta.')


if __name__ == "__main__":
    main()
