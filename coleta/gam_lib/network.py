#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Descobre a MOEDA de cada rede do Ad Manager (NetworkService.getCurrentNetwork()
-> currencyCode).

Por que isso importa: o Ad Manager guarda um relatorio de receita (inclusive a
receita vinda do Ad Exchange) na moeda PADRAO configurada daquela rede -- nao
fica fixo em dolar. Se a rede foi configurada em BRL, os valores que a API
devolve pra AD_EXCHANGE_LINE_ITEM_LEVEL_REVENUE ja vem em reais (convertidos
pelo Ad Manager na hora da entrega), nao em dolar. O painel antes assumia
"US$" pra qualquer rede -- por isso um site como o tribunadonorte (rede em
BRL) aparecia com o valor certo, mas o simbolo errado ("US$" em vez de "R$").

Igual a inventory.py, convertemos a resposta do zeep com
zeep.helpers.serialize_object antes de tratar como dict.
"""
from zeep.helpers import serialize_object

_cache_moeda_por_rede = {}


def moeda_da_rede(client, api_version, network_code):
    """Retorna (e cacheia) o codigo ISO da moeda da rede (ex.: 'BRL', 'USD').
    Se a chamada falhar por qualquer motivo, assume 'USD' (comportamento
    anterior) e avisa -- nunca derruba a geracao do site por causa disso."""
    network_code = str(network_code).strip()
    if network_code in _cache_moeda_por_rede:
        return _cache_moeda_por_rede[network_code]

    moeda = "USD"
    try:
        service = client.GetService("NetworkService", version=api_version)
        rede = service.getCurrentNetwork()
        rede = serialize_object(rede, target_cls=dict) if rede is not None else {}
        moeda = str(rede.get("currencyCode") or "USD").strip().upper()
    except Exception as e:  # noqa: BLE001 -- nao pode quebrar o site por isso
        print(f"  ! aviso: nao consegui descobrir a moeda da rede {network_code} ({e}); assumindo USD.")

    _cache_moeda_por_rede[network_code] = moeda
    return moeda
