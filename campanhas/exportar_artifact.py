#!/usr/bin/env python3
"""
exportar_artifact.py — gera os arquivos JSON que o painel "ZepOne Campanhas"
(artifact no claude.ai) lê.

O artifact roda no navegador e não consegue falar com o Google Ad Manager, nem
com o Google Sheets. Quem fala com o GAM continua sendo o gam_monitor.py (que
gera o output/data_cache.json). Este script pega esse cache e prepara 4
arquivos na pasta  output/gamone_dados/ :

  gamone__geral.json       resumo (data da coleta, lista de publishers)
  gamone_campanhas.json    todas as campanhas (o que o Dashboard/Insights/
                           Faturamento/Alertas/Relatórios usam)
  gamone_inventario.json   total de impressões da rede por Ad Unit e por dia
                           (alimenta o "% do inventário consumido")
  gamone_detalhe.json      entrega por tamanho de criativo + CPM + receita das
                           campanhas dos publishers em DETALHE_PUBLISHERS
                           (alimenta o relatório no padrão Diário do Rio)

Depois é só abrir o painel no claude.ai e clicar em "Enviar arquivos",
selecionando esses 4 arquivos.

Uso:
    py gam_monitor.py                 # (como sempre) atualiza o data_cache.json
    py exportar_artifact.py           # gera os 4 arquivos
    py exportar_artifact.py --sem-inventario --sem-detalhe   # rápido, só campanhas

Opções:
    --meses N          quantos meses de inventário buscar (padrão 4, contando o atual)
    --detalhe-desde M  só busca detalhe de campanhas ativas desde o mês M (AAAA-MM);
                       padrão = 3 meses atrás
    --hoje AAAA-MM-DD  data de referência dos períodos "últimos 7/30 dias"
                       (padrão: hoje)
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import gam_monitor as gm

# Publishers que precisam do relatório por tamanho de criativo (com CPM/receita).
# Cada campanha deles exige uma consulta ao GAM, por isso é uma lista curta.
DETALHE_PUBLISHERS = ["Diário do Rio"]

SAIDA = gm.OUTPUT_DIR / "gamone_dados"
DETALHE_CACHE = gm.OUTPUT_DIR / "detalhe_cache.json"


def dump(obj, path):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    kb = Path(path).stat().st_size / 1024
    print(f"  -> {Path(path).name}  ({kb:,.0f} KB)")


def meses_recentes(n, hoje):
    year, month = hoje.year, hoje.month
    out = []
    for _ in range(n):
        out.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return sorted(out)


def completar_campanhas(cache):
    """Garante que toda campanha tenha o campo 'anomaly' (caches gerados por
    versões antigas do gam_monitor.py não têm)."""
    for campanhas in cache.values():
        for c in campanhas:
            if "anomaly" not in c:
                c["anomaly"] = gm.detect_anomaly(c.get("daily_stats") or [], c.get("status"))
    return cache


def exportar_inventario(cache, meses, avisos):
    try:
        import reports_server as rs
    except Exception as e:  # Flask/gspread não instalados etc.
        avisos.append(f"Inventário não exportado: não consegui importar reports_server.py ({e}).")
        return {}

    redes = sorted({str(c["network_code"]) for cs in cache.values() for c in cs if c.get("network_code")})
    saida = {}
    for rede in redes:
        saida[rede] = {}
        for mes in meses:
            print(f"  inventário: rede {rede} · {mes} ...", flush=True)
            try:
                saida[rede][mes] = rs.get_network_total_impressions_cached(rede, mes)
            except Exception as e:
                avisos.append(f"Inventário da rede {rede} em {mes} falhou: {e}")
    return saida


def exportar_detalhe(cache, desde_mes, avisos):
    try:
        import reports_server as rs
    except Exception as e:
        avisos.append(f"Detalhe não exportado: não consegui importar reports_server.py ({e}).")
        return {}

    antigo = {}
    if DETALHE_CACHE.exists():
        try:
            antigo = json.loads(DETALHE_CACHE.read_text(encoding="utf-8"))
        except Exception:
            antigo = {}

    novo = {}
    alvo = [(p, c) for p in DETALHE_PUBLISHERS for c in cache.get(p, [])]
    for i, (pub, c) in enumerate(alvo, 1):
        cid = str(c["id"])
        recente = any(m >= desde_mes for m in (c.get("month_keys") or []))
        guardado = antigo.get(cid)
        # campanha já finalizada e já baixada antes: reaproveita, não consulta de novo
        if guardado and guardado.get("status") == "COMPLETED" and c.get("status") == "COMPLETED":
            novo[cid] = guardado
            continue
        if not recente and guardado:
            novo[cid] = guardado
            continue
        if not recente:
            continue
        print(f"  detalhe {i}/{len(alvo)}: {pub} · {c['name']} ...", flush=True)
        try:
            linhas = rs.fetch_detailed_report(c, break_by_creative_size=True, include_revenue=True)
            novo[cid] = {"status": c.get("status"), "linhas": linhas}
        except Exception as e:
            avisos.append(f"Detalhe da campanha {cid} ({c['name']}) falhou: {e}")
            if guardado:
                novo[cid] = guardado

    DETALHE_CACHE.write_text(json.dumps(novo, ensure_ascii=False), encoding="utf-8")
    return {cid: v["linhas"] for cid, v in novo.items()}


def main():
    ap = argparse.ArgumentParser(description="Gera os JSON do painel ZepOne Campanhas.")
    ap.add_argument("--sem-inventario", action="store_true")
    ap.add_argument("--sem-detalhe", action="store_true")
    ap.add_argument("--meses", type=int, default=4)
    ap.add_argument("--detalhe-desde", default=None)
    ap.add_argument("--hoje", default=None)
    args = ap.parse_args()

    if not gm.DATA_CACHE_PATH.exists():
        sys.exit("Não achei output/data_cache.json — rode  py gam_monitor.py  primeiro.")

    hoje = datetime.strptime(args.hoje, "%Y-%m-%d") if args.hoje else datetime.now()
    cache = completar_campanhas(json.loads(gm.DATA_CACHE_PATH.read_text(encoding="utf-8")))
    avisos = []

    SAIDA.mkdir(parents=True, exist_ok=True)
    print(f"Exportando para {SAIDA}")

    geral = {
        "versao": 1,
        "gerado_em": hoje.strftime("%d/%m/%Y %H:%M"),
        "hoje": hoje.strftime("%Y-%m-%d"),
        "tz": "America/Sao_Paulo",
        "publishers": sorted(cache.keys()),
        "detalhe_publishers": DETALHE_PUBLISHERS,
        "n_campanhas": sum(len(v) for v in cache.values()),
        "cache_gerado_em": datetime.fromtimestamp(gm.DATA_CACHE_PATH.stat().st_mtime).strftime("%d/%m/%Y %H:%M"),
        "config": {"underdelivery_threshold_pct": gm.UNDERDELIVERY_THRESHOLD_PCT, "days_before_end": gm.DAYS_BEFORE_END_TO_ALERT},
        "avisos": avisos,
    }

    inventario = {}
    if not args.sem_inventario:
        inventario = exportar_inventario(cache, meses_recentes(args.meses, hoje), avisos)

    detalhe = {}
    if not args.sem_detalhe:
        desde = args.detalhe_desde or meses_recentes(3, hoje)[0]
        detalhe = exportar_detalhe(cache, desde, avisos)

    geral["tem_inventario"] = bool(inventario)
    geral["tem_detalhe"] = bool(detalhe)
    geral["meses_inventario"] = sorted({m for r in inventario.values() for m in r})

    dump(geral, SAIDA / "gamone__geral.json")
    dump({"publishers": cache}, SAIDA / "gamone_campanhas.json")
    dump({"redes": inventario}, SAIDA / "gamone_inventario.json")
    dump({"campanhas": detalhe}, SAIDA / "gamone_detalhe.json")

    if avisos:
        print("\nAvisos:")
        for a in avisos:
            print("  -", a)
    print("\nPronto! No painel, clique em 'Enviar arquivos' e selecione os 4 arquivos gamone_*.json.")


if __name__ == "__main__":
    main()
