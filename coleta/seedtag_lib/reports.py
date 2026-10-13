#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chamadas na API de parceiros da Seedtag (Publisher Panel API). Base path
confirmado na doc oficial: https://publisher-panel.api.seedtag.com/v1
(protocolo HTTPS/REST/JSON, auth Bearer JWT -- ver seedtag_lib/auth.py).

IMPORTANTE -- nivel de confianca dos campos usados aqui:
- Endpoints, metodo HTTP e parametros OBRIGATORIOS (startDate/endDate em
  'AAAA-MM-DD', page/pageSize) foram confirmados direto na doc oficial
  (paginas "Endpoint map", "Summary & paginated data", "Export & period
  comparison", "Shared patterns"), nao chutados.
- A doc mostra o FORMATO da resposta de /analytics/data só parcialmente --
  o exemplo vem com "..." depois de alguns campos (date, revenue,
  impressions, clicks, cpm, rpm, ...). Ou seja, PODE haver mais campos na
  resposta real que a doc não detalhou. Por isso o parsing em
  gerar_dash_v5.py (parse_seedtag_api) e' DEFENSIVO: guarda tudo que vier,
  em vez de assumir uma lista fixa de campos -- ver ferramentas/
  testar_seedtag_api.py, que imprime o JSON CRU de uma chamada real ANTES
  de confiarmos no parsing final (mesma lição do incidente
  'adxReportCurrency' de 11/08: nunca finalizar parsing sem ver o dado
  real).
- A forma de mandar uma LISTA num filtro (ex.: 'publishers') na query
  string NAO veio especificada na doc (só o nome do parametro). Aqui
  mandamos como parametro repetido (ex.: ?publishers=ID1&publishers=ID2),
  que e' a convencao REST mais comum -- se a Seedtag esperar formato
  diferente (CSV, JSON), o testar_seedtag_api.py vai revelar isso no teste
  real (erro 400 ou filtro sem efeito) e ajustamos aqui.
"""
import requests

BASE_URL = "https://publisher-panel.api.seedtag.com/v1"
TIMEOUT_SEGUNDOS = 30
PAGE_SIZE_PADRAO = 100  # maximo aceito pela API, conforme a doc
MAX_PAGINAS = 200  # trava de seguranca (nunca deveria chegar nem perto disso)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _get(token, caminho, params=None):
    """GET generico -- deixa qualquer erro (rede, 4xx/5xx) subir como
    excecao. Quem chama (gerar_dash_v5.py) decide o que fazer (cair pro
    manual, avisar e seguir), igual ao padrao ja usado com
    gam_lib.reports.rodar_report() e ga_lib.reports.rodar_report()."""
    resp = requests.get(f"{BASE_URL}{caminho}", headers=_headers(token),
                         params=params or {}, timeout=TIMEOUT_SEGUNDOS)
    resp.raise_for_status()
    return resp.json()


def users_me(token):
    """GET /users/me -- {id, name, isActive, editorialGroups}. Bom pra
    validar que o token funciona antes de mais nada."""
    return _get(token, "/users/me")


def catalogs_all(token):
    """GET /catalogs/all -- todas as listas de referencia num call so
    (sourceTypes, adUnitTypes, products, groupByTypes, temporalAggregations,
    downloadFormats, countries), cada uma como [{'id','name'}]."""
    return _get(token, "/catalogs/all")


def listar_publishers(token, q=None, page_size=PAGE_SIZE_PADRAO):
    """GET /publishers, paginado -- devolve a lista COMPLETA (todas as
    paginas juntas) de publishers que essa conta/api_key tem acesso.
    Cada item deve ter pelo menos 'id'/'name' (a doc nao detalhou o shape
    exato do publisher -- ver testar_seedtag_api.py pra confirmar)."""
    todos = []
    page = 1
    while page <= MAX_PAGINAS:
        params = {"page": page, "pageSize": page_size}
        if q:
            params["q"] = q
        payload = _get(token, "/publishers", params=params)
        dados = payload.get("data", payload if isinstance(payload, list) else [])
        todos.extend(dados)
        pag = payload.get("pagination") if isinstance(payload, dict) else None
        if not pag or page >= int(pag.get("totalPages", page)):
            break
        page += 1
    return todos


def analytics_summary(token, data_ini, data_fim, publisher_ids=None, **filtros):
    """GET /analytics/summary -- receita (por moeda/payer), impressoes e
    cliques AGREGADOS pro periodo (sem quebra por dia). data_ini/data_fim:
    'AAAA-MM-DD'. publisher_ids: lista opcional (filtra pra so esse(s)
    publisher(s)). filtros extras aceitos pela doc: adunitTypes,
    productNames, sourceTypes, mediaTypes, userCountries (listas)."""
    params = {"startDate": str(data_ini), "endDate": str(data_fim)}
    if publisher_ids:
        params["publishers"] = list(publisher_ids)
    params.update({k: v for k, v in filtros.items() if v})
    return _get(token, "/analytics/summary", params=params)


def analytics_data(token, data_ini, data_fim, publisher_ids=None,
                    temporal_aggregation="day", group_by=None,
                    page_size=PAGE_SIZE_PADRAO, **filtros):
    """GET /analytics/data, paginado -- devolve a lista COMPLETA (todas as
    paginas) de linhas, cada uma com metricas por dia/mes (e por publisher
    se group_by incluir 'publisher'). temporal_aggregation: 'day' (padrao)
    ou 'month'. group_by: string ou lista dentre publisher, adunitType,
    productName, sourceType, userCountry, mediaType."""
    params = {"startDate": str(data_ini), "endDate": str(data_fim),
              "temporalAggregation": temporal_aggregation, "pageSize": page_size}
    if publisher_ids:
        params["publishers"] = list(publisher_ids)
    if group_by:
        params["groupBy"] = group_by if isinstance(group_by, list) else [group_by]
    params.update({k: v for k, v in filtros.items() if v})

    todas_linhas = []
    page = 1
    while page <= MAX_PAGINAS:
        params["page"] = page
        payload = _get(token, "/analytics/data", params=params)
        linhas = payload.get("data", [])
        todas_linhas.extend(linhas)
        pag = payload.get("pagination") or {}
        total_paginas = int(pag.get("totalPages", page))
        if page >= total_paginas or not linhas:
            break
        page += 1
    return todas_linhas


def analytics_overview_metrics(token, periodo_a_ini, periodo_a_fim, periodo_b_ini, periodo_b_fim,
                                publisher_ids=None):
    """GET /analytics/overview-metrics -- receita/RPM/CPM/impressoes/
    adRequests de DOIS periodos lado a lado, com % de variacao ja calculada
    pela propria Seedtag. Nomes de parametro conforme a doc:
    startPeriodStart/startPeriodEnd (periodo A) e endPeriodStart/
    endPeriodEnd (periodo B)."""
    params = {
        "startPeriodStart": str(periodo_a_ini), "startPeriodEnd": str(periodo_a_fim),
        "endPeriodStart": str(periodo_b_ini), "endPeriodEnd": str(periodo_b_fim),
    }
    if publisher_ids:
        params["publishers"] = list(publisher_ids)
    return _get(token, "/analytics/overview-metrics", params=params)
