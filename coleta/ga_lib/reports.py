#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pede o relatorio de pageviews por dia/dispositivo pra API do Google Analytics
(GA4 Data API -- BetaAnalyticsDataClient.run_report). Devolve a resposta crua
(objeto RunReportResponse); quem le e monta no formato que o painel usa e o
parse_ga_api() em gerar_dash_v5.py (mesmo padrao do gam_lib/reports.py, que
so busca/devolve o CSV cru e deixa o parsing pro gerar_dash_v5.py).

IMPORTANTE -- nomes de campo confirmados de verdade contra a biblioteca
'google-analytics-data' instalada (nao chutados): RunReportRequest tem
'property' (formato 'properties/<ID>'), 'dimensions' (lista de Dimension),
'metrics' (lista de Metric) e 'date_ranges' (lista de DateRange, aceitando
strings 'AAAA-MM-DD' direto em start_date/end_date). A resposta
(RunReportResponse) tem 'rows', cada Row com 'dimension_values' e
'metric_values' (na MESMA ordem em que 'dimensions'/'metrics' foram pedidos
no request -- ao contrario do SOAP do Ad Manager, aqui a API GARANTE a ordem).
"""
from google.analytics.data_v1beta.types import RunReportRequest, Dimension, Metric, DateRange

LIMITE_LINHAS = 100000  # bem acima do que qualquer site real deve gerar (dias x poucos dispositivos)


def montar_request(property_id, data_ini, data_fim):
    """data_ini/data_fim: string 'AAAA-MM-DD' (aceito direto pela API).
    Dimensoes fixas: date + deviceCategory. Metrica fixa: screenPageViews
    (nome atual, na API, do que o GA4 chama de "Visualizacoes"/pageviews)."""
    prop = str(property_id).strip()
    if not prop.startswith("properties/"):
        prop = "properties/" + prop
    return RunReportRequest(
        property=prop,
        dimensions=[Dimension(name="date"), Dimension(name="deviceCategory")],
        metrics=[Metric(name="screenPageViews")],
        date_ranges=[DateRange(start_date=str(data_ini), end_date=str(data_fim))],
        limit=LIMITE_LINHAS,
    )


def rodar_report(client, request):
    """Roda o request e devolve a resposta crua (RunReportResponse). Deixa
    qualquer excecao da API subir -- quem chamar (gerar_dash_v5.py) decide o
    que fazer (cair pro .xlsx manual, avisar e seguir, etc.), igual ao padrao
    ja usado com gam_lib.reports.rodar_report()."""
    return client.run_report(request)


def montar_request_pages(property_id, data_ini, data_fim, limite=250000):
    """Segundo relatorio: pageviews por dia + CAMINHO da URL (pagePath), pra
    classificar Home/Editoria/Materia. Mesma metrica screenPageViews. O limite
    e' maior porque ha muitas URLs distintas; se um site tiver mais que isso, a
    API pagina -- aqui pegamos o topo por views, que ja cobre a esmagadora
    maioria do trafego."""
    prop = str(property_id).strip()
    if not prop.startswith("properties/"):
        prop = "properties/" + prop
    return RunReportRequest(
        property=prop,
        dimensions=[Dimension(name="date"), Dimension(name="pagePath")],
        metrics=[Metric(name="screenPageViews"), Metric(name="userEngagementDuration"), Metric(name="sessions")],
        date_ranges=[DateRange(start_date=str(data_ini), end_date=str(data_fim))],
        limit=int(limite),
    )


def montar_request_sessao(property_id, data_ini, data_fim):
    """Terceiro relatorio: duracao media de sessao do SITE INTEIRO, por dia.
    Metricas: averageSessionDuration (segundos) + sessions (pra ponderar a
    media do periodo corretamente). Dimensao: date."""
    prop = str(property_id).strip()
    if not prop.startswith("properties/"):
        prop = "properties/" + prop
    return RunReportRequest(
        property=prop,
        dimensions=[Dimension(name="date")],
        metrics=[Metric(name="averageSessionDuration"), Metric(name="sessions")],
        date_ranges=[DateRange(start_date=str(data_ini), end_date=str(data_fim))],
        limit=100000,
    )
