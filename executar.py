#!/usr/bin/env python3
"""
ZepOne — coleta automática (GitHub Actions).

Roda a coleta do Acompanhamento/Metas (pasta coleta/) e o monitor de Campanhas
(pasta campanhas/) e guarda os resultados num Drive compartilhado do Google:

  entrada/   o que a coleta lê: sites.xlsx (obrigatório), config.json e
             campanhas.json (opcionais), anotacoes.json (opcional)
  saida/     o que os painéis do ZepOne no Claude leem: manifesto.json (aponta
             para todos os arquivos), zepone/zepone_*.json, metas_export.json,
             campanhas/gamone_*.json e log.txt
  estado/    memória do monitor de campanhas entre uma rodada e outra

Credenciais e o Drive vêm dos Secrets do repositório (nada sensível fica no código):
  GAM_SERVICE_ACCOUNT   conteúdo do service_account.json (GAM; também grava no Drive)
  GA_SERVICE_ACCOUNT    conteúdo do service_account_ga.json (opcional: usa o do GAM)
  MEMBRANA_ACCESS_KEY   chave da Membrana (opcional)
  SEEDTAG_API_KEY       chave da Seedtag (opcional)
  SMTP_USER, SMTP_PASSWORD, EMAIL_TO, EMAIL_FROM   e-mail de alertas (opcional)
  ZEPONE_DRIVE_ID       id do Drive compartilhado

Uso: python executar.py [--so acomp|campanhas] [--completo]
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import traceback
import zipfile
from pathlib import Path
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

RAIZ = Path(__file__).resolve().parent
TRAB = RAIZ / "trabalho"
LOG = TRAB / "log.txt"
FUSO = ZoneInfo("America/Sao_Paulo")
PASTA = "application/vnd.google-apps.folder"
CONTAS_PADRAO = [
    {"name": "AdZep", "network_code": "22877339784"},
    {"name": "Tribuna do Norte", "network_code": "1007775"},
    {"name": "Diário do Rio", "network_code": "1017220"},
    {"name": "Revista Oeste", "network_code": "22388345455"},
]


def agora():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def hoje_br():
    return dt.datetime.now(FUSO).strftime("%d/%m/%Y")


def anota(txt):
    linha = f"[{dt.datetime.now(FUSO).strftime('%H:%M:%S')}] {txt}"
    print(linha, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(linha + "\n")


def segredo(nome, obrigatorio=False):
    v = os.environ.get(nome, "").strip()
    if obrigatorio and not v:
        raise SystemExit(f"Falta cadastrar o segredo {nome} no GitHub (Settings > Secrets and variables > Actions).")
    return v


class Drive:
    """Arquivos do Drive compartilhado, endereçados por pasta + nome (sobrescreve o que já existe)."""

    def __init__(self, info, drive_id):
        cred = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/drive"])
        self.s = build("drive", "v3", credentials=cred, cache_discovery=False)
        self.drive_id = drive_id

    def _lista(self, q):
        r = self.s.files().list(q=q, corpora="drive", driveId=self.drive_id, includeItemsFromAllDrives=True,
                                supportsAllDrives=True, fields="files(id,name,modifiedTime)", pageSize=1000).execute()
        return r.get("files", [])

    @staticmethod
    def _q(nome):
        return nome.replace("\\", "\\\\").replace("'", "\\'")

    def pasta(self, nome, pai=None):
        pai = pai or self.drive_id
        f = self._lista(f"name = '{self._q(nome)}' and mimeType = '{PASTA}' and '{pai}' in parents and trashed = false")
        if f:
            return f[0]["id"]
        return self.s.files().create(body={"name": nome, "mimeType": PASTA, "parents": [pai]},
                                     supportsAllDrives=True, fields="id").execute()["id"]

    def acha(self, pasta, nome):
        f = self._lista(f"name = '{self._q(nome)}' and '{pasta}' in parents and trashed = false")
        return f[0] if f else None

    def baixa(self, pasta, nome, destino):
        f = self.acha(pasta, nome)
        if not f:
            return False
        req = self.s.files().get_media(fileId=f["id"], supportsAllDrives=True)
        with open(destino, "wb") as fh:
            d, feito = MediaIoBaseDownload(fh, req), False
            while not feito:
                _, feito = d.next_chunk()
        return True

    def sobe(self, pasta, caminho, nome=None, tipo="application/json"):
        nome = nome or Path(caminho).name
        midia = MediaFileUpload(str(caminho), mimetype=tipo, resumable=os.path.getsize(caminho) > 5 * 1024 * 1024)
        f = self.acha(pasta, nome)
        if f:
            return self.s.files().update(fileId=f["id"], media_body=midia, supportsAllDrives=True, fields="id").execute()["id"]
        return self.s.files().create(body={"name": nome, "parents": [pasta]}, media_body=midia,
                                     supportsAllDrives=True, fields="id").execute()["id"]

    def apaga(self, file_id):
        try:
            self.s.files().delete(fileId=file_id, supportsAllDrives=True).execute()
        except Exception:
            pass


def roda(pasta, args, minutos, env_extra=None):
    env = dict(os.environ, TZ="America/Sao_Paulo", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    for k in list(env):   # os scripts não precisam (nem devem ver) os segredos brutos
        if k in ("GAM_SERVICE_ACCOUNT", "GA_SERVICE_ACCOUNT", "MEMBRANA_ACCESS_KEY", "SEEDTAG_API_KEY", "SMTP_PASSWORD"):
            env.pop(k)
    env.update(env_extra or {})
    with open(LOG, "a", encoding="utf-8") as saida:
        try:
            r = subprocess.run([sys.executable, "-u"] + args, cwd=pasta, stdout=saida, stderr=subprocess.STDOUT,
                               env=env, timeout=minutos * 60)
            return r.returncode == 0
        except subprocess.TimeoutExpired:
            anota(f"[ERRO] {args[0]} passou de {minutos} minutos e foi encerrado.")
            return False


class Coleta:
    def __init__(self):
        TRAB.mkdir(exist_ok=True)
        LOG.write_text(f"===== inicio {dt.datetime.now(FUSO).strftime('%d/%m/%Y %H:%M')} =====\n", encoding="utf-8")
        self.sa = json.loads(segredo("GAM_SERVICE_ACCOUNT", True))
        self.drive = Drive(self.sa, segredo("ZEPONE_DRIVE_ID", True))
        d = self.drive
        self.p = {k: d.pasta(k) for k in ("entrada", "saida", "estado")}
        self.p["zepone"] = d.pasta("zepone", self.p["saida"])
        self.p["campanhas"] = d.pasta("campanhas", self.p["saida"])
        self.man = {}
        m = TRAB / "manifesto_ant.json"
        if d.baixa(self.p["saida"], "manifesto.json", m):
            try:
                self.man = json.loads(m.read_text(encoding="utf-8"))
            except Exception:
                self.man = {}

    def publica_manifesto(self, **campos):
        self.man.update(campos)
        self.man["atualizadoEm"] = agora()
        arq = TRAB / "manifesto.json"
        arq.write_text(json.dumps(self.man, ensure_ascii=False, indent=1), encoding="utf-8")
        self.drive.sobe(self.p["saida"], arq)

    def credenciais(self, pasta):
        (pasta / "service_account.json").write_text(json.dumps(self.sa), encoding="utf-8")
        ga = segredo("GA_SERVICE_ACCOUNT")
        (pasta / "service_account_ga.json").write_text(ga or json.dumps(self.sa), encoding="utf-8")
        for seg, nome in (("MEMBRANA_ACCESS_KEY", "membrana_access_key.txt"), ("SEEDTAG_API_KEY", "seedtag_api_key.txt")):
            if segredo(seg):
                (pasta / nome).write_text(segredo(seg), encoding="utf-8")

    # ---------- Acompanhamento e Metas ----------
    def acompanhamento(self):
        d, pasta = self.drive, TRAB / "coleta"
        shutil.rmtree(pasta, ignore_errors=True)
        shutil.copytree(RAIZ / "coleta", pasta)
        shutil.copy(pasta / "padrao_config.json", pasta / "config.json")
        d.baixa(self.p["entrada"], "config.json", pasta / "config.json")
        d.baixa(self.p["entrada"], "anotacoes.json", pasta / "anotacoes.json")
        if not d.baixa(self.p["entrada"], "sites.xlsx", pasta / "sites.xlsx"):
            raise RuntimeError("Falta a lista de sites: envie o sites.xlsx para a pasta 'entrada' do Drive compartilhado.")
        self.credenciais(pasta)
        inicio = agora()
        anota("[1 de 4] Buscando os dados nas APIs...")
        if not roda(pasta, ["gerar_dash_v5.py", "sites.xlsx", "painel.html"], 75):
            raise RuntimeError("A busca dos dados nas APIs falhou (veja o log.txt).")
        anota("[2 de 4] Gerando os arquivos do Acompanhamento...")
        if not roda(pasta, ["exportar_zepone.py"], 10):
            raise RuntimeError("Não foi possível gerar os arquivos do painel.")
        dados = pasta / "zepone_dados"
        pronto = json.loads((dados / "_pronto.json").read_text(encoding="utf-8"))
        geral, sites = None, {}
        for arq in sorted(dados.glob("zepone_*.json")):
            fid = d.sobe(self.p["zepone"], arq)
            k = arq.stem[len("zepone_"):]
            if k == "_geral":
                geral = fid
            else:
                sites[k] = fid
        if not geral or not sites:
            raise RuntimeError("A coleta não gerou os arquivos do painel.")
        anota(f"Acompanhamento: {len(sites)} sites enviados ao Drive.")
        self.publica_manifesto(acompanhamento={"geral": geral, "sites": sites, "gerado": pronto.get("gerado_em"),
                                               "coletaInicio": inicio, "coletaFim": agora()})
        # Metas: uma vez por dia basta (os números vão até o dia anterior)
        if (self.man.get("metas") or {}).get("dia") == hoje_br():
            anota("Metas: já atualizadas hoje, etapa pulada.")
            return
        anota("[3 de 4] Buscando os lances por faixa de preço...")
        if not roda(pasta, ["coletar_faixas.py"], 20):
            anota("[AVISO] Sem os lances por faixa de preço desta vez.")
        anota("[4 de 4] Gerando os lançamentos da área Metas...")
        if roda(pasta, ["exportar_metas.py", "painel.html"], 10) and (pasta / "metas_export.json").exists():
            j = json.loads((pasta / "metas_export.json").read_text(encoding="utf-8"))
            fid = d.sobe(self.p["saida"], pasta / "metas_export.json")
            self.publica_manifesto(metas={"id": fid, "gerado": j.get("gerado"), "dia": hoje_br(), "n": len(j.get("docs") or [])})
            anota(f"Metas: {len(j.get('docs') or [])} lançamentos ({j.get('gerado')}).")
        else:
            anota("[ERRO] Não foi possível gerar os lançamentos da área Metas.")

    # ---------- Campanhas ----------
    def campanhas(self, completo):
        d, pasta = self.drive, TRAB / "campanhas"
        shutil.rmtree(pasta, ignore_errors=True)
        shutil.copytree(RAIZ / "campanhas", pasta)
        (pasta / "output").mkdir(exist_ok=True)
        z = TRAB / "campanhas_output.zip"
        if d.baixa(self.p["estado"], "campanhas_output.zip", z):
            with zipfile.ZipFile(z) as zf:
                zf.extractall(pasta / "output")
        cfg = {}
        if d.baixa(self.p["entrada"], "campanhas.json", TRAB / "campanhas.json"):
            cfg = json.loads((TRAB / "campanhas.json").read_text(encoding="utf-8"))
        self.credenciais(pasta)
        (pasta / "accounts.yaml").write_text(json.dumps({
            "application_name": "ZepOne Campanhas",
            "path_to_private_key_file": str(pasta / "service_account.json"),
            "accounts": cfg.get("contas") or CONTAS_PADRAO}, ensure_ascii=False), encoding="utf-8")
        env = {}
        if segredo("SMTP_USER") and segredo("SMTP_PASSWORD") and segredo("EMAIL_TO"):
            env = {"SMTP_HOST": segredo("SMTP_HOST") or "smtp.gmail.com", "SMTP_PORT": segredo("SMTP_PORT") or "587",
                   "SMTP_USER": segredo("SMTP_USER"), "SMTP_PASSWORD": segredo("SMTP_PASSWORD"),
                   "EMAIL_FROM": segredo("EMAIL_FROM") or segredo("SMTP_USER"), "EMAIL_TO": segredo("EMAIL_TO")}
        # inventário, detalhe e segmentos: na primeira rodada do dia, ou quando pedido
        ult = (self.man.get("campanhas") or {}).get("completoDia")
        completo = completo or ult != hoje_br()
        anota(f"Campanhas: rodada {'completa' if completo else 'rápida'}...")
        ok = roda(pasta, ["rodar.py"] + (["--completo"] if completo else []), 40, env)
        saida = pasta / "output" / "gamone_dados"
        arquivos = {}
        for arq in sorted(saida.glob("gamone_*.json")):
            arquivos[arq.stem[len("gamone_"):]] = d.sobe(self.p["campanhas"], arq)
        # memória do monitor (alertas já enviados, caches) para a próxima rodada
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in (pasta / "output").rglob("*"):
                if f.is_file() and f.suffix in (".json", ".txt"):
                    zf.write(f, f.relative_to(pasta / "output"))
        d.sobe(self.p["estado"], z, tipo="application/zip")
        if not ok:
            raise RuntimeError("O monitor de campanhas falhou (veja o log.txt).")
        geral = json.loads((saida / "gamone__geral.json").read_text(encoding="utf-8"))
        ant = self.man.get("campanhas") or {}
        self.publica_manifesto(campanhas={"arquivos": arquivos, "gerado": geral.get("gerado_em"),
                                          "completoDia": hoje_br() if completo else ant.get("completoDia"),
                                          "n": geral.get("n_campanhas")})
        anota(f"Campanhas: {geral.get('n_campanhas')} campanhas ({geral.get('gerado_em')}).")


def main():
    so = "tudo"
    if "--so" in sys.argv:
        so = sys.argv[sys.argv.index("--so") + 1]
    completo = "--completo" in sys.argv
    c = Coleta()
    c.publica_manifesto(estado="coletando", inicio=agora(), msg="Coleta iniciada às " + dt.datetime.now(FUSO).strftime("%H:%M") + ".")
    erros = []
    for nome, func in (("acomp", c.acompanhamento), ("campanhas", lambda: c.campanhas(completo))):
        if so not in ("tudo", nome):
            continue
        try:
            func()
        except Exception as e:
            erros.append(str(e))
            anota("[ERRO] " + str(e))
            traceback.print_exc()
    anota(f"===== fim {dt.datetime.now(FUSO).strftime('%d/%m/%Y %H:%M')} =====")
    try:
        c.drive.sobe(c.p["saida"], LOG, tipo="text/plain")
    except Exception as e:
        print("Não consegui enviar o log:", e)
    if erros:
        c.publica_manifesto(estado="erro", fim=agora(), msg=" ".join(erros))
        sys.exit(1)
    c.publica_manifesto(estado="pronto", fim=agora(), msg="Dados atualizados.")


if __name__ == "__main__":
    main()
