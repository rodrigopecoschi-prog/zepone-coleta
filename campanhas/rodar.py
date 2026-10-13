#!/usr/bin/env python3
"""
ZepOne — área Campanhas: roda o monitor de campanhas no servidor.

1. gam_monitor.py busca as campanhas diretas das redes do GAM (accounts.yaml,
   gerado pelo ZepOne com a service account da tela "Coleta") e manda o e-mail
   de alertas (Under / prazo) uma vez por dia, se o SMTP estiver configurado.
2. Gera os arquivos gamone_*.json que a tela Campanhas lê (o mesmo formato do
   exportar_artifact.py). Inventário, detalhe por criativo e os segmentos (regiões, dispositivos,
   tamanhos e blocos de anúncio) pesam mais: só são
   buscados de novo com --completo; senão os arquivos anteriores continuam valendo.

Uso: python rodar.py [--completo]
"""
import json
import sys
import types
from datetime import datetime
from pathlib import Path

# reports_server.py é uma tela Flask; aqui só usamos as funções dele (relatório
# detalhado e inventário), então o Flask vira um módulo vazio.
if "flask" not in sys.modules:
    class _App:
        def __init__(self, *a, **k): pass
        def route(self, *a, **k): return lambda f: f
        def run(self, *a, **k): pass
    _fk = types.ModuleType("flask")
    _fk.Flask = _App
    _fk.jsonify = lambda *a, **k: None
    _fk.request = None
    sys.modules["flask"] = _fk

import gam_monitor as gm
import exportar_artifact as ex

# o ZepOne tem a própria tela: não precisa gerar o dashboard.html de 4 MB
gm.render_dashboard = lambda publishers: None
gm.upload_dashboard_to_gcs = lambda: None


COLUNAS_SEG = ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS", "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS"]


def relatorio(client, dimensoes, ids, inicio, visao=None, colunas=None):
    """Impressões, cliques e impressões visíveis (VA) por line item e pela dimensão pedida,
    de `inicio` até hoje. Devolve {line_item_id: {valor: [impressões, cliques, visíveis]}}."""
    rs = sys.modules.get("reports_server") or __import__("reports_server")
    servico = client.GetService("ReportService", version=gm.GAM_API_VERSION)
    hoje = datetime.now()
    job = {"reportQuery": {
        "dimensions": ["LINE_ITEM_ID"] + dimensoes,
        "columns": colunas or COLUNAS_SEG,
        "dateRangeType": "CUSTOM_DATE",
        "startDate": {"year": inicio.year, "month": inicio.month, "day": 1},
        "endDate": {"year": hoje.year, "month": hoje.month, "day": hoje.day},
        "statement": {"query": "WHERE LINE_ITEM_ID IN (" + ",".join(str(int(i)) for i in ids) + ")"},
    }}
    if visao:
        job["reportQuery"]["adUnitView"] = visao   # FLAT: o bloco de anúncio onde a peça entregou, não o site
    salvo = gm._to_dict(rs.retry_call(servico.runReportJob, job))
    status, esperou = salvo.get("reportJobStatus"), 0
    while status not in ("COMPLETED", "FAILED") and esperou < 300:
        gm.time.sleep(5)
        esperou += 5
        status = rs.retry_call(servico.getReportJobStatus, salvo["id"])
    if status != "COMPLETED":
        raise RuntimeError(f"relatório não completou (status={status})")
    arq = gm.io.BytesIO()
    rs.retry_call(client.GetDataDownloader(version=gm.GAM_API_VERSION).DownloadReportToFile, salvo["id"], "CSV_DUMP", arq, use_gzip_compression=True)
    arq.seek(0)
    with gm.gzip.GzipFile(fileobj=arq) as gz:
        leitor = gm.csv.DictReader(gm.io.StringIO(gz.read().decode("utf-8")))
    cab = leitor.fieldnames or []
    col = lambda nome: next((h for h in cab if nome in h.upper()), None)
    c_li, c_val, c_imp = col("LINE_ITEM_ID"), col(dimensoes[0]), col("AD_SERVER_IMPRESSIONS")
    c_clk, c_vis = col("AD_SERVER_CLICKS"), col("ACTIVE_VIEW_VIEWABLE_IMPRESSIONS")
    out = {}
    for linha in leitor:
        try:
            imp = int(linha.get(c_imp) or 0)
            clk = int(linha.get(c_clk) or 0) if c_clk else 0
            vis = int(linha.get(c_vis) or 0) if c_vis else 0
        except ValueError:
            continue
        if imp <= 0:
            continue
        val = (linha.get(c_val) or "").strip()
        if not val or val in ("-", "N/A", "(not set)"):
            val = "Não identificado"
        d = out.setdefault(str(linha.get(c_li)), {})
        t = d.setdefault(val, [0, 0, 0])
        t[0] += imp
        t[1] += clk
        t[2] += vis
    return out


