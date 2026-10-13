"""
Servidor de Relatórios — GAM Monitor
=====================================
Tela local (roda no seu navegador, em http://localhost:5000) pra escolher
uma campanha e gerar uma planilha do Google Sheets DEDICADA a ela, com
Impressões, Cliques, CTR e outros dados.

Requisitos:
    py -m pip install flask gspread google-auth

Como rodar:
    py reports_server.py

Depois é só abrir http://localhost:5000 no navegador. Precisa deixar essa
janela do terminal aberta enquanto estiver usando a tela.

Usa a MESMA service account já configurada em googleads.yaml/accounts.yaml
(veja README.md, seção 3.6, pra habilitar Sheets/Drive API e mais detalhes).
"""

import json
import re
import time
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify, request

import gam_monitor as gm

app = Flask(__name__)

BASE_DIR = Path(__file__).parent
REPORT_FILES_PATH = BASE_DIR / "output" / "report_files.json"
INVENTORY_CACHE_PATH = BASE_DIR / "output" / "inventory_cache.json"

NETWORK_ERROR_HINTS = (
    "nameresolutionerror", "getaddrinfo failed", "max retries exceeded",
    "connection refused", "temporary failure", "connection aborted",
    "connection reset", "timed out", "timeout",
)


def retry_call(func, *args, attempts=4, delay=4, **kwargs):
    """Roda func(*args, **kwargs) e tenta de novo automaticamente se o erro
    parecer ser de rede/DNS transitório (o mesmo tipo de falha que já vimos
    com oauth2.googleapis.com, sheets.googleapis.com, etc.) — evita precisar
    clicar de novo manualmente pra cada instabilidade passageira."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            is_network_error = any(hint in str(e).lower() for hint in NETWORK_ERROR_HINTS)
            if not is_network_error or attempt == attempts:
                raise
            print(f"[debug] Erro de rede transitório (tentativa {attempt}/{attempts}), "
                  f"tentando de novo em {delay}s: {e}")
            time.sleep(delay)
    raise last_error


class _RetryingWorksheet:
    """Encapsula uma worksheet do gspread, repetindo automaticamente
    append_rows/format/update_title em caso de erro de rede transitório."""

    def __init__(self, ws):
        self._ws = ws

    def update_title(self, title):
        return retry_call(self._ws.update_title, title)

    def append_rows(self, rows, value_input_option=None):
        return retry_call(self._ws.append_rows, rows, value_input_option=value_input_option)

    def format(self, rng, fmt):
        return retry_call(self._ws.format, rng, fmt)


def build_nav_bar(active):
    """Barra de navegação fina e escura, com o logo da Adzep e as abas
    Dashboard/Relatórios — destaca a aba ativa."""
    def tab(href, label, key):
        is_active = active == key
        style = (
            "color:#fff;text-decoration:none;font-weight:600;font-size:13px;"
            "padding:6px 14px;border-radius:999px;"
        )
        style += "background:rgba(255,255,255,.16);" if is_active else "opacity:.75;"
        return f'<a href="{href}" style="{style}">{label}</a>'

    return f"""
<div style="background:#190c26;padding:10px 24px;display:flex;gap:14px;align-items:center;
            font-family:'Poppins',-apple-system,Segoe UI,Roboto,sans-serif;">
  <img src="{gm.ADZEP_LOGO_DATA_URI}" alt="Adzep" style="height:26px;background:#fff;padding:5px 9px;
       border-radius:9px;display:block;">
  {tab("/", "📊 Dashboard", "dashboard")}
  {tab("/relatorios", "📁 Relatórios", "relatorios")}
  {tab("/insights", "📈 Insights", "insights")}
  {tab("/faturamento", "💰 Faturamento", "faturamento")}
  {tab("/alertas", "🔔 Alertas", "alertas")}
