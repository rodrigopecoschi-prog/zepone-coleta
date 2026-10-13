#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dash MULTI-SITE de evolucao por formato + GA (pageviews) + filtro de periodo.
VERSAO 5: busca o relatorio de Ad Exchange direto na API do Ad Manager
(ReportService), em vez de ler um .xlsx exportado manualmente.

VERSAO 5.1 (hora/dia/mes): alem da serie DIARIA do ano inteiro, agora tambem
busca um relatorio POR HORA (dimensao HOUR) numa janela curta (config
'hora_janela_dias', padrao 90) e monta o "padrao intradia" -- por hora do dia,
por dia da semana, e um mapa dia-da-semana x hora. O front-end ganhou um toggle
de granularidade (Dia / Mes), filtros de data e mes, e a secao "Analise por
horario". A serie por hora NAO e guardada crua (com 30-150 formatos por site
isso viraria centenas de MB) -- so os agregados intradia, que sao pequenos.

Uso: python gerar_dash_v5.py sites.xlsx saida.html
'sites.xlsx' (aba 'sites'): id, nome, network_code, root_ad_unit_id,
root_ad_unit_nome, dominio, ga_arquivo, data_implantacao.

- network_code: obrigatorio. O codigo da rede do Ad Manager (Admin >
  Configuracoes globais) onde esse site vive.
- root_ad_unit_id / root_ad_unit_nome: preencha SO se essa rede tiver mais
  de um site (cada site = um Ad Unit raiz dentro da mesma rede). Se a rede
  for exclusiva desse site, pode deixar os dois vazios.
- dominio: cosmetico (ex.: portalleodias.com), so aparece no subtitulo.
- ga_arquivo / data_implantacao: igual a versao anterior (GA continua manual
  por enquanto -- fora do escopo desta automacao).

