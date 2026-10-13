#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ajuda a resolver, pra cada site, QUAIS ad units (blocos) da rede pertencem a
ele -- usando o "Ad Unit raiz" configurado no sites.xlsx (por nome ou por id).

Isso e necessario porque, no caso de "uma rede com varios sites" (cada site
e uma pasta/ad-unit-raiz dentro da MESMA rede GAM), o relatorio da API traz
todos os ad units da rede junto -- entao filtramos pelo id do ad unit raiz
do site + todos os descendentes dele.

Usamos InventoryService.getAdUnitsByStatement, com paginacao, e sempre
convertendo a resposta do zeep com zeep.helpers.serialize_object antes de
tratar como dict (dica que o Joao ja tinha anotado: a resposta do SOAP nao
e um dict Python "de fato").
"""
from googleads import ad_manager
from zeep.helpers import serialize_object

_cache_ad_units_por_rede = {}


def listar_ad_units(client, api_version, network_code):
    """Retorna a lista completa (list[dict]) de ad units da rede, com paginacao.
    Cada item tem pelo menos: id, name, parentId (pode faltar em ad units raiz)."""
    network_code = str(network_code)
    if network_code in _cache_ad_units_por_rede:
        return _cache_ad_units_por_rede[network_code]

    service = client.GetService("InventoryService", version=api_version)
    statement_builder = ad_manager.StatementBuilder(version=api_version)
    ad_units = []
    while True:
        response = service.getAdUnitsByStatement(statement_builder.ToStatement())
        response = serialize_object(response, target_cls=dict) if response is not None else {}
        results = response.get("results") or []
        if not results:
            break
        for item in results:
            ad_units.append(
                {
                    "id": str(item.get("id")),
                    "name": item.get("name") or "",
                    "parentId": str(item.get("parentId")) if item.get("parentId") is not None else None,
                }
            )
        statement_builder.offset += statement_builder.limit
        if statement_builder.offset >= (response.get("totalResultSetSize") or 0):
            break

    _cache_ad_units_por_rede[network_code] = ad_units
    return ad_units


def resolver_ad_unit_raiz(ad_units, root_ad_unit_id=None, root_ad_unit_nome=None):
    """Acha o id do ad unit raiz de um site, por id (preferencial) ou por nome
    (case-insensitive, precisa casar EXATAMENTE um ad unit)."""
    if root_ad_unit_id:
        rid = str(root_ad_unit_id).strip()
        if any(au["id"] == rid for au in ad_units):
            return rid
        raise ValueError(f"Nenhum ad unit com id={rid!r} encontrado nessa rede.")

    if root_ad_unit_nome:
        nome = str(root_ad_unit_nome).strip().lower()
        candidatos = [au for au in ad_units if au["name"].strip().lower() == nome]
        if len(candidatos) == 1:
            return candidatos[0]["id"]
        if len(candidatos) > 1:
            ids = ", ".join(c["id"] for c in candidatos)
            raise ValueError(
                f"Mais de um ad unit chamado {root_ad_unit_nome!r} nessa rede (ids: {ids}). "
                "Preencha 'root_ad_unit_id' no sites.xlsx pra desambiguar."
            )
        raise ValueError(
            f"Nenhum ad unit chamado {root_ad_unit_nome!r} encontrado nessa rede. "
            "Rode ferramentas/listar_ad_units.py pra ver os nomes exatos."
        )

    return None  # sem raiz configurada = usa a rede inteira, sem filtro


def descendentes_de(ad_units, root_id):
    """Dado o id de um ad unit raiz, devolve o set de ids dele + todos os
    descendentes (subarvore completa), andando pelo parentId de cada ad unit."""
    por_id = {au["id"]: au for au in ad_units}
    filhos_de = {}
    for au in ad_units:
        if au["parentId"]:
            filhos_de.setdefault(au["parentId"], []).append(au["id"])

    vistos = set()
    pilha = [root_id]
    while pilha:
        atual = pilha.pop()
        if atual in vistos:
            continue
        vistos.add(atual)
        pilha.extend(filhos_de.get(atual, []))
    return vistos