</div>
"""



def get_client_for_network(network_code):
    """Acha (ou cria) um client do GAM pra rede específica de uma campanha,
    reaproveitando a mesma configuração (accounts.yaml ou googleads.yaml)."""
    for account_name, client in gm.get_clients():
        if str(client.network_code) == str(network_code):
            return client
    raise RuntimeError(
        f"Não achei uma conta configurada pra rede {network_code}. "
        "Confira o accounts.yaml/googleads.yaml."
    )


def fetch_detailed_report(campaign, break_by_creative_size=False, include_revenue=False):
    """Busca ao vivo (na hora) um relatório detalhado pra UMA campanha,
    com as dimensões/métricas extras escolhidas. Diferente do cache diário
    do gam_monitor.py, que só traz Impressões/Cliques/CTR por dia.

    Retorna uma lista de linhas: [Data, (Tamanho do criativo), Impressões,
    Cliques, CTR, (CPM, Receita)]
    """
    client = get_client_for_network(campaign["network_code"])
    report_service = client.GetService("ReportService", version=gm.GAM_API_VERSION)

    dimensions = ["LINE_ITEM_ID", "DATE"]
    if break_by_creative_size:
        dimensions.append("CREATIVE_SIZE")

    columns = ["AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS"]
    if include_revenue:
        columns += ["AD_SERVER_CPM_AND_CPC_REVENUE", "AD_SERVER_WITHOUT_CPD_AVERAGE_ECPM"]

    today = gm.datetime.now(gm.timezone.utc)
    report_job = {
        "reportQuery": {
            "dimensions": dimensions,
            "columns": columns,
            "dateRangeType": "CUSTOM_DATE",
            "startDate": {"year": today.year, "month": 1, "day": 1},
            "endDate": {"year": today.year, "month": today.month, "day": today.day},
            "statement": {
                "query": "WHERE LINE_ITEM_ID = :lineItemId",
                "values": [{
                    "key": "lineItemId",
                    "value": {"xsi_type": "NumberValue", "value": str(campaign["id"])},
                }],
            },
        }
    }

    saved_job = gm._to_dict(retry_call(report_service.runReportJob, report_job))
    job_id = saved_job["id"]

    status = saved_job.get("reportJobStatus")
    waited = 0
    while status not in ("COMPLETED", "FAILED") and waited < 120:
        gm.time.sleep(3)
        waited += 3
        status = retry_call(report_service.getReportJobStatus, job_id)

    if status != "COMPLETED":
        raise RuntimeError(f"Relatório não completou a tempo (status={status}).")

    downloader = client.GetDataDownloader(version=gm.GAM_API_VERSION)
    report_file = gm.io.BytesIO()
    retry_call(downloader.DownloadReportToFile, job_id, "CSV_DUMP", report_file, use_gzip_compression=True)
    report_file.seek(0)

    with gm.gzip.GzipFile(fileobj=report_file) as gz:
        content = gz.read().decode("utf-8")

    reader = gm.csv.DictReader(gm.io.StringIO(content))
    headers = reader.fieldnames or []

    def find_col(*needles):
        return next((h for h in headers if all(n in h.upper() for n in needles)), None)

    date_col = find_col("DATE")
    size_col = find_col("CREATIVE_SIZE") if break_by_creative_size else None
    imp_col = find_col("AD_SERVER_IMPRESSIONS")
    click_col = find_col("AD_SERVER_CLICKS")
    revenue_col = find_col("AD_SERVER_CPM_AND_CPC_REVENUE") if include_revenue else None
    cpm_col = find_col("AD_SERVER_WITHOUT_CPD_AVERAGE_ECPM") if include_revenue else None

    rows_by_key = {}
    for row in reader:
        date_str = row.get(date_col) if date_col else None
        if not date_str:
            continue
        size = row.get(size_col, "") if size_col else None
        key = (date_str, size) if break_by_creative_size else (date_str,)

        entry = rows_by_key.setdefault(key, {"impressions": 0, "clicks": 0, "revenue_micros": 0, "cpm_micros": []})
        try:
            entry["impressions"] += int(row.get(imp_col) or 0)
            entry["clicks"] += int(row.get(click_col) or 0)
            if revenue_col:
                entry["revenue_micros"] += int(row.get(revenue_col) or 0)
            if cpm_col:
                entry["cpm_micros"].append(int(row.get(cpm_col) or 0))
        except ValueError:
            continue

    results = []
    for key in sorted(rows_by_key.keys()):
        entry = rows_by_key[key]
        results.append({
            "date": key[0],
            "creative_size": key[1] if break_by_creative_size else None,
            "impressions": entry["impressions"],
            "clicks": entry["clicks"],
            "revenue": entry["revenue_micros"] / 1_000_000 if include_revenue else None,
            "cpm": (sum(entry["cpm_micros"]) / len(entry["cpm_micros"]) / 1_000_000)
                   if include_revenue and entry["cpm_micros"] else None,
        })
    return results


def load_campaigns():
    """Lê o cache de dados gerado pelo gam_monitor.py (não busca nada novo do GAM)."""
    if not gm.DATA_CACHE_PATH.exists():
        return {}
    return json.loads(gm.DATA_CACHE_PATH.read_text(encoding="utf-8"))


def load_report_files():
    if REPORT_FILES_PATH.exists():
        return json.loads(REPORT_FILES_PATH.read_text(encoding="utf-8"))
    return {}


def save_report_files(state):
    REPORT_FILES_PATH.write_text(json.dumps(state), encoding="utf-8")


def load_inventory_cache():
    if INVENTORY_CACHE_PATH.exists():
        return json.loads(INVENTORY_CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_inventory_cache(cache):
    INVENTORY_CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")


def get_network_total_impressions_cached(network_code, month_key):
    """Busca as impressões da rede POR AD UNIT e por dia pra um mês,
    reaproveitando cache em disco — só busca ao vivo no GAM na primeira vez
    pra cada rede+mês. O mês ATUAL nunca é guardado em cache (ainda está
    incompleto, o total muda a cada dia que passa).
    Devolve {'ad_unit_id': {'AAAA-MM-DD': impressões}}."""
    today_month_key = gm.datetime.now().strftime("%Y-%m")
    is_current_month = month_key == today_month_key

    if not is_current_month:
        cache = load_inventory_cache()
        key = f"{network_code}_{month_key}"
        cached_value = cache.get(key)
        # Protege contra cache no formato ANTIGO (antes de guardar por ad
        # unit) — lá os valores eram números direto; agora são sub-dicts.
        # Se detectar o formato velho, ignora e busca de novo.
        if cached_value and all(isinstance(v, dict) for v in cached_value.values()):
            return cached_value

    client = get_client_for_network(network_code)
    daily = fetch_network_total_impressions(client, month_key)

    if not is_current_month:
        cache = load_inventory_cache()
        cache[f"{network_code}_{month_key}"] = daily
        save_inventory_cache(cache)

    return daily


OAUTH_CREDENTIALS_PATH = BASE_DIR / "oauth_credentials.json"
OAUTH_TOKEN_PATH = BASE_DIR / "oauth_token.json"


def get_sheets_client():
    """Autentica com a SUA conta Google (não a service account), via OAuth.
    Isso é necessário porque service accounts não têm armazenamento próprio no
    Google Drive — qualquer arquivo criado por elas esbarra em erro de cota.
    Na primeira vez, abre o navegador pra você autorizar; depois disso, fica
    salvo em oauth_token.json e não pede de novo."""
    import gspread

    if not OAUTH_CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            "Não encontrei 'oauth_credentials.json'. Veja o README.md (seção 3.7) "
            "pra criar o OAuth Client ID no Google Cloud e baixar esse arquivo."
        )

    return gspread.oauth(
        credentials_filename=str(OAUTH_CREDENTIALS_PATH),
        authorized_user_filename=str(OAUTH_TOKEN_PATH),
    )


DIARIO_DO_RIO_NAME = "Diário do Rio"


def create_diario_do_rio_sheet(publisher_name, c):
    """Layout especial só pro Diário do Rio: um bloco por tamanho de criativo,
    cada um com seu próprio cabeçalho, linhas diárias, uma linha 'Total da
    peça' e uma linha em branco antes do próximo bloco. A primeira coluna
    ('Peça') fica sempre vazia."""
    gc = get_sheets_client()

    title = f"Relatório - {c['name']} ({publisher_name})"[:99]
    sh = retry_call(gc.create, title)
    ws = _RetryingWorksheet(sh.sheet1)
    ws.update_title("Relatório")

    daily_rows = fetch_detailed_report(c, break_by_creative_size=True, include_revenue=True)

    groups = {}
    order = []
    for row in daily_rows:
        size = row.get("creative_size") or "—"
        if size not in groups:
            groups[size] = []
            order.append(size)
        groups[size].append(row)

    header = ["Peça", "Tamanho", "Data", "Impressões", "Cliques", "CTR", "CPM (R$)", "Receita (R$)"]
    all_rows = []
    header_rows = []
    total_rows = []

    campaign_impressions = 0
    campaign_clicks = 0
    campaign_revenue = 0.0
    campaign_cpm = 0

    for size in order:
        rows = sorted(groups[size], key=lambda r: r["date"])
        header_rows.append(len(all_rows) + 1)
        all_rows.append(list(header))

        total_impressions = 0
        total_clicks = 0
        total_revenue = 0.0
        cpm_value = 0

        for r in rows:
            impressions = r.get("impressions", 0) or 0
            clicks = r.get("clicks", 0) or 0
            revenue = r.get("revenue") or 0.0
            cpm = r.get("cpm") or 0
            if cpm:
                cpm_value = cpm
            total_impressions += impressions
            total_clicks += clicks
            total_revenue += revenue
            ctr_ratio = round(clicks / impressions, 6) if impressions else 0

            try:
                date_display = "'" + gm.datetime.strptime(r["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
            except (ValueError, KeyError, TypeError):
                date_display = r.get("date", "")

            all_rows.append(["", size, date_display, impressions, clicks, ctr_ratio, cpm_value, round(revenue, 2)])

        blended_ctr = round(total_clicks / total_impressions, 6) if total_impressions else 0
        total_rows.append(len(all_rows) + 1)
        all_rows.append(["", "Total da peça", "", total_impressions, total_clicks, blended_ctr, cpm_value, round(total_revenue, 2)])
        all_rows.append([])  # linha em branco antes do próximo bloco

        campaign_impressions += total_impressions
        campaign_clicks += total_clicks
        campaign_revenue += total_revenue
        if cpm_value:
            campaign_cpm = cpm_value

    # Linha final com o total somando todos os tamanhos de criativo da campanha
    campaign_ctr = round(campaign_clicks / campaign_impressions, 6) if campaign_impressions else 0
    total_rows.append(len(all_rows) + 1)
    all_rows.append([
        "", "Total da campanha", "", campaign_impressions, campaign_clicks,
        campaign_ctr, campaign_cpm, round(campaign_revenue, 2),
    ])

    ws.append_rows(all_rows, value_input_option="USER_ENTERED")

    for hr in header_rows:
        ws.format(f"A{hr}:H{hr}", {
            "backgroundColor": {"red": 0.384, "green": 0.176, "blue": 0.569},
            "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
            "horizontalAlignment": "CENTER",
        })
    for tr in total_rows:
        ws.format(f"A{tr}:H{tr}", {
            "backgroundColor": {"red": 0.937, "green": 0.910, "blue": 1.0},
            "textFormat": {"bold": True},
            "horizontalAlignment": "CENTER",
        })

    last_row = len(all_rows)
    ws.format(f"A1:H{last_row}", {"horizontalAlignment": "CENTER"})
    ws.format(f"D1:E{last_row}", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})
    ws.format(f"F1:F{last_row}", {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}})
    ws.format(f"G1:H{last_row}", {"numberFormat": {"type": "CURRENCY", "pattern": "R$#,##0.00"}})

    return sh.url


def create_report_sheet(publisher_name, c, break_by_creative_size=False, include_revenue=False):
    """Cria uma planilha NOVA, só pra essa campanha, com o detalhamento diário
    (Data, Impressões, Cliques, CTR — e opcionalmente Tamanho do Criativo,
    CPM e Receita) e uma linha de Total — devolve a URL."""
    if publisher_name == DIARIO_DO_RIO_NAME:
        return create_diario_do_rio_sheet(publisher_name, c)

    gc = get_sheets_client()

    title = f"Relatório - {c['name']} ({publisher_name})"[:99]
    sh = retry_call(gc.create, title)
    ws = _RetryingWorksheet(sh.sheet1)
    ws.update_title("Relatório")

    # Cabeçalho dinâmico, conforme as opções escolhidas
    header = ["Data"]
    if break_by_creative_size:
        header.append("Tamanho do Criativo")
    header += ["Impressões", "Cliques", "CTR"]
    if include_revenue:
        header += ["CPM", "Receita"]
    num_cols = len(header)
    last_col_letter = chr(ord("A") + num_cols - 1)

    table_rows = [header]
    total_impressions = 0
    total_clicks = 0
    total_revenue = 0.0

    if break_by_creative_size or include_revenue:
        # Precisa buscar ao vivo no GAM (dimensão/métricas extras não estão no cache diário)
        daily_rows = fetch_detailed_report(c, break_by_creative_size, include_revenue)
    else:
        # Caminho rápido: usa o cache diário já calculado pelo gam_monitor.py
        daily_rows = [
            {"date": d["date"], "creative_size": None, "impressions": d.get("impressions", 0),
             "clicks": d.get("clicks", 0), "revenue": None, "cpm": None}
            for d in (c.get("daily_stats") or [])
        ]

    for day in daily_rows:
        impressions = day.get("impressions", 0) or 0
        clicks = day.get("clicks", 0) or 0
        total_impressions += impressions
        total_clicks += clicks
        ctr = round((clicks / impressions) * 100, 2) if impressions else 0.0

        try:
            date_display = gm.datetime.strptime(day["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
        except (ValueError, KeyError, TypeError):
            date_display = day.get("date", "")

        # O apóstrofo força o Sheets a tratar como TEXTO puro, sem tentar
        # "adivinhar" e reformatar como data (o que inverteria dia/mês em
        # datas ambíguas tipo 01/07, dependendo do idioma da planilha).
        row = [f"'{date_display}"]
        if break_by_creative_size:
            row.append(day.get("creative_size") or "—")
        row += [impressions, clicks, f"{ctr:.2f}%"]
        if include_revenue:
            revenue = day.get("revenue")
            cpm = day.get("cpm")
            if revenue is not None:
                total_revenue += revenue
            row += [round(cpm, 2) if cpm is not None else "—", round(revenue, 2) if revenue is not None else "—"]
        table_rows.append(row)

    total_ctr = round((total_clicks / total_impressions) * 100, 2) if total_impressions else 0.0
    total_row_values = ["Total"]
    if break_by_creative_size:
        total_row_values.append("")
    total_row_values += [total_impressions, total_clicks, f"{total_ctr:.2f}%"]
    if include_revenue:
        total_cpm = round((total_revenue * 1000 / total_impressions), 2) if total_impressions else "—"
        total_row_values += [total_cpm, round(total_revenue, 2)]
    table_rows.append(total_row_values)

    ws.append_rows(table_rows, value_input_option="USER_ENTERED")

    # Estiliza o cabeçalho da tabela diária (roxo #622D91, texto branco) e a linha de Total
    header_row = 1
    total_row = header_row + len(table_rows) - 1

    ws.format(f"A{header_row}:{last_col_letter}{header_row}", {
        "backgroundColor": {"red": 0.384, "green": 0.176, "blue": 0.569},
        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
        "horizontalAlignment": "CENTER",
    })
    ws.format(f"A{total_row}:{last_col_letter}{total_row}", {
        "backgroundColor": {"red": 0.937, "green": 0.910, "blue": 1.0},
        "textFormat": {"bold": True},
        "horizontalAlignment": "CENTER",
    })

    # Coluna de Impressões em formato de número (a posição dela muda se
    # "Tamanho do Criativo" estiver presente)
    impressions_col_index = 2 if break_by_creative_size else 1  # 0=Data, (1=Tamanho), Impressões
    impressions_col_letter = chr(ord("A") + impressions_col_index)
    ws.format(f"{impressions_col_letter}{header_row + 1}:{impressions_col_letter}{total_row}", {
        "numberFormat": {"type": "NUMBER", "pattern": "#,##0"},
    })

    # Colunas de CPM e Receita em formato de moeda (Real)
    if include_revenue:
        cpm_col_letter = chr(ord("A") + num_cols - 2)
        revenue_col_letter = chr(ord("A") + num_cols - 1)
        ws.format(f"{cpm_col_letter}{header_row + 1}:{revenue_col_letter}{total_row}", {
            "numberFormat": {"type": "CURRENCY", "pattern": "R$#,##0.00"},
        })

    # Tudo centralizado
    ws.format(f"A1:{last_col_letter}{total_row}", {"horizontalAlignment": "CENTER"})

    return sh.url


def create_diario_do_rio_group_sheet(publisher_name, campaigns):
    """Igual ao create_diario_do_rio_sheet (individual), só que combinando
    VÁRIOS itens de linha do mesmo grupo — soma Impressões/Cliques/Receita
    por (tamanho de criativo, data) entre todos os itens antes de montar os
    blocos, mantendo a mesma estrutura visual do relatório individual."""
    gc = get_sheets_client()

    name = campaigns[0]["name"]
    title = f"Relatório - {name} ({publisher_name}) - {len(campaigns)} itens"[:99]
    sh = retry_call(gc.create, title)
    ws = _RetryingWorksheet(sh.sheet1)
    ws.update_title("Relatório")

    # Busca o detalhamento por tamanho de criativo de CADA item do grupo (ao
    # vivo, igual ao relatório individual) e combina tudo por (tamanho, data)
    combined = {}
    for c in campaigns:
        daily_rows = fetch_detailed_report(c, break_by_creative_size=True, include_revenue=True)
        for row in daily_rows:
            size = row.get("creative_size") or "—"
            date_str = row.get("date")
            key = (size, date_str)
            entry = combined.setdefault(key, {"date": date_str, "impressions": 0, "clicks": 0, "revenue": 0.0, "cpm": 0})
            entry["impressions"] += row.get("impressions", 0) or 0
            entry["clicks"] += row.get("clicks", 0) or 0
            entry["revenue"] += row.get("revenue") or 0.0
            if row.get("cpm"):
                entry["cpm"] = row["cpm"]

    groups = {}
    for (size, date_str), entry in combined.items():
        groups.setdefault(size, []).append(entry)
    order = sorted(groups.keys())

    header = ["Peça", "Tamanho", "Data", "Impressões", "Cliques", "CTR", "CPM (R$)", "Receita (R$)"]
    all_rows = []
    header_rows = []
    total_rows = []

    campaign_impressions = 0
    campaign_clicks = 0
    campaign_revenue = 0.0
    campaign_cpm = 0

    for size in order:
        rows = sorted(groups[size], key=lambda r: r["date"])
        header_rows.append(len(all_rows) + 1)
        all_rows.append(list(header))

        total_impressions = 0
        total_clicks = 0
        total_revenue = 0.0
        cpm_value = 0

        for r in rows:
            impressions = r.get("impressions", 0) or 0
            clicks = r.get("clicks", 0) or 0
            revenue = r.get("revenue") or 0.0
            cpm = r.get("cpm") or 0
            if cpm:
                cpm_value = cpm
            total_impressions += impressions
            total_clicks += clicks
            total_revenue += revenue
            ctr_ratio = round(clicks / impressions, 6) if impressions else 0

            try:
                date_display = "'" + gm.datetime.strptime(r["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
            except (ValueError, KeyError, TypeError):
                date_display = r.get("date", "")

            all_rows.append(["", size, date_display, impressions, clicks, ctr_ratio, cpm_value, round(revenue, 2)])

        blended_ctr = round(total_clicks / total_impressions, 6) if total_impressions else 0
        total_rows.append(len(all_rows) + 1)
        all_rows.append(["", "Total da peça", "", total_impressions, total_clicks, blended_ctr, cpm_value, round(total_revenue, 2)])
        all_rows.append([])  # linha em branco antes do próximo bloco

        campaign_impressions += total_impressions
        campaign_clicks += total_clicks
        campaign_revenue += total_revenue
        if cpm_value:
            campaign_cpm = cpm_value

    # Linha final com o total somando todos os tamanhos de criativo do conjunto
    campaign_ctr = round(campaign_clicks / campaign_impressions, 6) if campaign_impressions else 0
    total_rows.append(len(all_rows) + 1)
    all_rows.append([
        "", "Total da campanha", "", campaign_impressions, campaign_clicks,
        campaign_ctr, campaign_cpm, round(campaign_revenue, 2),
    ])

    ws.append_rows(all_rows, value_input_option="USER_ENTERED")

    for hr in header_rows:
        ws.format(f"A{hr}:H{hr}", {
            "backgroundColor": {"red": 0.384, "green": 0.176, "blue": 0.569},
            "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
            "horizontalAlignment": "CENTER",
        })
    for tr in total_rows:
        ws.format(f"A{tr}:H{tr}", {
            "backgroundColor": {"red": 0.937, "green": 0.910, "blue": 1.0},
            "textFormat": {"bold": True},
            "horizontalAlignment": "CENTER",
        })

    last_row = len(all_rows)
    ws.format(f"A1:H{last_row}", {"horizontalAlignment": "CENTER"})
    ws.format(f"D1:E{last_row}", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}})
    ws.format(f"F1:F{last_row}", {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}})
    ws.format(f"G1:H{last_row}", {"numberFormat": {"type": "CURRENCY", "pattern": "R$#,##0.00"}})

    return sh.url


def create_group_report_sheet(publisher_name, campaigns):
    """Cria uma planilha NOVA combinando VÁRIOS itens de linha do mesmo Order
    (que rodam no mesmo período) — soma Impressões/Cliques por dia entre
    todos eles, mantendo a mesma estrutura do relatório de campanha única
    (Data, Impressões, Cliques, CTR, Total)."""
    if publisher_name == DIARIO_DO_RIO_NAME:
        return create_diario_do_rio_group_sheet(publisher_name, campaigns)

    gc = get_sheets_client()

    name = campaigns[0]["name"]
    title = f"Relatório - {name} ({publisher_name}) - {len(campaigns)} itens"[:99]
    sh = retry_call(gc.create, title)
    ws = _RetryingWorksheet(sh.sheet1)
    ws.update_title("Relatório")

    daily = {}
    for c in campaigns:
        for day in (c.get("daily_stats") or []):
            date_str = day.get("date")
            if not date_str:
                continue
            entry = daily.setdefault(date_str, {"impressions": 0, "clicks": 0})
            entry["impressions"] += day.get("impressions", 0) or 0
            entry["clicks"] += day.get("clicks", 0) or 0

    header = ["Data", "Impressões", "Cliques", "CTR"]
    table_rows = [header]
    total_impressions = 0
    total_clicks = 0

    for date_str in sorted(daily.keys()):
        impressions = daily[date_str]["impressions"]
        clicks = daily[date_str]["clicks"]
        total_impressions += impressions
        total_clicks += clicks
        ctr = round((clicks / impressions) * 100, 2) if impressions else 0.0

        try:
            date_display = gm.datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
        except (ValueError, TypeError):
            date_display = date_str

        table_rows.append([f"'{date_display}", impressions, clicks, f"{ctr:.2f}%"])

    total_ctr = round((total_clicks / total_impressions) * 100, 2) if total_impressions else 0.0
    table_rows.append(["Total", total_impressions, total_clicks, f"{total_ctr:.2f}%"])

    ws.append_rows(table_rows, value_input_option="USER_ENTERED")

    header_row = 1
    total_row = header_row + len(table_rows) - 1

    ws.format(f"A{header_row}:D{header_row}", {
        "backgroundColor": {"red": 0.384, "green": 0.176, "blue": 0.569},
        "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
        "horizontalAlignment": "CENTER",
    })
    ws.format(f"A{total_row}:D{total_row}", {
        "backgroundColor": {"red": 0.937, "green": 0.910, "blue": 1.0},
        "textFormat": {"bold": True},
        "horizontalAlignment": "CENTER",
    })
    ws.format(f"B{header_row + 1}:B{total_row}", {
        "numberFormat": {"type": "NUMBER", "pattern": "#,##0"},
    })
    ws.format(f"A1:D{total_row}", {"horizontalAlignment": "CENTER"})

    return sh.url


@app.route("/")
def dashboard():
    """Serve o dashboard.html mais recente (gerado pelo gam_monitor.py),
    com o menu de navegação injetado no topo."""
    if not gm.DASHBOARD_PATH.exists():
        return (
            "<p style='font-family:sans-serif;padding:24px;'>Não encontrei o dashboard ainda. "
            "Rode <code>py gam_monitor.py</code> primeiro.</p>"
        )

    html = gm.DASHBOARD_PATH.read_text(encoding="utf-8")
    html = re.sub(r"(<body[^>]*>)", r"\1" + build_nav_bar("dashboard"), html, count=1)
    return html


@app.route("/relatorios")
def relatorios():
    campaigns_by_publisher = load_campaigns()
    report_files = load_report_files()

    all_month_keys = set()
    for campaigns in campaigns_by_publisher.values():
        for c in campaigns:
            all_month_keys.update(c.get("month_keys") or [])

    current_month_key = gm.datetime.now().strftime("%Y-%m")

    def month_sort_key(key):
        return (1, "") if key == "sem-data" else (0, key)

    visible_month_keys = [k for k in all_month_keys if k == "sem-data" or k <= current_month_key]
    month_options_html = "".join(
        f'<option value="{key}"{" selected" if key == current_month_key else ""}>{gm.month_label_for_key(key)}</option>'
        for key in sorted(visible_month_keys, key=month_sort_key)
    )

    publisher_sections_html = []
    for publisher in sorted(campaigns_by_publisher.keys()):
        campaigns = sorted(campaigns_by_publisher[publisher], key=lambda x: x["name"])
        publisher_escaped = gm.html_module.escape(publisher or "", quote=True)

        def _row_html(c, extra_class="", group_attr=""):
            months_attr = ",".join(c.get("month_keys") or [])
            status_label, status_class = gm.status_label_and_class(c.get("status"))
            name_escaped = gm.html_module.escape(c["name"] or "", quote=True)
            existing_url = report_files.get(str(c["id"]))
            result_html = (
                f'<a href="{existing_url}" target="_blank">Abrir planilha existente</a> · '
                f'<a href="#" onclick="generateReport(this, \'{c["id"]}\', true); return false;" '
                f'style="color:var(--muted);">🔄 Recriar</a>'
                if existing_url else ""
            )
            return f"""
              <div class="row {extra_class}" data-publisher="{publisher_escaped}" data-months="{months_attr}"
                   data-name="{name_escaped.lower()}"{group_attr}>
                <div class="row-main">
                  <span class="row-name">{name_escaped}</span>
                  <span class="status-pill {status_class}">{status_label}</span>
                </div>
                <button class="row-btn" onclick="generateReport(this, '{c['id']}')">Gerar relatório</button>
                <div class="row-result">{result_html}</div>
              </div>"""

        # Agrupa por Order (Pedido): quando o mesmo Order tem mais de um item
        # de linha, mostra um resumo com contagem e deixa expandir pra ver
        # cada item em detalhe (igual ao Dashboard).
        order_groups = {}
        order_seen = []
        for c in campaigns:
            key = c.get("order_id") or f"single-{c['id']}"
            if key not in order_groups:
                order_groups[key] = []
                order_seen.append(key)
            order_groups[key].append(c)

        rows_html = []
        for key in order_seen:
            for group in gm.group_by_temporal_overlap(order_groups[key]):
                if len(group) == 1:
                    rows_html.append(_row_html(group[0]))
                else:
                    ids_joined = ",".join(str(c["id"]) for c in group)
                    group_id = f"order-{key}-{ids_joined}"
                    status_priority = ["DELIVERING", "READY", "PAUSED", "COMPLETED"]
                    statuses_present = {c.get("status") for c in group if c.get("status")}
                    summary_status = next(
                        (s for s in status_priority if s in statuses_present), next(iter(statuses_present), None)
                    )
                    status_label, status_class = gm.status_label_and_class(summary_status)
                    name_escaped = gm.html_module.escape(group[0]["name"] or "", quote=True)
                    existing_url = report_files.get(ids_joined)
                    group_result_html = (
                        f'<a href="{existing_url}" target="_blank">Abrir planilha existente</a> · '
                        f'<a href="#" onclick="generateReport(this, \'{ids_joined}\', true); return false;" '
                        f'style="color:var(--muted);">🔄 Recriar</a>'
                        if existing_url else ""
                    )
                    rows_html.append(f"""
              <div class="row-group-summary" data-group-toggle="{group_id}">
                <div class="row-main">
                  <span class="chevron" onclick="toggleOrderGroup(this)">▸</span>
                  <span class="row-name">{name_escaped}</span>
                  <span class="badge-mini">{len(group)} itens</span>
                  <span class="status-pill {status_class}">{status_label}</span>
                </div>
                <button class="row-btn" onclick="generateReport(this, '{ids_joined}')">Gerar relatório do conjunto</button>
                <div class="row-result">{group_result_html}</div>
              </div>""")
                    for child in group:
                        rows_html.append(_row_html(
                            child, extra_class="group-child group-collapsed", group_attr=f' data-group="{group_id}"'
                        ))

        publisher_sections_html.append(f"""
          <div class="publisher" data-publisher="{publisher_escaped}">
            <div class="publisher-title" onclick="togglePublisher(this)">
              <span class="chevron">▸</span>
              <h2>{publisher_escaped}</h2>
              <span class="badge">{len(campaigns)} campanha(s)</span>
            </div>
            <div class="publisher-body collapsed">
              {''.join(rows_html)}
            </div>
          </div>""")

    nav_bar_html = build_nav_bar("relatorios")

    return f"""<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="icon" type="image/png" href="{gm.ADZEP_FAVICON_DATA_URI}">
