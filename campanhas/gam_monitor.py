"""
GAM Campaign Monitor
====================
Conecta na API do Google Ad Manager, calcula a entrega das campanhas diretas
(por publisher), detecta underdelivery, avisa quando uma campanha está prestes
a terminar, e gera um dashboard HTML que se atualiza sozinho.

Rode: python3 gam_monitor.py
Configuração de API: veja README.md e config.example.yaml
Agendamento (cron / Task Scheduler): veja README.md, seção 3.
"""

import os
import re
import html as html_module
import sys
import json
import time
import io
import gzip
import csv
import math
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# Evita erros de encoding no console do Windows (cp1252) ao imprimir emojis/acentos
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from googleads import ad_manager, errors, oauth2
from zeep.helpers import serialize_object
import yaml


def _to_dict(zeep_obj):
    """Converte um objeto retornado pelo zeep (SOAP) em dict/list Python puro,
    para que .get() e [""] funcionem normalmente no restante do script."""
    if zeep_obj is None:
        return None
    return serialize_object(zeep_obj, target_cls=dict)

# ----------------------------------------------------------------------------
# CONFIGURAÇÃO — ajuste conforme sua conta
# ----------------------------------------------------------------------------

# Versão da API do GAM. As versões antigas são descontinuadas periodicamente
# (a cada poucos meses) — se aparecer um erro "Unrecognized version", troque
# aqui pela versão mais recente listada no erro ou em:
# https://developers.google.com/ad-manager/api/rel_notes
GAM_API_VERSION = "v202605"

# Logo da Adzep (embutido como base64, pra não depender de arquivo externo)
ADZEP_LOGO_B64 = "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"
ADZEP_LOGO_DATA_URI = f"data:image/png;base64,{ADZEP_LOGO_B64}"

# Favicon (só o símbolo, sem o texto "adzep")
ADZEP_FAVICON_B64 = "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"
ADZEP_FAVICON_DATA_URI = f"data:image/png;base64,{ADZEP_FAVICON_B64}"

# Onde está o "publisher" de cada campanha:
# - "order_name_prefix": lê o texto entre colchetes no início do nome do Order,
#   ex: "[Tribuna do Norte] Sportradar - Sports" -> publisher = "Tribuna do Norte" (padrão atual)
# - "adunit_parent": usa a Ad Unit de topo (filha direta da raiz da rede) que a
#   campanha tem como alvo
# - "advertiser": usa o nome do Advertiser (anunciante) do Order
# - "custom_field": usa um Custom Field do Order com o nome abaixo
PUBLISHER_SOURCE = "order_name_prefix"  # "order_name_prefix", "adunit_parent", "advertiser" ou "custom_field"
PUBLISHER_CUSTOM_FIELD_NAME = "Publisher"

# Busca VA (viewability) via ReportService (Active View). Deixa a execução mais
# lenta (o GAM processa o relatório de forma assíncrona, pode levar ~1-2 min),
# mas é o único jeito de trazer esse dado — o LineItemService normalmente não traz.
ENABLE_VIEWABILITY_REPORT = True
VIEWABILITY_REPORT_TIMEOUT_SECONDS = 120

# Publicar o dashboard.html num bucket do Google Cloud Storage (link público, sem
# precisar de servidor próprio). Veja README.md pra criar o bucket e ativar isto.
ENABLE_CLOUD_UPLOAD = False
GCS_BUCKET_NAME = "SEU_BUCKET_AQUI"

# Exporta pro Google Sheets um relatório (Impressões, Cliques, CTR, etc.) toda
# vez que uma campanha finaliza (status COMPLETED). Veja README.md pra configurar.
ENABLE_SHEETS_EXPORT = False
GOOGLE_SHEET_ID = "SEU_SHEET_ID_AQUI"
SHEETS_WORKSHEET_NAME = "Relatórios"

# Quantos pontos percentuais de atraso (real vs. esperado) contam como underdelivery
UNDERDELIVERY_THRESHOLD_PCT = 10

# Avisa quando faltarem <= N dias para o fim da campanha ("Termina em breve")
DAYS_BEFORE_END_TO_ALERT = 5

# Detecção de anomalias: compara o último dia disponível com a média dos N
# dias anteriores. Só roda pra campanhas "Em veiculação" com histórico e
# volume mínimos (evita alarme falso em campanha nova ou de volume baixo,
# onde uma variação de poucas impressões vira um % gigante sem significar nada).
ANOMALY_LOOKBACK_DAYS = 7
ANOMALY_DROP_THRESHOLD_PCT = 40      # queda de 40%+ vs a média dispara alerta
ANOMALY_SPIKE_THRESHOLD_PCT = 100    # alta de 100%+ (dobrou ou mais) dispara alerta
ANOMALY_MIN_AVG_IMPRESSIONS = 300    # ignora campanhas com média diária abaixo disso

# --- E-mail ---
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USER)
EMAIL_TO = [e.strip() for e in os.environ.get("EMAIL_TO", "").split(",") if e.strip()]

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
DASHBOARD_PATH = OUTPUT_DIR / "dashboard.html"
STATE_PATH = OUTPUT_DIR / "notified_state.json"
DATA_CACHE_PATH = OUTPUT_DIR / "data_cache.json"
REPORTED_STATE_PATH = OUTPUT_DIR / "reported_campaigns.json"

MONTH_NAMES_PT = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

STATUS_LABELS = {
    "DELIVERING": ("Em veiculação", "status-live"),
    "READY": ("Agendada", "status-scheduled"),
    "PAUSED": ("Pausada", "status-paused"),
    "COMPLETED": ("Finalizada", "status-done"),
}


def status_label_and_class(status):
    return STATUS_LABELS.get(status, (status or "—", "status-other"))


GOAL_UNIT_LABELS = {
    "IMPRESSIONS": "impressões",
    "CLICKS": "cliques",
    "VIEWABLE_IMPRESSIONS": "impressões visíveis",
    "DAILY_IMPRESSIONS": "impressões/dia",
    "DAILY_CLICKS": "cliques/dia",
}


# ----------------------------------------------------------------------------
# CONEXÃO COM A API
# ----------------------------------------------------------------------------

def get_client():
    """Carrega o cliente autenticado a partir do googleads.yaml na pasta atual."""
    yaml_path = BASE_DIR / "googleads.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(
            "Não encontrei 'googleads.yaml'. Copie config.example.yaml para "
            "googleads.yaml e preencha com seus dados (veja README.md, seção 1)."
        )
    return ad_manager.AdManagerClient.LoadFromStorage(str(yaml_path))


ACCOUNTS_CONFIG_PATH = BASE_DIR / "accounts.yaml"


def _get_service_account_key_path():
    """Acha o caminho do .json da service account, funcionando tanto no modo de
    conta única (googleads.yaml) quanto multi-conta (accounts.yaml)."""
    if ACCOUNTS_CONFIG_PATH.exists():
        config = yaml.safe_load(ACCOUNTS_CONFIG_PATH.read_text(encoding="utf-8"))
        return config["path_to_private_key_file"]

    yaml_path = BASE_DIR / "googleads.yaml"
    config = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return config["ad_manager"]["path_to_private_key_file"]


def load_reported_state():
    if REPORTED_STATE_PATH.exists():
        return json.loads(REPORTED_STATE_PATH.read_text())
    return {}


def save_reported_state(state):
    REPORTED_STATE_PATH.write_text(json.dumps(state))


def export_completed_campaigns_to_sheets(all_publishers):
    """Toda campanha com status COMPLETED que ainda não tem relatório gerado
    ganha uma linha nova numa aba do Google Sheets (Impressões, Cliques, CTR, etc.).
    Cada campanha só é exportada uma vez (controlado por reported_campaigns.json)."""
    if not ENABLE_SHEETS_EXPORT:
        return

    reported = load_reported_state()
    rows_to_add = []
    newly_reported_ids = []

    for publisher, campaigns in all_publishers.items():
        for c in campaigns:
            if c["status"] != "COMPLETED":
                continue
            cid = str(c["id"])
            if cid in reported:
                continue

            goal_text = (
                f'{c["goal_units"]} {c["goal_unit_type"]}' if c.get("goal_units") else "Sem meta definida"
            )
            rows_to_add.append([
                datetime.now().strftime("%d/%m/%Y %H:%M"),
                publisher,
                c["name"],
                c["type"],
                c["impressions"],
                c["clicks"],
                c["ctr"],
                goal_text,
                c["start_date"] or "",
                c["end_date"] or "",
            ])
            newly_reported_ids.append(cid)

    if not rows_to_add:
        return

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        key_path = _get_service_account_key_path()
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        credentials = Credentials.from_service_account_file(key_path, scopes=scopes)
        gc = gspread.authorize(credentials)

        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        try:
            ws = sh.worksheet(SHEETS_WORKSHEET_NAME)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=SHEETS_WORKSHEET_NAME, rows=1000, cols=10)
            ws.append_row([
                "Finalizado em", "Publisher", "Campanha", "Tipo",
                "Impressões", "Cliques", "CTR (%)", "Meta", "Início", "Fim",
            ])

        ws.append_rows(rows_to_add, value_input_option="USER_ENTERED")

        for cid in newly_reported_ids:
            reported[cid] = True
        save_reported_state(reported)

        print(f"[debug] {len(rows_to_add)} relatório(s) de campanha(s) finalizada(s) exportado(s) pro Google Sheets.")
    except Exception as e:
        print(f"[debug] Não consegui exportar pro Google Sheets: {e}")


def upload_dashboard_to_gcs():
    """Publica o dashboard.html num bucket do Google Cloud Storage, usando a
    mesma service account já configurada pra API do GAM. Veja README.md."""
    if not ENABLE_CLOUD_UPLOAD:
        return

    try:
        from google.cloud import storage
        from google.oauth2 import service_account

        key_path = _get_service_account_key_path()
        credentials = service_account.Credentials.from_service_account_file(key_path)
        client = storage.Client(credentials=credentials, project=credentials.project_id)
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob("dashboard.html")
        blob.cache_control = "no-cache, max-age=0"
        blob.upload_from_filename(str(DASHBOARD_PATH), content_type="text/html; charset=utf-8")
        print(f"Dashboard publicado em: https://storage.googleapis.com/{GCS_BUCKET_NAME}/dashboard.html")
    except Exception as e:
        print(f"[debug] Não consegui publicar o dashboard no Cloud Storage: {e}")


