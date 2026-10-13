#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cotacoes de cambio pra converter a receita de um parceiro (ex.: Seedtag)
pra outra moeda de exibicao no painel -- usado quando o parceiro nao tem
(ou nao documenta) uma opcao de pedir o valor ja em outra moeda na propria
API dele (confirmado com testar_seedtag_api.py em 19/08/2026: a Seedtag NAO
aceita nenhum parametro de moeda -- 'currency'/'reportCurrency'/
'targetCurrency' foram todos ignorados contra a API real).

NUNCA lanca excecao pra quem chama -- se as duas fontes falharem (rede fora,
API mudou, rate limit), obter_taxa() devolve None e quem chama decide
mostrar na moeda original em vez de travar o painel (mesma filosofia de
fallback seguro do resto do projeto: GA4/Seedtag falhar num site nunca
derruba o resto).

Duas fontes, nessa ordem (a 2a so e tentada se a 1a falhar) -- as duas sao
gratuitas e nao pedem api_key:
1. https://api.frankfurter.app -- cotacoes baseadas no BCE (European
   Central Bank), atualizadas em dias uteis.
2. https://open.er-api.com -- fallback independente, atualizado diario.
"""
import requests

TIMEOUT_SEGUNDOS = 10
_cache = {}  # (de, para) -> taxa ou None, cacheada por execucao (o painel roda 1x e termina)


def obter_taxa(moeda_de, moeda_para):
    """Devolve quantas unidades de 'moeda_para' equivalem a 1 unidade de
    'moeda_de' (ex.: obter_taxa('BRL','USD') ~ 0.19). Devolve 1.0 direto
    (sem chamar rede) se as moedas forem iguais; None se as duas fontes
    falharem."""
    moeda_de = (moeda_de or "").strip().upper()
    moeda_para = (moeda_para or "").strip().upper()
    if not moeda_de or not moeda_para:
        return None
    if moeda_de == moeda_para:
        return 1.0
    chave = (moeda_de, moeda_para)
    if chave in _cache:
        return _cache[chave]
    taxa = _tentar_frankfurter(moeda_de, moeda_para)
    if taxa is None:
        taxa = _tentar_erapi(moeda_de, moeda_para)
    _cache[chave] = taxa
    return taxa


def _tentar_frankfurter(moeda_de, moeda_para):
    try:
        resp = requests.get(
            "https://api.frankfurter.app/latest",
            params={"from": moeda_de, "to": moeda_para},
            timeout=TIMEOUT_SEGUNDOS,
        )
        resp.raise_for_status()
        dados = resp.json()
        taxa = (dados.get("rates") or {}).get(moeda_para)
        return float(taxa) if taxa is not None else None
    except Exception:  # noqa: BLE001 -- rede/API de cambio falhar aqui nao pode travar o painel
        return None


def _tentar_erapi(moeda_de, moeda_para):
    try:
        resp = requests.get(
            f"https://open.er-api.com/v6/latest/{moeda_de}",
            timeout=TIMEOUT_SEGUNDOS,
        )
        resp.raise_for_status()
        dados = resp.json()
        if dados.get("result") != "success":
            return None
        taxa = (dados.get("rates") or {}).get(moeda_para)
        return float(taxa) if taxa is not None else None
    except Exception:  # noqa: BLE001 -- mesma razao do try acima
        return None