<title>Relatórios — GAM Monitor</title>
<style>
  :root {{
    --bg:#f6f5f9; --card:#ffffff; --border:#eae6f0; --text:#241b33;
    --muted:#8a80a0; --brand-dark:#2f1440; --accent:#652f91; --accent-soft:#f1eaf8; --ok:#16a673; --ok-soft:#e3f8f0;
    --bad:#e5484d; --warn-soft:#fdf2e1; --done-soft:#ececec;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
    font-family:'Poppins',-apple-system,Segoe UI,Roboto,sans-serif; -webkit-font-smoothing:antialiased; }}
  header {{ background:linear-gradient(135deg, var(--brand-dark), var(--accent)); padding:18px 32px;
    box-shadow:0 2px 10px rgba(60,26,83,0.25); }}
  header h1 {{ color:#fff; font-size:19px; margin:0; font-weight:700; letter-spacing:.01em; }}
  main {{ max-width:860px; margin:26px auto 60px auto; padding:0 16px; }}

  .toolbar {{ background:var(--card); border:1px solid var(--border); border-radius:16px; padding:16px 20px;
    display:flex; gap:14px; flex-wrap:wrap; align-items:flex-end; margin-bottom:20px;
    box-shadow:0 2px 10px rgba(60,26,83,.06); }}
  .toolbar .field {{ flex:1; min-width:170px; }}
  .toolbar label {{ font-size:11px; font-weight:700; color:var(--muted); text-transform:uppercase;
    letter-spacing:.03em; display:block; margin-bottom:6px; }}
  .toolbar select {{ width:100%; padding:8px 12px; border:1px solid var(--border); border-radius:999px;
    background:var(--accent-soft); color:var(--accent); font-weight:600; font-size:13px; }}
  .toolbar input[type=text] {{ width:100%; padding:8px 12px; border:1px solid var(--border); border-radius:999px;
    font-size:13px; }}
  .summary-pill {{ font-size:12px; color:var(--muted); white-space:nowrap; padding-bottom:8px; }}

  .publisher {{ margin-bottom:12px; }}
  .publisher-title {{ display:flex; align-items:center; gap:10px; cursor:pointer; background:var(--card);
    border:1px solid var(--border); border-radius:14px; padding:13px 18px; user-select:none;
    box-shadow:0 1px 3px rgba(60,26,83,.06); }}
  .publisher-title:hover {{ border-color:var(--accent); }}
  .publisher-title h2 {{ font-size:14px; margin:0; color:var(--text); font-weight:600; }}
  .chevron {{ font-size:12px; color:var(--muted); width:10px; display:inline-block; }}
  .badge {{ background:var(--accent-soft); color:var(--accent); font-size:11px; font-weight:700;
    padding:2px 9px; border-radius:999px; }}
  .publisher-body {{ background:var(--card); border:1px solid var(--border); border-top:none;
    border-radius:0 0 14px 14px; margin-top:-1px; }}
  .publisher-body.collapsed {{ display:none; }}

  .row {{ display:flex; align-items:center; gap:12px; padding:13px 18px; border-bottom:1px solid var(--border);
    flex-wrap:wrap; }}
  .row:last-child {{ border-bottom:none; }}
  .row-main {{ flex:1; min-width:220px; display:flex; align-items:center; gap:10px; }}
  .row-name {{ font-size:13px; color:var(--text); }}
  .status-pill {{ font-size:10px; font-weight:700; padding:2px 8px; border-radius:999px; white-space:nowrap; }}
  .status-live {{ background:var(--ok-soft); color:var(--ok); }}
  .status-scheduled {{ background:var(--accent-soft); color:var(--accent); }}
  .status-paused {{ background:var(--warn-soft); color:#b5760a; }}
  .status-done {{ background:var(--done-soft); color:#6b6b6b; }}
  .status-other {{ background:var(--done-soft); color:#6b6b6b; }}
  .row-btn {{ background:linear-gradient(135deg, var(--brand-dark), var(--accent)); color:#fff; border:none;
    border-radius:999px; padding:8px 18px; font-size:12px; font-weight:700; cursor:pointer; white-space:nowrap; }}
  .row-btn:disabled {{ opacity:.6; cursor:default; }}
  .row-result {{ width:100%; font-size:12px; }}
  .row-result a {{ color:var(--accent); font-weight:700; }}
  .row-result .error {{ color:var(--bad); }}

  .row-group-summary {{ display:flex; align-items:center; gap:12px; padding:13px 18px; flex-wrap:wrap;
    border-bottom:1px solid var(--border); background:var(--accent-soft); }}
  .row-group-summary .row-main {{ flex:1; min-width:220px; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
  .row-group-summary .row-name {{ font-weight:700; }}
  .row-group-summary .row-result {{ width:100%; font-size:12px; }}
  .badge-mini {{ font-size:10px; font-weight:700; background:var(--card); color:var(--accent);
    padding:2px 8px; border-radius:999px; white-space:nowrap; }}
  .row.group-child .row-main {{ padding-left:20px; }}
  .group-collapsed {{ display:none !important; }}

  .empty-state {{ text-align:center; color:var(--muted); font-size:13px; padding:40px 0; }}
</style>
</head>
<body>
{nav_bar_html}
<header><h1>Relatórios — GAM Monitor</h1></header>
<main>
  <div class="toolbar">
    <div class="field">
      <label for="month-filter">Mês</label>
      <select id="month-filter" onchange="applyFilters()">
        <option value="all">Todos os meses</option>
        {month_options_html}
      </select>
    </div>
    <div class="field">
      <label for="publisher-filter">Publisher</label>
      <select id="publisher-filter" onchange="applyFilters()">
        <option value="all">Todos os publishers</option>
      </select>
    </div>
    <div class="field">
      <label for="search">Buscar</label>
      <input type="text" id="search" placeholder="Nome da campanha..." oninput="applyFilters()">
    </div>
    <span class="summary-pill" id="summary-pill"></span>
  </div>

  <div class="toolbar" style="gap:20px;">
    <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text);
                  text-transform:none;font-weight:500;cursor:pointer;">
      <input type="checkbox" id="opt-creative-size" style="width:16px;height:16px;">
      Detalhar por tamanho do criativo
    </label>
    <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text);
                  text-transform:none;font-weight:500;cursor:pointer;">
      <input type="checkbox" id="opt-revenue" style="width:16px;height:16px;">
      Incluir CPM e Receita
    </label>
    <span class="summary-pill" style="margin-left:auto;">
      Essas opções valem pro próximo relatório que você gerar (busca dados ao vivo, pode demorar mais)
    </span>
  </div>

  <div id="publishers-list">
    {''.join(publisher_sections_html) if publisher_sections_html else '<div class="empty-state">Nenhum dado encontrado — rode py gam_monitor.py primeiro.</div>'}
  </div>
</main>
<script>
function togglePublisher(headerEl) {{
  var pub = headerEl.closest('.publisher');
  var body = pub.querySelector('.publisher-body');
  body.classList.toggle('collapsed');
  var chevron = headerEl.querySelector('.chevron');
  chevron.textContent = body.classList.contains('collapsed') ? '▸' : '▾';
}}

function updatePublisherFilterOptions() {{
  var selectedMonth = document.getElementById('month-filter').value;
  var publisherSelect = document.getElementById('publisher-filter');
  var currentValue = publisherSelect.value;

  var validNames = new Set();
  document.querySelectorAll('.row').forEach(function(row) {{
    var months = (row.getAttribute('data-months') || '').split(',');
    if (selectedMonth === 'all' || months.indexOf(selectedMonth) !== -1) {{
      validNames.add(row.getAttribute('data-publisher'));
    }}
  }});

  var sortedNames = Array.from(validNames).sort();
  publisherSelect.textContent = '';
  var allOpt = document.createElement('option');
  allOpt.value = 'all';
  allOpt.textContent = 'Todos os publishers';
  publisherSelect.appendChild(allOpt);
  sortedNames.forEach(function(name) {{
    var opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    publisherSelect.appendChild(opt);
  }});
  publisherSelect.value = validNames.has(currentValue) ? currentValue : 'all';
}}

function applyFilters() {{
  updatePublisherFilterOptions();

  var selectedMonth = document.getElementById('month-filter').value;
  var selectedPublisher = document.getElementById('publisher-filter').value;
  var searchTerm = document.getElementById('search').value.toLowerCase();
  var visibleCount = 0;

  document.querySelectorAll('.publisher').forEach(function(pub) {{
    var pubMatches = selectedPublisher === 'all' || pub.getAttribute('data-publisher') === selectedPublisher;
    var visibleInPub = 0;

    pub.querySelectorAll('.row').forEach(function(row) {{
      var months = (row.getAttribute('data-months') || '').split(',');
      var monthMatch = selectedMonth === 'all' || months.indexOf(selectedMonth) !== -1;
      var textMatch = (row.getAttribute('data-name') || '').includes(searchTerm);
      var match = pubMatches && monthMatch && textMatch;
      row.style.display = match ? '' : 'none';
      if (match) {{ visibleInPub++; visibleCount++; }}
    }});

    pub.style.display = visibleInPub > 0 ? '' : 'none';
    var badge = pub.querySelector('.badge');
    if (badge) {{ badge.textContent = visibleInPub + ' campanha(s)'; }}
  }});

  // Resumo de grupo (Order com vários itens) só aparece se algum item filho estiver visível
  document.querySelectorAll('.row-group-summary').forEach(function(summary) {{
    var groupId = summary.getAttribute('data-group-toggle');
    var anyVisible = false;
    document.querySelectorAll('[data-group="' + groupId + '"]').forEach(function(child) {{
      if (child.style.display !== 'none') {{ anyVisible = true; }}
    }});
    summary.style.display = anyVisible ? '' : 'none';
  }});

  document.getElementById('summary-pill').textContent = visibleCount + ' campanha(s) exibida(s)';
}}

document.addEventListener('DOMContentLoaded', applyFilters);

function toggleOrderGroup(chevronEl) {{
  var container = chevronEl.closest('[data-group-toggle]');
  var groupId = container.getAttribute('data-group-toggle');
  var isNowCollapsed = null;

  document.querySelectorAll('[data-group="' + groupId + '"]').forEach(function(el) {{
    el.classList.toggle('group-collapsed');
    isNowCollapsed = el.classList.contains('group-collapsed');
  }});

  chevronEl.textContent = isNowCollapsed ? '▸' : '▾';
}}

function generateReport(btn, campaignId, force) {{
  var row = btn.closest('.row, .row-group-summary');
  var resultEl = row.querySelector('.row-result');
  var mainBtn = row.querySelector('.row-btn');
  var breakByCreativeSize = document.getElementById('opt-creative-size').checked;
  var includeRevenue = document.getElementById('opt-revenue').checked;

  if (mainBtn) {{ mainBtn.disabled = true; }}
  resultEl.textContent = (force ? 'Recriando...' : 'Gerando...') +
    (breakByCreativeSize || includeRevenue ? ' (buscando dados ao vivo, pode demorar um pouco)' : '');

  fetch('/api/generate', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      campaign_id: campaignId,
      force: !!force,
      break_by_creative_size: breakByCreativeSize,
      include_revenue: includeRevenue,
    }}),
  }})
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      if (mainBtn) {{ mainBtn.disabled = false; }}
      if (data.status === 'ok') {{
        resultEl.innerHTML = 'Pronto! <a href="' + data.url + '" target="_blank">Abrir planilha</a>' +
          (data.reused ? ' (já existia, reaproveitado)' : '') +
          ' · <a href="#" onclick="generateReport(this, \\'' + campaignId + '\\', true); return false;" ' +
          'style="color:var(--muted);">🔄 Recriar</a>';
      }} else {{
        resultEl.innerHTML = '<span class="error">Erro: ' + data.message + '</span>';
      }}
    }})
    .catch(function(err) {{
      if (mainBtn) {{ mainBtn.disabled = false; }}
      resultEl.innerHTML = '<span class="error">Erro: ' + err + '</span>';
    }});
}}
</script>
</body>
</html>"""


def _campaign_period_stats(c, month_filter):
    """Impressões/cliques só do período selecionado. Quando um mês específico
    está escolhido, soma o detalhamento diário filtrado por aquele mês (em vez
    do total acumulado da vida inteira da campanha). Com 'Todos os meses',
    usa o total geral mesmo (é o que faz sentido nesse caso)."""
    if month_filter in ("all", "sem-data"):
        return c["impressions"], c["clicks"]

    daily_stats = c.get("daily_stats") or []
    impressions = sum(
        d.get("impressions", 0) or 0 for d in daily_stats if (d.get("date") or "").startswith(month_filter)
    )
    clicks = sum(
        d.get("clicks", 0) or 0 for d in daily_stats if (d.get("date") or "").startswith(month_filter)
    )
    return impressions, clicks


def _aggregate_totals(campaigns_by_publisher, publisher_filter, month_filter):
    """Só os totais (impressões/cliques) pra um publisher+mês — usado tanto
    no cálculo principal quanto pra comparar com o mês anterior."""
    total_impressions = 0
    total_clicks = 0
    for publisher, campaigns in campaigns_by_publisher.items():
        if publisher_filter != "all" and publisher != publisher_filter:
            continue
        for c in campaigns:
            months = c.get("month_keys") or []
            if month_filter != "all" and month_filter not in months:
                continue
            impressions, clicks = _campaign_period_stats(c, month_filter)
            total_impressions += impressions
            total_clicks += clicks
    return total_impressions, total_clicks


def _previous_month_key(month_key):
    if month_key == "all" or month_key == "sem-data":
        return None
    year, month = month_key.split("-")
    year, month = int(year), int(month)
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def compute_insights(campaigns_by_publisher, publisher_filter, month_filter):
    """Agrega os dados (já em cache, sem chamar o GAM) pra montar a tela de
    Insights: KPIs, série diária, distribuição por status, ranking e tabela."""
    matched = []
    for publisher, campaigns in campaigns_by_publisher.items():
        if publisher_filter != "all" and publisher != publisher_filter:
            continue
        for c in campaigns:
            months = c.get("month_keys") or []
            if month_filter != "all" and month_filter not in months:
                continue
            impressions, clicks = _campaign_period_stats(c, month_filter)
            ctr = round((clicks / impressions) * 100, 2) if impressions else 0.0
            matched.append((publisher, c, impressions, clicks, ctr))

    total_impressions = sum(imp for _, _, imp, _, _ in matched)
    total_clicks = sum(clk for _, _, _, clk, _ in matched)
    blended_ctr = round((total_clicks / total_impressions) * 100, 2) if total_impressions else 0

    va_num = sum((c["va_rate"] or 0) * imp for _, c, imp, _, _ in matched if c.get("va_rate") is not None)
    va_den = sum(imp for _, c, imp, _, _ in matched if c.get("va_rate") is not None)
    blended_va = round(va_num / va_den, 2) if va_den else None

    status_counts = {}
    for _, c, _, _, _ in matched:
        label, _ = gm.status_label_and_class(c.get("status"))
        status_counts[label] = status_counts.get(label, 0) + 1

    # Série diária: soma impressões/cliques de todas as campanhas que casam com o filtro
    daily = {}
    for _, c, _, _, _ in matched:
        for day in (c.get("daily_stats") or []):
            date_str = day.get("date")
            if not date_str:
                continue
            if month_filter != "all" and not date_str.startswith(month_filter):
                continue
            entry = daily.setdefault(date_str, {"impressions": 0, "clicks": 0})
            entry["impressions"] += day.get("impressions", 0) or 0
            entry["clicks"] += day.get("clicks", 0) or 0
    daily_series = [{"date": d, **v} for d, v in sorted(daily.items())]

    # Comparativo com o mês anterior (mesmo publisher)
    comparison = None
    prev_key = _previous_month_key(month_filter)
    if prev_key:
        prev_impressions, prev_clicks = _aggregate_totals(campaigns_by_publisher, publisher_filter, prev_key)
        if prev_impressions:
            comparison = {
                "previous_month_label": gm.month_label_for_key(prev_key),
                "impressions_change_pct": round(((total_impressions - prev_impressions) / prev_impressions) * 100, 1),
                "clicks_change_pct": (
                    round(((total_clicks - prev_clicks) / prev_clicks) * 100, 1) if prev_clicks else None
                ),
                "previous_impressions": prev_impressions,
                "previous_clicks": prev_clicks,
            }

    # Meta atingida: entre as campanhas com meta definida, quantas bateram (>=95% do esperado)
    # (isso é sobre a campanha inteira vs. a meta contratada, não é recortado por mês)
    with_goal = [c for _, c, _, _, _ in matched if c.get("goal_units")]
    goal_achieved = [c for c in with_goal if (c.get("actual_pct") or 0) >= 95]
    goal_summary = {
        "with_goal": len(with_goal),
        "achieved": len(goal_achieved),
        "pct": round((len(goal_achieved) / len(with_goal)) * 100, 1) if with_goal else None,
    }

    # Ranking por CTR (só campanhas com volume mínimo, pra não distorcer com amostras pequenas)
    ctr_eligible = [(p, c, ctr) for p, c, imp, _, ctr in matched if imp >= 500]
    top_ctr = sorted(ctr_eligible, key=lambda pcc: pcc[2], reverse=True)[:5]
    top_ctr_data = [{"name": c["name"], "publisher": p, "ctr": ctr} for p, c, ctr in top_ctr]

    # Distribuição por tipo de campanha (impressões do período)
    type_breakdown = {}
    for _, c, imp, _, _ in matched:
        t = c.get("type") or "—"
        type_breakdown[t] = type_breakdown.get(t, 0) + imp

    # Padrão por dia da semana (média de impressões)
    weekday_totals = [0] * 7
    weekday_counts = [0] * 7
    for entry in daily_series:
        try:
            d = gm.datetime.strptime(entry["date"], "%Y-%m-%d")
        except ValueError:
            continue
        weekday_totals[d.weekday()] += entry["impressions"]
        weekday_counts[d.weekday()] += 1
    weekday_labels = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
    weekday_avg = [
        round(weekday_totals[i] / weekday_counts[i]) if weekday_counts[i] else 0
        for i in range(7)
    ]

    # Ranking: top 10 campanhas por impressões do período
    top_campaigns = sorted(matched, key=lambda pcc: pcc[2], reverse=True)[:10]
    top_campaigns_data = [
        {"name": c["name"], "publisher": publisher, "impressions": imp}
        for publisher, c, imp, _, _ in top_campaigns
    ]

    # Tabela detalhada (sem alertas de underdelivery/prazo — é pra mandar pro publisher)
    table_rows = []
    for publisher, c, imp, clk, ctr in sorted(matched, key=lambda pcc: (pcc[0], pcc[1]["name"])):
        status_label, _ = gm.status_label_and_class(c.get("status"))
        table_rows.append({
            "publisher": publisher,
            "name": c["name"],
            "type": c["type"],
            "status": status_label,
            "actual_pct": c.get("actual_pct"),
            "expected_pct": c.get("expected_pct"),
            "impressions": imp,
            "clicks": clk,
            "ctr": ctr,
            "va_rate": c.get("va_rate"),
        })

    return {
        "kpis": {
            "total_campaigns": len(matched),
            "total_impressions": total_impressions,
            "total_clicks": total_clicks,
            "blended_ctr": blended_ctr,
            "blended_va": blended_va,
        },
        "comparison": comparison,
        "goal_summary": goal_summary,
        "top_ctr": top_ctr_data,
        "type_breakdown": type_breakdown,
        "weekday_labels": weekday_labels,
        "weekday_avg": weekday_avg,
        "status_counts": status_counts,
        "daily_series": daily_series,
        "top_campaigns": top_campaigns_data,
        "table_rows": table_rows,
    }


def fetch_network_total_impressions(client, month_key):
    """Busca as impressões da rede POR AD UNIT (nível de topo) e POR DIA num
    mês — devolve {'ad_unit_id': {'AAAA-MM-DD': impressões}}. Isso serve pra
    duas coisas com uma chamada só: somar tudo pra ter o total da REDE
    inteira, ou pegar só a chave de um Ad Unit específico pra ter o total
    daquele SITE (bem mais preciso quando a rede é compartilhada por vários
    publishers). Usa a coluna TOTAL_LINE_ITEM_LEVEL_IMPRESSIONS (= "Total
    impressions" na UI), que é a única que soma TUDO — a AD_SERVER_IMPRESSIONS
    ignora o que foi vendido via Ad Exchange, subestimando o total.

    Precisa de uma dimensão de inventário (Ad Unit) além de DATE — um
    relatório só com DATE (sem nenhuma dimensão de entidade) não é aceito
    pela API com range de data customizado."""
    import calendar
    year, month = map(int, month_key.split("-"))
    last_day = calendar.monthrange(year, month)[1]

    # Nunca pede um range que inclua dias no futuro (isso é o que causava o
    # erro DATE_RANGE_NOT_SUPPORTED_FOR_REQUESTED_REPORT quando o mês
    # selecionado era o mês atual, ainda não terminado).
    today = gm.datetime.now(gm.timezone.utc)
    end_day = last_day
    end_month = month
    end_year = year
    if (year, month) == (today.year, today.month):
        end_day = today.day
    elif (year, month) > (today.year, today.month):
        # Mês inteiro no futuro — não tem como buscar nada ainda
        return {}

    report_service = client.GetService("ReportService", version=gm.GAM_API_VERSION)
    report_job = {
        "reportQuery": {
            "dimensions": ["AD_UNIT_ID", "DATE"],
            "adUnitView": "TOP_LEVEL",  # só as ad units de nível mais alto, evita contar a hierarquia em dobro
            "columns": ["TOTAL_LINE_ITEM_LEVEL_IMPRESSIONS"],  # "Total impressions" na UI — inclui Ad Exchange
            "dateRangeType": "CUSTOM_DATE",
            "startDate": {"year": year, "month": month, "day": 1},
            "endDate": {"year": end_year, "month": end_month, "day": end_day},
        }
    }
    saved_job = gm._to_dict(retry_call(report_service.runReportJob, report_job))
    job_id = saved_job["id"]

    status = saved_job.get("reportJobStatus")
    waited = 0
    while status not in ("COMPLETED", "FAILED") and waited < 120:
        gm.time.sleep(3)
        waited += 3
        status = retry_call(report_service.getReportJobStatus, job_id)

    if status != "COMPLETED":
        raise RuntimeError(f"Relatório de inventário não completou a tempo (status={status}).")

    downloader = client.GetDataDownloader(version=gm.GAM_API_VERSION)
    report_file = gm.io.BytesIO()
    retry_call(downloader.DownloadReportToFile, job_id, "CSV_DUMP", report_file, use_gzip_compression=True)
    report_file.seek(0)

    with gm.gzip.GzipFile(fileobj=report_file) as gz:
        content = gz.read().decode("utf-8")

    reader = gm.csv.DictReader(gm.io.StringIO(content))
    headers = reader.fieldnames or []
    date_col = next((h for h in headers if "DATE" in h.upper() and "AD_UNIT" not in h.upper()), None)
    ad_unit_col = next((h for h in headers if "AD_UNIT_ID" in h.upper()), None)
    imp_col = next((h for h in headers if "TOTAL_LINE_ITEM_LEVEL_IMPRESSIONS" in h.upper()), None)

    by_ad_unit = {}
    for row in reader:
        try:
            impressions = int(row.get(imp_col) or 0)
        except ValueError:
            continue
        date_str = row.get(date_col) if date_col else None
        ad_unit_id = row.get(ad_unit_col) if ad_unit_col else None
        if not date_str or not ad_unit_id:
            continue
        daily = by_ad_unit.setdefault(str(ad_unit_id), {})
        daily[date_str] = daily.get(date_str, 0) + impressions

    return by_ad_unit


def _parse_campaign_dates(c):
    """Converte start_date/end_date (strings salvas no cache) de volta pra
    datetime, pra dar pra calcular duração e meses cobertos."""
    start_dt = None
    end_dt = None
    if c.get("start_date"):
        try:
            start_dt = gm.datetime.strptime(c["start_date"], "%d/%m/%Y")
        except ValueError:
            pass
    if c.get("end_date"):
        try:
            end_dt = gm.datetime.strptime(c["end_date"], "%d/%m/%Y %H:%M")
        except ValueError:
            pass
    return start_dt, end_dt


def _calendar_months_between(start_dt, end_dt):
    """Lista de 'YYYY-MM' cobrindo do mês de início ao mês de fim (inclusive),
    calculado direto das datas de início/fim (sem depender do que já foi
    entregue de verdade — a regra de cobrança é sobre o período contratado)."""
    months = []
    year, month = start_dt.year, start_dt.month
    for _ in range(60):
        months.append(f"{year:04d}-{month:02d}")
        if (year, month) >= (end_dt.year, end_dt.month):
            break
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months


def compute_billing(campaigns_by_publisher, publisher_filter, month_filter, price_per_campaign):
    """Calcula quantas campanhas são cobradas num mês, pra publishers que
    pagam por campanha (regra: conta no mês de início; se a campanha durar
    MAIS de 30 dias e atravessar mais de um mês, conta em todos os meses que
    atravessa; se durar até 30 dias, conta só no mês em que começou)."""
    campaigns = campaigns_by_publisher.get(publisher_filter, [])
    rows = []

    for c in campaigns:
        start_dt, end_dt = _parse_campaign_dates(c)
        if not start_dt or not end_dt:
            continue

        duration_days = (end_dt.date() - start_dt.date()).days
        months_spanned = _calendar_months_between(start_dt, end_dt)
        start_month_key = f"{start_dt.year:04d}-{start_dt.month:02d}"

        if len(months_spanned) <= 1:
            billed_months = set(months_spanned)
        elif duration_days > 30:
            billed_months = set(months_spanned)
        else:
            billed_months = {start_month_key}

        # Só entra na lista se realmente for COBRADA nesse mês (não só "tocar" ele)
        if month_filter not in billed_months:
            continue

        rows.append({
            "name": c["name"],
            "status": gm.status_label_and_class(c.get("status"))[0],
            "start_date": c.get("start_date"),
            "end_date": c.get("end_date"),
            "duration_days": duration_days,
            "billed_months": sorted(billed_months),
            "_start_dt": start_dt,  # só pra ordenar, removido antes de devolver
        })

    rows.sort(key=lambda r: r["_start_dt"])
    for r in rows:
        del r["_start_dt"]

    billed_count = len(rows)
    revenue = round(billed_count * price_per_campaign, 2)

    return {
        "rows": rows,
        "billed_count": billed_count,
        "total_campaigns": len(rows),
        "revenue": revenue,
        "price_per_campaign": price_per_campaign,
    }


@app.route("/api/insights")
def api_insights():
    publisher_filter = request.args.get("publisher", "all")
    month_filter = request.args.get("month", "all")
    campaigns_by_publisher = load_campaigns()
    data = compute_insights(campaigns_by_publisher, publisher_filter, month_filter)
    return jsonify(data)


def _resolve_period_range(month_filter, period, custom_start=None, custom_end=None):
    """Devolve (data_inicio, data_fim, meses_envolvidos) pro período
    escolhido nesse card específico. 'month' usa o mês inteiro selecionado
    no filtro principal da página; '7d'/'30d' usam uma janela móvel
    terminando hoje, que pode atravessar a virada do mês (por isso pode
    envolver 2 meses, não só 1); 'custom' usa as datas exatas escolhidas
    pelo usuário nesse card."""
    import calendar
    today = gm.datetime.now(gm.timezone.utc)

    if period == "custom" and custom_start and custom_end:
        start_dt = gm.datetime.strptime(custom_start, "%Y-%m-%d").replace(tzinfo=gm.timezone.utc)
        end_dt = gm.datetime.strptime(custom_end, "%Y-%m-%d").replace(tzinfo=gm.timezone.utc)
        if end_dt < start_dt:
            start_dt, end_dt = end_dt, start_dt  # protege contra datas invertidas
        if end_dt > today:
            end_dt = today  # nunca inclui dias no futuro
    elif period in ("7d", "30d"):
        days = 7 if period == "7d" else 30
        end_dt = today
        start_dt = today - timedelta(days=days - 1)
    else:
        year, month = map(int, month_filter.split("-"))
        last_day = calendar.monthrange(year, month)[1]
        start_dt = gm.datetime(year, month, 1, tzinfo=gm.timezone.utc)
        end_day = min(last_day, today.day) if (year, month) == (today.year, today.month) else last_day
        end_dt = gm.datetime(year, month, end_day, tzinfo=gm.timezone.utc)

    return _months_spanned_and_format(start_dt, end_dt)


def _months_spanned_and_format(start_dt, end_dt):
    months_spanned = set()
    cursor = start_dt.replace(day=1)
    while cursor <= end_dt:
        months_spanned.add(cursor.strftime("%Y-%m"))
        cursor = (
            cursor.replace(year=cursor.year + 1, month=1)
            if cursor.month == 12 else cursor.replace(month=cursor.month + 1)
        )
    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"), sorted(months_spanned)


def _resolve_previous_period_range(start_date, end_date, period, month_filter):
    """Devolve o intervalo ANTERIOR, de mesma duração, imediatamente antes
    do período atual — pra comparar 'essa semana' com 'a semana passada',
    por exemplo. Pra '7d'/'30d', é só voltar N dias antes do início atual.
    Pra 'month', é o mês anterior inteiro (ou só até o mesmo dia relativo,
    se o mês atual ainda estiver em andamento — compara período igual)."""
    import calendar
    start_dt = gm.datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=gm.timezone.utc)
    end_dt = gm.datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=gm.timezone.utc)

    if period in ("7d", "30d", "custom"):
        length_days = (end_dt - start_dt).days + 1
        prev_end_dt = start_dt - timedelta(days=1)
        prev_start_dt = prev_end_dt - timedelta(days=length_days - 1)
    else:
        year, month = map(int, month_filter.split("-"))
        if month == 1:
            prev_year, prev_month = year - 1, 12
        else:
            prev_year, prev_month = year, month - 1
        prev_last_day = calendar.monthrange(prev_year, prev_month)[1]
        days_in_current = (end_dt - start_dt).days + 1
        prev_start_dt = gm.datetime(prev_year, prev_month, 1, tzinfo=gm.timezone.utc)
        prev_end_dt = gm.datetime(prev_year, prev_month, min(prev_last_day, days_in_current), tzinfo=gm.timezone.utc)

    return _months_spanned_and_format(prev_start_dt, prev_end_dt)


def _compute_inventory_share_for_range(
    start_date, end_date, months_spanned, publisher_filter, network_codes, primary_ad_unit, campaigns_by_publisher
):
    """Calcula o % de inventário consumido (e a série diária) pra QUALQUER
    intervalo de datas já resolvido — reaproveitado tanto pro período atual
    quanto pro período anterior de comparação."""
    network_by_ad_unit = {}
    for nc in network_codes:
        for month_key in months_spanned:
            for au_id, daily in get_network_total_impressions_cached(nc, month_key).items():
                merged = network_by_ad_unit.setdefault(au_id, {})
                for date_str, impr in daily.items():
                    if start_date <= date_str <= end_date:
                        merged[date_str] = merged.get(date_str, 0) + impr

    if primary_ad_unit and primary_ad_unit in network_by_ad_unit:
        denominator_daily = network_by_ad_unit[primary_ad_unit]
        scope_label = "ad_unit"
    else:
        denominator_daily = {}
        for daily in network_by_ad_unit.values():
            for date_str, impr in daily.items():
                denominator_daily[date_str] = denominator_daily.get(date_str, 0) + impr
        scope_label = "network"

    total_network_impressions = sum(denominator_daily.values())

    DIRECT_LINE_ITEM_TYPES = {"STANDARD", "SPONSORSHIP"}
    direct_daily = {}
    matched_impressions = 0
    for publisher, campaigns in campaigns_by_publisher.items():
        if publisher_filter != "all" and publisher != publisher_filter:
            continue
        for c in campaigns:
            if (c.get("type") or "").upper() not in DIRECT_LINE_ITEM_TYPES:
                continue
            if str(c.get("network_code")) not in network_codes:
                continue
            if not (set(c.get("month_keys") or []) & set(months_spanned)):
                continue
            for day in (c.get("daily_stats") or []):
                date_str = day.get("date")
                if not date_str or not (start_date <= date_str <= end_date):
                    continue
                impr = day.get("impressions", 0) or 0
                direct_daily[date_str] = direct_daily.get(date_str, 0) + impr
                matched_impressions += impr

    share_series = []
    for date_str in sorted(set(direct_daily) | set(denominator_daily)):
        denom_that_day = denominator_daily.get(date_str, 0)
        share_pct_day = (
            round((direct_daily.get(date_str, 0) / denom_that_day) * 100, 2)
            if denom_that_day else None
        )
        share_series.append({"date": date_str, "share_pct": share_pct_day})

    return {
        "share_pct": (
            round((matched_impressions / total_network_impressions) * 100, 2)
            if total_network_impressions else 0
        ),
        "matched_impressions": matched_impressions,
        "total_network_impressions": total_network_impressions,
        "scope": scope_label,
        "share_series": share_series,
        "start_date": start_date,
        "end_date": end_date,
    }


@app.route("/api/insights/inventory-share")
def api_insights_inventory_share():
    """Separado do /api/insights porque busca ao vivo no GAM (mais lento) —
    fica numa chamada própria pra não travar o resto da página enquanto
    calcula."""
    publisher_filter = request.args.get("publisher", "all")
    month_filter = request.args.get("month", "all")
    period = request.args.get("period", "month")  # "month", "30d", "7d" ou "custom"
    custom_start = request.args.get("start")
    custom_end = request.args.get("end")
    campaigns_by_publisher = load_campaigns()

    inventory_share = None
    if month_filter not in ("all", "sem-data"):
        start_date, end_date, months_spanned = _resolve_period_range(
            month_filter, period, custom_start, custom_end
        )

        # Descobre a rede predominante desse publisher (igual antes) e, além
        # dela, o AD UNIT PAI predominante — cada publisher tem o seu, então
        # comparar só com o Ad Unit dele (não a rede toda) dá um percentual
        # preciso mesmo quando a rede é compartilhada por vários publishers.
        # Pra decidir isso, usa qualquer um dos meses envolvidos no período.
        network_campaign_counts = {}
        ad_unit_campaign_counts = {}
        for publisher, campaigns in campaigns_by_publisher.items():
            if publisher_filter != "all" and publisher != publisher_filter:
                continue
            for c in campaigns:
                if not (set(c.get("month_keys") or []) & set(months_spanned)):
                    continue
                if c.get("network_code"):
                    nc = str(c["network_code"])
                    network_campaign_counts[nc] = network_campaign_counts.get(nc, 0) + 1
                if c.get("root_ad_unit_id"):
                    au = str(c["root_ad_unit_id"])
                    ad_unit_campaign_counts[au] = ad_unit_campaign_counts.get(au, 0) + 1

        if not network_campaign_counts:
            return jsonify({"inventory_share": None})

        primary_network = max(network_campaign_counts, key=network_campaign_counts.get)
        network_codes = {primary_network}

        # Ad Unit pai predominante — só usamos esse caminho (mais preciso) se
        # der pra identificar um com confiança. Cache antigo (gerado antes
        # dessa função existir) não vai ter "root_ad_unit_id" nas campanhas —
        # nesse caso cai pro método por rede inteira, mais abaixo.
        primary_ad_unit = (
            max(ad_unit_campaign_counts, key=ad_unit_campaign_counts.get)
            if ad_unit_campaign_counts and publisher_filter != "all" else None
        )

        try:
            current = _compute_inventory_share_for_range(
                start_date, end_date, months_spanned,
                publisher_filter, network_codes, primary_ad_unit, campaigns_by_publisher,
            )

            prev_start, prev_end, prev_months = _resolve_previous_period_range(
                start_date, end_date, period, month_filter
            )
            previous = _compute_inventory_share_for_range(
                prev_start, prev_end, prev_months,
                publisher_filter, network_codes, primary_ad_unit, campaigns_by_publisher,
            )

            ad_unit_name = (
                next(
                    (c.get("root_ad_unit_name") for campaigns in campaigns_by_publisher.values()
                     for c in campaigns if str(c.get("root_ad_unit_id")) == primary_ad_unit),
                    None,
                ) if current["scope"] == "ad_unit" else None
            )

            inventory_share = {
                **current,
                "ad_unit_name": ad_unit_name,
                "network_codes": sorted(network_codes),
                "previous": previous,
            }
        except Exception as e:
            inventory_share = {"error": str(e)}

    return jsonify({"inventory_share": inventory_share})


@app.route("/api/billing")
def api_billing():
    publisher_filter = request.args.get("publisher", "")
    month_filter = request.args.get("month", "")
    try:
        price = float(request.args.get("price", "70").replace(",", "."))
    except ValueError:
        price = 70.0

    campaigns_by_publisher = load_campaigns()
    if not publisher_filter or not month_filter:
        return jsonify({"rows": [], "billed_count": 0, "total_campaigns": 0, "revenue": 0, "price_per_campaign": price})

    data = compute_billing(campaigns_by_publisher, publisher_filter, month_filter, price)
    return jsonify(data)


def collect_all_anomalies(campaigns_by_publisher):
    """Percorre o cache e junta todas as campanhas com alguma anomalia
    detectada (calculada pelo gam_monitor.py), separadas por tipo e
    ordenadas da mais grave pra menos grave dentro de cada tipo."""
    zeroed, drops, spikes = [], [], []
    for publisher, campaigns in campaigns_by_publisher.items():
        for c in campaigns:
            anomaly = c.get("anomaly")
            if not anomaly:
                continue
            entry = {
                "publisher": publisher,
                "name": c["name"],
                "gam_url": c.get("gam_url"),
                "date": anomaly["date"],
                "last_impressions": anomaly["last_impressions"],
                "avg_impressions": anomaly["avg_impressions"],
                "expected": anomaly.get("expected", anomaly["avg_impressions"]),
                "change_pct": anomaly.get("change_pct"),
            }
            if anomaly["type"] == "zeroed":
                zeroed.append(entry)
            elif anomaly["type"] == "drop":
                drops.append(entry)
            elif anomaly["type"] == "spike":
                spikes.append(entry)

    zeroed.sort(key=lambda e: e["avg_impressions"], reverse=True)
    drops.sort(key=lambda e: e["change_pct"])  # mais negativo primeiro
    spikes.sort(key=lambda e: e["change_pct"], reverse=True)  # maior alta primeiro
    return zeroed, drops, spikes


@app.route("/alertas")
def alertas():
    campaigns_by_publisher = load_campaigns()
    nav_bar_html = build_nav_bar("alertas")
    zeroed, drops, spikes = collect_all_anomalies(campaigns_by_publisher)

    def _row(e):
        name_escaped = gm.html_module.escape(e["name"] or "", quote=True)
        name_html = (
            f'<a href="{gm.html_module.escape(e["gam_url"], quote=True)}" target="_blank" rel="noopener" '
            f'style="color:inherit;text-decoration:none;border-bottom:1px dotted var(--accent);">{name_escaped}</a>'
            if e.get("gam_url") else name_escaped
        )
        change = e.get("change_pct")
        change_txt = f"{change:+.1f}%" if change is not None else "—"
        return f"""
          <tr>
            <td>{gm.html_module.escape(e['publisher'], quote=True)}</td>
            <td>{name_html}</td>
            <td>{e['date']}</td>
            <td>{gm.format_number(e['last_impressions'])}</td>
            <td>{gm.format_number(e['expected'])}</td>
            <td>{change_txt}</td>
          </tr>"""

    def _section(title, icon, entries, empty_msg):
        if not entries:
            return f"""
              <div class="chart-card" style="margin-bottom:16px;">
                <h3>{icon} {title} (0)</h3>
                <p style="color:var(--muted);font-size:13px;margin:0;">{empty_msg}</p>
              </div>"""
        rows = "".join(_row(e) for e in entries)
        return f"""
          <div class="chart-card" style="margin-bottom:16px;">
            <h3>{icon} {title} ({len(entries)})</h3>
            <div style="overflow-x:auto;">
              <table class="insights-table">
                <thead>
                  <tr><th>Publisher</th><th>Campanha</th><th>Data</th>
                      <th>Impressões</th><th>Esperado</th><th>Variação</th></tr>
                </thead>
                <tbody>{rows}</tbody>
              </table>
            </div>
          </div>"""

    body_html = (
        _section("Campanhas zeradas", "⚠️", zeroed, "Nenhuma campanha zerada no momento.")
        + _section("Quedas de entrega", "📉", drops, "Nenhuma queda fora do padrão no momento.")
        + _section("Picos de entrega", "📈", spikes, "Nenhum pico fora do padrão no momento.")
    )

    return f"""<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="{gm.ADZEP_FAVICON_DATA_URI}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<title>Alertas Inteligentes — GAM Monitor</title>
<style>
  :root {{
    --bg:#f6f5f9; --card:#ffffff; --border:#eae6f0; --text:#241b33;
    --muted:#8a80a0; --brand-dark:#2f1440; --accent:#652f91; --accent-soft:#f1eaf8;
    --ok:#16a673; --ok-soft:#e3f8f0; --bad:#e5484d;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
    font-family:'Poppins',-apple-system,Segoe UI,Roboto,sans-serif; -webkit-font-smoothing:antialiased; }}
  header {{ background:linear-gradient(135deg, var(--brand-dark), var(--accent)); padding:18px 32px;
    box-shadow:0 2px 10px rgba(60,26,83,0.25); }}
  header h1 {{ color:#fff; font-size:19px; margin:0; font-weight:700; }}
  main {{ max-width:1100px; margin:24px auto 60px auto; padding:0 16px; }}
  .intro {{ background:var(--card); border:1px solid var(--border); border-radius:16px; padding:16px 20px;
    margin-bottom:20px; font-size:13px; color:var(--muted); line-height:1.5;
    box-shadow:0 2px 10px rgba(60,26,83,.06); }}
  .chart-card {{ background:var(--card); border:1px solid var(--border); border-radius:16px; padding:18px;
    box-shadow:0 2px 10px rgba(60,26,83,.06); }}
  .chart-card h3 {{ font-size:14px; margin:0 0 12px 0; color:var(--text); }}
  table.insights-table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  table.insights-table th {{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.02em;
    color:var(--muted); padding:10px 14px; border-bottom:1px solid var(--border); background:var(--accent-soft); }}
  table.insights-table td {{ padding:9px 14px; border-bottom:1px solid var(--border); }}
  table.insights-table tr:last-child td {{ border-bottom:none; }}
</style>
</head>
<body>
{nav_bar_html}
<header><h1>🔔 Alertas Inteligentes — Quedas e Picos de Entrega</h1></header>
<main>
  <div class="intro">
    Compara o último dia completo de cada campanha "Em veiculação" com a <b>tendência</b> que ela
    já vinha tendo nos 7 dias anteriores (não uma média fixa) — assim, uma campanha que já vem
    caindo/subindo aos poucos de forma normal não aparece aqui; só o que foge do padrão que ela
    mesma já vinha tendo. Atualiza a cada vez que o <code>gam_monitor.py</code> roda.
  </div>
  {body_html}
</main>
</body>
</html>"""


@app.route("/faturamento")
def faturamento():
    campaigns_by_publisher = load_campaigns()
    nav_bar_html = build_nav_bar("faturamento")

    all_month_keys = set()
    for campaigns in campaigns_by_publisher.values():
        for c in campaigns:
            all_month_keys.update(c.get("month_keys") or [])

    current_month_key = gm.datetime.now().strftime("%Y-%m")

    def month_sort_key(key):
        return (1, "") if key == "sem-data" else (0, key)

    visible_month_keys = [k for k in all_month_keys if k == "sem-data" or k <= current_month_key]
    month_options_html = "".join(
        f'<option value="{key}"{" selected" if key == current_month_key else ""}>{gm.month_label_for_key(key)}</option>'
        for key in sorted(visible_month_keys, key=month_sort_key)
    )

    default_publisher = "ABC do ABC" if "ABC do ABC" in campaigns_by_publisher else ""
    publisher_options_html = "".join(
        f'<option value="{gm.html_module.escape(name, quote=True)}"'
        f'{" selected" if name == default_publisher else ""}>{gm.html_module.escape(name)}</option>'
        for name in sorted(campaigns_by_publisher.keys())
    )

    # Mapa publisher -> meses em que ele tem campanha (baseado nas datas de
    # início/fim contratadas, igual à regra de faturamento — não no que já
    # foi entregue de verdade), pra filtrar o dropdown de publisher dinamicamente.
    publisher_months_map = {}
    for publisher, campaigns in campaigns_by_publisher.items():
        months = set()
        for c in campaigns:
            start_dt, end_dt = _parse_campaign_dates(c)
            if start_dt and end_dt:
                months.update(_calendar_months_between(start_dt, end_dt))
        publisher_months_map[publisher] = sorted(months)
    publisher_months_json = json.dumps(publisher_months_map, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="{gm.ADZEP_FAVICON_DATA_URI}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<title>Faturamento — GAM Monitor</title>
<style>
  :root {{
    --bg:#f6f5f9; --card:#ffffff; --border:#eae6f0; --text:#241b33;
    --muted:#8a80a0; --brand-dark:#2f1440; --accent:#652f91; --accent-soft:#f1eaf8;
    --ok:#16a673; --ok-soft:#e3f8f0; --bad:#e5484d;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
    font-family:'Poppins',-apple-system,Segoe UI,Roboto,sans-serif; -webkit-font-smoothing:antialiased; }}
  header {{ background:linear-gradient(135deg, var(--brand-dark), var(--accent)); padding:18px 32px;
    box-shadow:0 2px 10px rgba(60,26,83,0.25); }}
  header h1 {{ color:#fff; font-size:19px; margin:0; font-weight:700; }}
  main {{ max-width:1100px; margin:24px auto 60px auto; padding:0 16px; }}

  .toolbar {{ background:var(--card); border:1px solid var(--border); border-radius:16px; padding:16px 20px;
    display:flex; gap:14px; flex-wrap:wrap; align-items:flex-end; margin-bottom:20px;
    box-shadow:0 2px 10px rgba(60,26,83,.06); }}
  .toolbar .field {{ flex:1; min-width:170px; }}
  .toolbar label {{ font-size:11px; font-weight:700; color:var(--muted); text-transform:uppercase;
    letter-spacing:.03em; display:block; margin-bottom:6px; }}
  .toolbar select, .toolbar input {{ width:100%; padding:8px 12px; border:1px solid var(--border);
    border-radius:999px; background:var(--accent-soft); color:var(--accent); font-weight:600; font-size:13px; }}

  .summary-grid {{ display:grid; grid-template-columns:repeat(3, 1fr); gap:14px; margin-bottom:20px; }}
  .summary-card {{ background:var(--card); border:1px solid var(--border); border-radius:16px; padding:18px;
    box-shadow:0 2px 10px rgba(60,26,83,.06); }}
  .summary-label {{ font-size:11px; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:.03em; }}
  .summary-value {{ font-size:26px; font-weight:800; color:var(--text); margin-top:6px; }}
  .summary-value.accent {{ color:var(--accent); }}

  table.billing-table {{ width:100%; border-collapse:collapse; font-size:13px; background:var(--card);
    border:1px solid var(--border); border-radius:16px; overflow:hidden; }}
  table.billing-table th {{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.02em;
    color:var(--muted); padding:10px 14px; border-bottom:1px solid var(--border); background:var(--accent-soft); }}
  table.billing-table td {{ padding:9px 14px; border-bottom:1px solid var(--border); }}
  table.billing-table tr:last-child td {{ border-bottom:none; }}
</style>
</head>
<body>
{nav_bar_html}
<header><h1>💰 Faturamento por campanha</h1></header>
<main>
  <div class="toolbar">
    <div class="field">
      <label for="publisher-filter">Publisher</label>
      <select id="publisher-filter" onchange="loadBilling()">
        {publisher_options_html}
      </select>
    </div>
    <div class="field">
      <label for="month-filter">Mês</label>
      <select id="month-filter" onchange="loadBilling()">
        {month_options_html}
      </select>
    </div>
    <div class="field">
      <label for="price-input">Valor por campanha (R$)</label>
      <input type="text" id="price-input" value="70" onchange="loadBilling()">
    </div>
  </div>

  <div class="summary-grid" id="summary-grid"></div>

  <table class="billing-table" id="billing-table">
    <thead>
      <tr>
        <th>Campanha</th><th>Status</th><th>Início</th><th>Fim</th>
        <th>Duração</th><th>Meses cobrados</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>
</main>
<script>
var PUBLISHER_MONTHS = {publisher_months_json};

function escapeHtml(str) {{
  var div = document.createElement('div');
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}}

function updatePublisherFilterOptions() {{
  var selectedMonth = document.getElementById('month-filter').value;
  var publisherSelect = document.getElementById('publisher-filter');
  var currentValue = publisherSelect.value;

  var validNames = Object.keys(PUBLISHER_MONTHS).filter(function(name) {{
    return selectedMonth === 'all' || PUBLISHER_MONTHS[name].indexOf(selectedMonth) !== -1;
  }}).sort();

  publisherSelect.textContent = '';
  validNames.forEach(function(name) {{
    var opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    publisherSelect.appendChild(opt);
  }});
  publisherSelect.value = validNames.indexOf(currentValue) !== -1 ? currentValue : (validNames[0] || '');
}}

function loadBilling() {{
  updatePublisherFilterOptions();
  var publisher = document.getElementById('publisher-filter').value;
  var month = document.getElementById('month-filter').value;
  var price = document.getElementById('price-input').value;

  fetch('/api/billing?publisher=' + encodeURIComponent(publisher) +
        '&month=' + encodeURIComponent(month) + '&price=' + encodeURIComponent(price))
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      renderSummary(data);
      renderTable(data.rows);
    }});
}}

function renderSummary(data) {{
  var grid = document.getElementById('summary-grid');
  grid.innerHTML =
    '<div class="summary-card"><div class="summary-label">Campanhas cobradas nesse mês</div>' +
      '<div class="summary-value accent">' + data.billed_count + '</div></div>' +
    '<div class="summary-card"><div class="summary-label">Valor por campanha</div>' +
      '<div class="summary-value">R$ ' + Number(data.price_per_campaign).toFixed(2).replace('.', ',') + '</div></div>' +
    '<div class="summary-card"><div class="summary-label">Receita do mês</div>' +
      '<div class="summary-value accent">R$ ' + Number(data.revenue).toFixed(2).replace('.', ',') + '</div></div>';
}}

function renderTable(rows) {{
  var tbody = document.querySelector('#billing-table tbody');
  if (!rows.length) {{
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:24px;">' +
      'Nenhuma campanha desse publisher cobrada nesse mês.</td></tr>';
    return;
  }}
  tbody.innerHTML = rows.map(function(r) {{
    return '<tr>' +
      '<td>' + escapeHtml(r.name) + '</td>' +
      '<td>' + escapeHtml(r.status) + '</td>' +
      '<td>' + escapeHtml(r.start_date) + '</td>' +
      '<td>' + escapeHtml(r.end_date) + '</td>' +
      '<td>' + r.duration_days + ' dia(s)</td>' +
      '<td>' + r.billed_months.join(', ') + '</td>' +
    '</tr>';
  }}).join('');
}}

document.addEventListener('DOMContentLoaded', loadBilling);
</script>
</body>
</html>"""


@app.route("/insights")
def insights():
    campaigns_by_publisher = load_campaigns()
    nav_bar_html = build_nav_bar("insights")

    all_month_keys = set()
    for campaigns in campaigns_by_publisher.values():
        for c in campaigns:
            all_month_keys.update(c.get("month_keys") or [])

    current_month_key = gm.datetime.now().strftime("%Y-%m")

    def month_sort_key(key):
        return (1, "") if key == "sem-data" else (0, key)

    visible_month_keys = [k for k in all_month_keys if k == "sem-data" or k <= current_month_key]
    month_options_html = "".join(
        f'<option value="{key}"{" selected" if key == current_month_key else ""}>{gm.month_label_for_key(key)}</option>'
        for key in sorted(visible_month_keys, key=month_sort_key)
    )
    publisher_options_html = "".join(
        f'<option value="{gm.html_module.escape(name, quote=True)}">{gm.html_module.escape(name)}</option>'
        for name in sorted(campaigns_by_publisher.keys())
    )

    # Mapa publisher -> meses em que ele tem campanha, pra filtrar o dropdown
    # de publisher dinamicamente conforme o mês escolhido (igual às outras telas)
    publisher_months_map = {
        publisher: sorted({m for c in campaigns for m in (c.get("month_keys") or [])})
        for publisher, campaigns in campaigns_by_publisher.items()
    }
    publisher_months_json = json.dumps(publisher_months_map, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="{gm.ADZEP_FAVICON_DATA_URI}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<title>Insights — GAM Monitor</title>
<style>
  :root {{
    --bg:#f6f5f9; --card:#ffffff; --border:#eae6f0; --text:#241b33;
    --muted:#8a80a0; --brand-dark:#2f1440; --accent:#652f91; --accent-soft:#f1eaf8;
    --ok:#16a673; --ok-soft:#e3f8f0; --warn:#f2a93b; --bad:#e5484d;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
    font-family:'Poppins',-apple-system,Segoe UI,Roboto,sans-serif; -webkit-font-smoothing:antialiased; }}
  header {{ background:linear-gradient(135deg, var(--brand-dark), var(--accent)); padding:18px 32px;
    display:flex; justify-content:space-between; align-items:center;
    box-shadow:0 2px 10px rgba(60,26,83,0.25); }}
  header h1 {{ color:#fff; font-size:19px; margin:0; font-weight:700; }}
  #export-btn {{ background:#fff; color:var(--accent); border:none; border-radius:999px; padding:9px 20px;
    font-size:13px; font-weight:700; cursor:pointer; }}
  main {{ max-width:1200px; margin:24px auto 60px auto; padding:0 16px; }}

  .toolbar {{ background:var(--card); border:1px solid var(--border); border-radius:16px; padding:14px 18px;
    display:flex; gap:14px; flex-wrap:wrap; align-items:flex-end; margin-bottom:20px;
    box-shadow:0 2px 10px rgba(60,26,83,.06); }}
  .toolbar .field {{ flex:1; min-width:180px; }}
  .toolbar label {{ font-size:11px; font-weight:700; color:var(--muted); text-transform:uppercase;
    letter-spacing:.03em; display:block; margin-bottom:6px; }}
  .toolbar select {{ width:100%; padding:8px 12px; border:1px solid var(--border); border-radius:999px;
    background:var(--accent-soft); color:var(--accent); font-weight:600; font-size:13px; }}

  .kpi-grid {{ display:grid; grid-template-columns:repeat(5, 1fr); gap:14px; margin-bottom:20px; }}
  .kpi-card {{ background:var(--card); border:1px solid var(--border); border-radius:16px; padding:18px;
    box-shadow:0 2px 10px rgba(60,26,83,.06); }}
  .kpi-label {{ font-size:11px; font-weight:700; color:var(--muted); text-transform:uppercase; letter-spacing:.03em; }}
  .kpi-value {{ font-size:24px; font-weight:800; color:var(--text); margin-top:6px; }}

  .charts-grid {{ display:grid; grid-template-columns:2fr 1fr; gap:14px; margin-bottom:20px; align-items:stretch; }}
  .chart-card {{ background:var(--card); border:1px solid var(--border); border-radius:16px; padding:18px;
    box-shadow:0 2px 10px rgba(60,26,83,.06); display:flex; flex-direction:column; }}
  .chart-card h3 {{ font-size:14px; margin:0 0 12px 0; color:var(--text); }}
  .chart-card.full {{ grid-column:1 / -1; }}
  .chart-canvas-wrap {{ position:relative; height:280px; flex:1; }}
  .period-btn {{ background:var(--accent-soft); color:var(--accent); border:none; border-radius:999px;
    padding:6px 14px; font-size:12px; font-weight:700; cursor:pointer; font-family:inherit; }}
  .period-btn.active {{ background:var(--accent); color:#fff; }}

  table.insights-table {{ width:100%; border-collapse:collapse; font-size:13px; background:var(--card);
    border:1px solid var(--border); border-radius:16px; overflow:hidden; }}
  table.insights-table th {{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.02em;
    color:var(--muted); padding:10px 14px; border-bottom:1px solid var(--border); background:var(--accent-soft); }}
  table.insights-table td {{ padding:9px 14px; border-bottom:1px solid var(--border); }}
  table.insights-table tr:last-child td {{ border-bottom:none; }}
  .status-pill {{ font-size:10px; font-weight:700; padding:2px 8px; border-radius:999px; white-space:nowrap; }}
  .status-live {{ background:var(--ok-soft); color:var(--ok); }}
  .status-scheduled {{ background:var(--accent-soft); color:var(--accent); }}
  .status-paused {{ background:#fdf2e1; color:#b5760a; }}
  .status-done {{ background:#ececec; color:#6b6b6b; }}
  .status-other {{ background:#ececec; color:#6b6b6b; }}

  @media print {{
    .toolbar, #export-btn, nav {{ display:none !important; }}
    body {{ background:#fff; }}
    .kpi-card, .chart-card, table.insights-table {{ box-shadow:none !important; border:1px solid #ccc !important; }}
  }}
</style>
</head>
<body>
{nav_bar_html}
<header>
  <h1>📈 Insights — Resultados por Publisher</h1>
  <button id="export-btn" onclick="window.print()">⬇️ Exportar PDF</button>
</header>
<main>
  <div class="toolbar">
    <div class="field">
      <label for="month-filter">Mês</label>
      <select id="month-filter" onchange="loadInsights()">
        <option value="all">Todos os meses</option>
        {month_options_html}
      </select>
    </div>
    <div class="field">
      <label for="publisher-filter">Publisher</label>
      <select id="publisher-filter" onchange="loadInsights()">
        <option value="all">Todos os publishers</option>
        {publisher_options_html}
      </select>
    </div>
  </div>

  <div class="kpi-grid" id="kpi-grid"></div>

  <div id="comparison-row" class="kpi-grid" style="grid-template-columns:1fr; margin-bottom:20px;"></div>

  <div class="chart-card" style="margin-bottom:20px;">
    <h3>% do inventário consumido pelas campanhas diretas</h3>
    <p style="font-size:12px;color:var(--muted);margin:0 0 10px 0;">
      Mostra quanto as campanhas diretas representam do total de impressões servidas
      pela rede — se o publisher escolhido dividir a mesma rede com outros, o
      percentual reflete a rede inteira, não só esse recorte. (Só disponível quando
      um mês específico está selecionado.)
    </p>
    <div style="display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap; align-items:center;" id="inventory-period-toggle">
      <button type="button" class="period-btn active" data-period="month" onclick="setInventoryPeriod('month')">Mês</button>
      <button type="button" class="period-btn" data-period="30d" onclick="setInventoryPeriod('30d')">Últimos 30 dias</button>
      <button type="button" class="period-btn" data-period="7d" onclick="setInventoryPeriod('7d')">Últimos 7 dias</button>
      <button type="button" class="period-btn" data-period="custom" onclick="setInventoryPeriod('custom')">Personalizado</button>
      <span id="inventory-custom-range" style="display:none; align-items:center; gap:6px;">
        <input type="date" id="inventory-custom-start" onchange="applyCustomInventoryRange()"
               style="border:1px solid var(--border);border-radius:999px;padding:6px 10px;font-size:12px;font-family:inherit;">
        <span style="color:var(--muted);font-size:12px;">até</span>
        <input type="date" id="inventory-custom-end" onchange="applyCustomInventoryRange()"
               style="border:1px solid var(--border);border-radius:999px;padding:6px 10px;font-size:12px;font-family:inherit;">
      </span>
    </div>
    <div id="inventory-result" style="font-size:14px;"></div>
    <div class="chart-canvas-wrap" id="inventory-chart-wrap" style="display:none; margin-top:16px; height:420px;">
      <canvas id="chart-inventory"></canvas>
    </div>
  </div>

  <div class="charts-grid">
    <div class="chart-card full">
      <h3>Entrega ao longo do mês</h3>
      <div class="chart-canvas-wrap"><canvas id="chart-daily"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Distribuição por status</h3>
      <div class="chart-canvas-wrap"><canvas id="chart-status"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Top 10 campanhas (impressões)</h3>
      <div class="chart-canvas-wrap"><canvas id="chart-top"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Impressões por tipo de campanha</h3>
      <div class="chart-canvas-wrap"><canvas id="chart-type"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Audiência média por dia da semana</h3>
      <div class="chart-canvas-wrap"><canvas id="chart-weekday"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Top 5 campanhas por CTR</h3>
      <div id="top-ctr-list" style="font-size:13px;"></div>
    </div>
  </div>

  <div class="chart-card" style="margin-top:14px;">
    <h3>Detalhamento das campanhas</h3>
    <div style="overflow-x:auto;">
      <table class="insights-table" id="insights-table">
        <thead>
          <tr>
            <th>Publisher</th><th>Campanha</th><th>Tipo</th><th>Status</th>
            <th>Entrega</th><th>Impressões</th><th>Cliques</th><th>CTR</th><th>VA</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
</main>
<script>
var chartDaily = null, chartStatus = null, chartTop = null;
var PUBLISHER_MONTHS = {publisher_months_json};

function updatePublisherFilterOptions() {{
  var selectedMonth = document.getElementById('month-filter').value;
  var publisherSelect = document.getElementById('publisher-filter');
  var currentValue = publisherSelect.value;

  var validNames = Object.keys(PUBLISHER_MONTHS).filter(function(name) {{
    return selectedMonth === 'all' || PUBLISHER_MONTHS[name].indexOf(selectedMonth) !== -1;
  }}).sort();

  publisherSelect.textContent = '';
  var allOpt = document.createElement('option');
  allOpt.value = 'all';
  allOpt.textContent = 'Todos os publishers';
  publisherSelect.appendChild(allOpt);
  validNames.forEach(function(name) {{
    var opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    publisherSelect.appendChild(opt);
  }});
  publisherSelect.value = validNames.indexOf(currentValue) !== -1 ? currentValue : 'all';
}}

function formatNumberBR(n) {{
  return Math.round(n).toString().replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, '.');
}}

function statusClass(label) {{
  var map = {{'Em veiculação':'status-live', 'Agendada':'status-scheduled',
              'Pausada':'status-paused', 'Finalizada':'status-done'}};
  return map[label] || 'status-other';
}}

function escapeHtml(str) {{
  var div = document.createElement('div');
  div.textContent = str == null ? '' : String(str);
  return div.innerHTML;
}}

var insightsRequestId = 0;

function loadInsights() {{
  updatePublisherFilterOptions();
  var month = document.getElementById('month-filter').value;
  var publisher = document.getElementById('publisher-filter').value;
  var requestId = ++insightsRequestId;

  // Dados rápidos (tudo do cache) — carregam e renderizam na hora
  fetch('/api/insights?month=' + encodeURIComponent(month) + '&publisher=' + encodeURIComponent(publisher))
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      if (requestId !== insightsRequestId) {{ return; }}  // filtro já mudou de novo, ignora resposta velha
      renderKpis(data.kpis);
      renderComparisonRow(data.comparison);
      renderDailyChart(data.daily_series);
      renderStatusChart(data.status_counts);
      renderTopChart(data.top_campaigns);
      renderTypeChart(data.type_breakdown);
      renderWeekdayChart(data.weekday_labels, data.weekday_avg);
      renderTopCtrList(data.top_ctr);
      renderTable(data.table_rows);
    }});

  loadInventoryShare();
}}

var currentInventoryPeriod = 'month';

function setInventoryPeriod(period) {{
  currentInventoryPeriod = period;
  document.querySelectorAll('#inventory-period-toggle .period-btn').forEach(function(btn) {{
    btn.classList.toggle('active', btn.getAttribute('data-period') === period);
  }});

  var rangeEl = document.getElementById('inventory-custom-range');
  if (period === 'custom') {{
    rangeEl.style.display = 'inline-flex';
    var startInput = document.getElementById('inventory-custom-start');
    var endInput = document.getElementById('inventory-custom-end');
    if (!startInput.value || !endInput.value) {{
      // Sugere os últimos 7 dias como ponto de partida, pra não abrir vazio
      var today = new Date();
      var weekAgo = new Date();
      weekAgo.setDate(today.getDate() - 6);
      endInput.value = today.toISOString().slice(0, 10);
      startInput.value = weekAgo.toISOString().slice(0, 10);
    }}
    if (startInput.value && endInput.value) {{ loadInventoryShare(); }}
  }} else {{
    rangeEl.style.display = 'none';
    loadInventoryShare();
  }}
}}

function applyCustomInventoryRange() {{
  var startInput = document.getElementById('inventory-custom-start');
  var endInput = document.getElementById('inventory-custom-end');
  if (startInput.value && endInput.value) {{ loadInventoryShare(); }}
}}

function loadInventoryShare() {{
  var month = document.getElementById('month-filter').value;
  var publisher = document.getElementById('publisher-filter').value;
  var requestId = insightsRequestId;

  // % de inventário — busca ao vivo no GAM (mais lenta), separada, pra não
  // travar o resto da página enquanto calcula
  var resultEl = document.getElementById('inventory-result');
  document.getElementById('inventory-chart-wrap').style.display = 'none';
  if (month === 'all') {{
    resultEl.innerHTML = '<span style="color:var(--muted);">Escolhe um mês específico pra ver esse dado.</span>';
    return;
  }}
  resultEl.innerHTML = '<span style="color:var(--muted);">Calculando... (busca ao vivo no GAM, pode levar alguns segundos)</span>';

  var url = '/api/insights/inventory-share?month=' + encodeURIComponent(month) +
        '&publisher=' + encodeURIComponent(publisher) + '&period=' + encodeURIComponent(currentInventoryPeriod);
  if (currentInventoryPeriod === 'custom') {{
    var startVal = document.getElementById('inventory-custom-start').value;
    var endVal = document.getElementById('inventory-custom-end').value;
    if (!startVal || !endVal) {{ return; }}
    url += '&start=' + encodeURIComponent(startVal) + '&end=' + encodeURIComponent(endVal);
  }}

  fetch(url)
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      if (requestId !== insightsRequestId) {{ return; }}
      renderInventoryShare(data.inventory_share, month);
    }})
    .catch(function(err) {{
      if (requestId !== insightsRequestId) {{ return; }}
      resultEl.innerHTML = '<span style="color:var(--bad);">Erro ao calcular: ' + escapeHtml(String(err)) + '</span>';
    }});
}}

function renderKpis(k) {{
  var grid = document.getElementById('kpi-grid');
  grid.innerHTML =
    '<div class="kpi-card"><div class="kpi-label">Campanhas</div><div class="kpi-value">' + k.total_campaigns + '</div></div>' +
    '<div class="kpi-card"><div class="kpi-label">Impressões</div><div class="kpi-value">' + formatNumberBR(k.total_impressions) + '</div></div>' +
    '<div class="kpi-card"><div class="kpi-label">Cliques</div><div class="kpi-value">' + formatNumberBR(k.total_clicks) + '</div></div>' +
    '<div class="kpi-card"><div class="kpi-label">CTR médio</div><div class="kpi-value">' + k.blended_ctr + '%</div></div>' +
    '<div class="kpi-card"><div class="kpi-label">VA médio</div><div class="kpi-value">' + (k.blended_va !== null ? k.blended_va + '%' : '—') + '</div></div>';
}}

function renderComparisonRow(comparison) {{
  var row = document.getElementById('comparison-row');
  var html = '';

  if (comparison) {{
    var impArrow = comparison.impressions_change_pct >= 0 ? '▲' : '▼';
    var impColor = comparison.impressions_change_pct >= 0 ? 'var(--ok)' : 'var(--bad)';
    var clickTxt = comparison.clicks_change_pct !== null
      ? (comparison.clicks_change_pct >= 0 ? '▲' : '▼') + ' ' + Math.abs(comparison.clicks_change_pct) + '% cliques'
      : '';
    html += '<div class="kpi-card">' +
      '<div class="kpi-label">Vs. ' + escapeHtml(comparison.previous_month_label) + '</div>' +
      '<div class="kpi-value" style="color:' + impColor + ';font-size:20px;">' +
        impArrow + ' ' + Math.abs(comparison.impressions_change_pct) + '% impressões</div>' +
      '<div style="font-size:12px;color:var(--muted);margin-top:4px;">' + clickTxt + '</div>' +
    '</div>';
  }} else {{
    html += '<div class="kpi-card"><div class="kpi-label">Vs. mês anterior</div>' +
      '<div style="font-size:13px;color:var(--muted);margin-top:6px;">Sem dado do mês anterior pra comparar</div></div>';
  }}

  row.innerHTML = html;
}}

function renderDailyChart(series) {{
  var ctx = document.getElementById('chart-daily').getContext('2d');
  var labels = series.map(function(d) {{ return d.date.split('-').reverse().slice(0,2).join('/'); }});
  var impressions = series.map(function(d) {{ return d.impressions; }});
  var clicks = series.map(function(d) {{ return d.clicks; }});
  if (chartDaily) {{ chartDaily.destroy(); }}
  chartDaily = new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: labels,
      datasets: [
        {{ label: 'Impressões', data: impressions, borderColor: '#652f91', backgroundColor: 'rgba(101,47,145,.1)',
           yAxisID: 'y', tension: .3, fill: true }},
        {{ label: 'Cliques', data: clicks, borderColor: '#16a673', backgroundColor: 'rgba(22,166,115,.1)',
           yAxisID: 'y1', tension: .3, fill: true }},
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      scales: {{
        y: {{ type: 'linear', position: 'left', title: {{ display: true, text: 'Impressões' }} }},
        y1: {{ type: 'linear', position: 'right', title: {{ display: true, text: 'Cliques' }}, grid: {{ drawOnChartArea: false }} }},
      }}
    }}
  }});
}}

function renderStatusChart(statusCounts) {{
  var ctx = document.getElementById('chart-status').getContext('2d');
  var labels = Object.keys(statusCounts);
  var values = labels.map(function(l) {{ return statusCounts[l]; }});
  var colors = {{'Em veiculação':'#16a673','Agendada':'#652f91','Pausada':'#f2a93b','Finalizada':'#9c9c9c'}};
  if (chartStatus) {{ chartStatus.destroy(); }}
  chartStatus = new Chart(ctx, {{
    type: 'doughnut',
    data: {{ labels: labels, datasets: [{{ data: values, backgroundColor: labels.map(function(l) {{ return colors[l] || '#ccc'; }}) }}] }},
    options: {{ responsive: true, maintainAspectRatio: false }}
  }});
}}

function renderTopChart(topCampaigns) {{
  var ctx = document.getElementById('chart-top').getContext('2d');
  var labels = topCampaigns.map(function(c) {{ return c.name.length > 22 ? c.name.slice(0, 22) + '…' : c.name; }});
  var values = topCampaigns.map(function(c) {{ return c.impressions; }});
  if (chartTop) {{ chartTop.destroy(); }}
  chartTop = new Chart(ctx, {{
    type: 'bar',
    data: {{ labels: labels, datasets: [{{ label: 'Impressões', data: values, backgroundColor: '#652f91' }}] }},
    options: {{ indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }} }}
  }});
}}

var chartType = null, chartWeekday = null;

function renderTypeChart(typeBreakdown) {{
  var ctx = document.getElementById('chart-type').getContext('2d');
  var labels = Object.keys(typeBreakdown);
  var values = labels.map(function(l) {{ return typeBreakdown[l]; }});
  var palette = ['#652f91', '#16a673', '#f2a93b', '#e5484d', '#9c9c9c'];
  if (chartType) {{ chartType.destroy(); }}
  chartType = new Chart(ctx, {{
    type: 'pie',
    data: {{ labels: labels, datasets: [{{ data: values, backgroundColor: labels.map(function(_, i) {{ return palette[i % palette.length]; }}) }}] }},
    options: {{ responsive: true, maintainAspectRatio: false }}
  }});
}}

function renderWeekdayChart(labels, values) {{
  var ctx = document.getElementById('chart-weekday').getContext('2d');
  if (chartWeekday) {{ chartWeekday.destroy(); }}
  chartWeekday = new Chart(ctx, {{
    type: 'bar',
    data: {{ labels: labels, datasets: [{{ label: 'Impressões (média)', data: values, backgroundColor: '#652f91' }}] }},
    options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }} }}
  }});
}}