def get_clients():
    """Retorna uma lista de (nome_da_conta, client), uma para cada rede do GAM.

    Se existir 'accounts.yaml' na pasta, usa ele pra rodar contra VÁRIAS redes
    (mesma service account, network_code diferente pra cada uma — veja
    accounts.example.yaml e README.md). Caso contrário, cai no modo de conta
    única de sempre, usando 'googleads.yaml'.
    """
    if not ACCOUNTS_CONFIG_PATH.exists():
        return [("Padrão", get_client())]

    with open(ACCOUNTS_CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    application_name = config["application_name"]
    key_file = config["path_to_private_key_file"]
    oauth2_client = oauth2.GoogleServiceAccountClient(key_file, oauth2.GetAPIScope("ad_manager"))

    clients = []
    for account in config["accounts"]:
        client = ad_manager.AdManagerClient(
            oauth2_client, application_name, network_code=str(account["network_code"])
        )
        clients.append((account["name"], client))
    return clients


def fetch_custom_field_map(client):
    """Retorna {custom_field_option_id: label} para o campo de Publisher, se usado."""
    if PUBLISHER_SOURCE != "custom_field":
        return {}, None

    custom_field_service = client.GetService("CustomFieldService", version=GAM_API_VERSION)
    statement = (
        ad_manager.StatementBuilder(version=GAM_API_VERSION)
        .Where("name = :name")
        .WithBindVariable("name", PUBLISHER_CUSTOM_FIELD_NAME)
    )
    response = _to_dict(custom_field_service.getCustomFieldsByStatement(statement.ToStatement()))
    fields = response.get("results", []) if response else []
    if not fields:
        return {}, None

    field = fields[0]
    field_id = field["id"]
    option_map = {}
    for option in field.get("options", []) or []:
        option_map[option["id"]] = option.get("displayName", "Desconhecido")
    return option_map, field_id


def fetch_orders(client, order_ids):
    """Busca os Orders (para pegar publisher e/ou advertiser) em lote."""
    if not order_ids:
        return {}
    order_service = client.GetService("OrderService", version=GAM_API_VERSION)
    orders_by_id = {}
    ids_list = list(order_ids)
    chunk_size = 400  # limite de segurança para statements com muitos IDs
    for i in range(0, len(ids_list), chunk_size):
        chunk = ids_list[i : i + chunk_size]
        statement = (
            ad_manager.StatementBuilder(version=GAM_API_VERSION)
            .Where("id IN (%s)" % ", ".join(str(oid) for oid in chunk))
        )
        response = _to_dict(order_service.getOrdersByStatement(statement.ToStatement()))
        for order in response.get("results", []) if response else []:
            orders_by_id[order["id"]] = order
    return orders_by_id


def debug_check_network(client):
    """Mostra qual rede está configurada e quais redes a service account enxerga."""
    network_service = client.GetService("NetworkService", version=GAM_API_VERSION)

    try:
        current = _to_dict(network_service.getCurrentNetwork())
        print(f"[debug] Rede atual (do googleads.yaml): "
              f"networkCode={current.get('networkCode')} displayName={current.get('displayName')}")
    except Exception as e:
        print(f"[debug] Não consegui pegar a rede atual: {e}")

    try:
        all_networks = _to_dict(network_service.getAllNetworks())
        networks = all_networks if isinstance(all_networks, list) else (all_networks or [])
        print(f"[debug] Redes que essa service account consegue acessar ({len(networks)}):")
        for n in networks:
            print(f"    - networkCode={n.get('networkCode')} displayName={n.get('displayName')}")
    except Exception as e:
        print(f"[debug] Não consegui listar todas as redes: {e}")


def debug_check_any_orders(client):
    """Sanity check: existe QUALQUER order visível pra essa service account?"""
    order_service = client.GetService("OrderService", version=GAM_API_VERSION)
    statement = ad_manager.StatementBuilder(version=GAM_API_VERSION).Limit(1)
    response = _to_dict(order_service.getOrdersByStatement(statement.ToStatement()))
    total = response.get("totalResultSetSize") if response else None
    print(f"[debug] Total de Orders visíveis pra essa service account: {total}")


def debug_check_any_line_items(client):
    """Sanity check: existe QUALQUER line item na conta, independente do status?"""
    line_item_service = client.GetService("LineItemService", version=GAM_API_VERSION)
    statement = ad_manager.StatementBuilder(version=GAM_API_VERSION).Limit(1)
    response = _to_dict(line_item_service.getLineItemsByStatement(statement.ToStatement()))
    total = response.get("totalResultSetSize") if response else None
    print(f"[debug] Total de line items na conta (todos os status): {total}")
    if response and response.get("results"):
        print(f"[debug] Exemplo de status encontrado: {response['results'][0].get('status')}")


def fetch_companies(client, company_ids):
    """Busca nomes de Advertiser/Agency (Company) em lote, para exibir como publisher."""
    if not company_ids:
        return {}
    company_service = client.GetService("CompanyService", version=GAM_API_VERSION)
    companies_by_id = {}
    ids_list = list(company_ids)
    chunk_size = 400
    for i in range(0, len(ids_list), chunk_size):
        chunk = ids_list[i : i + chunk_size]
        statement = (
            ad_manager.StatementBuilder(version=GAM_API_VERSION)
            .Where("id IN (%s)" % ", ".join(str(cid) for cid in chunk))
        )
        response = _to_dict(company_service.getCompaniesByStatement(statement.ToStatement()))
        for company in response.get("results", []) if response else []:
            companies_by_id[company["id"]] = company.get("name")
    return companies_by_id


ORDER_NAME_PREFIX_PATTERN = re.compile(r"^\s*\[([^\]]+)\]")


def extract_publisher_from_order_name(order_name):
    """Extrai o texto entre colchetes no início do nome do Order.
    Ex: '[Tribuna do Norte] Sportradar - Sports' -> 'Tribuna do Norte'"""
    if not order_name:
        return "Sem publisher definido"
    match = ORDER_NAME_PREFIX_PATTERN.match(order_name)
    if match:
        return match.group(1).strip()
    return "Sem publisher definido"


def strip_publisher_prefix(order_name):
    """Remove o '[Publisher]' do início do nome do Order, pra exibir só o resto."""
    if not order_name:
        return order_name
    return ORDER_NAME_PREFIX_PATTERN.sub("", order_name).strip(" -–—:")


def fetch_report_data(client):
    """Busca, via ReportService (um único relatório), estes dados por line item:
    1) impressões e impressões visíveis (pra métrica de VA)
    2) em quais meses a campanha teve impressão de verdade (pra filtro de mês)
    3) detalhamento diário (impressões + cliques por dia, pra relatório exportável)
    Retorna (viewability_map, active_months_map, ok, daily_stats_map):
      viewability_map: {line_item_id (str): (impressions, viewable_impressions)}
      active_months_map: {line_item_id (str): {"YYYY-MM", ...}}
      daily_stats_map: {line_item_id (str): [{"date": "YYYY-MM-DD", "impressions": N, "clicks": N}, ...]}
    """
    if not ENABLE_VIEWABILITY_REPORT:
        return {}, {}, False, {}

    try:
        report_service = client.GetService("ReportService", version=GAM_API_VERSION)
        today = datetime.now(timezone.utc)
        report_job = {
            "reportQuery": {
                "dimensions": ["LINE_ITEM_ID", "DATE"],
                "columns": [
                    "AD_SERVER_IMPRESSIONS", "AD_SERVER_CLICKS",
                    "AD_SERVER_ACTIVE_VIEW_VIEWABLE_IMPRESSIONS",
                ],
                "dateRangeType": "CUSTOM_DATE",
                "startDate": {"year": today.year, "month": 1, "day": 1},
                "endDate": {"year": today.year, "month": today.month, "day": today.day},
            }
        }

        saved_job = _to_dict(report_service.runReportJob(report_job))
        job_id = saved_job["id"]
        print(f"[debug] Relatório de entrega diária iniciado (job {job_id}), aguardando...")

        status = saved_job.get("reportJobStatus")
        waited = 0
        while status not in ("COMPLETED", "FAILED") and waited < VIEWABILITY_REPORT_TIMEOUT_SECONDS:
            time.sleep(5)
            waited += 5
            status = report_service.getReportJobStatus(job_id)

        if status != "COMPLETED":
            print(f"[debug] Relatório não completou a tempo (status={status}). VA e filtro por mês real ficarão sem dado desta vez.")
            return {}, {}, False, {}

        downloader = client.GetDataDownloader(version=GAM_API_VERSION)
        report_file = io.BytesIO()
        downloader.DownloadReportToFile(job_id, "CSV_DUMP", report_file, use_gzip_compression=True)
        report_file.seek(0)

        with gzip.GzipFile(fileobj=report_file) as gz:
            content = gz.read().decode("utf-8")

        reader = csv.DictReader(io.StringIO(content))
        headers = reader.fieldnames or []
        print(f"[debug] Colunas do relatório: {headers}")

        li_col = next((h for h in headers if "LINE_ITEM_ID" in h.upper()), None)
        date_col = next((h for h in headers if h.upper().endswith(".DATE") or h.upper() == "DATE"), None)
        imp_col = next((h for h in headers if "AD_SERVER_IMPRESSIONS" in h.upper()), None)
        click_col = next((h for h in headers if h.upper().endswith("AD_SERVER_CLICKS")), None)
        view_col = next((h for h in headers if "ACTIVE_VIEW_VIEWABLE_IMPRESSIONS" in h.upper()), None)

        if not li_col or not date_col or not imp_col or not view_col:
            print("[debug] Não achei todas as colunas esperadas no relatório — "
                  "veja as colunas acima e me avise pra eu ajustar o script.")
            return {}, {}, False, {}

        viewability_totals = {}  # li_id -> [impressions, viewable_impressions]
        active_months = {}  # li_id -> set of "YYYY-MM"
        daily_stats = {}  # li_id -> {date_str: [impressions, clicks]}
        unparsed_dates = 0

        for row in reader:
            li_id = row.get(li_col)
            date_str = row.get(date_col)
            if not li_id or not date_str:
                continue
            try:
                imp = int(row.get(imp_col) or 0)
                clicks = int(row.get(click_col) or 0) if click_col else 0
                view = int(row.get(view_col) or 0)
            except ValueError:
                continue

            li_id = str(li_id)
            totals = viewability_totals.setdefault(li_id, [0, 0])
            totals[0] += imp
            totals[1] += view

            parsed_date = None
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
                try:
                    parsed_date = datetime.strptime(date_str, fmt)
                    break
                except ValueError:
                    continue

            if parsed_date is None:
                unparsed_dates += 1
                continue

            iso_date = parsed_date.strftime("%Y-%m-%d")
            day_totals = daily_stats.setdefault(li_id, {}).setdefault(iso_date, [0, 0])
            day_totals[0] += imp
            day_totals[1] += clicks

            if imp > 0:
                month_key = f"{parsed_date.year:04d}-{parsed_date.month:02d}"
                active_months.setdefault(li_id, set()).add(month_key)

        if unparsed_dates:
            print(f"[debug] Não consegui interpretar a data em {unparsed_dates} linha(s) do relatório "
                  f"(formato inesperado) — me avise se o filtro por mês real vier incompleto.")

        viewability_map = {li_id: tuple(vals) for li_id, vals in viewability_totals.items()}

        daily_stats_map = {}
        for li_id, dates_dict in daily_stats.items():
            daily_stats_map[li_id] = [
                {"date": date_str, "impressions": vals[0], "clicks": vals[1]}
                for date_str, vals in sorted(dates_dict.items())
            ]

        print(f"[debug] Viewability resolvida para {len(viewability_map)} line items. "
              f"Meses ativos resolvidos para {len(active_months)} line items. "
              f"Detalhamento diário resolvido para {len(daily_stats_map)} line items.")
        return viewability_map, active_months, True, daily_stats_map

    except Exception as e:
        print(f"[debug] Não consegui buscar o relatório de entrega diária: {e}")
        return {}, {}, False, {}


def fetch_root_ad_unit_id(client):
    """Pega o ID da ad unit raiz da rede — tudo abaixo dela é inventário real."""
    network_service = client.GetService("NetworkService", version=GAM_API_VERSION)
    network = _to_dict(network_service.getCurrentNetwork())
    return network.get("effectiveRootAdUnitId") if network else None


def fetch_ad_unit_map(client):
    """Busca todas as ad units da rede e retorna {id: {'parentId':..., 'name':...}}."""
    inventory_service = client.GetService("InventoryService", version=GAM_API_VERSION)
    ad_unit_map = {}
    statement = ad_manager.StatementBuilder(version=GAM_API_VERSION).Limit(500)
    while True:
        response = _to_dict(inventory_service.getAdUnitsByStatement(statement.ToStatement()))
        results = response.get("results", []) if response else []
        for au in results:
            ad_unit_map[au["id"]] = {"parentId": au.get("parentId"), "name": au.get("name")}
        if len(results) < statement.limit:
            break
        statement.offset += statement.limit
    return ad_unit_map


def resolve_publisher_from_ad_unit(ad_unit_id, ad_unit_map, root_id):
    """Sobe a árvore de ad units até achar o nó de topo (filho direto da raiz)."""
    if ad_unit_id is None:
        return "Sem publisher definido"

    current_id = ad_unit_id
    visited = set()
    while current_id is not None and current_id not in visited:
        visited.add(current_id)
        node = ad_unit_map.get(current_id)
        if node is None:
            return "Sem publisher definido"
        parent_id = node.get("parentId")
        if parent_id is None or str(parent_id) == str(root_id):
            return node.get("name") or "Sem publisher definido"
        current_id = parent_id

    return "Sem publisher definido"


def resolve_root_ad_unit(ad_unit_id, ad_unit_map, root_id):
    """Igual a resolve_publisher_from_ad_unit, mas devolve (id, nome) do
    Ad Unit de topo — usado pra saber o 'site' de verdade de cada campanha,
    independente de qual PUBLISHER_SOURCE está configurado. Isso permite
    comparar a entrega de uma campanha só com o inventário do SEU Ad Unit
    pai, não da rede inteira (que pode ser compartilhada por vários sites)."""
    if ad_unit_id is None:
        return None, None

    current_id = ad_unit_id
    visited = set()
    while current_id is not None and current_id not in visited:
        visited.add(current_id)
        node = ad_unit_map.get(current_id)
        if node is None:
            return None, None
        parent_id = node.get("parentId")
        if parent_id is None or str(parent_id) == str(root_id):
            return current_id, node.get("name")
        current_id = parent_id

    return None, None


def get_line_item_target_ad_unit_id(line_item):
    """Pega o primeiro ad unit alvo do targeting da line item, se houver."""
    targeting = line_item.get("targeting") or {}
    inventory_targeting = targeting.get("inventoryTargeting") or {}
    targeted = inventory_targeting.get("targetedAdUnits") or []
    if not targeted:
        return None
    return targeted[0].get("adUnitId")


def fetch_line_items(client):
    """Busca as line items ativas/em veiculação."""
    line_item_service = client.GetService("LineItemService", version=GAM_API_VERSION)
    statement = (
        ad_manager.StatementBuilder(version=GAM_API_VERSION)
        .Where(
            "status IN ('READY', 'DELIVERING', 'PAUSED', 'COMPLETED') "
            "AND lineItemType IN ('SPONSORSHIP', 'STANDARD', 'HOUSE')"
        )
        .OrderBy("id", ascending=True)
        .Limit(500)
    )

    all_line_items = []
    while True:
        response = _to_dict(line_item_service.getLineItemsByStatement(statement.ToStatement()))
        results = response.get("results", []) if response else []
        all_line_items.extend(results)
        if len(results) < statement.limit:
            break
        statement.offset += statement.limit
    return all_line_items


# ----------------------------------------------------------------------------
# CÁLCULOS
# ----------------------------------------------------------------------------

def to_datetime(gam_datetime):
    """Converte o DateTime da API do GAM para um datetime Python com timezone."""
    if gam_datetime is None:
        return None
    d = gam_datetime["date"]
    return datetime(
        d["year"], d["month"], d["day"],
        gam_datetime.get("hour", 0), gam_datetime.get("minute", 0), gam_datetime.get("second", 0),
        tzinfo=timezone.utc,  # aproximação; ajuste conforme timeZoneId se precisar de precisão exata
    )


def month_label_for_key(month_key):
    if month_key == "sem-data":
        return "Sem data definida"
    year, month = month_key.split("-")
    return f"{MONTH_NAMES_PT[int(month) - 1]}/{year}"


def month_range_keys(start_dt, end_dt):
    """Lista de 'YYYY-MM' cobrindo do mês de início ao mês de fim (inclusive).
    Se faltar uma das datas, usa a outra como início e fim."""
    if start_dt is None and end_dt is None:
        return ["sem-data"]

    start = start_dt or end_dt
    end = end_dt or start_dt
    if start > end:
        start, end = end, start

    months = []
    year, month = start.year, start.month
    # limite de segurança pra evitar loop infinito em datas malformadas
    for _ in range(120):
        months.append(f"{year:04d}-{month:02d}")
        if (year, month) >= (end.year, end.month):
            break
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months


def _linear_trend_predict(values):
    """Ajusta uma reta nos valores (regressão linear simples) e prevê o
    próximo ponto da série — usado pra saber o que seria 'esperado' pro
    último dia, DADA a tendência que a campanha já vinha tendo (em vez de
    comparar com uma média fixa, que dispara falso alarme em campanhas que
    sobem forte no início e vão equilibrando aos poucos, um padrão normal)."""
    n = len(values)
    if n < 2:
        return values[0] if values else 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return mean_y
    slope = sum((xs[i] - mean_x) * (values[i] - mean_y) for i in range(n)) / denominator
    intercept = mean_y - slope * mean_x
    predicted = slope * n + intercept  # x = n é o ponto seguinte na série
    return max(predicted, 0.0)


def detect_anomaly(daily_stats, status):
    """Compara o último dia COMPLETO disponível (nunca hoje, já que o dado de
    hoje é parcial — o dia ainda não terminou) com o que era ESPERADO pra
    esse dia, dada a TENDÊNCIA dos dias anteriores (regressão linear, não
    média fixa) — assim, uma campanha que já vem caindo/subindo aos poucos
    de forma normal não dispara alerta; só dispara quando foge do padrão que
    ela mesma já vinha tendo. Só considera campanhas 'Em veiculação' —
    pausada/finalizada não entra aqui, cair pra zero é esperado nesses casos.
    Devolve um dict com o tipo de anomalia encontrada, ou None."""
    if status != "DELIVERING" or not daily_stats:
        return None

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Nunca compara com hoje — o dado do dia corrente é sempre parcial (o
    # script roda de hora em hora, então "hoje" tem só uma fração das 24h de
    # entrega normal), o que pareceria uma "queda" todo santo dia sem ser de
    # verdade. Só olha dias já FECHADOS.
    sorted_days = sorted((d for d in daily_stats if d.get("date") != today_str), key=lambda d: d["date"])
    if len(sorted_days) < ANOMALY_LOOKBACK_DAYS + 1:
        return None  # ainda não tem histórico suficiente pra comparar

    last_day = sorted_days[-1]
    previous_days = sorted_days[-(ANOMALY_LOOKBACK_DAYS + 1):-1]
    values = [d.get("impressions", 0) or 0 for d in previous_days]
    avg_impressions = sum(values) / len(values)

    if avg_impressions < ANOMALY_MIN_AVG_IMPRESSIONS:
        return None  # volume baixo demais, a variação % não é confiável

    expected = _linear_trend_predict(values)
    # Protege contra tendência já em queda forte (previsão perto de zero) —
    # nesse caso, usa a média como piso, pra não deixar o "esperado" cair
    # rápido demais e a métrica ficar frouxa demais.
    baseline = max(expected, avg_impressions * 0.3)

    last_impressions = last_day.get("impressions", 0) or 0

    if last_impressions == 0:
        return {
            "type": "zeroed", "date": last_day["date"],
            "last_impressions": 0, "avg_impressions": round(avg_impressions), "expected": round(baseline),
        }

    if last_impressions < baseline * (1 - ANOMALY_DROP_THRESHOLD_PCT / 100):
        change_pct = round((1 - last_impressions / baseline) * 100, 1)
        return {
            "type": "drop", "date": last_day["date"], "last_impressions": last_impressions,
            "avg_impressions": round(avg_impressions), "expected": round(baseline), "change_pct": -change_pct,
        }

    if last_impressions > baseline * (1 + ANOMALY_SPIKE_THRESHOLD_PCT / 100):
        change_pct = round((last_impressions / baseline - 1) * 100, 1)
        return {
            "type": "spike", "date": last_day["date"], "last_impressions": last_impressions,
            "avg_impressions": round(avg_impressions), "expected": round(baseline), "change_pct": change_pct,
        }

    return None


def compute_campaign_metrics(line_item, publisher_name, display_name=None, viewability_override=None, active_months_override=None, network_code=None, daily_stats=None, root_ad_unit_id=None, root_ad_unit_name=None):
    stats = line_item.get("stats") or {}
    impressions = stats.get("impressionsDelivered", 0) or 0
    clicks = stats.get("clicksDelivered", 0) or 0
    viewable_impressions = stats.get("viewableImpressionsDelivered")
    impressions_for_va = impressions

    # Dado vindo do ReportService (Active View) tem prioridade, pois o
    # LineItemService normalmente não preenche viewableImpressionsDelivered.
    if viewability_override:
        report_impressions, report_viewable = viewability_override
        if report_impressions:
            viewable_impressions = report_viewable
            impressions_for_va = report_impressions

    ctr = round((clicks / impressions) * 100, 2) if impressions else 0.0
    va_rate = (
        round((viewable_impressions / impressions_for_va) * 100, 2)
        if viewable_impressions is not None and impressions_for_va
        else None
    )

    primary_goal = line_item.get("primaryGoal") or {}
    goal_units = primary_goal.get("units")
    goal_unit_type = primary_goal.get("unitType")

    delivery_indicator = line_item.get("deliveryIndicator") or {}
    expected_pct = delivery_indicator.get("expectedDeliveryPercentage")
    actual_pct = delivery_indicator.get("actualDeliveryPercentage")

    underdelivery = False
    if expected_pct is not None and actual_pct is not None:
        underdelivery = (expected_pct - actual_pct) >= UNDERDELIVERY_THRESHOLD_PCT

    end_dt = to_datetime(line_item.get("endDateTime"))
    start_dt = to_datetime(line_item.get("startDateTime"))
    now = datetime.now(timezone.utc)
    hours_left = (end_dt - now).total_seconds() / 3600 if end_dt else None

    ending_today = end_dt is not None and end_dt >= now and end_dt.date() == now.date()
    ending_soon = (
        hours_left is not None
        and 0 <= hours_left <= DAYS_BEFORE_END_TO_ALERT * 24
        and not ending_today
    )

    month_keys = month_range_keys(start_dt, end_dt)
    if active_months_override is not None:
        # Só mantém os meses em que a campanha teve impressão de verdade,
        # preservando "sem-data" (campanhas sem datas definidas continuam aparecendo).
        month_keys = [k for k in month_keys if k == "sem-data" or k in active_months_override]

    gam_url = (
        f"https://admanager.google.com/{network_code}#delivery/line_item/detail/"
        f"line_item_id={line_item['id']}&order_id={line_item.get('orderId')}"
        if network_code and line_item.get("orderId") else None
    )

    return {
        "id": line_item["id"],
        "name": display_name or line_item.get("name"),
        "publisher": publisher_name,
        "type": line_item.get("lineItemType"),
        "status": line_item.get("status"),
        "gam_url": gam_url,
        "network_code": network_code,
        "order_id": line_item.get("orderId"),
        "root_ad_unit_id": root_ad_unit_id,
        "root_ad_unit_name": root_ad_unit_name,
        "notes": (line_item.get("notes") or "").strip(),
        "daily_stats": daily_stats or [],
        "anomaly": detect_anomaly(daily_stats or [], line_item.get("status")),
        "impressions": impressions,
        "clicks": clicks,
        "ctr": ctr,
        "va_rate": va_rate,
        "viewable_impressions": viewable_impressions,
        "impressions_for_va": impressions_for_va,
        "goal_units": goal_units,
        "goal_unit_type": GOAL_UNIT_LABELS.get(goal_unit_type, goal_unit_type),
        "expected_pct": expected_pct,
        "actual_pct": actual_pct,
        "underdelivery": underdelivery,
        "start_date": start_dt.strftime("%d/%m/%Y") if start_dt else None,
        "end_date": end_dt.strftime("%d/%m/%Y %H:%M") if end_dt else None,
        "hours_left": round(hours_left, 1) if hours_left is not None else None,
        "ending_soon": ending_soon,
        "ending_today": ending_today,
        "month_keys": month_keys,
    }


def build_dataset(client):
    debug_check_network(client)
    debug_check_any_orders(client)
    debug_check_any_line_items(client)
    network_code = client.network_code
    line_items = fetch_line_items(client)
    print(f"[debug] Line items encontradas (status READY/DELIVERING/PAUSED/COMPLETED): {len(line_items)}")

    order_ids = {li["orderId"] for li in line_items if li.get("orderId")}
    print(f"[debug] Orders distintos referenciados: {len(order_ids)}")

    orders_by_id = fetch_orders(client, order_ids)
    print(f"[debug] Orders carregados: {len(orders_by_id)}")

    custom_field_options, custom_field_id = fetch_custom_field_map(client)
    print(f"[debug] Custom field '{PUBLISHER_CUSTOM_FIELD_NAME}' encontrado: {custom_field_id is not None}")

    companies_by_id = {}
    if PUBLISHER_SOURCE == "advertiser":
        advertiser_ids = {o.get("advertiserId") for o in orders_by_id.values() if o.get("advertiserId")}
        companies_by_id = fetch_companies(client, advertiser_ids)
        print(f"[debug] Advertisers (publishers) resolvidos: {len(companies_by_id)}")

    ad_unit_map = {}
    root_ad_unit_id = fetch_root_ad_unit_id(client)
    ad_unit_map = fetch_ad_unit_map(client)
    print(f"[debug] Root ad unit: {root_ad_unit_id} | Ad units carregadas: {len(ad_unit_map)}")

    viewability_map, active_months_map, report_ok, daily_stats_map = fetch_report_data(client)

    campaigns = []
    skipped_no_publisher = 0
    for li in line_items:
        order = orders_by_id.get(li.get("orderId"), {})
        publisher_name = "Sem publisher definido"

        if PUBLISHER_SOURCE == "order_name_prefix":
            publisher_name = extract_publisher_from_order_name(order.get("name"))
            if publisher_name == "Sem publisher definido":
                skipped_no_publisher += 1
                continue
        elif PUBLISHER_SOURCE == "adunit_parent":
            target_ad_unit_id = get_line_item_target_ad_unit_id(li)
            publisher_name = resolve_publisher_from_ad_unit(target_ad_unit_id, ad_unit_map, root_ad_unit_id)
        elif PUBLISHER_SOURCE == "advertiser":
            publisher_name = companies_by_id.get(order.get("advertiserId"), publisher_name)
        elif PUBLISHER_SOURCE == "custom_field" and custom_field_id:
            for cfv in order.get("customFieldValues", []) or []:
                if cfv.get("customFieldId") == custom_field_id:
                    option_id = cfv.get("customFieldOptionId")
                    publisher_name = custom_field_options.get(option_id, publisher_name)

        # Ad Unit pai da campanha — sempre resolvido, independente do modo de
        # agrupamento de publisher escolhido acima. Usado pra comparar a
        # entrega com o inventário do site de verdade (não da rede inteira).
        target_ad_unit_id = get_line_item_target_ad_unit_id(li)
        root_au_id, root_au_name = resolve_root_ad_unit(target_ad_unit_id, ad_unit_map, root_ad_unit_id)

        li_id_str = str(li["id"])
        campaigns.append(compute_campaign_metrics(
            li, publisher_name, display_name=strip_publisher_prefix(order.get("name")),
            viewability_override=viewability_map.get(li_id_str),
            active_months_override=(active_months_map.get(li_id_str, set()) if report_ok else None),
            network_code=network_code,
            daily_stats=daily_stats_map.get(li_id_str, []),
            root_ad_unit_id=root_au_id,
            root_ad_unit_name=root_au_name,
        ))

    print(f"[debug] Campanhas ignoradas (sem '[Publisher]' no nome do Order): {skipped_no_publisher}")

    publishers = {}
    for c in campaigns:
        publishers.setdefault(c["publisher"], []).append(c)

    return publishers


# ----------------------------------------------------------------------------
# NOTIFICAÇÕES POR E-MAIL
# ----------------------------------------------------------------------------

def load_notified_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_notified_state(state):
    STATE_PATH.write_text(json.dumps(state))


def collect_alerts(publishers, notified_state):
    """Retorna alertas novos (ainda não notificados hoje) para underdelivery
    e fim próximo — só isso vai por e-mail. Anomalias de entrega
    (queda/pico/zerada) são calculadas separadamente e aparecem só na tela
    de Alertas Inteligentes, não no e-mail (gera ruído demais lá).
    Só considera campanhas com status DELIVERING (rodando agora)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    alerts = []

    for publisher, campaigns in publishers.items():
        for c in campaigns:
            if c["status"] != "DELIVERING":
                continue

            key_under = f"under-{c['id']}-{today}"
            key_ending = f"ending-{c['id']}-{today}"
            key_today = f"today-{c['id']}-{today}"

            if c["underdelivery"] and key_under not in notified_state:
                alerts.append(("underdelivery", publisher, c))
                notified_state[key_under] = True

            if c["ending_today"] and key_today not in notified_state:
                alerts.append(("ending_today", publisher, c))
                notified_state[key_today] = True
            elif c["ending_soon"] and key_ending not in notified_state:
                alerts.append(("ending_soon", publisher, c))
                notified_state[key_ending] = True

    return alerts


def send_test_email():
    """Envia um e-mail de teste incondicional, só pra validar SMTP/credenciais,
    sem depender de existir algum alerta real no momento."""
    if not (SMTP_USER and SMTP_PASSWORD and EMAIL_TO):
        print(
            "Não deu pra testar: faltam variáveis (SMTP_USER, SMTP_PASSWORD ou EMAIL_TO). "
            "Confirme se está rodando via run_gam_monitor.bat (com essas variáveis preenchidas), "
            "e não direto com 'py gam_monitor.py'."
        )
        return

    msg = MIMEMultipart()
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(EMAIL_TO)
    msg["Subject"] = "[GAM Monitor] E-mail de teste"
    msg.attach(MIMEText(
        "Este é um e-mail de teste do GAM Monitor.\n\n"
        "Se você recebeu isso, a configuração de SMTP está funcionando certinho.",
        "plain",
    ))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print(f"E-mail de teste enviado com sucesso para: {', '.join(EMAIL_TO)}")
    except Exception as e:
        print(f"Erro ao enviar e-mail de teste: {e}")


def _pacing_text(c, html=False):
    """Texto de pacing (entrega real vs. esperada), se houver dado."""
    if c["actual_pct"] is not None and c["expected_pct"] is not None:
        actual = round(c["actual_pct"], 1)
        expected = round(c["expected_pct"], 1)
        if html:
            return f" — pacing: <b>{actual}%</b> / esperado <b>{expected}%</b>"
        return f" — pacing: {actual}% / esperado {expected}%"
    return ""


ALERT_STYLES = {
    "underdelivery": {"badge": "UNDER", "color": "#e5484d", "bg": "#fdeaea"},
    "ending_today": {"badge": "TERMINA HOJE", "color": "#e5484d", "bg": "#fdeaea"},
    "ending_soon": {"badge": "TERMINANDO", "color": "#b5760a", "bg": "#fdf2e1"},
    "anomaly_zeroed": {"badge": "ZERADA", "color": "#8b1a1a", "bg": "#fdeaea"},
    "anomaly_drop": {"badge": "QUEDA", "color": "#e5484d", "bg": "#fdeaea"},
    "anomaly_spike": {"badge": "PICO", "color": "#4a3aa7", "bg": "#efe8ff"},
}


def _alert_description(kind, c):
    pacing_txt = _pacing_text(c, html=True)
    if kind == "underdelivery":
        return f"Underdelivery{pacing_txt}"
    if kind == "ending_today":
        return f"Termina hoje{pacing_txt}"
    if kind in ("anomaly_zeroed", "anomaly_drop", "anomaly_spike"):
        anomaly = c.get("anomaly") or {}
        avg = format_number(anomaly.get("avg_impressions"))
        last = format_number(anomaly.get("last_impressions"))
        if kind == "anomaly_zeroed":
            return f"Zero impressões no último dia com dado ({anomaly.get('date')}) — média dos 7 dias antes: {avg}/dia"
        change = anomaly.get("change_pct")
        arrow = "▼" if kind == "anomaly_drop" else "▲"
        return (
            f"{arrow} {abs(change)}% vs. a média — {last} impressões em {anomaly.get('date')}, "
            f"contra média de {avg}/dia nos 7 dias anteriores"
        )
    days_left = math.ceil(c["hours_left"] / 24) if c["hours_left"] is not None else "?"
    return f"Faltam {days_left} dia(s){pacing_txt}"


EMAIL_LOG_PATH = OUTPUT_DIR / "email_log.txt"


def log_email_event(message):
    """Registra um evento de e-mail (enviado, pulado, erro) com timestamp,
    num arquivo separado — pra auditar rapidamente sem vasculhar o log geral."""
    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    with open(EMAIL_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")


def send_email_alerts(alerts):
    """Retorna True se o e-mail foi realmente enviado, False caso contrário
    (sem isso, o script marcaria alertas como 'notificados' mesmo sem enviar)."""
    if not alerts:
        return False
    if not (SMTP_USER and SMTP_PASSWORD and EMAIL_TO):
        print("Aviso: variáveis de SMTP/EMAIL_TO não configuradas — pulando envio de e-mail.")
        return False

    # Separa as campanhas que terminam HOJE — essas vão pra uma seção de
    # destaque no topo do e-mail, antes de tudo o mais.
    ending_today_alerts = [(k, p, c) for k, p, c in alerts if k == "ending_today"]
    other_alerts = [(k, p, c) for k, p, c in alerts if k != "ending_today"]

    grouped = {}
    for kind, publisher, c in other_alerts:
        grouped.setdefault(publisher, []).append((kind, c))

    # --- Versão em texto puro (fallback pra clientes de e-mail sem HTML) ---
    text_lines = ["Alertas do Monitor de Campanhas (Google Ad Manager):", ""]

    if ending_today_alerts:
        text_lines.append("!!! TERMINAM HOJE !!!")
        for kind, publisher, c in ending_today_alerts:
            pacing_txt = _pacing_text(c, html=False)
            text_lines.append(f"🔴 {c['name']} ({publisher}){pacing_txt}")
        text_lines.append("")

    for publisher in sorted(grouped.keys()):
        text_lines.append(f"=== {publisher} ===")
        for kind, c in grouped[publisher]:
            pacing_txt = _pacing_text(c, html=False)
            if kind == "underdelivery":
                text_lines.append(f"⚠️ UNDER — {c['name']}{pacing_txt}")
            elif kind in ("anomaly_zeroed", "anomaly_drop", "anomaly_spike"):
                label = ALERT_STYLES[kind]["badge"]
                text_lines.append(f"📊 {label} — {c['name']} — {_alert_description(kind, c)}")
            else:
                days_left = math.ceil(c["hours_left"] / 24) if c["hours_left"] is not None else "?"
                text_lines.append(f"⏰ {c['name']} — faltam {days_left} dia(s){pacing_txt}")
        text_lines.append("")
    text_body = "\n".join(text_lines)

    # --- Versão em HTML (a que aparece de fato no Gmail) ---
    ending_today_html = ""
    if ending_today_alerts:
        rows = []
        for kind, publisher, c in ending_today_alerts:
            rows.append(f"""
              <div style="margin:8px 0;padding:14px 16px;border-radius:8px;background:#fdeaea;
                          border:2px solid #e5484d;">
                <span style="display:inline-block;background:#e5484d;color:#ffffff;font-size:11px;
                             font-weight:800;letter-spacing:.03em;padding:3px 10px;border-radius:999px;margin-right:8px;">
                  TERMINA HOJE
                </span>
                <strong style="color:#1f1b2e;font-size:14px;">{html_module.escape(c['name'] or '')}</strong>
                <div style="color:#857f9b;font-size:12px;margin-top:4px;">
                  {publisher}{_pacing_text(c, html=True)}
                </div>
              </div>""")
        ending_today_html = f"""
          <div style="background:#fff5f5;border-radius:10px;padding:14px 16px 6px 16px;margin-bottom:8px;">
            <div style="font-size:13px;font-weight:800;color:#e5484d;text-transform:uppercase;
                        letter-spacing:.03em;margin-bottom:6px;">
              🔴 Terminam hoje
            </div>
            {''.join(rows)}
          </div>"""

    publisher_blocks = []
    for publisher in sorted(grouped.keys()):
        rows_html = []
        for kind, c in grouped[publisher]:
            style = ALERT_STYLES.get(kind, ALERT_STYLES["underdelivery"])
            rows_html.append(f"""
              <div style="margin:8px 0;padding:10px 14px;border-radius:8px;background:{style['bg']};
                          border-left:4px solid {style['color']};">
                <span style="display:inline-block;background:{style['color']};color:#ffffff;font-size:11px;
                             font-weight:700;letter-spacing:.02em;padding:2px 9px;border-radius:999px;margin-right:8px;">
                  {style['badge']}
                </span>
                <strong style="color:#1f1b2e;font-size:13px;">{html_module.escape(c['name'] or '')}</strong>
                <div style="color:#857f9b;font-size:12px;margin-top:4px;">{_alert_description(kind, c)}</div>
              </div>""")

        publisher_blocks.append(f"""
          <h2 style="font-size:14px;color:#1f1b2e;margin:22px 0 8px 0;padding-bottom:6px;
                     border-bottom:1px solid #e7e6f3;">{publisher}</h2>
          {''.join(rows_html)}""")

    html_body = f"""
    <div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#f4f5fa;padding:24px;">
      <div style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;
                  border:1px solid #e7e6f3;">
        <div style="background:#181321;padding:16px 24px;">
          <span style="color:#ffffff;font-size:16px;font-weight:700;">Monitor de Campanhas — Alertas</span>
        </div>
        <div style="padding:8px 24px 24px 24px;">
          {ending_today_html}
          {''.join(publisher_blocks)}
        </div>
      </div>
    </div>"""

    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(EMAIL_TO)
    msg["Subject"] = f"[GAM Monitor] {len(alerts)} alerta(s) de campanha"
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print(f"E-mail enviado com {len(alerts)} alerta(s).")
        log_email_event(f"ENVIADO — {len(alerts)} alerta(s) — destinatários: {', '.join(EMAIL_TO)}")
        return True
    except Exception as e:
        print(f"Erro ao enviar e-mail de alerta: {e}")
        log_email_event(f"ERRO — {len(alerts)} alerta(s) — falha: {e}")
        return False


# ----------------------------------------------------------------------------
# DASHBOARD HTML
# ----------------------------------------------------------------------------

DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="900">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="icon" type="image/png" href="{favicon_uri}">
<title>Monitor de Campanhas — Google Ad Manager</title>
<style>
  :root {{
    --bg: #f6f5f9; --card: #ffffff; --border: #eae6f0; --text: #241b33;
    --muted: #8a80a0; --brand-dark: #2f1440; --accent: #652f91; --accent-soft: #f1eaf8;
    --ok: #16a673; --ok-soft: #e3f8f0; --warn: #f2a93b; --warn-soft: #fdf2e1;
    --bad: #e5484d; --bad-soft: #fdeaea;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
    font-family: 'Poppins', -apple-system, Segoe UI, Roboto, sans-serif; -webkit-font-smoothing:antialiased; }}
  header {{ background:linear-gradient(135deg, var(--brand-dark), var(--accent)); padding: 18px 32px;
    display:flex; justify-content:space-between; align-items:center; box-shadow:0 2px 10px rgba(60,26,83,0.25); }}
  header h1 {{ font-size:19px; margin:0; color:#fff; font-weight:700; letter-spacing:.01em; }}
  header span {{ color:#d7c7e8; font-size:12px; }}
  .toolbar {{ background:var(--card); margin: 22px 32px 0 32px; padding: 14px 18px; border-radius:16px;
    display:flex; align-items:center; gap:12px; box-shadow: 0 2px 10px rgba(60,26,83,0.06); flex-wrap:wrap; }}
  .toolbar label {{ font-size:11px; color:var(--muted); font-weight:700; text-transform:uppercase; letter-spacing:.03em; }}
  .toolbar select {{ border:1px solid var(--border); background:var(--accent-soft); color:var(--accent);
    font-weight:600; font-size:13px; padding:7px 14px; border-radius:999px; outline:none; cursor:pointer; }}
  .summary-pill {{ background:var(--bg); border:1px solid var(--border); color:var(--muted); font-size:12px;
    padding:6px 12px; border-radius:999px; margin-left:auto; }}
  main {{ padding: 22px 32px 44px 32px; }}
  .publisher {{ margin-bottom: 12px; }}
  .publisher-title {{ display:grid; grid-template-columns: 18px minmax(160px, 340px) repeat(4, 110px) 1fr;
    align-items:center; column-gap:16px; margin-bottom:12px; cursor:pointer;
    background:var(--card); border:1px solid var(--border); border-radius:12px; padding:14px 20px;
    box-shadow: 0 1px 2px rgba(60,26,83,0.05); user-select:none; }}
  .publisher-title:hover {{ border-color:var(--accent); }}
  .publisher-name {{ display:flex; align-items:center; gap:10px; min-width:0; }}
  .publisher-name h2 {{ font-size:15px; margin:0; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .chevron {{ font-size:12px; color:var(--muted); transition:transform .15s; text-align:center; }}
  .publisher-summary {{ display:contents; }}
  .stat-box {{ background:var(--accent-soft); border-radius:10px; padding:8px 0; text-align:center; }}
  .stat-box .stat-label {{ display:block; font-size:10px; text-transform:uppercase; letter-spacing:.04em;
    color:var(--accent); font-weight:700; opacity:.85; }}
  .stat-box .stat-value {{ display:block; font-size:16px; font-weight:800; color:var(--text); margin-top:2px; }}
  .publisher-body.collapsed {{ display:none; }}
  .badge {{ background:var(--accent-soft); color:var(--accent); font-size:12px; font-weight:600; padding:3px 10px; border-radius:999px; white-space:nowrap; }}
  .grid {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap:14px; }}
  .card {{ background:var(--card); border:1px solid var(--border); border-radius:16px; padding:18px 20px;
    box-shadow: 0 1px 2px rgba(60,26,83,0.05); }}
  .card h3 {{ font-size:15px; margin:0 0 6px 0; color:var(--text); }}
  .card .type {{ color:var(--accent); background:var(--accent-soft); display:inline-block; font-size:11px;
    font-weight:700; letter-spacing:.02em; text-transform:uppercase; padding:2px 8px; border-radius:6px; margin-bottom:10px; }}
  .bar-track {{ background:#efe9f5; border-radius:6px; height:8px; overflow:hidden; margin: 6px 0 8px 0; }}
  .bar-fill {{ height:100%; border-radius:6px; background:var(--accent); }}
  .bar-fill.ok {{ background:var(--ok); }}
  .bar-fill.bad {{ background:var(--bad); }}
  .delivery-caption {{ font-size:12px; color:var(--muted); margin-bottom:10px; }}
  .delivery-caption b {{ color:var(--text); }}
  .metrics {{ display:grid; grid-template-columns: repeat(2, 1fr); gap:7px 12px; font-size:12px; color:var(--muted); margin-top:6px; }}
  .metrics b {{ color:var(--text); }}
  .flags {{ margin-top:12px; display:flex; gap:6px; flex-wrap:wrap; align-items:center; }}
  .info-icon {{ display:inline-flex; align-items:center; justify-content:center; width:18px; height:18px;
    border-radius:50%; background:var(--accent-soft); color:var(--accent); font-size:11px; font-weight:800;
    cursor:help; margin-left:auto; }}

  /* Agrupamento de itens de linha do mesmo Order (Pedido) */
  .card.group-summary {{ background:var(--accent-soft); border:1px dashed var(--accent); }}
  tr.group-summary {{ background:var(--accent-soft); }}
  tr.group-summary td, .card.group-summary h3 {{ font-weight:700; }}
  .chevron {{ display:inline-block; width:14px; cursor:pointer; color:var(--accent); user-select:none; }}
  .badge-mini {{ font-size:10px; font-weight:700; background:var(--card); color:var(--accent);
    padding:2px 8px; border-radius:999px; margin-left:6px; white-space:nowrap; }}
  tr.group-child td:first-child {{ padding-left:28px; }}
  .card.group-child {{ margin-left:16px; border-left:3px solid var(--accent-soft); }}
  .group-collapsed {{ display:none !important; }}

  .flag {{ font-size:11px; font-weight:700; padding:3px 9px; border-radius:8px; }}
  .flag.under {{ background:var(--bad-soft); color:var(--bad); }}
  .flag.ending {{ background:var(--warn-soft); color:#b5760a; }}
  .flag.ending-today {{ background:var(--bad-soft); color:var(--bad); }}
  .flag.flag-critical {{ box-shadow: 0 0 0 1.5px var(--bad); font-weight:800; }}
  .empty-state {{ text-align:center; color:var(--muted); font-size:13px; padding: 40px 0; }}

  .status-pill {{ font-size:11px; font-weight:700; padding:2px 9px; border-radius:999px; display:inline-block; }}
  .status-live {{ background:var(--ok-soft); color:var(--ok); }}
  .status-scheduled {{ background:var(--accent-soft); color:var(--accent); }}
  .status-paused {{ background:var(--warn-soft); color:#b5760a; }}
  .status-done {{ background:#ececec; color:#6b6b6b; }}
  .status-other {{ background:#ececec; color:#6b6b6b; }}

  .view-toggle {{ display:flex; background:var(--bg); border:1px solid var(--border); border-radius:999px; padding:3px; }}
  .view-toggle button {{ border:none; background:transparent; color:var(--muted); font-size:12px; font-weight:600;
    padding:6px 14px; border-radius:999px; cursor:pointer; }}
  .view-toggle button.active {{ background:var(--accent); color:#fff; }}

  .table-wrap {{ display:none; overflow-x:auto; background:var(--card); border:1px solid var(--border);
    border-radius:16px; box-shadow: 0 1px 2px rgba(60,26,83,0.05); }}
  body.table-view .grid {{ display:none; }}
  body.table-view .table-wrap {{ display:block; }}
  table.campaigns-table {{ width:100%; border-collapse:collapse; font-size:13px; table-layout:fixed; }}
  table.campaigns-table th {{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.02em;
    color:var(--muted); padding:10px 14px; border-bottom:1px solid var(--border); white-space:nowrap;
    overflow:hidden; text-overflow:ellipsis; }}
  table.campaigns-table td {{ padding:10px 14px; border-bottom:1px solid var(--border); vertical-align:middle;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  table.campaigns-table td:first-child {{ white-space:normal; word-break:break-word; }}
  table.campaigns-table td:nth-child(3) {{ white-space:normal; }}
  table.campaigns-table tr:last-child td {{ border-bottom:none; }}
  table.campaigns-table tr:hover td {{ background:var(--bg); }}
  table.campaigns-table .type-pill {{ color:var(--accent); background:var(--accent-soft); font-size:11px;
    font-weight:700; text-transform:uppercase; padding:2px 8px; border-radius:6px; }}
  .delivery-pct {{ font-weight:700; }}
  .delivery-pct.ok {{ color:var(--ok); }}
  .delivery-pct.bad {{ color:var(--bad); }}

  /* --- Responsivo (celular) --- */
  @media (max-width: 700px) {{
    header {{ padding:14px 16px; flex-direction:column; align-items:flex-start; gap:4px; }}
    header h1 {{ font-size:15px; }}
    .toolbar {{ margin:14px 12px 0 12px; padding:10px 12px; gap:8px; }}
    .toolbar label {{ display:none; }}
    .toolbar select {{ font-size:12px; padding:6px 10px; }}
    .summary-pill {{ width:100%; text-align:center; margin-left:0; order:99; }}
    main {{ padding:14px 12px 30px 12px; }}

    .publisher-title {{ grid-template-columns: 16px 1fr; grid-template-rows:auto auto; row-gap:10px;
      padding:12px 14px; }}
    .publisher-summary {{ display:flex; grid-column:1 / -1; flex-wrap:wrap; gap:8px; margin-left:0; }}
    .stat-box {{ flex:1 1 auto; min-width:70px; padding:6px 8px; }}
    .stat-box .stat-value {{ font-size:13px; }}
    .stat-box .stat-label {{ font-size:9px; }}
    .publisher-name h2 {{ font-size:13px; }}

    .grid {{ grid-template-columns: 1fr; }}
    .card {{ padding:12px 14px; }}
    .metrics {{ grid-template-columns: 1fr 1fr; font-size:11px; }}

    table.campaigns-table {{ table-layout:auto; }}
    table.campaigns-table th, table.campaigns-table td {{ padding:8px 10px; font-size:12px; white-space:nowrap; }}
  }}
</style>
</head>
<body class="table-view">
<header>
  <h1>Monitor de Campanhas — Google Ad Manager</h1>
  <span>Atualizado em {generated_at}</span>
</header>
<div class="toolbar">
  <label for="month-filter">Mês:</label>
  <select id="month-filter" onchange="applyFilters()">
    <option value="all">Todos os meses</option>
    {month_options}
  </select>
  <label for="publisher-filter">Publisher:</label>
  <select id="publisher-filter" onchange="applyFilters()">
    <option value="all">Todos os publishers</option>
    {publisher_options}
  </select>
  <label for="status-filter">Status:</label>
  <select id="status-filter" onchange="applyFilters()">
    <option value="all">Todos os status</option>
    {status_options}
  </select>
  <label for="ending-filter">Prazo:</label>
  <select id="ending-filter" onchange="applyFilters()">
    <option value="all">Todas</option>
    <option value="soon">Só terminando em breve/hoje</option>
  </select>
  <label for="under-filter">Entrega:</label>
  <select id="under-filter" onchange="applyFilters()">
    <option value="all">Todas</option>
    <option value="under">Só com underdelivery</option>
  </select>
  <div class="view-toggle">
    <button type="button" id="btn-view-cards" onclick="setView('cards')">Cards</button>
    <button type="button" id="btn-view-table" class="active" onclick="setView('table')">Tabela</button>
  </div>
  <span class="summary-pill" id="summary-pill"></span>
</div>
<main id="main-content">
{publisher_sections}
</main>
<script>
function setView(view) {{
  document.body.classList.toggle('table-view', view === 'table');
  document.getElementById('btn-view-cards').classList.toggle('active', view === 'cards');
  document.getElementById('btn-view-table').classList.toggle('active', view === 'table');
}}

function updatePublisherFilterOptions() {{
  var selectedMonth = document.getElementById('month-filter').value;
  var publisherSelect = document.getElementById('publisher-filter');
  var currentValue = publisherSelect.value;

  var validNames = [];
  document.querySelectorAll('.publisher').forEach(function(pub) {{
    var name = pub.getAttribute('data-publisher');
    var hasMatch = false;
    pub.querySelectorAll('.view-card-item').forEach(function(el) {{
      var months = (el.getAttribute('data-months') || '').split(',');
      if (selectedMonth === 'all' || months.indexOf(selectedMonth) !== -1) {{
        hasMatch = true;
      }}
    }});
    if (hasMatch) {{ validNames.push(name); }}
  }});
  validNames.sort();

  publisherSelect.textContent = '';
  var allOption = document.createElement('option');
  allOption.value = 'all';
  allOption.textContent = 'Todos os publishers';
  publisherSelect.appendChild(allOption);
  validNames.forEach(function(name) {{
    var opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    publisherSelect.appendChild(opt);
  }});

  publisherSelect.value = validNames.indexOf(currentValue) !== -1 ? currentValue : 'all';
}}

function togglePublisher(headerEl) {{
  var pub = headerEl.closest('.publisher');
  var body = pub.querySelector('.publisher-body');
  body.classList.toggle('collapsed');
  var chevron = headerEl.querySelector('.chevron');
  chevron.textContent = body.classList.contains('collapsed') ? '▸' : '▾';
}}

function toggleOrderGroup(chevronEl) {{
  var container = chevronEl.closest('[data-group-toggle]');
  var groupId = container.getAttribute('data-group-toggle');
  var isNowCollapsed = null;

  document.querySelectorAll('[data-group="' + groupId + '"]').forEach(function(el) {{
    el.classList.toggle('group-collapsed');
    isNowCollapsed = el.classList.contains('group-collapsed');
  }});

  // Sincroniza a seta nas duas versões do resumo (card e linha da tabela)
  document.querySelectorAll('[data-group-toggle="' + groupId + '"] .chevron').forEach(function(ch) {{
    ch.textContent = isNowCollapsed ? '▸' : '▾';
  }});
}}

function formatNumberBR(n) {{
  return Math.round(n).toString().replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, '.');
}}

function applyFilters() {{
  updatePublisherFilterOptions();

  var selectedMonth = document.getElementById('month-filter').value;
  var selectedPublisher = document.getElementById('publisher-filter').value;
  var selectedStatus = document.getElementById('status-filter').value;
  var selectedEnding = document.getElementById('ending-filter').value;
  var selectedUnder = document.getElementById('under-filter').value;

  var publishers = document.querySelectorAll('.publisher');
  var visibleCount = 0;

  publishers.forEach(function(pub) {{
    var pubMatches = selectedPublisher === 'all' || pub.getAttribute('data-publisher') === selectedPublisher;
    var entries = pub.querySelectorAll('.entry');
    var visibleInPub = 0;

    entries.forEach(function(el) {{
      var months = (el.getAttribute('data-months') || '').split(',');
      var monthMatch = selectedMonth === 'all' || months.indexOf(selectedMonth) !== -1;
      var statusMatch = selectedStatus === 'all' || el.getAttribute('data-status') === selectedStatus;
      var endingMatch = selectedEnding === 'all' || el.getAttribute('data-ending') === '1';
      var underMatch = selectedUnder === 'all' || el.getAttribute('data-under') === '1';
      var match = pubMatches && monthMatch && statusMatch && endingMatch && underMatch;
      el.style.display = match ? '' : 'none';
    }});

    // conta e soma só pelos cards (1 por campanha), pra não contar em dobro com as linhas da tabela
    var totalImpr = 0, totalClicks = 0, totalViewable = 0, totalVaImpr = 0;
    pub.querySelectorAll('.view-card-item').forEach(function(el) {{
      if (el.style.display !== 'none') {{
        visibleInPub++;
        visibleCount++;
        totalImpr += parseInt(el.getAttribute('data-impr') || '0', 10);
        totalClicks += parseInt(el.getAttribute('data-clicks-raw') || '0', 10);
        totalViewable += parseInt(el.getAttribute('data-viewable') || '0', 10);
        totalVaImpr += parseInt(el.getAttribute('data-va-impr') || '0', 10);
      }}
    }});

    pub.style.display = (pubMatches && visibleInPub > 0) ? '' : 'none';
    var badge = pub.querySelector('.badge');
    if (badge) {{ badge.textContent = visibleInPub + ' campanha(s)'; }}

    var ctr = totalImpr > 0 ? (totalClicks / totalImpr * 100).toFixed(2) : '0.00';
    var va = totalVaImpr > 0 ? (totalViewable / totalVaImpr * 100).toFixed(2) + '%' : '—';
    var summaryEl = pub.querySelector('.publisher-summary');
    if (summaryEl) {{
      summaryEl.innerHTML =
        '<div class="stat-box"><span class="stat-label">Impressões</span><span class="stat-value">' + formatNumberBR(totalImpr) + '</span></div>' +
        '<div class="stat-box"><span class="stat-label">Cliques</span><span class="stat-value">' + formatNumberBR(totalClicks) + '</span></div>' +
        '<div class="stat-box"><span class="stat-label">CTR</span><span class="stat-value">' + ctr + '%</span></div>' +
        '<div class="stat-box"><span class="stat-label">VA</span><span class="stat-value">' + va + '</span></div>';
    }}
  }});

  document.getElementById('summary-pill').textContent = visibleCount + ' campanha(s) exibida(s)';
}}
document.addEventListener('DOMContentLoaded', applyFilters);
</script>
</body>
</html>
"""

PUBLISHER_SECTION_TEMPLATE = """
<div class="publisher" data-publisher="{publisher_name}">
  <div class="publisher-title" onclick="togglePublisher(this)">
    <span class="chevron">▸</span>
    <div class="publisher-name">
      <h2>{publisher_name}</h2>
      <span class="badge">{count} campanha(s)</span>
    </div>
    <span class="publisher-summary" data-role="summary">{summary_text}</span>
  </div>
  <div class="publisher-body collapsed">
    <div class="grid">
      {cards}
    </div>
    <div class="table-wrap">
      <table class="campaigns-table">
        <colgroup>
          <col style="width:14%"><col style="width:6%"><col style="width:8%"><col style="width:11%">
          <col style="width:8%"><col style="width:6%"><col style="width:5%"><col style="width:5%">
          <col style="width:7%"><col style="width:9%"><col style="width:9%"><col style="width:12%">
        </colgroup>
        <thead>
          <tr>
            <th>Campanha</th><th>Tipo</th><th>Status</th><th>Entrega</th><th>Impressões</th>
            <th>Cliques</th><th>CTR</th><th>VA</th><th>Meta</th><th>Início</th><th>Fim</th><th>Alertas</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
    </div>
  </div>
</div>
"""

CARD_TEMPLATE = """
<div class="card entry {card_extra_class}" data-months="{month_keys}" data-status="{status_raw}" data-under="{under_flag}" data-ending="{ending_flag}"
     data-impr="{impressions_raw}" data-clicks-raw="{clicks_raw}" data-viewable="{viewable_raw}" data-va-impr="{va_impr_raw}"{group_attr}>
  <h3>{group_chevron}{name}{group_badge}</h3>
  <div class="type">{type}</div> <span class="status-pill {status_class}">{status_label}</span>
  {delivery_bar}
  <div class="metrics">
    <div>Impressões: <b>{impressions}</b></div>
    <div>Cliques: <b>{clicks}</b></div>
    <div>CTR: <b>{ctr}%</b></div>
    <div>VA: <b>{va_rate}</b></div>
    <div>Meta: <b>{goal}</b></div>
    <div>Início: <b>{start_date}</b></div>
    <div>Fim: <b>{end_date}</b></div>
  </div>
  <div class="flags">{flags}{info_icon}</div>
</div>
"""

TABLE_ROW_TEMPLATE = """
<tr class="entry {row_extra_class}" data-months="{month_keys}" data-status="{status_raw}" data-under="{under_flag}" data-ending="{ending_flag}"{group_attr}>
  <td>{group_chevron}{name}{group_badge}</td>
  <td><span class="type-pill">{type}</span></td>
  <td><span class="status-pill {status_class}">{status_label}</span></td>
  <td>{delivery_cell}</td>
  <td>{impressions}</td>
  <td>{clicks}</td>
  <td>{ctr}%</td>
  <td>{va_rate}</td>
  <td>{goal}</td>
  <td>{start_date}</td>
  <td>{end_date}</td>
  <td><div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;white-space:normal;">{flags}<span style="flex-shrink:0;">{info_icon}</span></div></td>
</tr>
"""


def format_number(value):
    """Formata número com separador de milhar no padrão BR (ex: 1.234.567)."""
    if value is None:
        return "—"
    return f"{value:,}".replace(",", ".")


def render_campaign_entry(c, is_group_summary=False, group_child_of=None, item_count=None):
    """Monta o card e a linha da tabela pra UMA campanha (ou pra um resumo
    agregado de Order, quando is_group_summary=True). Devolve (card_html, row_html)."""
    actual = c["actual_pct"]
    bar_class = "bad" if c["underdelivery"] else "ok"
    if actual is not None:
        actual_display = round(actual, 1)
        expected_txt = (
            f' / esperado <b>{round(c["expected_pct"], 1)}%</b>'
            if c["expected_pct"] is not None else ""
        )
        delivery_bar = (
            f'<div class="bar-track"><div class="bar-fill {bar_class}" '
            f'style="width:{min(actual, 100)}%"></div></div>'
            f'<div class="delivery-caption">Entrega: <b>{actual_display}%</b>{expected_txt}</div>'
        )
        delivery_cell = f'<span class="delivery-pct {bar_class}">{actual_display}%</span>'
        if c["expected_pct"] is not None:
            delivery_cell += f' <span style="color:var(--muted)">/ {round(c["expected_pct"], 1)}%</span>'
    else:
        delivery_bar = '<div class="delivery-caption">Sem dado de entrega</div>'
        delivery_cell = "—"

    goal = (
        f'{format_number(c["goal_units"])} {c["goal_unit_type"]}'
        if c["goal_units"]
        else "Sem meta definida"
    )
    va = f'{c["va_rate"]}%' if c["va_rate"] is not None else "—"

    has_deadline_flag = c["ending_today"] or c["ending_soon"]
    critical = c["underdelivery"] and has_deadline_flag
    flag_extra_class = " flag-critical" if critical else ""

    flags = ""
    if c["underdelivery"]:
        flags += f'<span class="flag under{flag_extra_class}">Under</span>'
    if c["ending_today"]:
        flags += f'<span class="flag ending-today{flag_extra_class}">Termina hoje</span>'
    elif c["ending_soon"]:
        flags += f'<span class="flag ending{flag_extra_class}">Termina em breve</span>'

    flags_cell = flags or "—"

    if c.get("notes"):
        notes_escaped = html_module.escape(c["notes"], quote=True)
        info_icon = f'<span class="info-icon" title="{notes_escaped}">?</span>'
    else:
        info_icon = ""

    month_keys_attr = ",".join(c["month_keys"])

    impressions_fmt = format_number(c["impressions"])
    clicks_fmt = format_number(c["clicks"])
    status_label, status_class = status_label_and_class(c["status"])
    status_raw = c["status"] or ""
    under_flag = "1" if c["underdelivery"] else "0"
    ending_flag = "1" if has_deadline_flag else "0"

    name_escaped = html_module.escape(c["name"] or "", quote=True)
    if c.get("gam_url"):
        name_html = (
            f'<a href="{html_module.escape(c["gam_url"], quote=True)}" target="_blank" rel="noopener" '
            f'style="color:inherit;text-decoration:none;border-bottom:1px dotted var(--accent);">'
            f'{name_escaped}</a>'
        )
    else:
        name_html = name_escaped

    if is_group_summary:
        # Resumo de Order com múltiplos itens de linha: não conta nos totais
        # (isso já é feito pelos itens filhos), tem uma seta pra expandir e
        # um selinho avisando quantos itens tem dentro.
        card_extra_class = "group-summary"
        row_extra_class = "group-summary"
        group_attr = f' data-group-toggle="{c["id"]}"'
        group_chevron = '<span class="chevron" onclick="toggleOrderGroup(this)">▸</span> '
        group_badge = f' <span class="badge-mini">{item_count} itens</span>'
        impressions_raw = clicks_raw = viewable_raw = va_impr_raw = 0
    elif group_child_of is not None:
        card_extra_class = "view-card-item group-child group-collapsed"
        row_extra_class = "view-table-item group-child group-collapsed"
        group_attr = f' data-group="{group_child_of}"'
        group_chevron = ""
        group_badge = ""
        impressions_raw, clicks_raw = c["impressions"], c["clicks"]
        viewable_raw = c["viewable_impressions"] or 0
        va_impr_raw = c["impressions_for_va"] if c["viewable_impressions"] is not None else 0
    else:
        card_extra_class = "view-card-item"
        row_extra_class = "view-table-item"
        group_attr = ""
        group_chevron = ""
        group_badge = ""
        impressions_raw, clicks_raw = c["impressions"], c["clicks"]
        viewable_raw = c["viewable_impressions"] or 0
        va_impr_raw = c["impressions_for_va"] if c["viewable_impressions"] is not None else 0

    card_html = CARD_TEMPLATE.format(
        name=name_html, type=c["type"], delivery_bar=delivery_bar,
        impressions=impressions_fmt, clicks=clicks_fmt, ctr=c["ctr"],
        va_rate=va, goal=goal, start_date=c["start_date"] or "—", end_date=c["end_date"] or "—", flags=flags,
        month_keys=month_keys_attr, status_label=status_label, status_class=status_class,
        status_raw=status_raw, under_flag=under_flag, ending_flag=ending_flag,
        impressions_raw=impressions_raw, clicks_raw=clicks_raw,
        viewable_raw=viewable_raw, va_impr_raw=va_impr_raw, info_icon=info_icon,
        card_extra_class=card_extra_class, group_attr=group_attr,
        group_chevron=group_chevron, group_badge=group_badge,
    )

    row_html = TABLE_ROW_TEMPLATE.format(
        name=name_html, type=c["type"], delivery_cell=delivery_cell,
        impressions=impressions_fmt, clicks=clicks_fmt, ctr=c["ctr"],
        va_rate=va, goal=goal, start_date=c["start_date"] or "—", end_date=c["end_date"] or "—", flags=flags_cell,
        info_icon=info_icon,
        month_keys=month_keys_attr, status_label=status_label, status_class=status_class,
        status_raw=status_raw, under_flag=under_flag, ending_flag=ending_flag,
        row_extra_class=row_extra_class, group_attr=group_attr,
        group_chevron=group_chevron, group_badge=group_badge,
    )
    return card_html, row_html


def _parse_date_str(value, fmt):
    try:
        return datetime.strptime(value, fmt)
    except (ValueError, TypeError):
        return None


def group_by_temporal_overlap(campaigns):
    """Recebe campanhas do MESMO Order e as separa em sub-grupos de itens que
    rodam no mesmo período (datas de início/fim se sobrepõem). Um item que
    não se sobrepõe com nenhum outro do Order fica sozinho (item único) —
    mesmo que o Order tenha outros itens de linha, só que rodando em outra
    época."""
    items = []
    for c in campaigns:
        start_dt = _parse_date_str(c.get("start_date"), "%d/%m/%Y")
        end_dt = _parse_date_str(c.get("end_date"), "%d/%m/%Y %H:%M")
        items.append((c, start_dt, end_dt))

    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for i in range(n):
        for j in range(i + 1, n):
            _, s1, e1 = items[i]
            _, s2, e2 = items[j]
            if s1 and e1 and s2 and e2 and s1 <= e2 and s2 <= e1:
                union(i, j)

    groups_map = {}
    for i in range(n):
        root = find(i)
        groups_map.setdefault(root, []).append(items[i][0])

    return list(groups_map.values())


def aggregate_order_group(group):
    """Combina várias campanhas (itens de linha) do MESMO Order num único
    resumo agregado: soma impressões/cliques/meta, recalcula CTR/VA/entrega
    com base nos totais (não faz média das porcentagens individuais)."""
    total_impressions = sum(c["impressions"] for c in group)
    total_clicks = sum(c["clicks"] for c in group)
    ctr = round((total_clicks / total_impressions) * 100, 2) if total_impressions else 0.0

    total_viewable = sum(c["viewable_impressions"] or 0 for c in group if c["viewable_impressions"] is not None)
    total_va_impr = sum(c["impressions_for_va"] for c in group if c["viewable_impressions"] is not None)
    va_rate = round((total_viewable / total_va_impr) * 100, 2) if total_va_impr else None

    goal_items = [c for c in group if c.get("goal_units")]
    goal_units = sum(c["goal_units"] for c in goal_items) if goal_items else None
    goal_unit_type = goal_items[0]["goal_unit_type"] if goal_items else None

    if goal_units:
        actual_pct = round((total_impressions / goal_units) * 100, 1)
        expected_num = sum(c["expected_pct"] * c["goal_units"] for c in goal_items if c.get("expected_pct") is not None)
        expected_den = sum(c["goal_units"] for c in goal_items if c.get("expected_pct") is not None)
        expected_pct = round(expected_num / expected_den, 1) if expected_den else None
    else:
        actual_pct = None
        expected_pct = None

    underdelivery = (
        expected_pct is not None and actual_pct is not None
        and (expected_pct - actual_pct) >= UNDERDELIVERY_THRESHOLD_PCT
    )
    ending_today = any(c["ending_today"] for c in group)
    ending_soon = any(c["ending_soon"] for c in group) and not ending_today

    # Propaga a anomalia mais grave entre os itens do grupo (senão ficaria
    # escondida dentro de um grupo recolhido). Prioridade: zerada > queda > pico.
    anomaly_priority = {"zeroed": 0, "drop": 1, "spike": 2}
    child_anomalies = [c["anomaly"] for c in group if c.get("anomaly")]
    group_anomaly = (
        min(child_anomalies, key=lambda a: anomaly_priority.get(a["type"], 99))
        if child_anomalies else None
    )

    status_priority = ["DELIVERING", "READY", "PAUSED", "COMPLETED"]
    statuses_present = {c["status"] for c in group if c.get("status")}
    status = next((s for s in status_priority if s in statuses_present), next(iter(statuses_present), None))

    def _parse(value, fmt):
        try:
            return datetime.strptime(value, fmt)
        except (ValueError, TypeError):
            return None

    starts = [(p, d) for d in (c.get("start_date") for c in group) if d for p in [_parse(d, "%d/%m/%Y")] if p]
    ends = [(p, d) for d in (c.get("end_date") for c in group) if d for p in [_parse(d, "%d/%m/%Y %H:%M")] if p]
    start_date = min(starts)[1] if starts else None
    end_date = max(ends)[1] if ends else None

    month_keys = sorted({m for c in group for m in (c.get("month_keys") or [])})
    types_present = {c.get("type") for c in group if c.get("type")}
    type_label = next(iter(types_present)) if len(types_present) == 1 else "Múltiplos"

    order_id = group[0].get("order_id")
    network_code = group[0].get("network_code")
    gam_url = (
        f"https://admanager.google.com/{network_code}#delivery/order/detail/orderId={order_id}"
        if network_code and order_id else None
    )

    return {
        "id": f"order-{order_id}",
        "order_id": order_id,
        "network_code": network_code,
        "name": group[0]["name"],
        "type": type_label,
        "status": status,
        "impressions": total_impressions,
        "clicks": total_clicks,
        "ctr": ctr,
        "va_rate": va_rate,
        "viewable_impressions": total_viewable if total_va_impr else None,
        "impressions_for_va": total_va_impr,
        "goal_units": goal_units,
        "goal_unit_type": goal_unit_type,
        "actual_pct": actual_pct,
        "expected_pct": expected_pct,
        "underdelivery": underdelivery,
        "ending_today": ending_today,
        "ending_soon": ending_soon,
        "start_date": start_date,
        "end_date": end_date,
        "month_keys": month_keys,
        "notes": None,
        "gam_url": gam_url,
        "anomaly": group_anomaly,
    }


def render_dashboard(publishers):
    sections_html = []
    all_month_keys = set()
    all_statuses_seen = set()

    for publisher_name, campaigns in sorted(publishers.items()):
        cards_html = []
        rows_html = []

        for c in campaigns:
            all_month_keys.update(c["month_keys"])
            all_statuses_seen.add(c["status"])

        # Agrupa por Order (Pedido): quando o mesmo Order tem mais de um item
        # de linha ativo, mostra um resumo (com a meta somada e recalculada)
        # e deixa expandir pra ver cada item de linha em detalhe.
        order_groups = {}
        order_seen_order = []
        for c in campaigns:
            key = c.get("order_id") or f"single-{c['id']}"
            if key not in order_groups:
                order_groups[key] = []
                order_seen_order.append(key)
            order_groups[key].append(c)

        for key in order_seen_order:
            for sub_index, group in enumerate(group_by_temporal_overlap(order_groups[key])):
                if len(group) == 1:
                    card_html, row_html = render_campaign_entry(group[0])
                    cards_html.append(card_html)
                    rows_html.append(row_html)
                else:
                    summary = aggregate_order_group(group)
                    summary["id"] = f"order-{key}-{sub_index}"
                    summary_card, summary_row = render_campaign_entry(
                        summary, is_group_summary=True, item_count=len(group)
                    )
                    cards_html.append(summary_card)
                    rows_html.append(summary_row)
                    for child in group:
                        child_card, child_row = render_campaign_entry(child, group_child_of=summary["id"])
                        cards_html.append(child_card)
                        rows_html.append(child_row)

        # Totais iniciais do publisher (antes de qualquer filtro no navegador),
        # recalculados dinamicamente via JS quando os filtros mudam.
        total_impressions = sum(c["impressions"] for c in campaigns)
        total_clicks = sum(c["clicks"] for c in campaigns)
        total_viewable = sum(c["viewable_impressions"] or 0 for c in campaigns if c["viewable_impressions"] is not None)
        total_va_impr = sum(
            c["impressions_for_va"] for c in campaigns if c["viewable_impressions"] is not None
        )
        total_ctr = round((total_clicks / total_impressions) * 100, 2) if total_impressions else 0.0
        total_va = round((total_viewable / total_va_impr) * 100, 2) if total_va_impr else None
        summary_text = (
            f'<div class="stat-box"><span class="stat-label">Impressões</span>'
            f'<span class="stat-value">{format_number(total_impressions)}</span></div>'
            f'<div class="stat-box"><span class="stat-label">Cliques</span>'
            f'<span class="stat-value">{format_number(total_clicks)}</span></div>'
            f'<div class="stat-box"><span class="stat-label">CTR</span>'
            f'<span class="stat-value">{total_ctr}%</span></div>'
            f'<div class="stat-box"><span class="stat-label">VA</span>'
            f'<span class="stat-value">{f"{total_va}%" if total_va is not None else "—"}</span></div>'
        )

        sections_html.append(PUBLISHER_SECTION_TEMPLATE.format(
            publisher_name=html_module.escape(publisher_name or "", quote=True), count=len(campaigns),
            cards="".join(cards_html), rows="".join(rows_html), summary_text=summary_text,
        ))

    # Ordena meses cronologicamente, deixando "sem-data" por último,
    # e remove meses futuros (além do mês atual) das opções do filtro
    current_month_key = datetime.now().strftime("%Y-%m")

    def month_sort_key(key):
        return (1, "") if key == "sem-data" else (0, key)

    visible_month_keys = [
        key for key in all_month_keys
        if key == "sem-data" or key <= current_month_key
    ]

    month_options_html = "".join(
        f'<option value="{key}"{" selected" if key == current_month_key else ""}>{month_label_for_key(key)}</option>'
        for key in sorted(visible_month_keys, key=month_sort_key)
    )

    publisher_options_html = "".join(
        f'<option value="{html_module.escape(name, quote=True)}">{html_module.escape(name)}</option>'
        for name in sorted(publishers.keys())
    )

    # Ordena status numa ordem lógica de ciclo de vida, ignorando os que não apareceram
    status_order = ["DELIVERING", "READY", "PAUSED", "COMPLETED"]
    ordered_statuses = [s for s in status_order if s in all_statuses_seen]
    ordered_statuses += sorted(s for s in all_statuses_seen if s and s not in status_order)

    status_options_html = "".join(
        f'<option value="{status}"{" selected" if status == "DELIVERING" else ""}>{status_label_and_class(status)[0]}</option>'
        for status in ordered_statuses
    )

    if not sections_html:
        sections_html = ['<div class="empty-state">Nenhuma campanha encontrada com os filtros atuais.</div>']

    html = DASHBOARD_TEMPLATE.format(
        generated_at=datetime.now().strftime("%d/%m/%Y %H:%M"),
        publisher_sections="".join(sections_html),
        month_options=month_options_html,
        publisher_options=publisher_options_html,
        status_options=status_options_html,
        logo_uri=ADZEP_LOGO_DATA_URI,
        favicon_uri=ADZEP_FAVICON_DATA_URI,
    )
    DASHBOARD_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard gerado em: {DASHBOARD_PATH}")


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def merge_publishers(target, source):
    """Mescla o dict de publishers de uma conta no dict combinado final."""
    for publisher_name, campaigns in source.items():
        target.setdefault(publisher_name, []).extend(campaigns)


def main():
    if "--test-email" in sys.argv:
        send_test_email()
        return

    if "--render-only" in sys.argv:
        if not DATA_CACHE_PATH.exists():
            print(
                "Não encontrei cache de dados em output/data_cache.json. "
                "Rode 'py gam_monitor.py' (sem --render-only) pelo menos uma vez antes, "
                "pra gerar o cache com dados reais do GAM."
            )
            return
        print("Modo --render-only: usando dados salvos em cache, sem consultar a API do GAM.")
        all_publishers = json.loads(DATA_CACHE_PATH.read_text(encoding="utf-8"))
        render_dashboard(all_publishers)
        upload_dashboard_to_gcs()
        return

    clients = get_clients()
    all_publishers = {}

    for account_name, client in clients:
        print(f"\n=== Conta: {account_name} ===")
        publishers = build_dataset(client)
        merge_publishers(all_publishers, publishers)

    DATA_CACHE_PATH.write_text(json.dumps(all_publishers), encoding="utf-8")

    export_completed_campaigns_to_sheets(all_publishers)

    notified_state = load_notified_state()
    alerts = collect_alerts(all_publishers, notified_state)

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    already_sent_today = notified_state.get("_last_email_sent_date") == today_str

    if alerts and not already_sent_today:
        sent_ok = send_email_alerts(alerts)
        if sent_ok:
            notified_state["_last_email_sent_date"] = today_str
            save_notified_state(notified_state)
    elif alerts and already_sent_today:
        print(
            f"[debug] {len(alerts)} alerta(s) novo(s) encontrados, mas o e-mail de hoje já foi "
            "enviado antes — só um e-mail por dia, próximo envio será amanhã."
        )
        log_email_event(f"PULADO (já enviado hoje) — {len(alerts)} alerta(s) novo(s) detectado(s)")

    render_dashboard(all_publishers)
    upload_dashboard_to_gcs()


if __name__ == "__main__":
    try:
        main()
    except errors.GoogleAdsError as e:
        print("Erro na API do Google Ad Manager:", e)
    except FileNotFoundError as e:
        print(e)
