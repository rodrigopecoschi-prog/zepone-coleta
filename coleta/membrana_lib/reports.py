#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chamadas na Membrana Publisher API (v1.0). Doc oficial fornecida pelo Joao
em 21/08/2026 como screenshots da pagina de referencia da API (nao um link
publico) -- fonte confiavel, nenhum campo abaixo foi chutado.

IMPORTANTE -- pontos confirmados direto na doc:
- Base path: https://api.membrana.media/publisher/v1 (visto no exemplo de
  curl da doc: 'https://api.membrana.media/publisher/v1/monetization/daily
  ?date_from=2022-12-01').
- Auth: header 'AccessKey' (nao 'Authorization: Bearer'), valor fixo
  fornecido pelo time de Customer Success -- ver membrana_lib/auth.py.
- A API separa TRES produtos de monetizacao, cada um com endpoint proprio:
  'monetization' (video player -- campos player_loads/visible_player_loads),
  'interstitial' e 'adwall' (formato com camada sobre a pagina, campos
  adwall_shows/pageviews). Sao produtos DIFERENTES, nao um groupBy de um
  endpoint so como na Seedtag -- por isso pedimos os 3 separadamente e
  tratamos cada um como um "formato" na aba Parceiros (mesma estrutura
  generica de porDiaFormato/formatosVistos ja usada pra Seedtag).
- NENHUM desses 3 endpoints aceita filtro por site na query string (a doc
  só documenta 'date_from'/'date_to') -- a resposta traz TODOS os sites da
  conta numa lista so, diferenciados pelo campo 'site_id'. Por isso este
  modulo busca uma vez só (sem filtro de site) e quem chama
  (gerar_dash_v5.py) filtra pelo site_id certo depois -- evita pedir a
  MESMA lista completa de novo pra cada site do Joao que tiver Membrana
  configurada.
- 'revenue' em monetization/interstitial/adwall e' EXPLICITAMENTE descrito
  na doc como "before Google deductions... not the final number and
  should not be used for invoicing" -- ou seja, e' um numero PROVISORIO de
  performance, nao o valor final a receber. O numero final e' o de
  /billing/report ("however the numbers in the billing report are final
  and not subject to change"). O parsing (gerar_dash_v5.py) trata os dois
  como metricas DIFERENTES e rotula cada um claramente -- nunca soma um
  no lugar do outro.
- A doc NAO diz em que moeda vem o campo 'revenue' de monetization/
  interstitial/adwall (only /billing/report tem um campo 'local_currency'
  explicito). Isso e' tratado como incerteza assumida -- ver
  config.json:'_membrana_moeda_nota' e a conversa com o Joao.
- /billing/report NAO tem nenhum parametro de data documentado na doc (a
  tela so mostra "Responses", sem "QUERY PARAMETERS") -- tratamos como
  "devolve o historico completo" e filtramos por site_id/data no cliente,
  igual aos outros 3 endpoints.
- Todos os 4 endpoints devolvem "{'data': [...]}" (array de objetos), sem
  paginacao documentada (diferente da Seedtag, que documenta page/pageSize)
  -- nao assumimos paginacao que a doc nao mostrou.
"""
import requests

BASE_URL = "https://api.membrana.media/publisher/v1"
TIMEOUT_SEGUNDOS = 30


def _headers(access_key):
    return {"AccessKey": access_key}


def _get(access_key, caminho, params=None):
    """GET generico -- deixa qualquer erro (rede, 4xx/5xx) subir como
    excecao. Quem chama decide o que fazer (avisar e pular esse produto/
    parceiro, sem derrubar o site), igual ao padrao de seedtag_lib."""
    resp = requests.get(f"{BASE_URL}{caminho}", headers=_headers(access_key),
                         params=params or {}, timeout=TIMEOUT_SEGUNDOS)
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict):
        return payload.get("data", [])
    if isinstance(payload, list):
        return payload
    return []


def monetization_daily(access_key, date_from, date_to=None):
    """GET /monetization/daily -- video player ads, TODOS os sites da conta
    (sem filtro de site na API). Campos confirmados na doc: date, site_id,
    site_url, impressions, revenue, player_loads, visible_player_loads."""
    params = {"date_from": date_from}
    if date_to:
        params["date_to"] = date_to
    return _get(access_key, "/monetization/daily", params=params)


def interstitial_daily(access_key, date_from, date_to=None):
    """GET /interstitial/daily -- mesmo padrao do monetization/daily, campos
    confirmados: date, site_id, impressions, revenue, site_url."""
    params = {"date_from": date_from}
    if date_to:
        params["date_to"] = date_to
    return _get(access_key, "/interstitial/daily", params=params)


def adwall_daily(access_key, date_from, date_to=None):
    """GET /adwall/daily -- campos confirmados: date, site_id, impressions,
    revenue, clicks, adwall_shows, pageviews, site_url."""
    params = {"date_from": date_from}
    if date_to:
        params["date_to"] = date_to
    return _get(access_key, "/adwall/daily", params=params)


def billing_report(access_key):
    """GET /billing/report -- SEM parametros documentados (devolve o
    historico completo, ao que a doc mostra). Campos confirmados: date,
    site_id, banner_revenue, video_revenue, amp_video_revenue,
    adwall_revenue, commission, net_billing, local_currency,
    net_billing_in_local_currency, headline_revenue. Esses SAO os numeros
    finais (a doc diz explicitamente que nao mudam mais), diferente do
    'revenue' dos endpoints de monetization/interstitial/adwall."""
    return _get(access_key, "/billing/report")
