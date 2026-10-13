# -*- coding: utf-8 -*-
"""
exportar_metas.py -- gera a planilha que o painel AdZep Metas le.

Como usar: coloque este arquivo na pasta painelgamauto e rode DEPOIS do
Atualizar_painel.bat:

    python exportar_metas.py

Ele le os dados ja coletados (painel_metas.html da coleta enxuta, ou o
painel.html completo -- o que for mais recente) e nao chama nenhuma API de novo; e grava metas_export.xlsx com as colunas
Mes | Publisher | Formato | PageViews | Impressoes | Receita Bruta | % de Rev |
Dados ate | Dias | Gerado em | Solicitacoes | Impressoes AdX | Origem |
Receita AdX (fill rate do Ad Exchange e quebra da receita por demanda).
Depois e so anexar o metas_export.xlsx em "Lancar dados" no AdZep Metas.

Tambem grava metas_export.json, com os mesmos dados do mes atual prontos para
a atualizacao automatica do painel.

Receita por formato = Ad Exchange + Open Bidding do mesmo ad unit, mais uma
linha por parceiro programatico (Membrana, SeedTag). Deals diretos e
SportRadar ficam fora da meta.
"""
import collections
import datetime
import calendar
import json
import os
import re
import sys
import unicodedata

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

# ----------------------------------------------------------------------------
# AJUSTES -- confira estes tres blocos
# ----------------------------------------------------------------------------

# Quantos meses exportar, contando o mes atual.
MESES = 4

# O dia da coleta ainda esta incompleto, e o GA4 entrega os PageViews com mais
# atraso que o Ad Manager entrega a receita -- isso infla o RPM do dia.
# False = o painel de metas recebe so dias fechados (ate ontem). Recomendado.
# True  = inclui o dia de hoje, parcial.
INCLUIR_HOJE = False

# Como cada ad unit vira um formato. A primeira palavra encontrada no nome do
# ad unit decide. Esta ordem reproduz a tabela manual de setembro/2026.
GRUPOS = [
    ("intext", "Intext"), ("in-content", "Intext"),
    ("billboard", "Billboard"), ("top-banner", "Billboard"),
    ("in-feed", "Billboard"), ("top-results", "Billboard"),
    ("anchor", "Anchor"), ("slider", "Slider"), ("reward", "Rewarded"),
    ("side", "Side"), ("footer", "Footer"), ("interstitial", "Interstitial"),
]
GRUPO_PADRAO = "Outros"

# Parceiros: nome da linha no painel e fator aplicado sobre a receita da API.
# 0.8 na Membrana reproduz a tabela manual de setembro (API x 0,8) -- CONFIRME
# se esse desconto e a regra de voces; use 1.0 para a receita cheia da API.
PARCEIROS = {
    "membrana": ("Membrana", 0.8),
    "seedtag": ("SeedTag", 1.0),
}

# A meta considera so Ad Exchange, Open Bidding e parceiros programaticos.
# Por isso ficam FORA: deals diretos ("deal") e SportRadar (pago por clique).
# Para incluir deals, troque para ("ob", "deal"); para incluir o SportRadar,
# acrescente  "sportradar": ("SportRadar", 1.0)  em PARCEIROS.
FONTES_DEMANDA = ("ob",)

# Sites em que a nossa tag roda so em parte dos dispositivos. Para esses, os
# PageViews do GA4 entram so dos dispositivos listados, senao o RPM de pagina
# fica diluido pelo trafego onde nao temos anuncio. Use o id do sites.xlsx.
# Dispositivos possiveis: "mobile", "desktop", "tablet".
PV_DISPOSITIVOS = {
    "riovagas": ("mobile",),
}

# Nome do site no AdZep Metas, quando for diferente do "nome" do sites.xlsx.
NOMES = {
    "revistaoeste": "Revista Oeste",
    "comidinhasdochef": "Comidinhas do Chef",
}
# ----------------------------------------------------------------------------


def grupo(ad_unit):
    n = (ad_unit or "").lower()
    for chave, nome in GRUPOS:
        if chave in n:
            return nome
    return GRUPO_PADRAO


