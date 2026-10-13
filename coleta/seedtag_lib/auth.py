#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Autenticacao com a API de parceiros da Seedtag (Publisher Panel API,
https://publisher-panel.api.seedtag.com/v1 -- doc: "Integration guide",
OAS 3.0 v1.0.0).

Fluxo confirmado na doc oficial (nao chutado):
1) A Seedtag emite um 'api_key' pessoal, uma vez por usuario (voce pede pro
   time de contato da Seedtag).
2) Troca-se esse api_key por um accessToken (JWT), via:
       GET https://login.seedtag.com/api/token?api_key=<API_KEY>
       -> {"accessToken": "eyJhbGci..."}
   Esse accessToken expira em 30 DIAS.
3) Toda chamada na API usa esse token como Bearer:
       Authorization: Bearer <accessToken>

Decisao de design: em vez de guardar o accessToken (que dura 30 dias) e ter
que controlar validade entre execucoes do painel, este modulo simplesmente
pede um accessToken NOVO a cada execucao do gerar_dash_v5.py (o painel roda
uma vez por dia/sob demanda, nao a cada minuto -- pedir 1 token por execucao
nao deve ter problema de limite de uso; se um dia a Seedtag informar um
rate-limit especifico pra esse endpoint, revisitar isso e cachear em disco).

Igual ao padrao ja usado pra Ad Manager (service_account.json) e GA4
(service_account_ga.json): o 'api_key' da Seedtag e uma credencial e NUNCA
deve ir dentro de um zip entregue/repassado. Aqui ele fica num arquivo de
texto simples separado (config.json -> 'seedtag_api_key_file', padrao
'seedtag_api_key.txt'), nunca direto dentro do config.json (que normalmente
E' incluido nas entregas) -- assim o arquivo de credencial fica isolado e
facil de excluir de qualquer zip, do mesmo jeito que os .json de service
account.

Se a chave nao estiver configurada, o arquivo nao existir, ou a troca por
token falhar (rede, api_key invalida, etc.), 'obter_token()' devolve None
-- NUNCA lanca excecao. Quem chamar deve tratar isso como "Seedtag nao
disponivel agora" e simplesmente pular esse parceiro pra aquele site, sem
quebrar o resto do painel (mesmo padrao ja usado com ga_lib.auth.obter_client
e gam_lib.auth.obter_client).
"""
import os

try:
    import requests
    _LIB_OK = True
except ImportError:
    _LIB_OK = False

TOKEN_URL = "https://login.seedtag.com/api/token"
TIMEOUT_SEGUNDOS = 20

_token_cache = {}  # api_key -> accessToken (cache só durante esta execução)


def _resolver_caminho_key_file(config, base_dir):
    key_file = str(config.get("seedtag_api_key_file") or "").strip()
    if not key_file:
        return None
    if not os.path.isabs(key_file):
        key_file = os.path.join(base_dir, key_file)
    return key_file


def _ler_api_key(config, base_dir):
    caminho = _resolver_caminho_key_file(config, base_dir)
    if not caminho or not os.path.exists(caminho):
        return None
    try:
        with open(caminho, encoding="utf-8") as f:
            chave = f.read().strip()
        return chave or None
    except OSError:
        return None


def obter_token(config, base_dir):
    """Devolve (e cacheia, so durante esta execucao) o accessToken (Bearer)
    da Seedtag. Devolve None -- sem levantar excecao -- se:
    - a biblioteca 'requests' nao estiver instalada;
    - 'seedtag_api_key_file' nao estiver preenchido no config.json;
    - o arquivo apontado nao existir ou estiver vazio;
    - a troca do api_key por accessToken falhar (rede, api_key invalido etc.)
    """
    if not _LIB_OK:
        return None
    api_key = _ler_api_key(config, base_dir)
    if not api_key:
        return None
    if api_key in _token_cache:
        return _token_cache[api_key]
    try:
        resp = requests.get(TOKEN_URL, params={"api_key": api_key}, timeout=TIMEOUT_SEGUNDOS)
        resp.raise_for_status()
        token = resp.json().get("accessToken")
    except Exception:  # noqa: BLE001 -- qualquer falha aqui = Seedtag indisponivel, nao pode derrubar o painel
        return None
    if not token:
        return None
    _token_cache[api_key] = token
    return token