Este projeto e SEPARADO do outro que ja usa a API do Ad Manager -- so
reaproveita o MESMO arquivo de credencial da service account (copiado pra
'service_account.json' nesta pasta), configurado em config.json.
"""
import sys
import os
import json
import re
import datetime

sys.stdout.reconfigure(encoding="utf-8")  # evita erro de encoding no console do Windows

from openpyxl import load_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from gam_lib import auth, inventory, reports, network  # noqa: E402
from googleads import errors as gam_errors  # noqa: E402
from anotacoes_io import ler_anotacoes  # noqa: E402

try:
    # GA4 via API -- opcional. Se 'google-analytics-data' nao estiver instalado,
    # o painel continua funcionando 100% igual a antes (GA manual, .xlsx por
    # site) -- ninguem que ainda nao configurou isso deve ser afetado.
    from ga_lib import auth as ga_auth, reports as ga_reports  # noqa: E402
    GA_API_LIB_OK = True
except ImportError:
    GA_API_LIB_OK = False

try:
    # Parceiros de monetizacao via API (aba "Parceiros") -- opcional, por site.
    # Primeiro parceiro: Seedtag. Se 'requests' nao estiver instalado, ou
    # nenhum site tiver 'seedtag_publisher_id' preenchido, essa aba so mostra
    # o aviso "nenhum parceiro conectado" -- nao afeta o resto do painel.
    from seedtag_lib import auth as seedtag_auth, reports as seedtag_reports  # noqa: E402
    SEEDTAG_LIB_OK = True
except ImportError:
    SEEDTAG_LIB_OK = False

try:
    # Segundo parceiro de monetizacao: Membrana (aba "Parceiros"). Doc oficial
    # (Membrana Publisher API v1.0) fornecida pelo Joao em 21/08/2026 --
    # ver membrana_lib/reports.py pro detalhamento completo. Diferenca
    # importante em relacao a Seedtag: NENHUM endpoint da Membrana aceita
    # filtro por site -- por isso essa API e' consultada UMA VEZ SO por
    # rodada do painel (nao por site, ver build()), e cada site filtra o
    # resultado localmente pelo seu 'membrana_site_id'.
    from membrana_lib import auth as membrana_auth, reports as membrana_reports  # noqa: E402
    MEMBRANA_LIB_OK = True
except ImportError:
    MEMBRANA_LIB_OK = False

try:
    # Conversao de moeda pra exibicao do parceiro (opcional -- ver
    # config.json > parceiros_moeda_exibicao). Usada quando o parceiro (ex.:
    # Seedtag) nao tem opcao de pedir o valor ja em outra moeda na API dele.
    from fx_lib import rates as fx_rates  # noqa: E402
    FX_LIB_OK = True
except ImportError:
    FX_LIB_OK = False

CSS = open(os.path.join(HERE, "css_inner.txt"), encoding="utf-8").read()
CHARTJS = open(os.path.join(HERE, "chartjs_inline.txt"), encoding="utf-8").read()
# HTML/CSS/JS do painel (front-end). Externalizado num .txt (mesmo padrao de
# CSS/CHARTJS acima) pra o gerar_dash_v5.py ficar so com a logica Python --
# ver template_inner.txt. Conteudo idêntico ao que antes ficava embutido
# aqui como uma string Python gigante; nao muda nada em runtime.
TEMPLATE = open(os.path.join(HERE, "template_inner.txt"), encoding="utf-8").read()
DEVMAP = {
    # nomes como vem do xlsx manual (pt-BR) ou da API (varia por rede/locale)
    "smartphone": "mobile", "mobile": "mobile", "celular": "mobile",
    "computador": "desktop", "desktop": "desktop", "computer": "desktop",
    "tablet": "tablet",
    "smart tv": "tv", "smarttv": "tv", "tv": "tv", "connected tv": "tv", "connectedtv": "tv",
    "set-top box": "tv", "settop box": "tv", "stb": "tv",
}
DEVS_OUT = ("total", "mobile", "desktop")  # devices que o painel mostra


def pnum(x, d=0.0):
    if x is None or x == "":
        return d
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).replace("US$", "").replace("R$", "").replace("%", "").replace("\xa0", " ").strip().replace(" ", "")
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return d


def datestr(x):
    if x is None:
        return None
    if hasattr(x, "strftime"):
        return x.strftime("%Y-%m-%d")
    s = str(x).strip()
    if s.replace(".", "").isdigit() and len(s.split(".")[0]) == 8:  # 20260802 (GA)
        s = s.split(".")[0]
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    s = s[:10]
    return s if (len(s) == 10 and s[4] == "-") else None


def _a0():
    return {"rev": 0.0, "req": 0.0, "impr": 0.0, "clk": 0.0, "vaw": 0.0, "matchw": 0.0}


def _i0():
    return {"rev": 0.0, "req": 0.0, "impr": 0.0, "clk": 0.0, "vaw": 0.0, "mw": 0.0}


def calcular_janela(config):
    """Por padrao busca de 1o de janeiro do ano atual at hoje (decisao do Joao:
    'pode buscar do inicio do ano at o momento'). Pode ser sobrescrito com uma
    data fixa 'AAAA-MM-DD' em config['busca_data_inicio']."""
    hoje = datetime.date.today()
    modo = config.get("busca_data_inicio", "ano_atual")
    if modo == "ano_atual":
        inicio = datetime.date(hoje.year, 1, 1)
    else:
        inicio = datetime.datetime.strptime(modo, "%Y-%m-%d").date()
    return inicio, hoje


def calcular_janela_hora(config):
    """Janela do relatorio POR HORA (padrao intradia). Volta (inicio, fim) ou
    None se hora_janela_dias<=0 (busca horaria desligada)."""
    dias = int(config.get("hora_janela_dias", 0) or 0)
    if dias <= 0:
        return None
    hoje = datetime.date.today()
    return hoje - datetime.timedelta(days=dias - 1), hoje


def _nome_limpo_do_breadcrumb(texto):
    """Fallback SO usado se por algum motivo a coluna Dimension.AD_UNIT_ID nao
    vier no relatorio: a API devolve o nome do ad unit como um 'breadcrumb'
    tipo 'portalleodias (22979280805) » anchor (22979281120)' -- pega so o
    ultimo nivel e tira o '(id)' do final."""
    ultimo = texto.split("»")[-1].strip()
    return re.sub(r"\s*\(\d+\)\s*$", "", ultimo).strip()


def _idx_map(header, config):
    """Acha cada coluna pelo NOME no cabecalho (nao pela posicao -- a API
    devolve colunas extras que a gente nao pediu). Volta um dict de indices."""
    def idx_de(rotulo):
        return header.index(rotulo) if rotulo in header else None

    idx = {
        "data": idx_de("Dimension.DATE"),
        "hora": idx_de("Dimension.HOUR"),
        "adunit_id": idx_de("Dimension.AD_UNIT_ID"),
        "adunit_nome": idx_de("Dimension.AD_UNIT_NAME"),
        "dev": idx_de("Dimension.DEVICE_CATEGORY_NAME"),
        # aceita CREATIVE_SIZE ou CREATIVE_SIZE_DELIVERED (o "entregue" costuma
        # trazer o tamanho real; o simples volta 1x1 pra demanda de Ad Exchange)
        "size": next((k for k, h in enumerate(header) if "CREATIVE_SIZE" in str(h).upper()), None),
        "metrica": {},
    }
    for chave, enum_gam in config["report_columns"].items():
        i = idx_de(f"Column.{enum_gam}")
        if i is None:
            print(f"  ! aviso: coluna 'Column.{enum_gam}' (campo '{chave}') nao veio no relatorio -- "
                  "confira config.json / rode ferramentas/testar_colunas.py.")
        else:
            idx["metrica"][chave] = i
    return idx


def _valores_da_linha(linha, idx, colunas_micros, id_para_nome, exige_fmt=True):
    """Extrai (fmt, dev, rev, req, impr, clk, match, va) de uma linha do CSV,
    ou None se a linha nao for aproveitavel. Compartilhado entre o relatorio
    diario e o horario. exige_fmt=False (relatorio horario, que ja soma todos
    os formatos no total do site) dispensa a coluna de formato -- assim o
    relatorio horario pode ser pedido SEM a dimensao AD_UNIT_NAME (muito mais
    leve: ~150x menos linhas)."""
    imx = idx["metrica"]
    # so considera indices que EXISTEM neste relatorio -- o relatorio por
    # TAMANHO, por ex., nao tem a dimensao DATE (idx["data"] fica None) e pode
    # nao ter DEVICE (idx["dev"] None); antes isso estourava no max([...None...]).
    _idxs = [i for i in ([idx["data"], idx["dev"]] + list(imx.values())) if i is not None]
    if not linha or (_idxs and len(linha) <= max(_idxs)):
        return None

    fmt = None
    if idx["adunit_id"] is not None:
        fmt = id_para_nome.get(str(linha[idx["adunit_id"]]).strip())
    if not fmt and idx["adunit_nome"] is not None:
        fmt = _nome_limpo_do_breadcrumb(linha[idx["adunit_nome"]] or "")
    fmt = (fmt or "").strip()
    if exige_fmt and not fmt:
        return None

    dev = DEVMAP.get(str(linha[idx["dev"]] or "").strip().lower(), "outros") if idx["dev"] is not None else "outros"

    def pega(chave):
        if chave not in imx:
            return 0.0
        v = pnum(linha[imx[chave]])
        return v / 1_000_000 if chave in colunas_micros else v

    rev, req, impr, clk = pega("rev"), pega("req"), pega("impr"), pega("clk")
    match, va = pega("match"), pega("va")
    if va <= 1.5:
        va *= 100
    if match <= 1.5:
        match *= 100
    return fmt, dev, rev, req, impr, clk, match, va


def _devs_saida(dev):
    """Onde essa linha entra: sempre em 'total', e em 'mobile'/'desktop' quando
    for o caso (tablet/tv/outros ficam so no total, como na v5 original)."""
    return ("total",) + ((dev,) if dev in ("mobile", "desktop") else ())


# ---------- Ad Manager (via API) ----------
def _resolver_alvo(site_row, config, base_dir):
    """Autentica, lista ad units da rede, resolve a subarvore do site."""
    network_code = site_row["network_code"]
    api_version = config["gam_api_version"]
    client = auth.obter_client(network_code, config, base_dir)
    ad_units = inventory.listar_ad_units(client, api_version, network_code)
    id_para_nome = {au["id"]: au["name"] for au in ad_units}
    moeda_forcada = str(config.get("report_currency") or "").strip().upper()
    if moeda_forcada:
        # config.json > report_currency preenchido: montar_report_job() ja
        # manda 'adxReportCurrency' pra API converter a receita pra essa
        # moeda -- entao e ela (nao a moeda nativa da rede) que descreve o
        # que veio no relatorio. Evita tambem a chamada extra ao
        # NetworkService (nao precisamos mais saber a moeda nativa).
        moeda = moeda_forcada
    else:
        moeda = network.moeda_da_rede(client, api_version, network_code)

    ad_unit_ids = None
    root_nome_ou_id = site_row.get("root_ad_unit_id") or site_row.get("root_ad_unit_nome")
    if root_nome_ou_id:
        root_id = inventory.resolver_ad_unit_raiz(
            ad_units,
            root_ad_unit_id=site_row.get("root_ad_unit_id"),
            root_ad_unit_nome=site_row.get("root_ad_unit_nome"),
        )
        if root_id:
            ad_unit_ids = inventory.descendentes_de(ad_units, root_id)
    return client, api_version, network_code, ad_unit_ids, id_para_nome, moeda


def _poll_seg(config):
    """Intervalo de polling (segundos) do WaitForReport. Padrao 6 (antes
    era 15) -- checa o relatorio mais vezes e baixa assim que fica pronto.
    Ajustavel via config.json > poll_segundos."""
    try:
        return max(2, int(config.get("poll_segundos", 6) or 6))
    except (TypeError, ValueError):
        return 6


def _fetch_diario(client, api_version, network_code, ad_unit_ids, id_para_nome, config, site_row):
    data_ini, data_fim = calcular_janela(config)
    job = reports.montar_report_job(config, data_ini, data_fim, ad_unit_ids)
    try:
        texto_csv = reports.rodar_report(client, api_version, job, poll_time_seconds=_poll_seg(config))
    except gam_errors.AdManagerReportError as e:
        raise RuntimeError(
            f"[{site_row.get('id')}] A API recusou o relatorio diario (rede {network_code}): {e}\n"
            "  -> Confira 'report_columns' no config.json (rode ferramentas/testar_colunas.py "
            f"{network_code}) e se a service account foi adicionada como usuario nessa rede."
        ) from e
    except Exception as e:
        # Erros de MONTAGEM local do pedido SOAP (antes de qualquer resposta da
        # API) nao sao AdManagerReportError -- ex.: um KeyError com o nome de um
        # campo que nao existe/nao e mais valido na versao configurada da API
        # (foi o que aconteceu com 'adxReportCurrency' em 11/08/2026, testado
        # so com mock e nao com a API de verdade -- nunca mais repetir esse
        # erro sem validar contra a API real primeiro). Pegamos aqui tambem
        # pra dar uma dica util em vez de um erro criptico, e pra NAO derrubar
        # os outros sites (build() so pula este).
        raise RuntimeError(
            f"[{site_row.get('id')}] Erro ao montar/enviar o relatorio diario (rede {network_code}): "
            f"{type(e).__name__}: {e}\n"
            "  -> Se a mensagem citar um nome de campo (ex.: 'adxReportCurrency'), esse campo "
            f"nao e valido na versao da API configurada ('{config.get('gam_api_version')}') -- "
            "confira 'report_currency' e 'report_columns' no config.json (volte pros valores "
            "originais e rode de novo)."
        ) from e

    header, linhas = reports.ler_csv(texto_csv)
    idx = _idx_map(header, config)
    if idx["data"] is None or idx["dev"] is None or (idx["adunit_id"] is None and idx["adunit_nome"] is None):
        raise RuntimeError(
            f"[{site_row.get('id')}] O cabecalho do relatorio diario veio diferente do esperado: {header}"
        )
    colunas_micros = set(config.get("report_columns_micros", []))

    acc, dates = {}, set()
    for linha in linhas:
        d = datestr(linha[idx["data"]]) if idx["data"] < len(linha) else None
        if not d:
            continue
        vals = _valores_da_linha(linha, idx, colunas_micros, id_para_nome)
        if not vals:
            continue
        fmt, dev, rev, req, impr, clk, match, va = vals
        dates.add(d)
        for target in (fmt, "__ALL__"):
            for dd in (dev, "total"):
                x = acc.setdefault((target, dd, d), _a0())
                x["rev"] += rev
                x["req"] += req
                x["impr"] += impr
                x["clk"] += clk
                x["vaw"] += va * impr
                x["matchw"] += match * impr
    return acc, sorted(dates)


def _fetch_hora(client, api_version, network_code, ad_unit_ids, id_para_nome, config, site_row):
    """Relatorio POR HORA numa janela curta -> agregados intradia. Nunca
    derruba a geracao do site: se falhar, volta temHora=False e um aviso."""
    vazio = {"temHora": False, "janela_dias": int(config.get("hora_janela_dias", 0) or 0),
             "datasHora": [], "horasDia": {}}
    janela = calcular_janela_hora(config)
    if janela is None:
        return vazio
    data_ini, data_fim = janela
    dims = config.get("report_dimensions_hora", ["DATE", "HOUR", "DEVICE_CATEGORY_NAME"])
    tzt = config.get("report_time_zone_type") or None

    def _rodar(dims_usar):
        job = reports.montar_report_job(config, data_ini, data_fim, ad_unit_ids, dimensions=dims_usar, time_zone_type=tzt)
        return reports.rodar_report(client, api_version, job, poll_time_seconds=_poll_seg(config))

    try:
        texto_csv = _rodar(dims)
    except Exception as e:  # noqa: BLE001 -- hora e "bonus"; nao pode quebrar o site
        # A versao enxuta (sem AD_UNIT_NAME) e ~150x mais leve, mas se por algum
        # motivo a API recusar essa combinacao, tentamos DE NOVO com as dimensoes
        # antigas (com AD_UNIT_NAME) antes de desistir -- assim a aba "Por horario"
        # nunca some so por causa da otimizacao de velocidade.
        if "AD_UNIT_NAME" not in dims:
            print(f"  ! aviso: relatorio POR HORA enxuto falhou para {site_row.get('id')} ({e}); "
                  "tentando de novo com as dimensoes antigas (com formato)...")
            try:
                texto_csv = _rodar(["DATE", "HOUR", "AD_UNIT_NAME", "DEVICE_CATEGORY_NAME"])
            except Exception as e2:  # noqa: BLE001
                print(f"  ! aviso: relatorio POR HORA falhou de vez para {site_row.get('id')} (rede {network_code}): {e2}")
                print("    -> o painel sai sem o 'Analise por horario' deste site; a serie Dia/Mes esta ok.")
                return vazio
        else:
            print(f"  ! aviso: relatorio POR HORA falhou para {site_row.get('id')} (rede {network_code}): {e}")
            print("    -> o painel sai sem o 'Analise por horario' deste site; a serie Dia/Mes esta ok.")
            return vazio

    header, linhas = reports.ler_csv(texto_csv)
    idx = _idx_map(header, config)
    if idx["data"] is None or idx["hora"] is None or idx["dev"] is None:
        print(f"  ! aviso: cabecalho do relatorio horario inesperado para {site_row.get('id')}: {header}")
        return vazio
    colunas_micros = set(config.get("report_columns_micros", []))

    # horasDia[dev][data] = [24] celulas {rev,req,impr,clk,vaw,mw} -- SO o total do
    # site (todos os blocos somados, = a coluna "Bloco de anuncios (todos os
    # niveis)" da UI). Guardar por-formato x por-data x 24h seria pesado demais
    # (30-150 formatos); o total do site cobre a comparacao dia-a-dia por hora
    # (que e o que a tela do Ad Manager mostra) e, no front, dele derivamos o
    # padrao por hora-do-dia / dia-da-semana / heatmap.
    horas = {d: {} for d in DEVS_OUT}   # dev -> data -> [24] celulas
    datas = set()
    tem_dado = False

    for linha in linhas:
        d = datestr(linha[idx["data"]]) if idx["data"] < len(linha) else None
        if not d or idx["hora"] >= len(linha):
            continue
        try:
            hh = int(float(str(linha[idx["hora"]]).strip()))
        except (ValueError, TypeError):
            continue
        if not (0 <= hh <= 23):
            continue
        vals = _valores_da_linha(linha, idx, colunas_micros, id_para_nome, exige_fmt=False)
        if not vals:
            continue
        _fmt, dev, rev, req, impr, clk, match, va = vals
        tem_dado = True
        datas.add(d)
        for dev_out in _devs_saida(dev):
            cells = horas[dev_out].setdefault(d, [_i0() for _ in range(24)])
            c = cells[hh]
            c["rev"] += rev
            c["req"] += req
            c["impr"] += impr
            c["clk"] += clk
            c["vaw"] += va * impr
            c["mw"] += match * impr

    if not tem_dado:
        return vazio

    def _round_cell(o):
        return {"rev": round(o["rev"], 3), "req": int(o["req"]), "impr": int(o["impr"]),
                "clk": int(o["clk"]), "vaw": round(o["vaw"], 1), "mw": round(o["mw"], 1)}

    horas = {dev: {d: [_round_cell(o) for o in arr] for d, arr in por_data.items()}
             for dev, por_data in horas.items()}
    return {"temHora": True, "janela_dias": int(config.get("hora_janela_dias", 0) or 0),
            "datasHora": sorted(datas), "horasDia": horas}


def calcular_janela_sizes(config):
    """Janela do relatorio por TAMANHO de criativo (bloco 'tamanhos que
    entregam'). Volta (inicio, fim) ou None se sizes_janela_dias<=0 (desligado).
    Padrao 30 dias -- o foco e o desempenho RECENTE por tamanho (pra otimizar),
    entao nao precisa do ano inteiro e o dado fica minusculo (agregado, sem dia
    a dia)."""
    dias = int(config.get("sizes_janela_dias", 30) or 0)
    if dias <= 0:
        return None
    hoje = datetime.date.today()
    return hoje - datetime.timedelta(days=dias - 1), hoje


_RE_SIZE = re.compile(r"^\d{1,4}x\d{1,4}$", re.I)
# diagnostico do relatorio por tamanho -- preenchido por _fetch_sizes e gravado
# em diagnostico_tamanhos.txt no fim de build(). Ajuda a saber, sem abrir o
# console, por que os tamanhos apareceram ou nao (qual combinacao a API aceitou).
_SIZES_DIAG = []
# diagnostico do relatorio de DEALS (por rede) -- gravado em diagnostico_deals.txt
_DEALS_DIAG = []
# diagnostico da Membrana (site_id que a API ve x o que esta no sites.xlsx)
_MEMBRANA_DIAG = []
# versao ESTRUTURADA do diagnostico da Membrana, embutida no DATA pra o painel
# poder mostrar o motivo direto na aba Parceiros (sem abrir arquivo).
_MEMBRANA_INFO = {}


def _fetch_sizes(client, api_version, network_code, ad_unit_ids, id_para_nome, config, site_row):
    """Relatorio por TAMANHO de criativo (Dimension.CREATIVE_SIZE), AGREGADO
    numa janela recente (config 'sizes_janela_dias', padrao 30). Devolve
    sizesD[fmt][tamanho][device] = {rev,req,impr,clk,vaw,mw} na MESMA convencao
    do seriesD (vaw=Σ va*impr, mw=Σ match*impr), pro front reaproveitar metric().

    AUTO-CORRETIVO: como nem toda rede aceita CREATIVE_SIZE junto das metricas de
    Ad Exchange (e as vezes so recusa quando junta com DEVICE), tenta algumas
    combinacoes em ordem e usa a PRIMEIRA que devolver tamanhos. AD_UNIT_NAME e
    obrigatorio nas tentativas (o bloco e POR FORMATO). Sempre grava o que
    aconteceu em _SIZES_DIAG. Nunca quebra o site (devolve {} em ultimo caso)."""
    vazio = {"sizesD": {}, "sizesInfo": {}}
    jan = calcular_janela_sizes(config)
    if jan is None:
        return vazio
    data_ini, data_fim = jan
    sid = site_row.get("id")
    # Guardado POR DIA (DATE) e POR DISPOSITIVO (DEVICE) pro painel poder seguir o
    # PERIODO e o filtro Total/Mobile/Desktop da tela. Tenta o tamanho ENTREGUE
    # (CREATIVE_SIZE_DELIVERED) primeiro -- e o que traz 300x250 etc. de verdade;
    # o CREATIVE_SIZE simples volta "1x1" (placeholder de Ad Exchange). Se a rede
    # recusar a combinacao com DEVICE, cai pras variacoes sem device (so 'total').
    # Usa a 1a variacao que trouxer tamanhos REAIS; se so vier 1x1, guarda reserva.
    variacoes = config.get("report_dimensions_size_tentativas") or [
        ["DATE", "AD_UNIT_NAME", "CREATIVE_SIZE_DELIVERED", "DEVICE_CATEGORY_NAME"],
        ["DATE", "AD_UNIT_NAME", "CREATIVE_SIZE", "DEVICE_CATEGORY_NAME"],
        ["DATE", "AD_UNIT_NAME", "CREATIVE_SIZE_DELIVERED"],
        ["DATE", "AD_UNIT_NAME", "CREATIVE_SIZE"],
    ]
    colunas_micros = set(config.get("report_columns_micros", []))
    max_sz = int(config.get("sizes_max_por_formato", 20) or 0)
    PLACEHOLDER = {"1x1", "0x0"}

    def _monta(por_fmt):
        # por_fmt[fmt][size][device] = {dia: cell}; mantem os top max_sz tamanhos
        # por (receita+impressao) do TOTAL, guardando a serie POR DIA e POR DISPOSITIVO.
        sd = {}
        for fmt, sizes in por_fmt.items():
            def _peso(sz):
                dias = sizes[sz].get("total") or {}
                return sum(c["rev"] + c["impr"] for c in dias.values())
            ordenados = sorted(sizes.keys(), key=lambda sz: -_peso(sz))
            keep = ordenados[:max_sz] if max_sz > 0 else ordenados
            out = {}
            for sz in keep:
                out[sz] = {dev: {d: {"rev": round(c["rev"], 3), "req": int(c["req"]), "impr": int(c["impr"]),
                                     "clk": int(c["clk"]), "vaw": round(c["vaw"], 1), "mw": round(c["mw"], 1)}
                                 for d, c in dias.items()}
                           for dev, dias in sizes[sz].items()}
            if out:
                sd[fmt] = out
        return sd

    reserva = None  # resultado so-placeholder (1x1), usado se nada real aparecer

    for dims in variacoes:
        try:
            job = reports.montar_report_job(config, data_ini, data_fim, ad_unit_ids, dimensions=dims)
            texto_csv = reports.rodar_report(client, api_version, job, poll_time_seconds=_poll_seg(config))
        except Exception as e:  # noqa: BLE001
            _SIZES_DIAG.append(f"[{sid}] dims={dims} -> RECUSADO pela API: {type(e).__name__}: {str(e)[:250]}")
            continue

        header, linhas = reports.ler_csv(texto_csv)
        idx = _idx_map(header, config)
        if idx["size"] is None or idx["data"] is None or (idx["adunit_id"] is None and idx["adunit_nome"] is None):
            _SIZES_DIAG.append(f"[{sid}] dims={dims} -> ACEITOU, mas sem coluna de DATA/TAMANHO/FORMATO. Cabecalho: {header}")
            continue

        por_fmt = {}  # fmt -> size -> device -> dia -> celula
        vistos = set()
        tem_dev = idx["dev"] is not None
        for linha in linhas:
            if idx["size"] >= len(linha) or idx["data"] >= len(linha):
                continue
            dia = datestr(linha[idx["data"]])
            if not dia:
                continue
            size = str(linha[idx["size"]] or "").strip().lower().replace(" ", "")
            if not _RE_SIZE.match(size) or size == "0x0":
                continue  # ignora "Various"/vazio e o lixo 0x0
            vals = _valores_da_linha(linha, idx, colunas_micros, id_para_nome, exige_fmt=True)
            if not vals:
                continue
            fmt, dev, rev, req, impr, clk, match, va = vals
            vistos.add(size)
            for dev_out in (_devs_saida(dev) if tem_dev else ("total",)):
                dias = por_fmt.setdefault(fmt, {}).setdefault(size, {}).setdefault(dev_out, {})
                c = dias.get(dia)
                if c is None:
                    c = dias[dia] = {"rev": 0.0, "req": 0.0, "impr": 0.0, "clk": 0.0, "vaw": 0.0, "mw": 0.0}
                c["rev"] += rev
                c["req"] += req
                c["impr"] += impr
                c["clk"] += clk
                c["vaw"] += va * impr
                c["mw"] += match * impr
        if not por_fmt:
            _SIZES_DIAG.append(f"[{sid}] dims={dims} -> ACEITOU ({len(linhas)} linha(s)) mas 0 tamanhos NxN.")
            continue

        sizesD = _monta(por_fmt)
        if not sizesD:
            continue

        reais = sorted(s for s in vistos if s not in PLACEHOLDER)
        info = {"de": data_ini.strftime("%Y-%m-%d"), "ate": data_fim.strftime("%Y-%m-%d"),
                "temDevice": tem_dev, "soPlaceholder": (len(reais) == 0)}
        if reais:
            _SIZES_DIAG.append(f"[{sid}] dims={dims} -> OK, tamanhos REAIS por dia: {reais[:12]}")
            return {"sizesD": sizesD, "sizesInfo": info}
        _SIZES_DIAG.append(f"[{sid}] dims={dims} -> ACEITOU mas so 1x1 (placeholder); tento outra dimensao.")
        if reserva is None:
            reserva = {"sizesD": sizesD, "sizesInfo": info}

    if reserva is not None:
        _SIZES_DIAG.append(f"[{sid}] -> nenhuma dimensao trouxe tamanho real; usando 1x1 (tipico de Ad Exchange, a rede nao informa o tamanho entregue).")
        return reserva
    return vazio


def _col_por_nome(header, nome):
    """Acha o indice da coluna cujo rotulo contem 'nome' (ex.: 'AD_UNIT_NAME',
    'LINE_ITEM_NAME'). A API prefixa com 'Dimension.'/'Column.' e pode devolver
    colunas extras -- por isso casamos pelo NOME, nao pela posicao."""
    up = str(nome).upper()
    for i, h in enumerate(header):
        if up in str(h).upper():
            return i
    return None


def _deal_incluir(camp, config):
    """True se a campanha deve entrar nos Deals. Quando 'deals_filtro_ativo',
    só entram as que contêm um dos nomes de 'deals_filtro_nomes' (ex.: Criteo,
    RTB House) -- assim campanhas de venda direta ficam de fora."""
    if not config.get("deals_filtro_ativo", False):
        return True
    nomes = [str(n).strip().lower() for n in (config.get("deals_filtro_nomes") or []) if str(n).strip()]
    if not nomes:
        return True
    c = str(camp or "").lower()
    return any(n in c for n in nomes)


def fetch_deals_rede(client, api_version, network_code, config, base_dir):
    """Relatorio de DEALS/itens de linha da REDE inteira (todos os ad units),
    AGREGADO por (ad unit de nivel superior, campanha/item de linha, tipo) numa
    janela recente. Valores monetarios em USD (reportCurrency do config).
    Devolve (itens, ini, fim, moeda). NUNCA quebra a geracao (retorna [] em erro).

    itens = [{"site": adUnitTopLevel, "camp": lineItemName, "tipo": lineItemType,
              "impr","rev","clk","v","me","el"}]  (v/me/el = Active View viewable/measurable/eligible)"""
    dias = int(config.get("deals_janela_dias", 30) or 30)
    fim = datetime.date.today() - datetime.timedelta(days=1)  # ate ontem (dia fechado)
    ini = fim - datetime.timedelta(days=dias - 1)
    dims_base = config.get("deals_dimensions") or ["AD_UNIT_NAME", "LINE_ITEM_NAME", "LINE_ITEM_TYPE"]
    colmap = config.get("report_columns_deals") or {}
    if not colmap:
        return [], ini, fim, config.get("report_currency", "USD")
    cols = list(colmap.values())
    micros = set()
    for k in (config.get("report_columns_deals_micros") or []):
        if k in colmap:
            micros.add(colmap[k])
    view = config.get("deals_ad_unit_view", "TOP_LEVEL")
    # filtro por PROPOSTA (Criteo/RTB House): tenta com a dimensão da proposta;
    # se a rede recusar, refaz sem ela e filtra pelo nome do item de linha.
    filtrar = bool(config.get("deals_filtro_ativo", False)) and bool(config.get("deals_filtro_nomes"))
    filtro_dim = config.get("deals_filtro_dimensao", "PROPOSAL_NAME") if filtrar else None
    dims = dims_base + ([filtro_dim] if filtro_dim and filtro_dim not in dims_base else [])

    def _run(_dims):
        job = reports.montar_report_job(config, ini, fim, dimensions=_dims, columns=cols, ad_unit_view=view)
        return reports.rodar_report(client, api_version, job, poll_time_seconds=_poll_seg(config))
    texto_csv = None
    try:
        texto_csv = _run(dims)
    except Exception as e:  # noqa: BLE001
        if filtro_dim:
            _DEALS_DIAG.append(f"[rede {network_code}] proposta '{filtro_dim}' recusada ({str(e)[:120]}); refazendo sem ela e filtrando pelo nome do item de linha.")
            dims = list(dims_base); filtro_dim = None
            try:
                texto_csv = _run(dims)
            except Exception as e2:  # noqa: BLE001
                _DEALS_DIAG.append(f"[rede {network_code}] dims={dims} -> RECUSADO: {type(e2).__name__}: {str(e2)[:250]}")
                return [], ini, fim, config.get("report_currency", "USD")
        else:
            _DEALS_DIAG.append(f"[rede {network_code}] dims={dims} -> RECUSADO: {type(e).__name__}: {str(e)[:250]}")
            return [], ini, fim, config.get("report_currency", "USD")

    header, linhas = reports.ler_csv(texto_csv)
    i_site = _col_por_nome(header, "AD_UNIT_NAME")
    i_camp = _col_por_nome(header, "LINE_ITEM_NAME")
    i_tipo = _col_por_nome(header, "LINE_ITEM_TYPE")
    i_prop = _col_por_nome(header, filtro_dim) if filtro_dim else None
    idxcol = {k: _col_por_nome(header, v) for k, v in colmap.items()}
    if i_site is None or i_camp is None or idxcol.get("impr") is None:
        _DEALS_DIAG.append(f"[rede {network_code}] ACEITOU mas faltou coluna (site/camp/impr). Cabecalho: {header}")
        return [], ini, fim, config.get("report_currency", "USD")

    def num(linha, chave):
        j = idxcol.get(chave)
        if j is None or j >= len(linha):
            return 0.0
        v = pnum(linha[j], 0.0)
        return v / 1_000_000 if colmap.get(chave) in micros else v

    agg = {}
    for linha in linhas:
        if i_site >= len(linha) or i_camp >= len(linha):
            continue
        site = str(linha[i_site] or "").strip()
        camp = str(linha[i_camp] or "").strip()
        tipo = str(linha[i_tipo] or "").strip() if (i_tipo is not None and i_tipo < len(linha)) else ""
        if not site or not camp:
            continue
        if filtrar:
            alvo_filtro = (str(linha[i_prop]) if (i_prop is not None and i_prop < len(linha)) else camp)
            if not _deal_incluir(alvo_filtro, config):
                continue  # fora do filtro (proposta != Criteo/RTB House; ex.: venda direta)
        impr = num(linha, "impr")
        if impr <= 0 and num(linha, "rev") <= 0:
            continue  # linha sem entrega nem receita -> ignora
        k = (site, camp, tipo)
        o = agg.get(k)
        if o is None:
            o = agg[k] = {"impr": 0.0, "rev": 0.0, "clk": 0.0, "v": 0.0, "me": 0.0, "el": 0.0}
        o["impr"] += impr
        o["rev"] += num(linha, "rev")
        o["clk"] += num(linha, "clk")
        o["v"] += num(linha, "v")
        o["me"] += num(linha, "me")
        o["el"] += num(linha, "el")

    itens = [{"site": k[0], "camp": k[1], "tipo": k[2],
              "impr": int(round(v["impr"])), "rev": round(v["rev"], 4), "clk": int(round(v["clk"])),
              "v": int(round(v["v"])), "me": int(round(v["me"])), "el": int(round(v["el"]))}
             for k, v in agg.items()]
    n_sites = len({it["site"] for it in itens})
    _DEALS_DIAG.append(f"[rede {network_code}] OK -> {len(itens)} deal(s) em {n_sites} ad unit(s) top-level "
                       f"({ini.isoformat()} a {fim.isoformat()}).")
    return itens, ini, fim, config.get("report_currency", "USD")


def fetch_deals_diario_rede(client, api_version, network_code, config, base_dir):
    """Deals POR DIA e POR DISPOSITIVO, por ad unit top-level (p/ o consolidado
    seguir o período/dispositivo selecionado). Report leve: [DATE, AD_UNIT_NAME,
    DEVICE_CATEGORY_NAME] × (impr, rev). Valores em USD. NUNCA quebra a geração.
    Devolve {adUnitTopLevel: {device: {data: {rev, impr}}}} com device em
    'total'/'mobile'/'desktop' (total sempre; mobile/desktop quando for o caso)."""
    dias = int(config.get("deals_janela_dias", 30) or 30)
    fim = datetime.date.today() - datetime.timedelta(days=1)
    ini = fim - datetime.timedelta(days=dias - 1)
    colmap = config.get("report_columns_deals") or {}
    if not colmap or "impr" not in colmap or "rev" not in colmap:
        return {}
    cols = [colmap["impr"], colmap["rev"]]
    micros = set()
    for k in (config.get("report_columns_deals_micros") or []):
        if k in colmap:
            micros.add(colmap[k])
    # filtro por PROPOSTA (Criteo/RTB House): precisa da dimensão da proposta.
    # Se a rede recusar, refaz sem ela e filtra pelo nome do item de linha.
    filtrar = bool(config.get("deals_filtro_ativo", False)) and bool(config.get("deals_filtro_nomes"))
    filtro_dim = config.get("deals_filtro_dimensao", "PROPOSAL_NAME") if filtrar else None
    base = ["DATE", "AD_UNIT_NAME", "DEVICE_CATEGORY_NAME"]
    dims = base + ([filtro_dim] if filtro_dim else [])

    def _run(_dims):
        job = reports.montar_report_job(config, ini, fim, dimensions=_dims, columns=cols,
                                        ad_unit_view=config.get("deals_ad_unit_view", "TOP_LEVEL"))
        return reports.rodar_report(client, api_version, job, poll_time_seconds=_poll_seg(config))
    texto_csv = None
    try:
        texto_csv = _run(dims)
    except Exception as e:  # noqa: BLE001
        if filtro_dim:
            dims = base + ["LINE_ITEM_NAME"]; filtro_dim = None
            try:
                texto_csv = _run(dims)
            except Exception as e2:  # noqa: BLE001
                _DEALS_DIAG.append(f"[rede {network_code}] deals DIÁRIO recusado: {type(e2).__name__}: {str(e2)[:180]}")
                return {}
        else:
            _DEALS_DIAG.append(f"[rede {network_code}] deals DIÁRIO recusado: {type(e).__name__}: {str(e)[:180]}")
            return {}
    header, linhas = reports.ler_csv(texto_csv)
    i_dt = _col_por_nome(header, "DATE")
    i_site = _col_por_nome(header, "AD_UNIT_NAME")
    i_dev = _col_por_nome(header, "DEVICE_CATEGORY_NAME")
    i_camp = _col_por_nome(header, filtro_dim) if filtro_dim else _col_por_nome(header, "LINE_ITEM_NAME")
    i_impr = _col_por_nome(header, colmap["impr"])
    i_rev = _col_por_nome(header, colmap["rev"])
    if i_dt is None or i_site is None or i_impr is None or i_rev is None:
        return {}
    out = {}
    for ln in linhas:
        if i_site >= len(ln) or i_dt >= len(ln):
            continue
        site = str(ln[i_site] or "").strip()
        data = datestr(ln[i_dt])
        if not site or not data:
            continue
        if filtrar:
            camp = str(ln[i_camp] or "").strip() if (i_camp is not None and i_camp < len(ln)) else ""
            if not _deal_incluir(camp, config):
                continue
        impr = pnum(ln[i_impr], 0.0) if i_impr < len(ln) else 0.0
        rev = pnum(ln[i_rev], 0.0) if i_rev < len(ln) else 0.0
        if colmap["rev"] in micros:
            rev /= 1_000_000
        if impr <= 0 and rev <= 0:
            continue
        dev = DEVMAP.get(str(ln[i_dev] or "").strip().lower(), "outros") if (i_dev is not None and i_dev < len(ln)) else "outros"
        alvo = out.setdefault(site, {})
        for devout in _devs_saida(dev):
            dd = alvo.setdefault(devout, {})
            c = dd.get(data)
            if c is None:
                c = dd[data] = {"rev": 0.0, "impr": 0.0}
            c["rev"] += rev
            c["impr"] += impr
    # arredonda
    for site in out:
        for devout in out[site]:
            for data in out[site][devout]:
                c = out[site][devout][data]
                c["rev"] = round(c["rev"], 4)
                c["impr"] = int(round(c["impr"]))
    return out


_SR_DIAG = []


def _sportradar_incluir(camp, config):
    """True se o NOME do item de linha bate com a SportRadar (contem um dos
    termos de 'sportradar_filtro_nomes' -- padrao: 'sportradar' -- comparacao
    case-insensitive por substring)."""
    termos = [str(n).strip().lower() for n in (config.get("sportradar_filtro_nomes") or ["sportradar"]) if str(n).strip()]
    if not termos:
        return False
    c = str(camp or "").lower()
    return any(t in c for t in termos)


def _sportradar_excluir_tipo(tipo, config):
    """True se o TIPO do item de linha deve ser EXCLUIDO (por pedido do Joao:
    Preferred Deal fica de fora -- e uma transacao reservada/direta, fora do
    acordo por clique). Compara por substring, case-insensitive, contra
    'sportradar_excluir_tipos'."""
    termos = [str(n).strip().lower() for n in (config.get("sportradar_excluir_tipos") or ["preferred_deal", "preferred deal"]) if str(n).strip()]
    t = str(tipo or "").lower()
    return any(term in t for term in termos)


def fetch_sportradar_rede(client, api_version, network_code, config, base_dir):
    """Relatorio de itens de linha da SportRadar, POR REDE (todos os ad
    units), POR DIA -- agregado por (ad unit TOP-LEVEL = publisher, item de
    linha, tipo, dia). A API do Ad Manager NAO filtra relatorio por nome de
    item de linha (so por AD_UNIT_ID via 'statement') -- por isso este
    relatorio baixa TODOS os itens de linha da rede nesses ad units/periodo e
    filtra AQUI: so entram os que tem 'sportradar' (ou variante configurada)
    no NOME, e sao excluidos os cujo TIPO bate com 'sportradar_excluir_tipos'
    (padrao: Preferred Deal). NAO pede a coluna de receita do GAM -- a
    SportRadar paga por CLIQUE (valor fixo, config > sportradar_valor_por_clique),
    entao a receita e' calculada por quem chama (build()), nunca lida daqui
    (a linha de item pode nem ter um valor configurado de verdade no GAM).

    Devolve (itens, ini, fim). itens = [{"site": adUnitTopLevel, "camp":
    lineItemName, "tipo": lineItemType, "data": 'AAAA-MM-DD', "impr", "clk",
    "v", "me", "el"}] (v/me/el = Active View viewable/measurable/eligible).
    NUNCA lanca excecao pra fora -- devolve ([], ini, fim) e grava o motivo em
    _SR_DIAG (ver diagnostico_sportradar.txt)."""
    dias = int(config.get("sportradar_janela_dias", 60) or 60)
    fim = datetime.date.today() - datetime.timedelta(days=1)  # ate ontem (dia fechado)
    ini = fim - datetime.timedelta(days=dias - 1)
    dims = ["DATE", "AD_UNIT_NAME", "LINE_ITEM_NAME", "LINE_ITEM_TYPE"]
    # reaproveita o mapa de metricas ja confirmado pelos Deals (mesmo tipo de
    # relatorio -- item de linha) -- so nao usamos a chave 'rev' (nao lemos
    # receita do GAM aqui).
    colmap = config.get("report_columns_deals") or {}
    cols_chaves = [k for k in ("impr", "clk", "v", "me", "el") if k in colmap]
    if not cols_chaves:
        _SR_DIAG.append(f"[rede {network_code}] 'report_columns_deals' sem impr/clk/v/me/el no config.json -- SportRadar desativado.")
        return [], ini, fim
    cols = [colmap[k] for k in cols_chaves]
    view = config.get("deals_ad_unit_view", "TOP_LEVEL")  # mesmo agrupamento (publisher = ad unit pai) dos Deals

    try:
        job = reports.montar_report_job(config, ini, fim, dimensions=dims, columns=cols, ad_unit_view=view)
        texto_csv = reports.rodar_report(client, api_version, job, poll_time_seconds=_poll_seg(config))
    except Exception as e:  # noqa: BLE001
        _SR_DIAG.append(f"[rede {network_code}] dims={dims} -> RECUSADO: {type(e).__name__}: {str(e)[:250]}")
        return [], ini, fim

    header, linhas = reports.ler_csv(texto_csv)
    i_dt = _col_por_nome(header, "DATE")
    i_site = _col_por_nome(header, "AD_UNIT_NAME")
    i_camp = _col_por_nome(header, "LINE_ITEM_NAME")
    i_tipo = _col_por_nome(header, "LINE_ITEM_TYPE")
    idxcol = {k: _col_por_nome(header, colmap[k]) for k in cols_chaves}
    if i_dt is None or i_site is None or i_camp is None or idxcol.get("impr") is None:
        _SR_DIAG.append(f"[rede {network_code}] ACEITOU mas faltou coluna (data/site/camp/impr). Cabecalho: {header}")
        return [], ini, fim

    def num(linha, chave):
        j = idxcol.get(chave)
        if j is None or j >= len(linha):
            return 0.0
        return pnum(linha[j], 0.0)

    agg = {}
    n_vistas_nome = 0
    for linha in linhas:
        if i_dt >= len(linha) or i_site >= len(linha) or i_camp >= len(linha):
            continue
        camp = str(linha[i_camp] or "").strip()
        if not camp or not _sportradar_incluir(camp, config):
            continue
        n_vistas_nome += 1
        tipo = str(linha[i_tipo] or "").strip() if (i_tipo is not None and i_tipo < len(linha)) else ""
        if _sportradar_excluir_tipo(tipo, config):
            continue  # ex.: Preferred Deal -- fora do acordo por clique
        site = str(linha[i_site] or "").strip()
        data = datestr(linha[i_dt])
        if not site or not data:
            continue
        impr = num(linha, "impr")
        clk = num(linha, "clk")
        if impr <= 0 and clk <= 0:
            continue  # linha sem entrega nenhuma -> ignora
        k = (site, camp, tipo, data)
        o = agg.get(k)
        if o is None:
            o = agg[k] = {"impr": 0.0, "clk": 0.0, "v": 0.0, "me": 0.0, "el": 0.0}
        o["impr"] += impr
        o["clk"] += clk
        o["v"] += num(linha, "v")
        o["me"] += num(linha, "me")
        o["el"] += num(linha, "el")

    itens = [{"site": k[0], "camp": k[1], "tipo": k[2], "data": k[3],
              "impr": int(round(v["impr"])), "clk": int(round(v["clk"])),
              "v": int(round(v["v"])), "me": int(round(v["me"])), "el": int(round(v["el"]))}
             for k, v in agg.items()]
    n_sites = len({it["site"] for it in itens})
    _SR_DIAG.append(f"[rede {network_code}] OK -> {n_vistas_nome} linha(s) com nome batendo 'sportradar' (config), "
                     f"{len(itens)} linha(s) final(is) apos excluir tipo(s) configurado(s) e linhas sem entrega, "
                     f"em {n_sites} ad unit(s) top-level ({ini.isoformat()} a {fim.isoformat()}).")
    return itens, ini, fim


def _sportradar_acumula(alvo, it, rev):
    """Soma um item (linha ja filtrada de fetch_sportradar_rede, com receita
    ja calculada) dentro de um agregado {"porDia":{}, "porDiaFormato":{},
    "formatosVistos": set()}. Usado tanto pro agregado POR AD UNIT quanto
    pro agregado da REDE INTEIRA (fallback pra sites sem root_ad_unit_id/
    root_ad_unit_nome no sites.xlsx -- ver bloco SportRadar em build())."""
    cell = alvo["porDia"].setdefault(it["data"], {
        "revenue": 0.0, "impressions": 0, "clicks": 0, "viewable": 0, "measuravel": 0, "elegivel": 0,
    })
    cell["revenue"] += rev
    cell["impressions"] += it["impr"]
    cell["clicks"] += it["clk"]
    cell["viewable"] += it["v"]
    cell["measuravel"] += it["me"]
    cell["elegivel"] += it["el"]
    fcell = alvo["porDiaFormato"].setdefault(it["data"], {}).setdefault(it["camp"], {
        "revenue": 0.0, "impressions": 0, "clicks": 0,
    })
    fcell["revenue"] += rev
    fcell["impressions"] += it["impr"]
    fcell["clicks"] += it["clk"]
    if rev or it["impr"]:
        alvo["formatosVistos"].add(it["camp"])


def _sportradar_arredonda(alvo):
    for _c in alvo["porDia"].values():
        _c["revenue"] = round(_c["revenue"], 4)
    for _fmts in alvo["porDiaFormato"].values():
        for _c2 in _fmts.values():
            _c2["revenue"] = round(_c2["revenue"], 4)


_OB_DIAG = []
_DEMFMT_DIAG = []

# ---- DEMANDA POR FORMATO: Open Bidding (canal) + Deals (PREFERRED_DEAL), por ad
# unit FLAT, com metricas p/ eCPM/CTR/VA. Somado no painel com o AdX (seriesD).
_DEMFMT_METR = {
    "impr": "TOTAL_LINE_ITEM_LEVEL_IMPRESSIONS",
    "rev": "TOTAL_LINE_ITEM_LEVEL_CPM_AND_CPC_REVENUE",
    "clk": "TOTAL_LINE_ITEM_LEVEL_CLICKS",
    "v": "TOTAL_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS",
    "me": "TOTAL_ACTIVE_VIEW_MEASURABLE_IMPRESSIONS",
}


def _demfmt_norm(s):
    import unicodedata as _uc
    s = _uc.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if _uc.category(c) != "Mn")
    return "".join(c for c in s.lower() if c.isalnum())


def _demfmt_sitefmt(bc):
    """De 'revistaoeste (id) » anchor (id)' -> (site='revistaoeste', fmt='anchor').
    Sem ' » ' (linha no ad unit RAIZ) -> fmt='__ALL__' (demanda site inteiro)."""
    partes = [p.strip() for p in str(bc).split("»")]
    limpa = lambda t: re.sub(r"\s*\(\d+\)\s*$", "", t).strip()
    site = limpa(partes[0]) if partes else ""
    fmt = limpa(partes[-1]) if len(partes) > 1 else "__ALL__"
    return site, fmt


def fetch_demanda_fmt_rede(client, api_version, network_code, config, base_dir):
    """Devolve ({siteNorm: {fmt: {'ob':{date:m}, 'deal':{date:m}}}}, ini_iso, fim_iso),
    m = {rev,impr,clk,v,me}. Guardado DIA A DIA por formato. OB = canal 'Open
    Bidding'. Deal = MESMO filtro da aba Deals (_deal_incluir por PROPOSAL_NAME).
    NUNCA quebra a geracao."""
    dias = int(config.get("deals_janela_dias", 30) or 30)
    fim = datetime.date.today() - datetime.timedelta(days=1)
    ini = fim - datetime.timedelta(days=dias - 1)
    micros = "rev" in (config.get("report_columns_deals_micros") or ["rev"])
    cols = list(_DEMFMT_METR.values())
    out = {}

    def _num(x):
        try:
            return float(str(x).replace(",", "").strip() or 0)
        except Exception:
            return 0.0

    def _idx(header, enum):
        return next((k for k, h in enumerate(header) if str(h).endswith(enum)), None)

    def _cel(site, fmt, fonte, data):
        z = lambda: {"rev": 0.0, "impr": 0.0, "clk": 0.0, "v": 0.0, "me": 0.0}
        d = out.setdefault(_demfmt_norm(site), {}).setdefault(fmt, {"ob": {}, "deal": {}})
        return d[fonte].setdefault(data, z())

    def _soma(dst, ln, ci):
        for k, e in _DEMFMT_METR.items():
            i = ci.get(k)
            if i is not None and i < len(ln):
                val = _num(ln[i])
                if k == "rev" and micros:
                    val /= 1_000_000
                dst[k] += val

    # ----- Open Bidding por formato (dia a dia) -----
    incluir = [str(x).strip().lower() for x in (config.get("openbidding_canal_incluir") or ["open bidding"])]
    try:
        job = reports.montar_report_job(config, ini, fim,
                                        dimensions=["DATE", "AD_UNIT_ID", "AD_UNIT_NAME", "DEMAND_CHANNEL_NAME"],
                                        columns=cols, ad_unit_view="FLAT")
        csv = reports.rodar_report(client, api_version, job, poll_time_seconds=_poll_seg(config))
        header, linhas = reports.ler_csv(csv)
        i_dt = next((k for k, h in enumerate(header) if str(h).upper().endswith("DATE")), None)
        i_nome = next((k for k, h in enumerate(header) if "AD_UNIT_NAME" in str(h).upper()), None)
        i_canal = next((k for k, h in enumerate(header) if "DEMAND_CHANNEL_NAME" in str(h).upper()), None)
        ci = {k: _idx(header, e) for k, e in _DEMFMT_METR.items()}
        if i_nome is not None and i_dt is not None:
            for ln in linhas:
                if i_canal is not None and i_canal < len(ln):
                    if not any(inc in str(ln[i_canal]).strip().lower() for inc in incluir):
                        continue
                d = datestr(ln[i_dt]) if i_dt < len(ln) else None
                if not d:
                    continue
                site, fmt = _demfmt_sitefmt(ln[i_nome])
                _soma(_cel(site, fmt, "ob", d), ln, ci)
        else:
            _DEMFMT_DIAG.append(f"[rede {network_code}] OB/fmt: sem AD_UNIT_NAME/DATE. Cabecalho: {header}")
    except Exception as e:  # noqa: BLE001
        _DEMFMT_DIAG.append(f"[rede {network_code}] OB/fmt RECUSADO: {type(e).__name__}: {str(e)[:180]}")

    # ----- Deals por formato (dia a dia) -- MESMO filtro da aba Deals -----
    filtrar = bool(config.get("deals_filtro_ativo", False)) and bool(config.get("deals_filtro_nomes"))
    filtro_dim = config.get("deals_filtro_dimensao", "PROPOSAL_NAME") if filtrar else None
    dims_base = ["DATE", "AD_UNIT_ID", "AD_UNIT_NAME", "LINE_ITEM_NAME", "LINE_ITEM_TYPE"]

    def _run_deals(_dims):
        job = reports.montar_report_job(config, ini, fim, dimensions=_dims, columns=cols, ad_unit_view="FLAT")
        return reports.rodar_report(client, api_version, job, poll_time_seconds=_poll_seg(config))

    try:
        dims = dims_base + ([filtro_dim] if filtro_dim and filtro_dim not in dims_base else [])
        try:
            csv = _run_deals(dims)
        except Exception as e_prop:  # noqa: BLE001 -- proposta recusada -> filtra pelo nome do item
            if filtro_dim:
                _DEMFMT_DIAG.append(f"[rede {network_code}] Deals/fmt: proposta recusada ({str(e_prop)[:90]}); filtrando pelo nome do item.")
                filtro_dim = None
                dims = dims_base
                csv = _run_deals(dims)
            else:
                raise
        header, linhas = reports.ler_csv(csv)
        i_dt = next((k for k, h in enumerate(header) if str(h).upper().endswith("DATE")), None)
        i_nome = next((k for k, h in enumerate(header) if "AD_UNIT_NAME" in str(h).upper()), None)
        i_camp = next((k for k, h in enumerate(header) if str(h).upper().endswith("LINE_ITEM_NAME")), None)
        i_prop = _col_por_nome(header, filtro_dim) if filtro_dim else None
        ci = {k: _idx(header, e) for k, e in _DEMFMT_METR.items()}
        if i_nome is not None and i_dt is not None:
            for ln in linhas:
                camp = str(ln[i_camp]).strip() if (i_camp is not None and i_camp < len(ln)) else ""
                # aplica EXATAMENTE o mesmo filtro da aba Deals
                if filtrar:
                    alvo = str(ln[i_prop]) if (i_prop is not None and i_prop < len(ln)) else camp
                    if not _deal_incluir(alvo, config):
                        continue
                elif not camp or camp == "-":
                    continue  # sem filtro: ignora linhas sem item de linha (AdX ROS, "-", etc.)
                d = datestr(ln[i_dt]) if i_dt < len(ln) else None
                if not d:
                    continue
                site, fmt = _demfmt_sitefmt(ln[i_nome])
                _soma(_cel(site, fmt, "deal", d), ln, ci)
        else:
            _DEMFMT_DIAG.append(f"[rede {network_code}] Deals/fmt: sem AD_UNIT_NAME/DATE. Cabecalho: {header}")
    except Exception as e:  # noqa: BLE001
        _DEMFMT_DIAG.append(f"[rede {network_code}] Deals/fmt RECUSADO: {type(e).__name__}: {str(e)[:180]}")

    return out, ini.isoformat(), fim.isoformat()




    """OPEN BIDDING a partir do(s) CSV(s) exportado(s) do Ad Manager (o jeito
    CERTO e confiável). O relatório tem dimensões 'Yield partner', 'Date',
    'Ad unit (top level)' e métricas 'Total impressions', 'Total revenue'.
    Soma todos os yield partners EXCETO os de 'openbidding_excluir' (por padrão
    'DoubleClick Ad Exchange', que é o próprio AdX) e o vazio. Valores em USD.
    Devolve {adUnitTopLevel: {'total': {data: {rev, impr}}}}.

    'openbidding_arquivo' no config pode ser uma PASTA (lê todo *.csv dela) ou um
    arquivo. Assim é só exportar o relatório e jogar o CSV nessa pasta."""
    import csv as _csv
    import glob as _glob
    alvo = str(config.get("openbidding_arquivo") or "ob").strip()
    cam = alvo if os.path.isabs(alvo) else os.path.join(base, alvo)
    arquivos = []
    if os.path.isdir(cam):
        arquivos = sorted(_glob.glob(os.path.join(cam, "*.csv")))
    elif os.path.isfile(cam):
        arquivos = [cam]
    if not arquivos:
        _OB_DIAG.append(f"Sem CSV de Open Bidding em '{alvo}'. Exporte o relatório (Yield partner x Date x Ad unit top level x Total impressions/revenue) e jogue o .csv nessa pasta.")
        return {}
    excl = set(str(x).strip().lower() for x in (config.get("openbidding_excluir") or ["doubleclick ad exchange"]))
    excl.add("")
    out = {}
    n_linhas = 0
    for arq in arquivos:
        try:
            rows = list(_csv.reader(open(arq, encoding="utf-8-sig")))
        except Exception as e:  # noqa: BLE001
            _OB_DIAG.append(f"Falha lendo {os.path.basename(arq)}: {type(e).__name__}: {str(e)[:120]}")
            continue
        # acha a linha de cabeçalho real (tem 'date', 'revenue' e um separador de
        # canal: 'demand channel' OU 'yield partner')
        hi = None
        for i, r in enumerate(rows):
            low = [str(c).strip().lower() for c in r]
            if "date" in low and any("revenue" in c for c in low) and ("demand channel" in low or "yield partner" in low):
                hi = i
                break
        if hi is None:
            _OB_DIAG.append(f"{os.path.basename(arq)}: não achei o cabeçalho (Date/Total revenue/Demand channel|Yield partner).")
            continue
        hdr = [str(c).strip().lower() for c in rows[hi]]

        def _c(*termos):
            for t in termos:
                if t in hdr:
                    return hdr.index(t)
            for k, h in enumerate(hdr):
                if any(t in h for t in termos):
                    return k
            return None
        i_canal = _c("demand channel", "canal de demanda")
        i_yp = _c("yield partner")
        i_dt = _c("date")
        i_ad = _c("ad unit (top level)", "ad unit")
        i_impr = _c("total impressions", "impressions", "impression")
        i_rev = _c("total revenue", "revenue")
        if None in (i_dt, i_ad, i_rev):
            _OB_DIAG.append(f"{os.path.basename(arq)}: faltou coluna (date/adunit/revenue). Cabeçalho: {rows[hi]}")
            continue
        incluir = [str(x).strip().lower() for x in (config.get("openbidding_canal_incluir") or ["open bidding"])]
        idxs = [x for x in (i_canal, i_yp, i_dt, i_ad, i_impr, i_rev) if x is not None]
        mx = max(idxs)
        for r in rows[hi + 1:]:
            if not r or len(r) <= mx:
                continue
            # se tem 'Demand channel', pega SÓ o canal Open Bidding; senão, usa
            # 'Yield partner' e exclui o AdX (compatibilidade com export antigo).
            if i_canal is not None:
                canal = str(r[i_canal] or "").strip().lower()
                if not any(inc in canal for inc in incluir):
                    continue
            elif i_yp is not None:
                if str(r[i_yp] or "").strip().lower() in excl:
                    continue
            ad = str(r[i_ad] or "").strip()
            data = datestr(r[i_dt])
            if not ad or not data:
                continue
            try:
                rev = float(str(r[i_rev]).replace(",", "").strip() or 0)
            except ValueError:
                rev = 0.0
            impr = 0
            if i_impr is not None:
                try:
                    impr = int(float(str(r[i_impr]).replace(",", "").strip() or 0))
                except ValueError:
                    impr = 0
            if rev == 0 and impr == 0:
                continue
            dd = out.setdefault(ad, {}).setdefault("total", {})
            c = dd.get(data)
            if c is None:
                c = dd[data] = {"rev": 0.0, "impr": 0}
            c["rev"] += rev
            c["impr"] += impr
            n_linhas += 1
    if out:
        for ad in out:
            for data in out[ad]["total"]:
                out[ad]["total"][data]["rev"] = round(out[ad]["total"][data]["rev"], 4)
        _OB_DIAG.append(f"Open Bidding (CSV): {len(out)} ad unit(s), {n_linhas} linha(s) de {len(arquivos)} arquivo(s).")
    return out


def fetch_openbidding_diario_rede(client, api_version, network_code, config, base_dir):
    """OPEN BIDDING (Exchange Bidding / yield groups) por DIA e por DISPOSITIVO,
    por ad unit top-level. Best-effort: tenta as combinações de dims/métricas do
    config ('openbidding_tentativas') e usa a 1ª que trouxer dado. NUNCA quebra a
    geração. Devolve {adUnitTopLevel: {device: {data: {rev, impr}}}} em USD, ou {}.
    (Se nada funcionar, rode ferramentas/testar_openbidding_api.py e me mande.)"""
    if not config.get("openbidding_ativo", False):
        return {}
    dias = int(config.get("openbidding_janela_dias", config.get("deals_janela_dias", 30)) or 30)
    fim = datetime.date.today() - datetime.timedelta(days=1)
    ini = fim - datetime.timedelta(days=dias - 1)
    tentativas = config.get("openbidding_tentativas") or []
    view = config.get("deals_ad_unit_view", "TOP_LEVEL")
    for tt in tentativas:
        dims = tt.get("dims") or ["DATE", "AD_UNIT_NAME", "DEVICE_CATEGORY_NAME"]
        c_impr, c_rev = tt.get("impr"), tt.get("rev")
        if not c_impr or not c_rev:
            continue
        try:
            job = reports.montar_report_job(config, ini, fim, dimensions=dims, columns=[c_impr, c_rev], ad_unit_view=view)
            texto_csv = reports.rodar_report(client, api_version, job, poll_time_seconds=_poll_seg(config))
        except Exception as e:  # noqa: BLE001
            _OB_DIAG.append(f"[rede {network_code}] OB dims={dims} cols=[{c_impr},{c_rev}] -> RECUSADO: {str(e)[:180]}")
            continue
        header, linhas = reports.ler_csv(texto_csv)
        i_dt = _col_por_nome(header, "DATE")
        i_site = _col_por_nome(header, "AD_UNIT_NAME")
        i_dev = _col_por_nome(header, "DEVICE_CATEGORY_NAME")
        i_impr = _col_por_nome(header, c_impr)
        i_rev = _col_por_nome(header, c_rev)
        # coluna do CANAL DE DEMANDA -- OBRIGATÓRIA. É ela que separa Open Bidding
        # de Ad Exchange e Ad server. Sem ela, o relatório soma tudo (valor errado,
        # tipo os US$17 mil) -> pula a tentativa.
        canal_dim = tt.get("canal")
        i_canal = _col_por_nome(header, canal_dim) if canal_dim else None
        if canal_dim and i_canal is None:
            _OB_DIAG.append(f"[rede {network_code}] OB: rede não trouxe a coluna do canal de demanda '{canal_dim}' -> pulando (evita somar AdX/Ad server). Cabecalho: {header}")
            continue
        if i_dt is None or i_site is None or i_impr is None or i_rev is None:
            _OB_DIAG.append(f"[rede {network_code}] OB aceitou mas faltou coluna. Cabecalho: {header}")
            continue
        incluir = [str(x).strip().lower() for x in (config.get("openbidding_canal_incluir") or ["open bidding"])]
        canais_vistos = set()
        parceiros_ok = set()
        out = {}
        for ln in linhas:
            if i_site >= len(ln) or i_dt >= len(ln):
                continue
            if i_canal is not None:
                canal = str(ln[i_canal] or "").strip() if i_canal < len(ln) else ""
                canais_vistos.add(canal)
                cl = canal.lower()
                if not any(inc in cl for inc in incluir):
                    continue  # só o canal Open Bidding entra
                parceiros_ok.add(canal)
            site = str(ln[i_site] or "").strip()
            data = datestr(ln[i_dt])
            if not site or not data:
                continue
            impr = pnum(ln[i_impr], 0.0) if i_impr < len(ln) else 0.0
            rev = pnum(ln[i_rev], 0.0) if i_rev < len(ln) else 0.0
            if tt.get("micros"):
                rev /= 1_000_000
            if impr <= 0 and rev <= 0:
                continue
            dev = DEVMAP.get(str(ln[i_dev] or "").strip().lower(), "outros") if (i_dev is not None and i_dev < len(ln)) else "outros"
            alvo = out.setdefault(site, {})
            for devout in _devs_saida(dev):
                dd = alvo.setdefault(devout, {})
                c = dd.get(data)
                if c is None:
                    c = dd[data] = {"rev": 0.0, "impr": 0.0}
                c["rev"] += rev
                c["impr"] += impr
        if out:
            for site in out:
                for devout in out[site]:
                    for data in out[site][devout]:
                        cc = out[site][devout][data]
                        cc["rev"] = round(cc["rev"], 4)
                        cc["impr"] = int(round(cc["impr"]))
            _OB_DIAG.append(f"[rede {network_code}] OB OK -> {len(out)} ad unit(s) | canal(is) somado(s): {sorted(parceiros_ok)[:6]} | dims={dims}.")
            return out
        _OB_DIAG.append(f"[rede {network_code}] OB: nenhuma linha do canal Open Bidding (dims={dims}). Canais que a rede devolveu: {sorted(canais_vistos)[:10]} -- se o nome do canal for outro, ajuste 'openbidding_canal_incluir' no config.json.")
    return {}


def fetch_admanager(site_row, config, base_dir):
    client, api_version, network_code, ad_unit_ids, id_para_nome, moeda = _resolver_alvo(site_row, config, base_dir)
    # O relatorio DIARIO e o unico essencial -- se ele falhar, o site nao sai.
    acc, dates = _fetch_diario(client, api_version, network_code, ad_unit_ids, id_para_nome, config, site_row)
    # Hora e Tamanhos sao "bonus": QUALQUER erro neles NUNCA pode derrubar o
    # site (guardas extras alem das internas -- cinto e suspensorio).
    try:
        intraday = _fetch_hora(client, api_version, network_code, ad_unit_ids, id_para_nome, config, site_row)
    except Exception as e:  # noqa: BLE001
        print(f"  ! aviso: relatorio POR HORA derrubou uma excecao inesperada em {site_row.get('id')} ({type(e).__name__}: {e}); segue sem intradia.")
        intraday = {"temHora": False, "janela_dias": int(config.get("hora_janela_dias", 0) or 0), "datasHora": [], "horasDia": {}}
    try:
        sizes = _fetch_sizes(client, api_version, network_code, ad_unit_ids, id_para_nome, config, site_row)
    except Exception as e:  # noqa: BLE001
        print(f"  ! aviso: relatorio por TAMANHO derrubou uma excecao inesperada em {site_row.get('id')} ({type(e).__name__}: {e}); segue sem o bloco de tamanhos.")
        sizes = {"sizesD": {}, "sizesInfo": {}}
    site_domain = (site_row.get("dominio") or "").strip()
    return acc, dates, site_domain, intraday, moeda, sizes


# ---------- GA (segue manual por enquanto, fora do escopo da automacao GAM) ----------
def parse_ga(path):
    if not path or not os.path.exists(path):
        return {}
    wb = load_workbook(path, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = [r for r in ws.iter_rows(values_only=True)]
    hi = None
    hdr = []
    for i, r in enumerate(rows):
        vals = [str(c).strip().lower() if c is not None else "" for c in r]
        if "date" in vals and "views" in vals:
            hi = i
            hdr = vals
            break
    if hi is None:
        return {}
    ci_d = hdr.index("date")
    ci_dev = hdr.index("device category") if "device category" in hdr else 1
    ci_v = hdr.index("views")
    pv = {}
    for r in rows[hi + 1:]:
        if r[ci_d] is None:
            continue
        d = datestr(r[ci_d])
        if not d:
            continue
        dev = DEVMAP.get(str(r[ci_dev] or "").strip().lower(), "outros")
        v = int(pnum(r[ci_v]))
        for dd in (dev, "total"):
            pv.setdefault(dd, {})
            pv[dd][d] = pv[dd].get(d, 0) + v
    return pv


def parse_ga_api(client, property_id, data_ini, data_fim):
    """Busca pageviews por dia/dispositivo DIRETO na API do GA4 (Data API),
    pra property_id no periodo [data_ini, data_fim] (strings 'AAAA-MM-DD').
    Devolve no MESMO formato que parse_ga() devolve pro .xlsx manual --
    {dispositivo: {'AAAA-MM-DD': views}}, com 'total' somando tudo -- assim
    build_site() usa o resultado igual, nao importa se veio da API ou do
    arquivo. 'client' e' o BetaAnalyticsDataClient ja autenticado (ver
    ga_lib.auth.obter_client). Deixa qualquer excecao subir -- quem chama
    decide o que fazer (cair pro .xlsx manual, avisar e seguir, etc.), igual
    ao padrao ja usado com gam_lib.reports.rodar_report()."""
    req = ga_reports.montar_request(property_id, data_ini, data_fim)
    resp = ga_reports.rodar_report(client, req)
    pv = {}
    for row in resp.rows:
        d = datestr(row.dimension_values[0].value)  # dimensao 'date' -> 'AAAAMMDD'
        if not d:
            continue
        dev_raw = str(row.dimension_values[1].value or "").strip().lower()  # dimensao 'deviceCategory'
        dev = DEVMAP.get(dev_raw, "outros")
        v = int(pnum(row.metric_values[0].value))
        for dd in (dev, "total"):
            pv.setdefault(dd, {})
            pv[dd][d] = pv[dd].get(d, 0) + v
    return pv


# ---------- Classificacao de URLs: Home / Editoria / Materia / Outros ----------
# Overrides por site (chave = id do site em sites.xlsx). Deixe vazio pra usar o
# padrao. Ex.: 'meusite': {'editoria': ['/secao/'], 'materia_regex': [r'/noticia/']}
REGRAS_URL_POR_SITE = {}
_EDITORIA_PADRAO = ["/category/", "/categoria/", "/editoria/", "/editorias/",
                    "/section/", "/secao/", "/tag/", "/tags/", "/assunto/", "/temas/"]
_MATERIA_RX_PADRAO = [r"/\d{4}/\d{2}/", r"\.html", r"\.htm", r"/\d{4}/\d{2}/\d{2}/"]


def classify_url(path, site=None):
    if not path:
        return "outros"
    p = str(path).split("?")[0].split("#")[0].strip()
    p = re.sub(r"^https?://[^/]+", "", p)
    if not p.startswith("/"):
        p = "/" + p
    if len(p) > 1:
        p = p.rstrip("/")
    p = p.lower()
    if p in ("", "/"):
        return "home"
    reg = REGRAS_URL_POR_SITE.get(site, {})
    for seg in reg.get("editoria", _EDITORIA_PADRAO):
        if seg in p:
            return "editoria"
    for rx in reg.get("materia_regex", _MATERIA_RX_PADRAO):
        if re.search(rx, p):
            return "materia"
    segs = [s for s in p.split("/") if s]
    return "materia" if len(segs) >= 2 else "outros"


def parse_ga_pages_api(client, property_id, data_ini, data_fim, site_id=None):
    """Segundo relatorio do GA4: por dia + pagePath, com views + tempo de
    engajamento + sessoes. Classifica cada URL e devolve DOIS dicts:
      pages  = {categoria: {'AAAA-MM-DD': views}}                (grafico de URLs)
      tempo  = {categoria: {'AAAA-MM-DD': {'eng': seg, 'sess': n}}}  (tempo por tipo)
    So categorias com dados. Deixa excecao subir pra quem chama decidir."""
    req = ga_reports.montar_request_pages(property_id, data_ini, data_fim)
    resp = ga_reports.rodar_report(client, req)
    pages, tempo = {}, {}
    for row in resp.rows:
        d = datestr(row.dimension_values[0].value)      # 'date'
        if not d:
            continue
        path = row.dimension_values[1].value            # 'pagePath'
        cat = classify_url(path, site_id)
        v = int(pnum(row.metric_values[0].value))                       # screenPageViews
        eng = pnum(row.metric_values[1].value) if len(row.metric_values) > 1 else 0.0  # userEngagementDuration (s)
        sess = int(pnum(row.metric_values[2].value)) if len(row.metric_values) > 2 else 0  # sessions
        pages.setdefault(cat, {})
        pages[cat][d] = pages[cat].get(d, 0) + v
        t = tempo.setdefault(cat, {})
        cell = t.setdefault(d, {"eng": 0.0, "sess": 0})
        cell["eng"] += eng
        cell["sess"] += sess
    pages = {k: val for k, val in pages.items() if val}
    tempo = {k: {dd: {"eng": round(c["eng"], 1), "sess": c["sess"]} for dd, c in val.items()}
             for k, val in tempo.items() if val}
    return pages, tempo


def parse_ga_sessao_api(client, property_id, data_ini, data_fim):
    """Duracao media de sessao do site inteiro, por dia. Devolve
    {'AAAA-MM-DD': {'dur': segundos_media_do_dia, 'sess': sessoes}} -- o painel
    faz a media ponderada por sessoes no periodo selecionado."""
    req = ga_reports.montar_request_sessao(property_id, data_ini, data_fim)
    resp = ga_reports.rodar_report(client, req)
    out = {}
    for row in resp.rows:
        d = datestr(row.dimension_values[0].value)
        if not d:
            continue
        dur = pnum(row.metric_values[0].value)          # averageSessionDuration (s)
        sess = int(pnum(row.metric_values[1].value))    # sessions
        out[d] = {"dur": round(dur, 1), "sess": sess}
    return out


# ---------- Parceiros de monetizacao (aba "Parceiros") ----------
# Campos "conhecidos" de /analytics/data da Seedtag, confirmados na doc
# oficial (pagina "Summary & paginated data" do integration guide). O JSON de
# exemplo da doc vem TRUNCADO ("...") depois desses campos -- pode haver mais
# campos na resposta real. Por isso _seedtag_extrair_linha() guarda qualquer
# campo desconhecido dentro de "extra", em vez de descartar -- ver
# ferramentas/testar_seedtag_api.py, que mostra o JSON cru real ANTES de
# confiarmos 100% nesse parsing (mesma licao do incidente 'adxReportCurrency'
# de 11/08/2026: nao finalizar parsing sem ver o dado real).
_SEEDTAG_CAMPOS_CONHECIDOS = {
    "date", "revenue", "impressions", "clicks", "cpm", "rpm",
    "publisher", "publisherId", "publisherName", "adRequests", "fillRate", "currency",
}


def _seedtag_valor_moeda(campo):
    """'revenue' pode vir como numero direto OU como lista [{'currency','value',
    'payer'}] (formato usado em /analytics/summary, pela doc). Aceita os dois
    formatos e devolve (valor_total, moeda_ou_None)."""
    if campo is None:
        return 0.0, None
    if isinstance(campo, (int, float)):
        return float(campo), None
    if isinstance(campo, list):
        total = 0.0
        moeda = None
        for item in campo:
            if isinstance(item, dict):
                total += pnum(item.get("value"))
                moeda = moeda or item.get("currency")
        return total, moeda
    return pnum(campo), None


def parse_seedtag_api(token, publisher_id, data_ini, data_fim):
    """Busca receita/impressoes/cliques (+ cpm/rpm/fillRate/adRequests, todos
    confirmados na doc oficial) por dia na API da Seedtag, pra um
    publisher_id no periodo [data_ini, data_fim] ('AAAA-MM-DD'). cpm/rpm/
    fillRate sao devolvidos por dia como MEDIA PONDERADA POR IMPRESSOES
    (o front-end pondera de novo ao somar varios dias do periodo). Devolve
    {'porDia': {'AAAA-MM-DD': {...}}, 'moeda': 'USD'/'BRL'/etc,
    'publisherId': str, 'publisherName': str|None, 'camposExtras':
    [nomes de campos nao documentados vistos na resposta real, se algum]}.
    Deixa qualquer excecao subir -- quem chama (build_site) decide o que
    fazer (so aquele parceiro fica sem card, o resto do site continua
    normal)."""
    linhas = seedtag_reports.analytics_data(
        token, data_ini, data_fim, publisher_ids=[publisher_id], temporal_aggregation="day",
    )
    por_dia = {}
    moeda = None
    publisher_nome = None
    campos_extras = set()
    for row in linhas:
        if not isinstance(row, dict):
            continue
        d = datestr(row.get("date"))
        if not d:
            continue
        cell = por_dia.setdefault(d, {
            "revenue": 0.0, "impressions": 0, "clicks": 0, "adRequests": 0,
            "_cpmSoma": 0.0, "_cpmPeso": 0, "_rpmSoma": 0.0, "_rpmPeso": 0,
            "_fillSoma": 0.0, "_fillPeso": 0, "extra": {},
        })
        rev, moeda_linha = _seedtag_valor_moeda(row.get("revenue"))
        cell["revenue"] += rev
        moeda = moeda or moeda_linha or row.get("currency")
        impr = int(pnum(row.get("impressions")))
        cell["impressions"] += impr
        cell["clicks"] += int(pnum(row.get("clicks")))
        cell["adRequests"] += int(pnum(row.get("adRequests")))
        peso = impr or 1  # pondera por impressoes; sem impressoes, peso 1 (nao descarta o dia)
        if row.get("cpm") is not None:
            cell["_cpmSoma"] += pnum(row.get("cpm")) * peso
            cell["_cpmPeso"] += peso
        if row.get("rpm") is not None:
            cell["_rpmSoma"] += pnum(row.get("rpm")) * peso
            cell["_rpmPeso"] += peso
        if row.get("fillRate") is not None:
            cell["_fillSoma"] += pnum(row.get("fillRate")) * peso
            cell["_fillPeso"] += peso
        publisher_nome = publisher_nome or row.get("publisherName")
        for k, v in row.items():
            if k not in _SEEDTAG_CAMPOS_CONHECIDOS:
                cell["extra"][k] = v
                campos_extras.add(k)
    for cell in por_dia.values():
        cell["cpm"] = (cell["_cpmSoma"] / cell["_cpmPeso"]) if cell["_cpmPeso"] else None
        cell["rpm"] = (cell["_rpmSoma"] / cell["_rpmPeso"]) if cell["_rpmPeso"] else None
        cell["fillRate"] = (cell["_fillSoma"] / cell["_fillPeso"]) if cell["_fillPeso"] else None
        if not cell["adRequests"]:
            cell["adRequests"] = None
        for k in ("_cpmSoma", "_cpmPeso", "_rpmSoma", "_rpmPeso", "_fillSoma", "_fillPeso"):
            cell.pop(k, None)
    return {
        "porDia": por_dia, "moeda": moeda or "USD",
        "publisherId": publisher_id, "publisherName": publisher_nome,
        "camposExtras": sorted(campos_extras),
    }


# groupBy=adunitType confirmado como VALOR DE PARAMETRO na doc oficial (ver
# seedtag_lib/reports.py). O que a doc NAO diz e' qual CHAVE a resposta usa
# pra devolver esse valor de volta em cada linha -- tentamos os nomes mais
# prováveis, na ordem, e SO agrupamos por formato se um deles bater em pelo
# menos uma linha. Ajustar essa lista se ferramentas/testar_seedtag_api.py
# revelar o nome real.
_SEEDTAG_CAMPOS_FORMATO_CANDIDATOS = (
    "adunitType", "adUnitType", "adUnitTypeName", "adUnitTypeId", "productName", "product",
)


def _seedtag_extrai_formato(row):
    """Acha o campo que identifica o formato/adunitType numa linha de
    /analytics/data com groupBy=adunitType. Devolve None se nenhum dos
    candidatos bateu (quem chama decide nao usar essa linha em vez de
    arriscar agrupar tudo num balaio so)."""
    for k in _SEEDTAG_CAMPOS_FORMATO_CANDIDATOS:
        if row.get(k) is not None:
            return str(row[k])
    return None


def parse_seedtag_formatos(token, publisher_id, data_ini, data_fim):
    """Busca receita/impressoes/cliques por dia E POR FORMATO (adunitType) na
    API da Seedtag -- chamada SEPARADA de parse_seedtag_api (mesmo
    publisher/periodo, so acrescenta groupBy). Devolve
    {'porDiaFormato': {'AAAA-MM-DD': {'<formato>': {revenue, impressions,
    clicks}}}, 'formatosVistos': [nomes distintos, ordenados]}. Se a API nao
    devolver NENHUM campo reconhecivel de formato em nenhuma linha, devolve
    tudo vazio (o card do parceiro simplesmente nao mostra a divisao por
    formato, em vez de arriscar). Deixa exception de rede/HTTP subir -- quem
    chama decide (essa chamada e' um extra, falha nela nao pode derrubar o
    card principal do parceiro)."""
    linhas = seedtag_reports.analytics_data(
        token, data_ini, data_fim, publisher_ids=[publisher_id],
        temporal_aggregation="day", group_by="adunitType",
    )
    por_dia_fmt = {}
    formatos_vistos = set()
    teve_linha_sem_formato = False
    for row in linhas:
        if not isinstance(row, dict):
            continue
        d = datestr(row.get("date"))
        if not d:
            continue
        fmt = _seedtag_extrai_formato(row)
        if fmt is None:
            teve_linha_sem_formato = True
            continue
        formatos_vistos.add(fmt)
        cell = por_dia_fmt.setdefault(d, {}).setdefault(fmt, {"revenue": 0.0, "impressions": 0, "clicks": 0})
        rev, _ = _seedtag_valor_moeda(row.get("revenue"))
        cell["revenue"] += rev
        cell["impressions"] += int(pnum(row.get("impressions")))
        cell["clicks"] += int(pnum(row.get("clicks")))
    if teve_linha_sem_formato and not formatos_vistos:
        print(
            "  ! aviso: Seedtag devolveu linhas de /analytics/data (groupBy=adunitType) sem nenhum "
            "campo de formato reconhecivel (tentei: " + ", ".join(_SEEDTAG_CAMPOS_FORMATO_CANDIDATOS) + "). "
            "Divisao por formato fica oculta pra esse publisher -- veja o JSON cru em "
            "ferramentas/testar_seedtag_api.py e avise o nome do campo real que apareceu."
        )
        return {"porDiaFormato": {}, "formatosVistos": []}
    return {"porDiaFormato": por_dia_fmt, "formatosVistos": sorted(formatos_vistos)}


_MEMBRANA_PRODUTOS = (
    # (chave do endpoint em membrana_lib.reports, label mostrado como
    # "formato" na aba Parceiros -- reaproveita a mesma UI generica de
    # porDiaFormato/formatosVistos ja construida pra Seedtag)
    ("monetization", "Monetization (vídeo)"),
    ("interstitial", "Interstitial"),
    ("adwall", "Adwall"),
)


def fetch_membrana_conta(access_key, data_ini, data_fim):
    """Busca os 3 produtos da Membrana (monetization/interstitial/adwall) +
    /billing/report UMA VEZ SO pra CONTA TODA -- nenhum desses endpoints
    aceita filtro de site (ver membrana_lib/reports.py), entao pedir de novo
    por site so repetiria a mesma resposta inteira. Quem chama (build())
    guarda o resultado e cada site filtra localmente depois
    (parse_membrana_site). Deixa exception de monetization/interstitial/
    adwall subir (sao os dados principais -- se falharem, build() decide
    desativar a Membrana pra rodada toda); /billing/report e' tratado como
    extra (falhar nele so tira o numero 'final', mantendo o provisorio por
    produto)."""
    por_produto = {}
    for chave, _label in _MEMBRANA_PRODUTOS:
        fn = getattr(membrana_reports, f"{chave}_daily")
        por_produto[chave] = fn(access_key, str(data_ini), str(data_fim))
    try:
        billing = membrana_reports.billing_report(access_key)
    except Exception as e:  # noqa: BLE001 -- extra opcional, nao pode derrubar o resto
        print(f"  ! aviso: Membrana /billing/report falhou ({e}); numero final (net_billing) "
              f"fica indisponivel, mas a receita provisoria por produto continua normal.")
        billing = []
    return {"porProduto": por_produto, "billing": billing}


def parse_membrana_site(dados_conta, membrana_site_id, data_ini, data_fim):
    """Filtra os dados JA BUSCADOS pra conta toda (fetch_membrana_conta) pro
    'site_id' desse site especifico, dentro de [data_ini, data_fim]. Monta a
    MESMA forma generica ja usada por qualquer parceiro (porDia/
    porDiaFormato/formatosVistos/moeda) -- ver comentario no topo de
    template_inner.txt (buildParceiros) -- tratando cada PRODUTO
    (monetization/interstitial/adwall) como um "formato", pra reusar o
    card/grafico de formatos ja feito pra Seedtag sem tocar no front-end.
    Devolve None se esse site nao tiver nenhuma linha no periodo (sem card
    pra ele, sem erro)."""
    membrana_site_id = str(membrana_site_id or "").strip()
    if not membrana_site_id or not dados_conta:
        return None
    ini_s, fim_s = str(data_ini), str(data_fim)
    por_dia = {}
    por_dia_fmt = {}
    formatos_vistos = set()
    for chave, label in _MEMBRANA_PRODUTOS:
        linhas = (dados_conta.get("porProduto") or {}).get(chave) or []
        for row in linhas:
            if not isinstance(row, dict) or str(row.get("site_id") or "").strip() != membrana_site_id:
                continue
            d = datestr(row.get("date"))
            if not d or d < ini_s or d > fim_s:
                continue
            rev = pnum(row.get("revenue"))
            impr = int(pnum(row.get("impressions")))
            clk = int(pnum(row.get("clicks"))) if row.get("clicks") is not None else 0
            cell = por_dia.setdefault(d, {"revenue": 0.0, "impressions": 0, "clicks": 0})
            cell["revenue"] += rev
            cell["impressions"] += impr
            cell["clicks"] += clk
            fcell = por_dia_fmt.setdefault(d, {}).setdefault(label, {"revenue": 0.0, "impressions": 0, "clicks": 0})
            fcell["revenue"] += rev
            fcell["impressions"] += impr
            fcell["clicks"] += clk
            if rev or impr:
                formatos_vistos.add(label)
    if not por_dia:
        return None
    # MOEDA: a doc oficial NAO diz em que moeda vem 'revenue' de
    # monetization/interstitial/adwall (so /billing/report tem um campo
    # 'local_currency' explicito). Ate confirmar com a Membrana/Joao, ASSUMIMOS
    # aqui a mesma moeda que /billing/report reporta pra esse MESMO site_id
    # (mesma conta/site, e' razoavel supor a mesma moeda local em tudo) -- mas
    # isso e' uma SUPOSICAO, marcada explicitamente com 'moedaConfirmada:
    # False' pra nunca ficar escondida. Se esse site ainda nao tiver nenhuma
    # linha em /billing/report, moeda fica None (o painel NAO tenta converter
    # nem finge saber -- ver _converter_parceiro_moeda) em vez de arriscar
    # assumir errado.
    moeda = None
    net_billing_total = 0.0
    net_billing_moeda = None
    for row in (dados_conta.get("billing") or []):
        if not isinstance(row, dict) or str(row.get("site_id") or "").strip() != membrana_site_id:
            continue
        d = datestr(row.get("date"))
        lc = row.get("local_currency")
        if lc and moeda is None:
            moeda = str(lc).strip().upper()
        if d and ini_s <= d <= fim_s and row.get("net_billing_in_local_currency") is not None:
            net_billing_total += pnum(row.get("net_billing_in_local_currency"))
            net_billing_moeda = net_billing_moeda or (str(lc).strip().upper() if lc else None)
    return {
        "porDia": por_dia, "porDiaFormato": por_dia_fmt,
        "formatosVistos": sorted(formatos_vistos),
        "moeda": moeda, "moedaConfirmada": False,
        "siteId": membrana_site_id,
        "billingFinal": (round(net_billing_total, 2) if net_billing_moeda else None),
        "billingFinalMoeda": net_billing_moeda,
    }


def parse_sportradar_site(dados_resolvidos, data_ini, data_fim, valor_por_clique, janela_dias,
                           ad_unit_label="", rede_inteira=False):
    """Filtra os dados JA AGREGADOS e JA RESOLVIDOS pra este site (montados em
    build() -- ou o agregado de UM ad unit top-level, ou, quando o site nao
    tem root_ad_unit_id/root_ad_unit_nome no sites.xlsx (rede exclusiva desse
    site, ver docstring do arquivo), o agregado da REDE INTEIRA) dentro de
    [data_ini, data_fim]. Monta a MESMA forma generica ja usada por qualquer
    parceiro (porDia/porDiaFormato/formatosVistos/moeda) -- aqui "formato" =
    item de linha (LINE_ITEM_NAME) da SportRadar, ja que um publisher pode
    ter mais de um (pedido do Joao) -- reaproveita o card/grafico de formatos
    ja feito pra Seedtag sem tocar no front-end.

    Moeda e' SEMPRE 'USD' e SEMPRE confirmada: a receita e' CALCULADA aqui
    (cliques x valor_por_clique, um valor fixo combinado com a SportRadar),
    nunca lida do GAM -- entao nao ha ambiguidade de moeda de origem pra
    resolver (ao contrario da Membrana, que depende do que a API devolve).

    'janela_dias' e' repassado no retorno (campo 'janelaDias') so pra
    informar o front-end de que este parceiro NAO cobre o ano inteiro como
    Seedtag/Membrana (o relatorio do GAM que alimenta a SportRadar so busca
    os ultimos N dias -- ver nota em config.json > sportradar_janela_dias) --
    o card mostra um aviso quando o periodo selecionado no topo for mais
    largo que isso, em vez de deixar a lacuna passar em silencio.

    Devolve None se esse site nao tiver nenhum dia com dado no periodo."""
    d = dados_resolvidos
    if not d:
        return None
    ini_s, fim_s = str(data_ini), str(data_fim)
    por_dia = {dt: dict(v) for dt, v in d["porDia"].items() if ini_s <= dt <= fim_s}
    if not por_dia:
        return None
    por_dia_fmt = {dt: {f: dict(v) for f, v in fmts.items()}
                   for dt, fmts in d["porDiaFormato"].items() if ini_s <= dt <= fim_s}
    formatos_no_periodo = sorted({f for fmts in por_dia_fmt.values() for f in fmts.keys()})
    return {
        "porDia": por_dia, "porDiaFormato": por_dia_fmt,
        "formatosVistos": formatos_no_periodo,
        "moeda": "USD", "moedaConfirmada": True,
        "adUnit": ad_unit_label,
        "adUnitRedeInteira": bool(rede_inteira),
        "valorPorClique": valor_por_clique,
        "janelaDias": janela_dias,
    }


def _converter_parceiro_moeda(dados, moeda_alvo):
    """Converte a receita (porDia e porDiaFormato) de um parceiro pra
    'moeda_alvo' usando a cotacao do dia (fx_lib.rates). Se a cotacao
    falhar (rede/API de cambio fora), a moeda de origem ja for a mesma do
    alvo, OU a moeda de origem for desconhecida (dados.moeda vazio/None --
    ex.: Membrana sem nenhuma linha de /billing/report ainda pra confirmar),
    devolve 'dados' sem mexer -- o parceiro mostra na moeda original (ou sem
    moeda confirmada), nunca trava o painel por causa disso e NUNCA assume
    uma moeda de origem que nao foi realmente confirmada (evitaria multiplicar
    pela cotacao errada). Quando converte, marca 'moedaOriginal'/'taxaCambio'
    no dict (transparencia -- mesma filosofia de nunca esconder o que foi
    calculado/estimado usada no resto do parceiro, ex.: 'Fill (estimado)')."""
    moeda_alvo = (moeda_alvo or "").strip().upper()
    moeda_origem = (dados.get("moeda") or "").strip().upper()
    if not moeda_alvo or not moeda_origem or moeda_alvo == moeda_origem or not FX_LIB_OK:
        return dados
    taxa = fx_rates.obter_taxa(moeda_origem, moeda_alvo)
    if not taxa:
        print(f"  ! aviso: nao consegui buscar a cotacao {moeda_origem}->{moeda_alvo} "
              f"(rede ou API de cambio fora do ar); parceiro fica na moeda original ({moeda_origem}).")
        return dados
    for cell in (dados.get("porDia") or {}).values():
        if cell.get("revenue") is not None:
            cell["revenue"] = cell["revenue"] * taxa
    for dia in (dados.get("porDiaFormato") or {}).values():
        for cell in dia.values():
            if cell.get("revenue") is not None:
                cell["revenue"] = cell["revenue"] * taxa
    dados["moedaOriginal"] = moeda_origem
    dados["taxaCambio"] = round(taxa, 6)
    dados["moeda"] = moeda_alvo
    return dados


# ---------- Recirculacao (GA4 por UTM, export "free form") ----------
def parse_recirc(path):
    """Le um export do GA4 (free form) com recirculacao por UTM. O nome do
    FORMATO vem numa das colunas de UTM (Session medium/campaign/content) -- a
    gente detecta qual coluna tem os nomes de formato (a que tem mais valores
    reais, ignorando '(not set)'/'(direct)'). Devolve por formato: views,
    usuarios, engajamento e tempo medio, e a serie por data."""
    if not path or not os.path.exists(path):
        return {}
    wb = load_workbook(path, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = [r for r in ws.iter_rows(values_only=True)]
    hi = None
    hdr = []
    for i, r in enumerate(rows):
        vals = [str(c).strip().lower() if c is not None else "" for c in r]
        if "date" in vals and "views" in vals:
            hi = i
            hdr = vals
            break
    if hi is None:
        return {}

    def ci(name):
        return hdr.index(name) if name in hdr else None

    ci_date, ci_views = ci("date"), ci("views")
    ci_users = ci("total users")
    ci_eng = ci("engagement rate")
    ci_time = ci("average engagement time per session")
    if ci_date is None or ci_views is None:
        return {}
    ignora = {"(not set)", "(direct)", "(none)", "(organic)", "(referral)", ""}
    cand = [ci("session medium"), ci("session campaign"), ci("session content"), ci("session source")]
    cand = [c for c in cand if c is not None]

    def score(c):
        vals = set()
        for r in rows[hi + 1:]:
            if r and c < len(r) and r[c]:
                v = str(r[c]).strip().lower()
                if v not in ignora:
                    vals.add(v)
        return len(vals)

    ci_fmt = max(cand, key=score) if cand else None
    if ci_fmt is None:
        return {}

    fmts = {}
    for r in rows[hi + 1:]:
        if not r or ci_date >= len(r) or not r[ci_date]:
            continue  # pula "Grand total" (sem data)
        d = datestr(r[ci_date])
        if not d:
            continue
        fmt = str(r[ci_fmt]).strip() if ci_fmt < len(r) and r[ci_fmt] else ""
        if not fmt or fmt.lower() in ignora:
            continue
        views = int(pnum(r[ci_views])) if ci_views < len(r) else 0
        users = pnum(r[ci_users]) if ci_users is not None and ci_users < len(r) else 0
        eng = pnum(r[ci_eng]) if ci_eng is not None and ci_eng < len(r) else 0
        tim = pnum(r[ci_time]) if ci_time is not None and ci_time < len(r) else 0
        f = fmts.setdefault(fmt, {"byDate": {}, "views": 0, "users": 0.0, "engw": 0.0, "timew": 0.0})
        f["byDate"][d] = f["byDate"].get(d, 0) + views
        f["views"] += views
        f["users"] += users
        f["engw"] += eng * views       # engajamento ponderado por views
        f["timew"] += tim * users      # tempo medio ponderado por usuarios
    out = {}
    for fmt, f in fmts.items():
        out[fmt] = {
            "byDate": f["byDate"],
            "views": int(f["views"]),
            "users": int(round(f["users"])),
            "eng": round(f["engw"] / f["views"] * 100, 1) if f["views"] else 0,
            "tempo": round(f["timew"] / f["users"], 0) if f["users"] else 0,
        }
    return {"temRecirc": bool(out), "formatos": out}


def build_site(site_row, config, base_dir, ga_path, ajuste, recirc_path="", ga_client=None, ga_property_id="", site_id="",
                seedtag_token=None, seedtag_publisher_id="", membrana_dados_conta=None, membrana_site_id="",
                sportradar_dados_site=None, sportradar_ad_unit_label="", sportradar_rede_inteira=False):
    acc, dates, site_domain, intraday, moeda, sizes = fetch_admanager(site_row, config, base_dir)
    pvraw = {}
    pagesraw = {}
    pagestime = {}
    sessraw = {}
    ga_property_id = str(ga_property_id or "").strip()
    if ga_client and ga_property_id and dates:
        try:
            pvraw = parse_ga_api(ga_client, ga_property_id, dates[0], dates[-1])
            print(f"  + GA4 via API (property {ga_property_id}): {sum(len(v) for v in pvraw.values())} ponto(s)")
        except Exception as e:  # noqa: BLE001 -- API do GA falhar aqui nao pode derrubar o site
            print(f"  ! aviso: GA4 via API falhou pra property {ga_property_id} ({e}); usando arquivo manual (ga_arquivo) se houver.")
            pvraw = {}
        try:
            pagesraw, pagestime = parse_ga_pages_api(ga_client, ga_property_id, dates[0], dates[-1], site_id)
            if pagesraw:
                tot_pg = sum(sum(v.values()) for v in pagesraw.values())
                print(f"  + GA4 paginas por tipo (Home/Editoria/Materia): {tot_pg:,} views + tempo por tipo")
        except Exception as e:  # noqa: BLE001 -- nao pode derrubar o site
            print(f"  ! aviso: GA4 paginas (pagePath) falhou pra property {ga_property_id} ({e}); grafico de URLs fica oculto.")
            pagesraw, pagestime = {}, {}
        try:
            sessraw = parse_ga_sessao_api(ga_client, ga_property_id, dates[0], dates[-1])
            if sessraw:
                print(f"  + GA4 tempo de sessao: {len(sessraw)} dia(s)")
        except Exception as e:  # noqa: BLE001 -- nao pode derrubar o site
            print(f"  ! aviso: GA4 tempo de sessao falhou pra property {ga_property_id} ({e}); card fica oculto.")
            sessraw = {}
    if not pvraw:
        pvraw = parse_ga(ga_path)
    recirc = parse_recirc(recirc_path) or {"temRecirc": False, "formatos": {}}

    parceiros = {}
    seedtag_publisher_id = str(seedtag_publisher_id or "").strip()
    if seedtag_token and seedtag_publisher_id and dates:
        try:
            dados_seedtag = parse_seedtag_api(seedtag_token, seedtag_publisher_id, dates[0], dates[-1])
            n_dias = len(dados_seedtag.get("porDia") or {})
            if n_dias:
                try:
                    dados_fmt = parse_seedtag_formatos(seedtag_token, seedtag_publisher_id, dates[0], dates[-1])
                    dados_seedtag.update(dados_fmt)
                    if dados_fmt.get("formatosVistos"):
                        print(f"  + Seedtag por formato: {len(dados_fmt['formatosVistos'])} formato(s) "
                              f"({', '.join(dados_fmt['formatosVistos'])})")
                except Exception as e:  # noqa: BLE001 -- extra opcional, nao pode derrubar o card principal
                    print(f"  ! aviso: Seedtag por formato falhou pro publisher {seedtag_publisher_id} "
                          f"({e}); card fica sem divisao por formato (resto normal).")
                parceiros["seedtag"] = dados_seedtag
                print(f"  + Seedtag via API (publisher {seedtag_publisher_id}): {n_dias} dia(s)")
            else:
                print(f"  (Seedtag: 0 dia(s) com dado no periodo pro publisher {seedtag_publisher_id})")
        except Exception as e:  # noqa: BLE001 -- Seedtag falhar aqui nao pode derrubar o site
            print(f"  ! aviso: Seedtag via API falhou pro publisher {seedtag_publisher_id} ({e}); card fica oculto pra esse site.")

    membrana_site_id = str(membrana_site_id or "").strip()
    if membrana_dados_conta and membrana_site_id and dates:
        try:
            dados_membrana = parse_membrana_site(membrana_dados_conta, membrana_site_id, dates[0], dates[-1])
            if dados_membrana:
                parceiros["membrana"] = dados_membrana
                print(f"  + Membrana via API (site_id {membrana_site_id}): {len(dados_membrana['porDia'])} dia(s) "
                      f"com dado, formatos: {', '.join(dados_membrana['formatosVistos']) or '(nenhum)'}"
                      + ("" if dados_membrana.get('moeda') else " -- moeda NAO confirmada ainda (sem /billing/report pra esse site_id)"))
            else:
                print(f"  (Membrana: 0 dia(s) com dado no periodo pro site_id {membrana_site_id})")
        except Exception as e:  # noqa: BLE001 -- Membrana falhar aqui nao pode derrubar o site
            print(f"  ! aviso: Membrana via API falhou pro site_id {membrana_site_id} ({e}); card fica oculto pra esse site.")

    sportradar_ad_unit_label = str(sportradar_ad_unit_label or "").strip()
    sportradar_rotulo = (f"rede {sportradar_ad_unit_label} inteira" if sportradar_rede_inteira
                          else (f"ad unit '{sportradar_ad_unit_label}'" if sportradar_ad_unit_label else "SportRadar"))
    if sportradar_dados_site and dates:
        try:
            dados_sr = parse_sportradar_site(
                sportradar_dados_site, dates[0], dates[-1],
                config.get("sportradar_valor_por_clique", 0.25), config.get("sportradar_janela_dias", 60),
                sportradar_ad_unit_label, sportradar_rede_inteira,
            )
            if dados_sr:
                parceiros["sportradar"] = dados_sr
                print(f"  + SportRadar via GAM ({sportradar_rotulo}): {len(dados_sr['porDia'])} dia(s) "
                      f"com dado, item(ns) de linha: {', '.join(dados_sr['formatosVistos']) or '(nenhum)'}")
            else:
                print(f"  (SportRadar: 0 dia(s) com dado no periodo pra {sportradar_rotulo})")
        except Exception as e:  # noqa: BLE001 -- SportRadar falhar aqui nao pode derrubar o site
            print(f"  ! aviso: SportRadar via GAM falhou pra {sportradar_rotulo} ({e}); card fica oculto pra esse site.")

    # Conversao de moeda de exibicao dos parceiros (opcional -- config.json >
    # parceiros_moeda_exibicao). Generico pra qualquer parceiro (nao so
    # Seedtag) -- cada um decide sozinho, dentro de _converter_parceiro_moeda,
    # se ha algo pra converter. Cotacao falhar (rede fora) so mantem a moeda
    # original desse parceiro -- nunca derruba o site.
    moeda_alvo_parceiros = str(config.get("parceiros_moeda_exibicao") or "").strip()
    if moeda_alvo_parceiros and parceiros:
        for pid in list(parceiros.keys()):
            try:
                parceiros[pid] = _converter_parceiro_moeda(parceiros[pid], moeda_alvo_parceiros)
            except Exception as e:  # noqa: BLE001 -- conversao falhar nao pode derrubar o parceiro/site
                print(f"  ! aviso: conversao de moeda do parceiro '{pid}' falhou ({e}); mostrando na moeda original.")

    fmts = sorted({k[0] for k in acc if k[0] != "__ALL__"})

    def rev_of(f):
        return sum(acc[(f, "total", d)]["rev"] for d in dates if (f, "total", d) in acc)

    fmts = sorted(fmts, key=rev_of, reverse=True)
    ids = ["__ALL__"] + fmts

    def recs(f, dd):
        out = []
        for d in dates:
            x = acc.get((f, dd, d))
            if not x or x["impr"] == 0:
                out.append({"rev": 0, "req": 0, "impr": 0, "clk": 0, "match": 0, "va": 0})
                continue
            impr = x["impr"]
            out.append({
                "rev": round(x["rev"], 3), "req": int(x["req"]), "impr": int(impr), "clk": int(x["clk"]),
                "match": round(min(100, x["matchw"] / impr), 2) if impr else 0,
                "va": round(min(100, x["vaw"] / impr), 2) if impr else 0,
            })
        return out

    series = {f: {dd: recs(f, dd) for dd in DEVS_OUT} for f in ids}
    labels = {"__ALL__": "Site (todos os formatos)"}
    for f in fmts:
        labels[f] = f
    return {
        "site": site_domain, "ajuste": ajuste, "dias": dates, "ids": ids,
        "labels": labels, "seriesD": series, "pvDays": pvraw, "temGA": bool(pvraw),
        "pages": pagesraw or None,
        "pagesTime": pagestime or None,
        "sessDays": sessraw or None,
        "intraday": intraday, "recirc": recirc, "moeda": moeda,
        "parceiros": parceiros or None,
        "sizesD": (sizes or {}).get("sizesD") or None,
        "sizesInfo": (sizes or {}).get("sizesInfo") or None,
    }


VERSAO_SCRIPT = "gerar_dash_v5.py -- build 2026-08-10 (hora/dia/mes: serie diaria do ano + padrao intradia por hora)"


def ler_fontes_extra(cfg_path):
    """NOVAS DEMANDAS sem código: lê uma aba opcional 'fontes_extra' do sites.xlsx
    e devolve {site_id: [{label,color,dia:{'total':{data:{rev,impr}}}}]}. Assim,
    pra plugar uma fonte nova, basta preencher a planilha -- ela aparece sozinha
    no consolidado (card + gráficos), somando no total.

    Colunas (cabeçalho, em qualquer ordem; maiúsc/minúsc indiferente):
      site      -> id do site (mesmo da aba 'sites') OU 'todos' p/ todos
      fonte     -> nome da demanda (ex.: 'Amazon', 'Criteo Direto')
      cor       -> opcional: token de cor (--blue/--violet/--aqua/--orange/--yellow/--good/--bad) ou nome (azul, roxo, verde, laranja, amarelo)
      data      -> AAAA-MM-DD (ou DD/MM/AAAA)
      receita   -> receita do dia (na moeda do painel, USD)
      impressoes-> impressões do dia (opcional)
    """
    COR = {"azul": "--blue", "roxo": "--violet", "violeta": "--violet", "verde": "--aqua",
           "laranja": "--orange", "amarelo": "--yellow", "vermelho": "--bad", "cinza": "--muted"}
    try:
        wb = load_workbook(cfg_path, data_only=True)
    except Exception:  # noqa: BLE001
        return {}
    aba = None
    for nome in wb.sheetnames:
        if str(nome).strip().lower() in ("fontes_extra", "fontes", "demandas", "demandas_extra"):
            aba = wb[nome]
            break
    if aba is None:
        return {}
    linhas = list(aba.iter_rows(values_only=True))
    if not linhas:
        return {}
    hdr = [str(c).strip().lower() if c else "" for c in linhas[0]]

    def col(*nomes):
        for n in nomes:
            if n in hdr:
                return hdr.index(n)
        return None
    ci = {"site": col("site", "id"), "fonte": col("fonte", "demanda", "nome"),
          "cor": col("cor", "color"), "data": col("data", "date"),
          "rev": col("receita", "revenue", "rev"), "impr": col("impressoes", "impressões", "impressions", "impr")}
    if ci["site"] is None or ci["fonte"] is None or ci["data"] is None or ci["rev"] is None:
        return {}
    acc = {}  # (site, fonte) -> {"color":..., "dia": {data:{rev,impr}}}
    for r in linhas[1:]:
        if not r or (ci["site"] >= len(r)):
            continue
        site = str(r[ci["site"]] or "").strip()
        fonte = str(r[ci["fonte"]] or "").strip() if ci["fonte"] < len(r) else ""
        data = datestr(r[ci["data"]]) if ci["data"] < len(r) else ""
        if not site or not fonte or not data:
            continue
        rev = pnum(r[ci["rev"]], 0.0) if (ci["rev"] is not None and ci["rev"] < len(r)) else 0.0
        impr = pnum(r[ci["impr"]], 0.0) if (ci["impr"] is not None and ci["impr"] < len(r)) else 0.0
        cor_raw = str(r[ci["cor"]] or "").strip().lower() if (ci["cor"] is not None and ci["cor"] < len(r)) else ""
        cor = cor_raw if cor_raw.startswith("--") else COR.get(cor_raw, "--muted")
        k = (site, fonte)
        o = acc.get(k)
        if o is None:
            o = acc[k] = {"color": cor, "dia": {}}
        c = o["dia"].get(data)
        if c is None:
            c = o["dia"][data] = {"rev": 0.0, "impr": 0.0}
        c["rev"] += rev
        c["impr"] += impr
    # monta por site
    por_site = {}
    for (site, fonte), o in acc.items():
        dia = {d: {"rev": round(v["rev"], 4), "impr": int(round(v["impr"]))} for d, v in o["dia"].items()}
        entry = {"label": fonte, "color": o["color"], "dia": {"total": dia}}
        por_site.setdefault(site, []).append(entry)
    return por_site


def render(DATA, out):
    CONFIG = {"nota": "Site (todos os formatos) = soma dos formatos-filho. Taxas recalculadas dos totais de cada dia (VA/match ponderados por impressoes). Pageviews vindos do GA. Serie Dia/Mes buscada direto na API do Ad Manager; 'Analise por horario' vem de um relatorio por hora dos ultimos dias (ver config.json > hora_janela_dias)."}
    html = TEMPLATE
    for k, v in {"@@CSS@@": CSS, "@@CHARTJS@@": CHARTJS,
                 "@@DATA@@": json.dumps(DATA, ensure_ascii=False),
                 "@@CONFIG@@": json.dumps(CONFIG, ensure_ascii=False)}.items():
        html = html.replace(k, v)
    open(out, "w", encoding="utf-8").write(html)


def build(cfg_path, out):
    print(f">>> {VERSAO_SCRIPT}")
    print(f">>> rodando a partir de: {HERE}")
    base = os.path.dirname(os.path.abspath(cfg_path)) or HERE
    caminho_config = os.path.join(HERE, "config.json")
    with open(caminho_config, encoding="utf-8") as f:
        config = json.load(f)
    if "report_columns_micros" not in config:
        print(
            "!!! ATENCAO: este config.json (" + caminho_config + ") NAO tem "
            "'report_columns_micros' -- e a versao ANTIGA do arquivo. Substitua "
            "o config.json por essa pasta antes de continuar."
        )
        sys.exit(1)

    wb = load_workbook(cfg_path, data_only=True)
    ws = wb["sites"] if "sites" in wb.sheetnames else wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(c).strip().lower() if c else "" for c in rows[0]]

    def col(name):
        return hdr.index(name) if name in hdr else None

    campos = ["id", "nome", "network_code", "root_ad_unit_id", "root_ad_unit_nome", "dominio", "ga_arquivo", "data_implantacao", "recirc_arquivo", "ga_property_id", "seedtag_publisher_id", "membrana_site_id", "desconto_pct"]
    ci = {k: col(k) for k in campos}
    if ci["network_code"] is None:
        print("ERRO: sites.xlsx precisa ter a coluna 'network_code' (veja COMO_USAR_v5.md).")
        sys.exit(1)

    def valor(r, campo):
        idx = ci.get(campo)
        return r[idx] if idx is not None and idx < len(r) else None

    # GA4 via API -- client criado uma unica vez (nao depende do site, so da
    # service account do GA); cada site so passa o SEU property_id. Se a
    # biblioteca nao estiver instalada, ou 'ga_service_account_key_file' nao
    # estiver configurado/nao existir, ga_client fica None e todo mundo cai
    # pro .xlsx manual (ga_arquivo) -- comportamento identico ao de antes
    # desta automacao.
    ga_client = ga_auth.obter_client(config, HERE) if GA_API_LIB_OK else None
    if ci.get("ga_property_id") is not None and not ga_client:
        print(
            "  (aviso: sites.xlsx tem coluna 'ga_property_id' mas o GA4 via API nao esta ativo -- "
            "confira 'ga_service_account_key_file' no config.json e se 'pip install google-analytics-data' "
            "foi rodado. Usando arquivo manual 'ga_arquivo' pra todos os sites por enquanto.)"
        )

    # Seedtag (aba "Parceiros") -- token trocado uma unica vez (nao depende do
    # site, so do api_key da conta); cada site so passa o SEU publisher_id.
    # Se a biblioteca 'requests' nao estiver instalada, ou 'seedtag_api_key_file'
    # nao estiver configurado/nao existir/for invalido, seedtag_token fica None
    # e nenhum site mostra card da Seedtag -- nao afeta mais nada no painel.
    seedtag_token = seedtag_auth.obter_token(config, HERE) if SEEDTAG_LIB_OK else None
    if ci.get("seedtag_publisher_id") is not None and not seedtag_token:
        print(
            "  (aviso: sites.xlsx tem coluna 'seedtag_publisher_id' mas a Seedtag via API nao esta ativa -- "
            "confira 'seedtag_api_key_file' no config.json, se o arquivo existe com o api_key dentro, e se "
            "'pip install requests' foi rodado. Rode ferramentas/testar_seedtag_api.py pra diagnosticar. "
            "Nenhum site mostra card da Seedtag por enquanto.)"
        )

    # Membrana (aba "Parceiros") -- access key lido uma unica vez (fixo, sem
    # troca por token). Diferente de Seedtag/GA4: NENHUM endpoint da Membrana
    # aceita filtro por site, entao os dados da conta TODA tambem sao
    # buscados uma unica vez aqui (nao dentro do loop por site) -- cada
    # site so filtra localmente pelo seu 'membrana_site_id' (parse_membrana_site,
    # chamado dentro de build_site). Isso evita pedir a MESMA resposta inteira
    # de novo pra cada site configurado.
    membrana_access_key = membrana_auth.obter_access_key(config, HERE) if MEMBRANA_LIB_OK else None
    if ci.get("membrana_site_id") is not None and not membrana_access_key:
        print(
            "  (aviso: sites.xlsx tem coluna 'membrana_site_id' mas a Membrana via API nao esta ativa -- "
            "confira 'membrana_access_key_file' no config.json, se o arquivo existe com a AccessKey dentro, e se "
            "'pip install requests' foi rodado. Rode ferramentas/testar_membrana_api.py pra diagnosticar. "
            "Nenhum site mostra card da Membrana por enquanto.)"
        )
    _MEMBRANA_INFO.clear()
    _MEMBRANA_INFO.update({"ativa": False, "siteIdsApi": [], "temChave": bool(membrana_access_key)})
    membrana_dados_conta = None
    if membrana_access_key:
        try:
            janela_ini, janela_fim = calcular_janela(config)
            membrana_dados_conta = fetch_membrana_conta(membrana_access_key, janela_ini, janela_fim)
            n_linhas = sum(len(v) for v in (membrana_dados_conta.get("porProduto") or {}).values())
            print(f"  + Membrana via API: {n_linhas} linha(s) buscada(s) pra conta toda "
                  f"({janela_ini} a {janela_fim}, todos os produtos/sites)")
            # DIAGNOSTICO: lista os site_id que a API REALMENTE devolveu -- se o
            # card nao aparece, quase sempre e porque o codigo em 'membrana_site_id'
            # (sites.xlsx) nao bate com nenhum destes.
            _ids_api = set()
            for _prod, _linhas in (membrana_dados_conta.get("porProduto") or {}).items():
                for _row in _linhas:
                    if isinstance(_row, dict) and _row.get("site_id"):
                        _ids_api.add(str(_row.get("site_id")).strip())
            for _row in (membrana_dados_conta.get("billing") or []):
                if isinstance(_row, dict) and _row.get("site_id"):
                    _ids_api.add(str(_row.get("site_id")).strip())
            _MEMBRANA_DIAG.append("site_id que a API da Membrana devolveu (use EXATAMENTE um destes na "
                                  "coluna 'membrana_site_id' do sites.xlsx):")
            _MEMBRANA_DIAG.append("  " + (", ".join(sorted(_ids_api)) if _ids_api else "(NENHUM -- a AccessKey nao ve nenhum site nesse periodo)"))
            _MEMBRANA_INFO.update({"ativa": True, "siteIdsApi": sorted(_ids_api)})
        except Exception as e:  # noqa: BLE001 -- Membrana falhar aqui nao pode derrubar o resto do painel
            print(f"  ! aviso: Membrana via API falhou ao buscar dados da conta ({e}); "
                  f"nenhum site mostra card da Membrana nesta rodada.")
            membrana_dados_conta = None

    # 1a passada (local, sem rede): monta a lista de tarefas na ORDEM do xlsx.
    tarefas = []
    for r in rows[1:]:
        if not r or not valor(r, "id"):
            continue
        sid = str(valor(r, "id")).strip()
        network_code = str(valor(r, "network_code") or "").strip()
        if not network_code:
            print("  ! pulando", sid, "- 'network_code' vazio no sites.xlsx")
            continue
        site_row = {
            "id": sid,
            "network_code": network_code,
            "root_ad_unit_id": str(valor(r, "root_ad_unit_id") or "").strip() or None,
            "root_ad_unit_nome": str(valor(r, "root_ad_unit_nome") or "").strip() or None,
            "dominio": str(valor(r, "dominio") or "").strip(),
        }
        gap_raw = str(valor(r, "ga_arquivo") or "").strip()
        gap = (gap_raw if os.path.isabs(gap_raw) else os.path.join(base, gap_raw)) if gap_raw else ""
        rap_raw = str(valor(r, "recirc_arquivo") or "").strip() if ci["recirc_arquivo"] is not None else ""
        rap = (rap_raw if os.path.isabs(rap_raw) else os.path.join(base, rap_raw)) if rap_raw else ""
        aj = datestr(valor(r, "data_implantacao")) if ci["data_implantacao"] is not None else None
        gpid = str(valor(r, "ga_property_id") or "").strip() if ci.get("ga_property_id") is not None else ""
        stpid = str(valor(r, "seedtag_publisher_id") or "").strip() if ci.get("seedtag_publisher_id") is not None else ""
        mbid = str(valor(r, "membrana_site_id") or "").strip() if ci.get("membrana_site_id") is not None else ""
        # desconto/comissao do site (ex.: 20 ou "20%" ou 0.2) -> guarda em % (20.0)
        desc = None
        if ci.get("desconto_pct") is not None:
            _dv = pnum(str(valor(r, "desconto_pct") or "").replace("%", ""), None)
            if _dv is not None:
                desc = _dv * 100 if 0 < _dv <= 1 else _dv  # aceita 0.2 (fracao) ou 20 (%)
                desc = round(max(0.0, min(100.0, desc)), 4)
        nome_in = str(valor(r, "nome") or "").strip()
        tarefas.append({"sid": sid, "network_code": network_code, "site_row": site_row,
                        "gap": gap, "rap": rap, "aj": aj, "gpid": gpid, "stpid": stpid,
                        "mbid": mbid, "desc": desc, "nome_in": nome_in})

    # SportRadar (aba "Parceiros") -- veicula pelo GAM, paga por CLIQUE (nao
    # por CPM reportado pelo GAM), $/clique fixo (config > sportradar_valor_por_clique).
    # Puxado 1x POR REDE (nao por site, mesmo mecanismo dos Deals), AGREGADO
    # por ad unit TOP-LEVEL -- assim NAO precisa de nenhuma coluna nova no
    # sites.xlsx: o "publisher" e' identificado automaticamente casando o
    # nome do site (id/dominio/root_ad_unit_nome) com o ad unit top-level que
    # a API devolveu (mesma tecnica de casamento por nome ja usada nos Deals).
    # Sites SEM root_ad_unit_id/root_ad_unit_nome no sites.xlsx sao tratados
    # como "rede exclusiva desse site" -- MESMA convencao ja usada pro
    # relatorio principal do Ad Exchange (ver _resolver_alvo() e a docstring
    # do arquivo: "Se a rede for exclusiva desse site, pode deixar os dois
    # vazios") -- nesse caso NAO tenta casar por nome, pega a rede INTEIRA
    # (todo ad unit top-level daquela rede, somado). Isso cobre o caso de um
    # site cuja estrutura de ad units nao tem nada parecido com o nome/
    # dominio configurado (casamento por nome nunca ia bater de jeito
    # nenhum) -- pedido do Joao em 27/08 apos o "diario do poder" nao
    # aparecer com card de SportRadar.
    sportradar_dados_site, sportradar_label_site, sportradar_rede_inteira_site = {}, {}, {}
    sportradar_por_rede = {}  # {network_code: {"por_adunit": {...}, "total": {...}}} -- p/ diagnostico
    if config.get("sportradar_ativo", True):
        try:
            redes_sr = []
            for t in tarefas:
                if t["network_code"] not in redes_sr:
                    redes_sr.append(t["network_code"])
            taxa_sr = float(config.get("sportradar_valor_por_clique", 0.25) or 0.25)
            total_linhas_sr = 0
            for nc in redes_sr:
                try:
                    client_sr = auth.obter_client(nc, config, base)
                    itens_sr, _ii_sr, _ff_sr = fetch_sportradar_rede(client_sr, config["gam_api_version"], nc, config, base)
                except Exception as e:  # noqa: BLE001
                    _SR_DIAG.append(f"[rede {nc}] ERRO ao puxar SportRadar: {type(e).__name__}: {str(e)[:200]}")
                    continue
                if not itens_sr:
                    continue
                print(f"  + SportRadar rede {nc}: {len(itens_sr)} linha(s)")
                total_linhas_sr += len(itens_sr)
                por_adunit = {}
                total_rede = {"porDia": {}, "porDiaFormato": {}, "formatosVistos": set()}
                for it in itens_sr:
                    rev = it["clk"] * taxa_sr
                    alvo = por_adunit.setdefault(it["site"], {"porDia": {}, "porDiaFormato": {}, "formatosVistos": set()})
                    _sportradar_acumula(alvo, it, rev)
                    _sportradar_acumula(total_rede, it, rev)  # agregado da rede INTEIRA (fallback)
                for _d in por_adunit.values():
                    _sportradar_arredonda(_d)
                _sportradar_arredonda(total_rede)
                sportradar_por_rede[nc] = {"por_adunit": por_adunit, "total": total_rede}
            if sportradar_por_rede:
                # site_map por nome normalizado (mesma tecnica ja usada nos Deals),
                # AGORA restrito a rede de CADA site (antes casava contra ad units
                # de QUALQUER rede junta -- risco real de colisao se duas redes
                # tiverem um ad unit com o mesmo nome, ex. "Anchor"/"ROS" nas duas).
                import unicodedata as _uc_sr

                def _n_sr(s):
                    s = _uc_sr.normalize("NFD", str(s or ""))
                    s = "".join(c for c in s if _uc_sr.category(c) != "Mn")
                    return "".join(c for c in s.lower() if c.isalnum())
                n_casado_nome, n_rede_toda = 0, 0
                for t in tarefas:
                    nc = t["network_code"]
                    rede = sportradar_por_rede.get(nc)
                    if not rede:
                        continue
                    sid = t["sid"]
                    sr_row = t.get("site_row") or {}
                    tem_root = bool(sr_row.get("root_ad_unit_id") or sr_row.get("root_ad_unit_nome"))
                    if not tem_root:
                        if rede["total"]["porDia"]:
                            sportradar_dados_site[sid] = rede["total"]
                            sportradar_label_site[sid] = nc
                            sportradar_rede_inteira_site[sid] = True
                            n_rede_toda += 1
                        continue
                    mapa_norm_sr = {}
                    for nd in rede["por_adunit"].keys():
                        mapa_norm_sr.setdefault(_n_sr(nd), nd)
                    for cand in (sid, sr_row.get("dominio"), sr_row.get("root_ad_unit_nome"), sr_row.get("nome")):
                        nn = _n_sr(cand)
                        if nn and nn in mapa_norm_sr:
                            ad_unit_nome = mapa_norm_sr[nn]
                            sportradar_dados_site[sid] = rede["por_adunit"][ad_unit_nome]
                            sportradar_label_site[sid] = ad_unit_nome
                            n_casado_nome += 1
                            break
                print(f"  + SportRadar: {total_linhas_sr} linha(s) no total | {len(sportradar_por_rede)} rede(s) com dado "
                      f"| {n_casado_nome} site(s) casado(s) por ad unit | {n_rede_toda} site(s) usando a rede inteira "
                      f"(sem root_ad_unit no sites.xlsx) | {len(sportradar_dados_site)} site(s) mapeado(s) no total")
        except Exception as e:  # noqa: BLE001 -- SportRadar nunca derruba o painel
            _SR_DIAG.append(f"ERRO geral no SportRadar: {type(e).__name__}: {str(e)[:200]}")
            print(f"  ! aviso: bloco SportRadar falhou ({type(e).__name__}) -- painel segue sem o card SportRadar.")
    if _SR_DIAG:
        try:
            with open(os.path.join(base, "diagnostico_sportradar.txt"), "w", encoding="utf-8") as _fsr:
                _fsr.write("Diagnostico do relatorio do SportRadar (aba Parceiros).\n")
                _fsr.write("Se aparecer 'RECUSADO', mande este arquivo -- o motivo diz quais\n")
                _fsr.write("dimensoes/metricas a rede aceita. Rode ferramentas/testar_sportradar_api.py <rede>.\n")
                _fsr.write("Ad unit(s) top-level vistos POR REDE (com item(ns) de linha da SportRadar):\n")
                if sportradar_por_rede:
                    for _nc, _rede in sorted(sportradar_por_rede.items()):
                        _fsr.write(f"  rede {_nc}: " + (", ".join(sorted(_rede["por_adunit"].keys())) or "(nenhum)") + "\n")
                else:
                    _fsr.write("  (nenhum)\n")
                _fsr.write("\n")
                _fsr.write("=" * 68 + "\n\n" + "\n".join(_SR_DIAG) + "\n")
            print("  + diagnostico do SportRadar salvo em diagnostico_sportradar.txt")
        except Exception:  # noqa: BLE001
            pass

    # 2a passada: busca na API. Como cada site tem CLIENT proprio (auth por
    # network_code) e as chamadas sao I/O-bound (espera do Google), rodamos os
    # sites EM PARALELO -- e a maior economia de tempo. 'max_sites_paralelo' no
    # config.json controla quantos ao mesmo tempo (padrao 5; use 1 pra voltar
    # ao modo antigo, um de cada vez). ga_client/seedtag_token/membrana_dados_conta
    # sao so-leitura, compartilhados; render() so acontece depois, na thread principal.
    def _processa(t):
        print("  ... buscando", t["sid"], "na API (rede", t["network_code"], ") ...")
        s = build_site(t["site_row"], config, HERE, t["gap"], t["aj"], t["rap"],
                       ga_client, t["gpid"], t["sid"], seedtag_token, t["stpid"],
                       membrana_dados_conta, t["mbid"],
                       sportradar_dados_site.get(t["sid"]), sportradar_label_site.get(t["sid"], ""),
                       sportradar_rede_inteira_site.get(t["sid"], False))
        s["nome"] = t["nome_in"] if t["nome_in"] else ("Acompanhamento do " + (s["site"] or t["sid"]))
        s["descontoPct"] = t.get("desc")
        return s

    try:
        _nworkers = int(config.get("max_sites_paralelo", 8) or 8)
    except (TypeError, ValueError):
        _nworkers = 8
    _nworkers = max(1, min(_nworkers, len(tarefas) or 1))

    def _sequencial():
        r = {}
        for t in tarefas:
            try:
                r[t["sid"]] = _processa(t)
            except Exception as e:
                print("  ! ERRO buscando", t["sid"], "->", e)
        return r

    resultados = {}
    if _nworkers <= 1 or len(tarefas) <= 1:
        resultados = _sequencial()
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        print(f"  (rodando {len(tarefas)} sites em paralelo, ate {_nworkers} ao mesmo tempo)")
        try:
            with ThreadPoolExecutor(max_workers=_nworkers) as ex:
                fut_para_sid = {ex.submit(_processa, t): t["sid"] for t in tarefas}
                for fut in as_completed(fut_para_sid):
                    sid = fut_para_sid[fut]
                    try:
                        resultados[sid] = fut.result()
                    except Exception as e:
                        print("  ! ERRO buscando", sid, "->", e)
        except Exception as e:  # noqa: BLE001 -- o proprio pool falhou
            print(f"  ! aviso: execucao em paralelo falhou ({type(e).__name__}: {e}).")
        # Rede de seguranca: se o paralelo nao trouxe NENHUM site (falha
        # sistemica -- ex.: a lib do Ad Manager nao curtiu o multi-thread nesse
        # ambiente), refaz TUDO em modo sequencial (o modo antigo, comprovado).
        if not resultados and tarefas:
            print("  ! nenhum site veio no paralelo -- refazendo em modo sequencial (1 de cada vez)...")
            resultados = _sequencial()

    # remonta na ORDEM original do xlsx (o paralelo termina fora de ordem)
    sites = {}
    order = []
    for t in tarefas:
        s = resultados.get(t["sid"])
        if s is None:
            continue
        sites[t["sid"]] = s
        order.append(t["sid"])
        print("  + site", t["sid"], "| formatos:", len(s["ids"]) - 1, "| dias:", len(s["dias"]),
              "| GA:", s["temGA"], "| hora:", s["intraday"]["temHora"], "| moeda:", s["moeda"],
              "| parceiros:", sorted((s.get("parceiros") or {}).keys()))

    if not order:
        print("Nenhum site foi gerado -- confira os erros acima antes de abrir o painel.")

    # diagnostico do bloco "tamanhos que entregam" -- gravado num arquivinho pra
    # voce nao precisar caçar no console (a janela fecha quando da certo).
    if _SIZES_DIAG:
        try:
            with open(os.path.join(base, "diagnostico_tamanhos.txt"), "w", encoding="utf-8") as _fd:
                _fd.write("Diagnostico do relatorio por TAMANHO (bloco 'tamanhos que entregam').\n")
                _fd.write("Linha 'OK' = os tamanhos vao aparecer no painel pra aquele site.\n")
                _fd.write("Se TODAS forem 'RECUSADO', mande este arquivo -- o motivo citado diz\n")
                _fd.write("qual dimensao a rede aceita junto das metricas de Ad Exchange.\n")
                _fd.write("=" * 68 + "\n\n")
                _fd.write("\n".join(_SIZES_DIAG) + "\n")
            print("  + diagnostico dos tamanhos salvo em diagnostico_tamanhos.txt "
                  f"({sum(1 for x in _SIZES_DIAG if '-> OK' in x)} site(s) com tamanhos)")
        except Exception:  # noqa: BLE001 -- log nunca pode derrubar a geracao
            pass

    # diagnostico da MEMBRANA -- por que o card aparece ou nao, por site.
    if _MEMBRANA_DIAG or any((sites.get(s, {}).get("parceiros") or {}) for s in order):
        try:
            linhas_mb = list(_MEMBRANA_DIAG)
            linhas_mb.append("")
            linhas_mb.append("Por site (o que o sites.xlsx pediu x se veio card da Membrana):")
            _cfg_por_site = {t["sid"]: t.get("mbid") or "" for t in tarefas}
            _por_site = {}
            for sid_ in order:
                s_ = sites.get(sid_, {})
                tem = "membrana" in (s_.get("parceiros") or {})
                _por_site[sid_] = tem
                linhas_mb.append(f"  {sid_}: membrana_site_id no xlsx = '{_cfg_por_site.get(sid_, '')}' -> card Membrana = {'SIM' if tem else 'NAO'}")
            _MEMBRANA_INFO["porSite"] = _por_site
            _MEMBRANA_INFO["configPorSite"] = _cfg_por_site
            with open(os.path.join(base, "diagnostico_membrana.txt"), "w", encoding="utf-8") as _fm:
                _fm.write("Diagnostico da Membrana (aba Parceiros).\n")
                _fm.write("Se um site esta com 'card Membrana = NAO', confira se o codigo na coluna\n")
                _fm.write("'membrana_site_id' do sites.xlsx e EXATAMENTE um dos site_id que a API devolveu\n")
                _fm.write("(lista abaixo). Ex.: se voce pos 'CHF' mas a API usa outro codigo, nao casa.\n")
                _fm.write("=" * 68 + "\n\n")
                _fm.write("\n".join(linhas_mb) + "\n")
            print("  + diagnostico da Membrana salvo em diagnostico_membrana.txt")
        except Exception:  # noqa: BLE001
            pass

    # ---------- NOVAS DEMANDAS via planilha (aba 'fontes_extra') ----------
    try:
        fx = ler_fontes_extra(cfg_path)
        if fx:
            n_fx = 0
            todos_fx = fx.get("todos", []) + fx.get("TODOS", []) + fx.get("Todos", [])
            for sid in order:
                extra = list(todos_fx)
                if sid in fx:
                    extra += fx[sid]
                if extra:
                    sites[sid]["fontesExtra"] = extra
                    n_fx += 1
            if n_fx:
                print(f"  + fontes_extra (novas demandas via planilha) em {n_fx} site(s)")
    except Exception as e:  # noqa: BLE001 -- nunca derruba o painel
        print(f"  ! aviso: leitura de 'fontes_extra' falhou ({type(e).__name__}) -- segue sem elas.")

    # ---------- DEALS (por REDE, todos os ad units) ----------
    # Puxa 1x por rede (nao por site): o relatorio de deals ja vem com todos os
    # ad units da rede. Junta MI + MA (e qualquer outra rede do sites.xlsx) numa
    # lista unica, em USD. Mapeia cada site do painel pro seu ad unit top-level.
    deals_bloco = None
    if config.get("deals_ativo", True):
        try:
            redes = []
            for t in tarefas:
                nc = t.get("network_code")
                if nc and nc not in redes:
                    redes.append(nc)
            todos_itens = []
            deals_dia_por_adunit = {}  # adUnitTopLevel -> {device:{data:{rev,impr}}}
            d_ini = d_fim = d_moeda = None

            def _deals_de_rede(nc):
                # client THREAD-LOCAL (auth cacheia por thread) -> seguro em paralelo
                client_d = auth.obter_client(nc, config, base)
                itens, ii, ff, moe = fetch_deals_rede(client_d, config["gam_api_version"], nc, config, base)
                try:
                    diario = fetch_deals_diario_rede(client_d, config["gam_api_version"], nc, config, base)
                except Exception as e2:  # noqa: BLE001
                    _DEALS_DIAG.append(f"[rede {nc}] deals diário falhou: {type(e2).__name__}: {str(e2)[:150]}")
                    diario = {}
                return nc, itens, ii, ff, moe, diario

            # roda as redes de deals em paralelo (mesmo cap dos sites) -- I/O-bound
            try:
                _wd = max(1, min(int(config.get("max_sites_paralelo", 8) or 8), len(redes) or 1))
            except (TypeError, ValueError):
                _wd = 1
            _res_deals = []
            if _wd > 1 and len(redes) > 1:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                try:
                    with ThreadPoolExecutor(max_workers=_wd) as exd:
                        futs = {exd.submit(_deals_de_rede, nc): nc for nc in redes}
                        for f in as_completed(futs):
                            try:
                                _res_deals.append(f.result())
                            except Exception as e:  # noqa: BLE001
                                _DEALS_DIAG.append(f"[rede {futs[f]}] ERRO deals: {type(e).__name__}: {str(e)[:180]}")
                except Exception as e:  # noqa: BLE001
                    _DEALS_DIAG.append(f"deals paralelo falhou ({type(e).__name__}) -- refazendo sequencial.")
                    _res_deals = []
            if not _res_deals:  # sequencial (fallback ou 1 rede só)
                for nc in redes:
                    try:
                        _res_deals.append(_deals_de_rede(nc))
                    except Exception as e:  # noqa: BLE001
                        _DEALS_DIAG.append(f"[rede {nc}] ERRO ao puxar deals: {type(e).__name__}: {str(e)[:200]}")
            for nc, itens, ii, ff, moe, diario in _res_deals:
                todos_itens += itens
                d_ini = d_ini or ii.isoformat()
                d_fim = d_fim or ff.isoformat()
                d_moeda = d_moeda or moe
                deals_dia_por_adunit.update(diario or {})
                print(f"  + deals rede {nc}: {len(itens)} campanha(s)")
            if todos_itens:
                # siteMap: id do site no painel -> ad unit top-level do relatorio de deals
                nomes_deal = {it["site"] for it in todos_itens}

                def _n(s):
                    import unicodedata
                    s = unicodedata.normalize("NFD", str(s or ""))
                    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
                    return "".join(c for c in s.lower() if c.isalnum())
                mapa_norm = {}
                for nd in nomes_deal:
                    mapa_norm.setdefault(_n(nd), nd)
                site_map = {}
                for t in tarefas:
                    sid = t["sid"]
                    sr = t.get("site_row") or {}
                    for cand in (sid, sr.get("dominio"), sr.get("root_ad_unit_nome"), sr.get("nome")):
                        nn = _n(cand)
                        if nn and nn in mapa_norm:
                            site_map[sid] = mapa_norm[nn]
                            break
                deals_bloco = {"moeda": d_moeda or config.get("report_currency", "USD"),
                               "ini": d_ini, "fim": d_fim, "siteMap": site_map, "itens": todos_itens}
                # anexa dealsDia (por dia/dispositivo) em cada site do painel, via siteMap
                if deals_dia_por_adunit:
                    n_dd = 0
                    for sid, adunit in site_map.items():
                        dd = deals_dia_por_adunit.get(adunit)
                        if dd and sid in sites:
                            sites[sid]["dealsDia"] = dd
                            n_dd += 1
                    print(f"  + deals por dia anexados a {n_dd} site(s) (consolidado segue o período)")
                print(f"  + DEALS: {len(todos_itens)} campanha(s) no total | {len(site_map)} site(s) mapeado(s)")
        except Exception as e:  # noqa: BLE001 -- deals nunca derruba o painel
            _DEALS_DIAG.append(f"ERRO geral nos deals: {type(e).__name__}: {str(e)[:200]}")
            print(f"  ! aviso: bloco de deals falhou ({type(e).__name__}) -- painel segue sem a aba Deals.")
    # diagnostico dos deals num arquivinho
    if _DEALS_DIAG:
        try:
            with open(os.path.join(base, "diagnostico_deals.txt"), "w", encoding="utf-8") as _fdd:
                _fdd.write("Diagnostico do relatorio de DEALS (aba Deals).\n")
                _fdd.write("Se aparecer 'RECUSADO', mande este arquivo -- o motivo diz quais\n")
                _fdd.write("dimensoes/metricas a rede aceita. Rode ferramentas/testar_deals_api.py <rede>.\n")
                _fdd.write("=" * 68 + "\n\n" + "\n".join(_DEALS_DIAG) + "\n")
            print("  + diagnostico dos deals salvo em diagnostico_deals.txt")
        except Exception:  # noqa: BLE001
            pass

    # ---------- OPEN BIDDING (por REDE, todos os ad units) ----------
    if config.get("openbidding_ativo", False):
        try:
            import unicodedata as _uc

            def _n2(s):
                s = _uc.normalize("NFD", str(s or ""))
                s = "".join(c for c in s if _uc.category(c) != "Mn")
                return "".join(c for c in s.lower() if c.isalnum())
            # AUTOMÁTICO por API (por yield partner, excluindo o AdX). Se a API não
            # trouxer nada, cai pro CSV da pasta 'ob/' (fallback).
            ob_por_adunit = {}
            if config.get("openbidding_tentativas") or []:
                redes_ob = []
                for t in tarefas:
                    nc = t.get("network_code")
                    if nc and nc not in redes_ob:
                        redes_ob.append(nc)

                def _ob_de_rede(nc):
                    cl = auth.obter_client(nc, config, base)
                    return nc, fetch_openbidding_diario_rede(cl, config["gam_api_version"], nc, config, base)
                for nc in redes_ob:
                    try:
                        _, dd = _ob_de_rede(nc)
                        ob_por_adunit.update(dd or {})
                    except Exception as e:  # noqa: BLE001
                        _OB_DIAG.append(f"[rede {nc}] ERRO OB: {type(e).__name__}: {str(e)[:150]}")
            if not ob_por_adunit:
                ob_por_adunit = ler_openbidding_arquivo(base, config)  # fallback CSV
            if ob_por_adunit:
                mapa2 = {}
                for ad in ob_por_adunit:
                    mapa2.setdefault(_n2(ad), ad)
                n_ob = 0
                for t in tarefas:
                    sid = t["sid"]
                    sr = t.get("site_row") or {}
                    for cand in (sid, sr.get("dominio"), sr.get("root_ad_unit_nome"), sr.get("nome")):
                        nn = _n2(cand)
                        if nn and nn in mapa2 and sid in sites:
                            sites[sid]["obDia"] = ob_por_adunit[mapa2[nn]]
                            n_ob += 1
                            break
                print(f"  + OPEN BIDDING anexado a {n_ob} site(s)")
            else:
                print("  (Open Bidding: nenhum dado -- exporte o relatorio e jogue o CSV na pasta 'ob/'. Veja ob/LEIA-ME.txt)")
        except Exception as e:  # noqa: BLE001 -- OB nunca derruba o painel
            _OB_DIAG.append(f"ERRO geral OB: {type(e).__name__}: {str(e)[:180]}")
        if _OB_DIAG:
            try:
                with open(os.path.join(base, "diagnostico_openbidding.txt"), "w", encoding="utf-8") as _fo:
                    _fo.write("Diagnostico do OPEN BIDDING.\n")
                    _fo.write("Fonte = CSV(s) exportado(s) do Ad Manager na pasta 'ob/' (ver ob/LEIA-ME.txt).\n")
                    _fo.write("Soma os yield partners, excluindo 'DoubleClick Ad Exchange' (o proprio AdX).\n")
                    _fo.write("=" * 68 + "\n\n" + "\n".join(_OB_DIAG) + "\n")
                print("  + diagnostico do Open Bidding salvo em diagnostico_openbidding.txt")
            except Exception:  # noqa: BLE001
                pass

    # ---------- DEMANDA POR FORMATO (AdX + Open Bidding + Deals, por posicao) ----------
    if config.get("demanda_fmt_ativo", True):
        try:
            redes_df = []
            for t in tarefas:
                nc = t.get("network_code")
                if nc and nc not in redes_df:
                    redes_df.append(nc)
            df_all, df_ini, df_fim = {}, None, None
            def _demfmt_de_rede(nc):
                cl = auth.obter_client(nc, config, base)
                return (nc,) + fetch_demanda_fmt_rede(cl, config["gam_api_version"], nc, config, base)
            _wf = max(1, min(int(config.get("max_sites_paralelo", 8) or 8), len(redes_df) or 1))
            _res_df = []
            if _wf <= 1 or len(redes_df) <= 1:
                for nc in redes_df:
                    try:
                        _res_df.append(_demfmt_de_rede(nc))
                    except Exception as e:  # noqa: BLE001
                        _DEMFMT_DIAG.append(f"[rede {nc}] ERRO: {type(e).__name__}: {str(e)[:150]}")
            else:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                with ThreadPoolExecutor(max_workers=_wf) as _ex:
                    _futs = {_ex.submit(_demfmt_de_rede, nc): nc for nc in redes_df}
                    for _f in as_completed(_futs):
                        try:
                            _res_df.append(_f.result())
                        except Exception as e:  # noqa: BLE001
                            _DEMFMT_DIAG.append(f"[rede {_futs[_f]}] ERRO: {type(e).__name__}: {str(e)[:150]}")
            for nc, res, ii, ff in _res_df:
                df_all.update(res or {})
                df_ini = df_ini or ii
                df_fim = df_fim or ff
            n_df = 0
            for t in tarefas:
                sid = t["sid"]
                sr = t.get("site_row") or {}
                for cand in (sid, sr.get("dominio"), sr.get("root_ad_unit_nome"), sr.get("nome")):
                    nn = _demfmt_norm(cand)
                    if nn and nn in df_all and sid in sites:
                        sites[sid]["demandaFmtD"] = df_all[nn]
                        sites[sid]["demandaFmtWin"] = [df_ini, df_fim]
                        n_df += 1
                        break
            print(f"  + DEMANDA por formato (AdX+OB+Deals) anexada a {n_df} site(s)")
        except Exception as e:  # noqa: BLE001 -- nunca derruba o painel
            _DEMFMT_DIAG.append(f"ERRO geral demanda/fmt: {type(e).__name__}: {str(e)[:180]}")
        if _DEMFMT_DIAG:
            try:
                with open(os.path.join(base, "diagnostico_demanda_fmt.txt"), "w", encoding="utf-8") as _fx:
                    _fx.write("Diagnostico da DEMANDA POR FORMATO (AdX + Open Bidding + Deals).\n")
                    _fx.write("OB = canal 'Open Bidding'; Deal = LINE_ITEM_TYPE PREFERRED_DEAL; ambos FLAT.\n")
                    _fx.write("=" * 68 + "\n\n" + "\n".join(_DEMFMT_DIAG) + "\n")
                print("  + diagnostico da demanda por formato salvo em diagnostico_demanda_fmt.txt")
            except Exception:  # noqa: BLE001
                pass

    _dirs = [base] if os.path.abspath(base) == os.path.abspath(HERE) else [base, HERE]
    anotacoes = []
    for _d in _dirs:
        anotacoes += ler_anotacoes(_d)
    if anotacoes:
        print("  + anotacoes (otimizacoes/testes) embutidas:", len(anotacoes))
    gerado_em = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    render({"order": order, "sites": sites, "anotacoes": anotacoes,
            "tz": config.get("painel_timezone", "America/Sao_Paulo"),
            "gerado_em": gerado_em,
            "membranaDiag": (dict(_MEMBRANA_INFO) or None),
            "deals": deals_bloco,
            "pasta": base}, out)
    print("OK ->", out, "| sites:", order, "| gerado_em:", gerado_em)


if __name__ == "__main__":
    cfg = sys.argv[1] if len(sys.argv) > 1 else "sites.xlsx"
    out = sys.argv[2] if len(sys.argv) > 2 else "painel.html"
    build(cfg, out)