def ler_data(caminho):
    texto = open(caminho, encoding="utf-8", errors="replace").read()
    marca = "const DATA = "
    i = texto.index(marca) + len(marca)
    data, _ = json.JSONDecoder().raw_decode(texto, i)
    return data


def meses_alvo(n):
    hoje = datetime.date.today()
    y, m, out = hoje.year, hoje.month, []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


def slug(texto):
    t = unicodedata.normalize("NFD", str(texto).lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def momento_geracao(data):
    """'gerado_em' do painel ('02/10/2026 19:46') -> datetime; agora, se faltar."""
    try:
        return datetime.datetime.strptime(str(data.get("gerado_em")), "%d/%m/%Y %H:%M")
    except Exception:  # noqa: BLE001
        return datetime.datetime.now()


def exportar(data, meses):
    linhas, avisos, diario = [], [], {}
    gerado = momento_geracao(data)
    gerado_txt = gerado.strftime("%d/%m/%Y %H:%M")
    hoje = gerado.strftime("%Y-%m-%d")
    mes_atual = hoje[:7]

    def vale(dia):
        return INCLUIR_HOJE or dia != hoje
    for sid in data.get("order") or list(data["sites"]):
        s = data["sites"][sid]
        nome = NOMES.get(sid) or str(s.get("nome") or sid).strip()
        share = (s.get("descontoPct") or 0) / 100.0
        dias = s.get("dias") or []
        ob_ini = (s.get("demandaFmtWin") or [None])[0]
        for mes in meses:
            idx = [i for i, d in enumerate(dias) if d.startswith(mes) and vale(d)]
            dd = collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0.0]))  # formato -> dia -> [impr, rec]
            fm = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0.0, "gam"])  # formato -> [impr, rec, solicitacoes AdX, impr AdX, receita AdX, origem]
            for f in s.get("ids") or []:
                if f == "__ALL__":
                    continue
                serie = s["seriesD"][f]["total"]
                for i in idx:
                    dd[grupo(f)][dias[i]][0] += serie[i]["impr"]
                    dd[grupo(f)][dias[i]][1] += serie[i]["rev"]
                    fm[grupo(f)][0] += serie[i]["impr"]
                    fm[grupo(f)][1] += serie[i]["rev"]
                    fm[grupo(f)][2] += serie[i].get("req") or 0
                    fm[grupo(f)][3] += serie[i]["impr"]
                    fm[grupo(f)][4] += serie[i]["rev"]
            for f, fontes in (s.get("demandaFmtD") or {}).items():
                if f == "__ALL__":
                    continue
                for fonte in FONTES_DEMANDA:
                    for d, x in (fontes.get(fonte) or {}).items():
                        if d.startswith(mes) and vale(d):
                            dd[grupo(f)][d][0] += x.get("impr") or 0
                            dd[grupo(f)][d][1] += x.get("rev") or 0
                            fm[grupo(f)][0] += x.get("impr") or 0
                            fm[grupo(f)][1] += x.get("rev") or 0
            if ob_ini and idx and mes + "-01" < ob_ini and mes >= ob_ini[:7]:
                avisos.append(f"{nome} {mes}: Open Bidding por formato so a partir de {ob_ini}")
            elif idx and (not ob_ini or mes < ob_ini[:7]):
                avisos.append(f"{nome} {mes}: sem Open Bidding por formato (fora da janela), so Ad Exchange")
            for pid, (rotulo, fator) in PARCEIROS.items():
                p = (s.get("parceiros") or {}).get(pid)
                if not p:
                    continue
                for d, x in (p.get("porDia") or {}).items():
                    if d.startswith(mes) and vale(d):
                        dd[rotulo][d][0] += x.get("impressions") or 0
                        dd[rotulo][d][1] += (x.get("revenue") or 0) * fator
                        fm[rotulo][0] += x.get("impressions") or 0
                        fm[rotulo][1] += (x.get("revenue") or 0) * fator
                        fm[rotulo][5] = "par"
            devs = PV_DISPOSITIVOS.get(str(sid).strip()) or ("total",)
            pv = sum(v for dev in devs for d, v in ((s.get("pvDays") or {}).get(dev) or {}).items() if d.startswith(mes) and vale(d))
            base_pv = "total" if devs == ("total",) else "so " + " + ".join(devs)
            fm = {k: v for k, v in fm.items() if v[0] > 0 or v[1] >= 0.005}
            if not fm:
                continue
            if not pv:
                avisos.append(f"{nome} {mes}: sem PageViews do GA4 (RPM nao sera calculado)")
            # ultimo dia do mes com entrega, e quantos dias de dado isso representa
            serie_all = (s.get("seriesD") or {}).get("__ALL__", {}).get("total") or []
            com_dado = [dias[i] for i in idx if i < len(serie_all) and (serie_all[i].get("impr") or 0) > 0]
            ate = max(com_dado) if com_dado else (dias[idx[-1]] if idx else mes + "-01")
            dia_ate = int(ate[8:10])
            dias_mes = calendar.monthrange(int(mes[:4]), int(mes[5:]))[1]
            if mes < mes_atual:  # mes fechado: vale o mes inteiro
                n_dias = dias_mes
            elif ate == hoje:  # dia de hoje, ainda incompleto
                n_dias = round(dia_ate - 1 + (gerado.hour + gerado.minute / 60) / 24, 2)
            else:
                n_dias = dia_ate
            n_dias = max(0.25, min(n_dias, dias_mes))
            ate_data = datetime.date(int(ate[:4]), int(ate[5:7]), dia_ate)
            # serie dia a dia (grafico diario do painel)
            ds = [f"{mes}-{n:02d}" for n in range(1, dia_ate + 1) if vale(f"{mes}-{n:02d}")]
            pv_dia = [int(sum(((s.get("pvDays") or {}).get(dev) or {}).get(d) or 0 for dev in devs)) for d in ds]
            diario[(mes, nome)] = {"d": [int(d[8:10]) for d in ds],
                                   "f": {k: {"r": [round(dd[k][d][1], 2) for d in ds], "i": [int(round(dd[k][d][0])) for d in ds]} for k in fm},
                                   **({"pv": pv_dia} if any(pv_dia) else {})}
            for formato, (impr, rec, req, iadx, radx, origem) in sorted(fm.items(), key=lambda kv: -kv[1][1]):
                linhas.append((datetime.date(int(mes[:4]), int(mes[5:]), 1), nome, formato, int(pv), int(round(impr)), round(rec, 2), share, ate_data, n_dias, gerado_txt, int(round(req)), int(round(iadx)),
                               "Parceiro" if origem == "par" else "Ad Manager", round(radx, 2), base_pv))
    return linhas, avisos, diario


