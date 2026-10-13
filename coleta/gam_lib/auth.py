#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Autenticacao com o Google Ad Manager usando a MESMA service account (.json)
de outro projeto seu -- so precisa dela estar adicionada como "usuario" em
cada rede (network) que este painel for buscar dados.

Como cada site pode estar em uma rede (network_code) diferente, mantemos um
cache de 1 AdManagerClient por network_code (nao precisa recriar o client a
cada site/relatorio).
"""
import os
import threading
from googleads import ad_manager, oauth2

# Cache de clients POR THREAD (thread-local). O AdManagerClient (SOAP/zeep) não é
# seguro pra ser compartilhado entre threads -- por isso cada worker paralelo tem
# o SEU próprio client por rede. Assim dá pra rodar vários sites ao mesmo tempo
# sem o bug de antes (que zerava o painel). Uma thread reaproveita o mesmo client
# entre relatórios (não recria a cada chamada).
_tls = threading.local()
_cria_lock = threading.Lock()


def _resolver_caminho_key_file(config, base_dir):
    key_file = config["service_account_key_file"]
    if os.path.isabs(key_file):
        return key_file
    return os.path.join(base_dir, key_file)


def obter_client(network_code, config, base_dir):
    """Retorna (e cacheia POR THREAD) um AdManagerClient autenticado para a rede."""
    network_code = str(network_code).strip()
    cache = getattr(_tls, "clients", None)
    if cache is None:
        cache = _tls.clients = {}
    if network_code in cache:
        return cache[network_code]

    key_file = _resolver_caminho_key_file(config, base_dir)
    if not os.path.exists(key_file):
        raise FileNotFoundError(
            "Nao encontrei o arquivo de credencial da service account em:\n  "
            + key_file
            + "\nCopie o .json da sua service account (o mesmo do outro projeto) pra essa "
              "pasta com esse nome, ou ajuste 'service_account_key_file' no config.json."
        )

    # a criação do client faz I/O (lê a chave, busca token) -- serializa com um
    # lock só pra evitar corrida na 1ª criação; o uso depois é por thread.
    with _cria_lock:
        oauth2_client = oauth2.GoogleServiceAccountClient(
            key_file, oauth2.GetAPIScope("ad_manager")
        )
        client = ad_manager.AdManagerClient(
            oauth2_client,
            config.get("application_name", "Painel Auto GAM"),
            network_code=network_code,
        )
    cache[network_code] = client
    return client