function renderTopCtrList(topCtr) {{
  var el = document.getElementById('top-ctr-list');
  if (!topCtr || !topCtr.length) {{
    el.innerHTML = '<div style="color:var(--muted);">Sem campanhas com volume suficiente pra comparar CTR.</div>';
    return;
  }}
  el.innerHTML = topCtr.map(function(c, i) {{
    return '<div style="display:flex;justify-content:space-between;padding:8px 0;' +
      (i < topCtr.length - 1 ? 'border-bottom:1px solid var(--border);' : '') + '">' +
      '<span>' + (i + 1) + '. ' + escapeHtml(c.name) + '</span>' +
      '<strong style="color:var(--accent);">' + c.ctr + '%</strong>' +
    '</div>';
  }}).join('');
}}

function renderTable(rows) {{
  var tbody = document.querySelector('#insights-table tbody');
  tbody.innerHTML = rows.map(function(r) {{
    var entrega = (r.actual_pct !== null && r.actual_pct !== undefined)
      ? r.actual_pct.toFixed(1) + '%' + (r.expected_pct !== null && r.expected_pct !== undefined ? ' / ' + r.expected_pct.toFixed(1) + '%' : '')
      : '—';
    var va = (r.va_rate !== null && r.va_rate !== undefined) ? r.va_rate + '%' : '—';
    return '<tr>' +
      '<td>' + escapeHtml(r.publisher) + '</td>' +
      '<td>' + escapeHtml(r.name) + '</td>' +
      '<td>' + escapeHtml(r.type) + '</td>' +
      '<td><span class="status-pill ' + statusClass(r.status) + '">' + escapeHtml(r.status) + '</span></td>' +
      '<td>' + entrega + '</td>' +
      '<td>' + formatNumberBR(r.impressions) + '</td>' +
      '<td>' + formatNumberBR(r.clicks) + '</td>' +
      '<td>' + r.ctr + '%</td>' +
      '<td>' + va + '</td>' +
    '</tr>';
  }}).join('');
}}