def ler_faixas(base):
    """faixas_export.json (coletar_faixas.py), se existir e tiver menos de 3 dias."""
    c = os.path.join(base, "faixas_export.json")
    try:
        if datetime.datetime.now().timestamp() - os.path.getmtime(c) > 3 * 86400:
            return None
        with open(c, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def montar_json(linhas, meses, diario=None, faixas=None):
    """Documentos do mes atual e do anterior, no formato do painel."""
    alvo = set(meses[-2:])
    docs = {}
    for mes_d, nome, formato, pv, impr, rec, share, ate, n_dias, gerado_txt, req, iadx, origem, radx, base_pv in linhas:
        mes = mes_d.strftime("%Y-%m")
        if mes not in alvo:
            continue
        doc_id = f"{mes}__{slug(nome)}"
        d = docs.setdefault(doc_id, {"mes": mes, "site": nome, "pv": pv, "share": share, "dias": n_dias,
                                     "ate": ate.strftime("%Y-%m-%d"), "gerado": gerado_txt, "formatos": {},
                                     **({"pvBase": base_pv} if base_pv != "total" else {})})
        d["formatos"][formato] = {"imp": impr, "rec": rec, **({"req": req, "iadx": iadx} if req else {}),
                                 "src": "par" if origem == "Parceiro" else "gam", **({"radx": radx} if origem != "Parceiro" else {})}
    for d in docs.values():
        x = (diario or {}).get((d["mes"], d["site"]))
        if x:
            d["diario"] = x
        fx = ((faixas or {}).get("sites") or {}).get(d["site"])
        if fx and d["mes"] == meses[-1]:
            d["faixas"] = {"de": faixas.get("de"), "ate": faixas.get("ate"), "f": fx}
    return {"gerado": linhas[0][9] if linhas else "", "docs": [{"id": k, "data": v} for k, v in docs.items()]}


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    if len(sys.argv) > 1:
        entrada = sys.argv[1]
    else:  # usa o mais recente entre a coleta enxuta e o painel completo
        cand = [os.path.join(base, n) for n in ("painel_metas.html", "painel.html")]
        cand = [c for c in cand if os.path.exists(c)]
        entrada = max(cand, key=os.path.getmtime) if cand else os.path.join(base, "painel.html")
    print(f"Lendo: {entrada}")
    saida = sys.argv[2] if len(sys.argv) > 2 else os.path.join(base, "metas_export.xlsx")
    from openpyxl import Workbook
    data = ler_data(entrada)
    meses = meses_alvo(MESES)
    linhas, avisos, diario = exportar(data, meses)
    wb = Workbook()
    ws = wb.active
    ws.title = "Metas"
    ws.append(["Mês", "Publisher", "Formato", "PageViews", "Impressões", "Receita Bruta", "% de Rev", "Dados até", "Dias", "Gerado em", "Solicitações", "Impressões AdX", "Origem", "Receita AdX", "Base PV"])
    for l in linhas:
        ws.append(list(l))
    for row in ws.iter_rows(min_row=2):
        row[0].number_format = "mm/yyyy"
        row[3].number_format = row[4].number_format = "#,##0"
        row[5].number_format = "#,##0.00"
        row[6].number_format = "0%"
        row[7].number_format = "dd/mm/yyyy"
        row[8].number_format = "0.00"
        row[10].number_format = row[11].number_format = "#,##0"
        row[13].number_format = "#,##0.00"
    for col, w in zip("ABCDEFGHIJKLMNO", (10, 26, 16, 14, 14, 14, 10, 12, 8, 18, 14, 14, 12, 12, 12)):
        ws.column_dimensions[col].width = w
    # aba "Por dia": alimenta o grafico diario do painel (mes atual e anterior)
    wd = wb.create_sheet("Por dia")
    wd.append(["Mês", "Publisher", "Série", "Dia", "Impressões", "Receita", "PageViews"])
    for (mes, nome), x in diario.items():
        if mes not in meses[-2:]:
            continue
        mes_d = datetime.date(int(mes[:4]), int(mes[5:]), 1)
        for i, dia in enumerate(x["d"]):
            if x.get("pv"):
                wd.append([mes_d, nome, "PageViews", dia, 0, 0, x["pv"][i]])
            for k, v in x["f"].items():
                wd.append([mes_d, nome, k, dia, v["i"][i], v["r"][i], 0])
    for row in wd.iter_rows(min_row=2):
        row[0].number_format = "mm/yyyy"
    faixas = ler_faixas(base)
    if faixas:  # aba "Faixas": lances por faixa de preco, para o simulador de regra de preco
        wf = wb.create_sheet("Faixas")
        wf.append(["Mês", "Publisher", "Série", "Faixa", "Ganhadores", "Receita ganhadores", "Barrados no piso", "Superados", "De", "Até", "Regra"])
        mes_d = datetime.date(int(meses[-1][:4]), int(meses[-1][5:]), 1)
        for nome, fmts in (faixas.get("sites") or {}).items():
            for k, v in fmts.items():
                for b in v["b"]:
                    wf.append([mes_d, nome, k, b[0], b[1], b[2], b[3], b[4], faixas.get("de"), faixas.get("ate"), v.get("regra", "")])
    wb.save(saida)
    saida_json = os.path.splitext(saida)[0] + ".json"
    with open(saida_json, "w", encoding="utf-8") as fj:
        json.dump(montar_json(linhas, meses, diario, faixas), fj, ensure_ascii=False)
    sites = len({l[1] for l in linhas})
    print(f"Painel gerado em: {data.get('gerado_em')}")
    print(f"Exportado: {len(linhas)} linhas, {sites} sites, meses {meses[0]} a {meses[-1]}")
    print(f"Arquivo: {saida}")
    print(f"Arquivo: {saida_json}")
    if avisos:
        print(f"\n{len(avisos)} aviso(s):")
        for a in avisos:
            print("  - " + a)


if __name__ == "__main__":
    main()
