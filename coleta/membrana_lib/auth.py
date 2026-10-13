#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Autenticacao com a Membrana Publisher API (doc oficial, versao 1.0,
screenshots mandados pelo Joao em 21/08/2026 apos login no portal
https://pilot.membrana.media -- NAO acessamos esse portal, so lemos o
conteudo que ele mandou).

Fluxo confirmado na doc oficial (nao chutado): diferente da Seedtag (que
troca um api_key por um accessToken JWT com validade de 30 dias), a
Membrana usa um token FIXO, direto: "To authorize the API request, add
the AccessKey header. The header's value is the API access token the
Customer Success team provides." Ou seja, nao ha troca nenhuma -- o valor
configurado JA E' o que vai no header 'AccessKey' de toda chamada.

Por isso este modulo e' bem mais simples que seedtag_lib/auth.py: so le o
arquivo de credencial e devolve o valor, sem nenhuma chamada de rede.

Mesmo padrao de credencial isolada ja usado pra Ad Manager
(service_account.json), GA4 (service_account_ga.json) e Seedtag
(seedtag_api_key.txt): o token fica num arquivo de texto simples separado
(config.json -> 'membrana_access_key_file', padrao 'membrana_access_key.txt'),
nunca direto dentro do config.json -- assim fica facil excluir esse arquivo
de qualquer zip entregue/repassado.

Se a chave nao estiver configurada ou o arquivo nao existir/estiver vazio,
'obter_access_key()' devolve None -- NUNCA lanca excecao. Quem chamar deve
tratar isso como "Membrana nao disponivel agora" e pular esse parceiro pra
aquele site, sem quebrar o resto do painel.
"""
import os


def _resolver_caminho_key_file(config, base_dir):
    key_file = str(config.get("membrana_access_key_file") or "").strip()
    if not key_file:
        return None
    if not os.path.isabs(key_file):
        key_file = os.path.join(base_dir, key_file)
    return key_file


def obter_access_key(config, base_dir):
    """Devolve o valor do header AccessKey configurado, ou None se a chave
    nao estiver configurada / o arquivo nao existir ou estiver vazio."""
    caminho = _resolver_caminho_key_file(config, base_dir)
    if not caminho or not os.path.exists(caminho):
        return None
    try:
        with open(caminho, encoding="utf-8") as f:
            chave = f.read().strip()
        return chave or None
    except OSError:
        return None
