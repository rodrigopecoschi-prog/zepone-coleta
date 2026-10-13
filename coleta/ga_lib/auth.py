#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Autenticacao com a API do Google Analytics (GA4 Data API), usando uma service
account SEPARADA da do Ad Manager (config.json -> 'ga_service_account_key_file',
por padrao 'service_account_ga.json').

Igual ao Ad Manager: essa service account precisa ser adicionada como usuario
(Leitor/Viewer) em CADA propriedade GA4 que o painel for buscar dados --
Admin > Acesso a propriedade (Property access management), dentro do GA4,
usando o e-mail que esta no campo "client_email" do .json.

Se a chave nao estiver configurada ou o arquivo nao existir, obter_client()
devolve None (nao levanta excecao) -- quem chamar deve tratar isso como "GA
via API nao disponivel pra esse site" e cair pro .xlsx manual (ga_arquivo),
exatamente como o painel ja funcionava antes desta automacao. Isso e
proposital: ninguem que ja usa o painel hoje (sem essa chave configurada)
deve ter a geracao quebrada por causa disso.
"""
import os

try:
    from google.oauth2 import service_account
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    _LIB_OK = True
except ImportError:
    _LIB_OK = False

SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]

_client_cache = {}


def _resolver_caminho_key_file(config, base_dir):
    key_file = str(config.get("ga_service_account_key_file") or "").strip()
    if not key_file:
        return None
    if not os.path.isabs(key_file):
        key_file = os.path.join(base_dir, key_file)
    return key_file


def obter_client(config, base_dir):
    """Retorna (e cacheia) um BetaAnalyticsDataClient autenticado com a service
    account do GA4. Devolve None -- sem levantar excecao -- se:
    - a biblioteca 'google-analytics-data' nao estiver instalada;
    - 'ga_service_account_key_file' nao estiver preenchido no config.json;
    - o arquivo apontado nao existir.
    """
    if not _LIB_OK:
        return None
    key_file = _resolver_caminho_key_file(config, base_dir)
    if not key_file or not os.path.exists(key_file):
        return None
    if key_file in _client_cache:
        return _client_cache[key_file]

    creds = service_account.Credentials.from_service_account_file(key_file, scopes=SCOPES)
    client = BetaAnalyticsDataClient(credentials=creds)
    _client_cache[key_file] = client
    return client