def exportar_segmentos(cache, hoje, avisos):
    """Regiões, dispositivos, tamanhos de criativo e blocos de anúncio de cada campanha com entrega nos últimos 3 meses (um relatório por rede e dimensão)."""
    rs = sys.modules.get("reports_server") or __import__("reports_server")
    recentes = set(ex.meses_recentes(3, hoje))
    por_rede = {}
    for cs in cache.values():
        for c in cs:
            if c.get("network_code") and recentes.intersection(c.get("month_keys") or []):
                por_rede.setdefault(str(c["network_code"]), []).append(c["id"])
    inicio = datetime(hoje.year, 1, 1)
    saida = {}
    for rede, ids in por_rede.items():
        try:
            client = rs.get_client_for_network(rede)
        except Exception as e:
            avisos.append(f"Regiões/dispositivos da rede {rede}: {e}")
            continue
        for chave, dim, visao in (("regioes", "REGION_NAME", None), ("devices", "DEVICE_CATEGORY_NAME", None),
                                 ("tamanhos", "CREATIVE_SIZE", None), ("blocos", "AD_UNIT_NAME", "FLAT")):
            print(f"  {chave}: rede {rede} · {len(ids)} campanhas ...", flush=True)
            try:
                res = relatorio(client, [dim], ids, inicio, visao)
            except Exception as e:
                # o GAM não aceita impressões visíveis (VA) com algumas dimensões em algumas redes: tenta sem o VA
                try:
                    res = relatorio(client, [dim], ids, inicio, visao, COLUNAS_SEG[:2])
                    avisos.append(f"{chave} da rede {rede}: sem VA (o GAM recusou a coluna de visibilidade: {e})")
                except Exception as e2:
                    avisos.append(f"{chave} da rede {rede} falhou: {e2}")
                    continue
            for li, valores in res.items():
                saida.setdefault(li, {})[chave] = valores
    return saida


def main():
    completo = "--completo" in sys.argv
    print(f"Monitor de campanhas ({'completo' if completo else 'rápido'})", flush=True)
    sys.argv = [sys.argv[0]]
    gm.main()   # busca no GAM, grava output/data_cache.json e manda o e-mail do dia

    hoje = datetime.now()
    cache = ex.completar_campanhas(json.loads(gm.DATA_CACHE_PATH.read_text(encoding="utf-8")))
    avisos = []
    ex.SAIDA.mkdir(parents=True, exist_ok=True)
    arq_inv, arq_det = ex.SAIDA / "gamone_inventario.json", ex.SAIDA / "gamone_detalhe.json"
    arq_seg = ex.SAIDA / "gamone_segmentos.json"

    geral = {
        "versao": 1,
        "gerado_em": hoje.strftime("%d/%m/%Y %H:%M"),
        "hoje": hoje.strftime("%Y-%m-%d"),
        "tz": "America/Sao_Paulo",
        "publishers": sorted(cache.keys()),
        "detalhe_publishers": ex.DETALHE_PUBLISHERS,
        "n_campanhas": sum(len(v) for v in cache.values()),
        "cache_gerado_em": hoje.strftime("%d/%m/%Y %H:%M"),
        "config": {"underdelivery_threshold_pct": gm.UNDERDELIVERY_THRESHOLD_PCT, "days_before_end": gm.DAYS_BEFORE_END_TO_ALERT},
        "avisos": avisos,
    }
    novos = ["_geral", "campanhas"]
    if completo or not arq_inv.exists() or not arq_det.exists():
        inventario = ex.exportar_inventario(cache, ex.meses_recentes(4, hoje), avisos)
        detalhe = ex.exportar_detalhe(cache, ex.meses_recentes(3, hoje)[0], avisos)
        ex.dump({"redes": inventario}, arq_inv)
        ex.dump({"campanhas": detalhe}, arq_det)
        novos += ["inventario", "detalhe"]
    if completo or not arq_seg.exists():
        ex.dump({"campanhas": exportar_segmentos(cache, hoje, avisos)}, arq_seg)
        novos.append("segmentos")
    else:
        inventario = json.loads(arq_inv.read_text(encoding="utf-8")).get("redes") or {}
        detalhe = json.loads(arq_det.read_text(encoding="utf-8")).get("campanhas") or {}

    geral["tem_inventario"] = bool(inventario)
    geral["tem_detalhe"] = bool(detalhe)
    geral["meses_inventario"] = sorted({m for r in inventario.values() for m in r})
    ex.dump(geral, ex.SAIDA / "gamone__geral.json")
    ex.dump({"publishers": cache}, ex.SAIDA / "gamone_campanhas.json")
    for a in avisos:
        print("  [aviso]", a)
    (ex.SAIDA / "_pronto.json").write_text(json.dumps({"gerado_em": geral["gerado_em"], "novos": novos, "n": geral["n_campanhas"]}), encoding="utf-8")
    print("Pronto.", flush=True)


if __name__ == "__main__":
    main()