var chartInventory = null;

function renderInventoryShare(inventoryShare, month) {{
  var resultEl = document.getElementById('inventory-result');
  var chartWrap = document.getElementById('inventory-chart-wrap');

  if (month === 'all') {{
    resultEl.innerHTML = '<span style="color:var(--muted);">Escolhe um mês específico pra ver esse dado.</span>';
    chartWrap.style.display = 'none';
    return;
  }}
  if (!inventoryShare) {{
    resultEl.innerHTML = '<span style="color:var(--muted);">Nenhuma campanha encontrada nesse recorte.</span>';
    chartWrap.style.display = 'none';
    return;
  }}
  if (inventoryShare.error) {{
    resultEl.innerHTML = '<span style="color:var(--bad);">Erro ao calcular: ' + escapeHtml(inventoryShare.error) + '</span>';
    chartWrap.style.display = 'none';
    return;
  }}

  var scopeText;
  if (inventoryShare.scope === 'ad_unit') {{
    scopeText = 'impressões do Ad Unit "' + escapeHtml(inventoryShare.ad_unit_name || '') + '"' +
      ' <span title="Comparado só com o inventário do Ad Unit pai desse publisher, não da rede inteira.">ℹ️</span>';
  }} else {{
    scopeText = 'impressões da rede' +
      (inventoryShare.network_codes && inventoryShare.network_codes.length
        ? ' (' + inventoryShare.network_codes.join(', ') + ')' : '') +
      ' <span style="color:#b5760a;" title="Não foi possível identificar o Ad Unit pai desse publisher ' +
      '(rode o gam_monitor.py de novo pra atualizar o cache) — esse percentual está comparando com a rede ' +
      'inteira, que pode incluir outros publishers.">⚠️ rede inteira, não o site específico</span>';
  }}

  resultEl.innerHTML = '<strong style="font-size:22px;color:var(--accent);">' + inventoryShare.share_pct + '%</strong>' +
    ' <span style="color:var(--muted);">(' + formatNumberBR(inventoryShare.matched_impressions) + ' de ' +
    formatNumberBR(inventoryShare.total_network_impressions) + ' ' + scopeText + ')</span>';

  var prev = inventoryShare.previous;
  if (prev && prev.total_network_impressions > 0) {{
    var delta = Math.round((inventoryShare.share_pct - prev.share_pct) * 100) / 100;
    var arrow = delta >= 0 ? '▲' : '▼';
    var color = delta >= 0 ? 'var(--ok)' : 'var(--bad)';
    resultEl.innerHTML +=
      '<div style="margin-top:8px;font-size:13px;color:var(--muted);">' +
        'Período anterior (' + formatDateBR(prev.start_date) + ' a ' + formatDateBR(prev.end_date) + '): ' +
        '<strong style="color:var(--text);">' + prev.share_pct + '%</strong> ' +
        '<strong style="color:' + color + ';">' + arrow + ' ' + Math.abs(delta) + ' p.p.</strong> vs. agora' +
      '</div>';
  }}

  var series = (inventoryShare.share_series || []).filter(function(d) {{ return d.share_pct !== null; }});
  var prevSeries = prev ? (prev.share_series || []).filter(function(d) {{ return d.share_pct !== null; }}) : [];
  if (series.length < 2) {{
    chartWrap.style.display = 'none';
    return;
  }}

  chartWrap.style.display = 'block';
  var ctx = document.getElementById('chart-inventory').getContext('2d');
  // Alinha os dois períodos por posição ("Dia 1, Dia 2...") em vez de data
  // real — assim dá pra comparar o FORMATO da curva lado a lado, já que as
  // datas de verdade são diferentes entre os dois períodos.
  var maxLen = Math.max(series.length, prevSeries.length);
  var labels = [];
  for (var i = 0; i < maxLen; i++) {{ labels.push('Dia ' + (i + 1)); }}
  var currentValues = series.map(function(d) {{ return d.share_pct; }});
  var previousValues = prevSeries.map(function(d) {{ return d.share_pct; }});

  var datasets = [{{
    label: 'Período atual',
    data: currentValues,
    borderColor: '#652f91',
    backgroundColor: 'rgba(101,47,145,.1)',
    tension: .3,
    fill: true,
  }}];
  if (previousValues.length >= 2) {{
    datasets.push({{
      label: 'Período anterior',
      data: previousValues,
      borderColor: '#b0a8c2',
      backgroundColor: 'transparent',
      borderDash: [6, 4],
      tension: .3,
      fill: false,
    }});
  }}

  if (chartInventory) {{ chartInventory.destroy(); }}
  chartInventory = new Chart(ctx, {{
    type: 'line',
    data: {{ labels: labels, datasets: datasets }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{ legend: {{ display: datasets.length > 1, position: 'top' }} }},
      scales: {{ y: {{ min: 0, max: 100, ticks: {{ stepSize: 10, callback: function(v) {{ return v + '%'; }} }} }} }}
    }}
  }});
}}

