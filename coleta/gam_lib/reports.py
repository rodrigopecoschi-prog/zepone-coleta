#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pede o relatorio de Ad Exchange (ReportService) pra API do Ad Manager e devolve
o CSV baixado.

IMPORTANTE -- descoberto testando com dados reais (rede 22877339784, 07/08/2026):
a API DEVOLVE colunas extras que a gente nao pediu (ex.: 'Dimension.AD_UNIT_ID' e
'Dimension.DEVICE_CATEGORY_ID' aparecem sozinhas, mesmo pedindo so AD_UNIT_NAME e
DEVICE_CATEGORY_NAME) -- entao NAO dá pra confiar na posicao das colunas (dimensoes
pedidas + metricas pedidas, na ordem). Por isso ler_csv() devolve o cabecalho junto,
e quem le o CSV (gerar_dash_v5.py) acha cada coluna pelo NOME dela no cabecalho
('Dimension.DATE', 'Column.AD_EXCHANGE_...' etc.), nao pela posicao.
"""
import csv
import io

from googleads import errors


def _data_gam(d):
    return {"year": d.year, "month": d.month, "day": d.day}


def montar_report_job(config, data_ini, data_fim, ad_unit_ids=None, dimensions=None, time_zone_type=None, columns=None, ad_unit_view=None):
    """Monta o report job. Por padrao usa as dimensoes diarias
    (config['report_dimensions']); passe 'dimensions' pra sobrescrever -- e o
    que fetch_admanager() faz pra pedir o relatorio HORARIO (com a dimensao
    HOUR, ver config['report_dimensions_hora']).

    time_zone_type (opcional): fuso em que a API devolve o relatorio (ex.:
    'PUBLISHER' = fuso da rede). Usado no relatorio HORARIO pra as horas virem
    no fuso certo (America/Sao_Paulo). Se None, nao envia (comportamento padrao
    da API) -- e o que o relatorio DIARIO usa, ja validado."""
    # 'columns' permite pedir metricas diferentes das de Ad Exchange (ex.: o
    # relatorio de DEALS usa metricas de item de linha). Por padrao usa as de
    # Ad Exchange do config (comportamento de sempre).
    colunas = list(columns) if columns is not None else list(config["report_columns"].values())
    dims = dimensions if dimensions is not None else config.get(
        "report_dimensions", ["DATE", "AD_UNIT_NAME", "DEVICE_CATEGORY_NAME"])
    query = {
        "dimensions": dims,
        "adUnitView": ad_unit_view or config.get("ad_unit_view", "FLAT"),
        "columns": colunas,
        "dateRangeType": "CUSTOM_DATE",
        "startDate": _data_gam(data_ini),
        "endDate": _data_gam(data_fim),
    }
    if ad_unit_ids:
        ids_str = ",".join(str(i) for i in sorted(ad_unit_ids, key=int))
        query["statement"] = {"query": f"WHERE AD_UNIT_ID IN ({ids_str})"}
    if time_zone_type:
        query["timeZoneType"] = time_zone_type
    moeda_forcada = str(config.get("report_currency") or "").strip().upper()
    if moeda_forcada:
        # 'reportCurrency' -- CONFIRMADO em 11/08/2026 direto no WSDL real da
        # API (ferramentas/testar_moeda_api.py, rodado pelo Joao contra a
        # rede 1007775/tribunadonorte, versao v202605): e um campo string,
        # existe de fato em ReportQuery nessa versao. Isso substitui a
        # tentativa anterior ('adxReportCurrency', campo de uma versao bem
        # mais antiga da API que ja nao existe mais e quebrou a geracao de
        # todos os sites -- ver conversa/decisoes de 11/08). Quando setado,
        # a API converte os valores monetarios do relatorio pra essa moeda
        # (aplicando a cotacao dela mesma) antes de devolver -- os campos
        # ainda vem em micros (ver report_columns_micros), so que agora na
        # moeda pedida em vez da moeda nativa da rede.
        query["reportCurrency"] = moeda_forcada
    return {"reportQuery": query}


def rodar_report(client, api_version, report_job, poll_time_seconds=3):
    """Roda o report job, espera terminar e devolve o CSV (texto) baixado.
    Levanta googleads.errors.AdManagerReportError se o job falhar na API."""
    downloader = client.GetDataDownloader(version=api_version)
    report_job_id = downloader.WaitForReport(report_job, poll_time_seconds=poll_time_seconds)

    buf = io.BytesIO()
    downloader.DownloadReportToFile(
        report_job_id, "CSV_DUMP", buf, use_gzip_compression=False
    )
    return buf.getvalue().decode("utf-8-sig")


def ler_csv(texto_csv):
    """Devolve (cabecalho, linhas) -- cabecalho e a lista de rotulos tipo
    'Dimension.DATE' / 'Column.AD_EXCHANGE_TOTAL_REQUESTS', na ordem em que a
    API realmente devolveu (que pode nao ser a ordem que pedimos)."""
    leitor = list(csv.reader(io.StringIO(texto_csv)))
    if not leitor:
        return [], []
    return leitor[0], leitor[1:]


def linhas_do_csv(texto_csv):
    """Mantido por compatibilidade (ex.: ferramentas/testar_colunas.py) --
    devolve so as linhas de dado, sem cabecalho."""
    _, linhas = ler_csv(texto_csv)
    return linhas