function formatDateBR(isoDate) {{
  if (!isoDate) {{ return ''; }}
  return isoDate.split('-').reverse().slice(0, 2).join('/');
}}

document.addEventListener('DOMContentLoaded', loadInsights);
</script>
</body>
</html>"""


@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json(force=True)
    campaign_id = str(data.get("campaign_id", ""))
    force_regenerate = bool(data.get("force"))
    break_by_creative_size = bool(data.get("break_by_creative_size"))
    include_revenue = bool(data.get("include_revenue"))

    campaign_ids = [x for x in campaign_id.split(",") if x]
    is_group = len(campaign_ids) > 1

    # Chave de cache diferente pra cada combinação de opções, senão um
    # relatório simples já gerado seria "reaproveitado" no lugar do detalhado.
    report_key = campaign_id
    if not is_group:
        if break_by_creative_size:
            report_key += "_size"
        if include_revenue:
            report_key += "_rev"

    campaigns_by_publisher = load_campaigns()

    if is_group:
        found_list = []
        found_publisher = None
        for publisher, campaigns in campaigns_by_publisher.items():
            for c in campaigns:
                if str(c["id"]) in campaign_ids:
                    found_list.append(c)
                    found_publisher = publisher
        if len(found_list) != len(campaign_ids):
            return jsonify({"status": "error", "message": "Uma ou mais campanhas do conjunto não foram encontradas no cache."})

        report_files = load_report_files()
        if report_key in report_files and not force_regenerate:
            return jsonify({"status": "ok", "url": report_files[report_key], "reused": True})

        try:
            url = create_group_report_sheet(found_publisher, found_list)
            report_files[report_key] = url
            save_report_files(report_files)
            return jsonify({"status": "ok", "url": url, "reused": False})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})

    found = None
    found_publisher = None
    for publisher, campaigns in campaigns_by_publisher.items():
        for c in campaigns:
            if str(c["id"]) == campaign_id:
                found = c
                found_publisher = publisher
                break
        if found:
            break

    if not found:
        return jsonify({"status": "error", "message": "Campanha não encontrada no cache."})

    report_files = load_report_files()
    if report_key in report_files and not force_regenerate:
        return jsonify({"status": "ok", "url": report_files[report_key], "reused": True})

    try:
        url = create_report_sheet(
            found_publisher, found,
            break_by_creative_size=break_by_creative_size,
            include_revenue=include_revenue,
        )
        report_files[report_key] = url
        save_report_files(report_files)
        return jsonify({"status": "ok", "url": url, "reused": False})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


if __name__ == "__main__":
    print("Abra http://localhost:5000 no navegador (deixe esta janela aberta).")
    app.run(host="127.0.0.1", port=5000, debug=False)
