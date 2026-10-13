#!/usr/bin/env python3
"""
ZepOne — vigia do servidor (roda no GitHub a cada 10 minutos, fora do servidor).

Confere se https://zepone.net responde. Só avisa quando a situação MUDA:
  - caiu: e-mail para a equipe (se o e-mail de envio estiver nos Secrets) e o job termina com
    erro, o que faz o GitHub mandar e-mail para o dono do repositório;
  - voltou: e-mail dizendo por quanto tempo ficou fora.
A situação anterior fica em estado.json (branch "vigia" deste repositório).

Secrets usados (opcionais): SMTP_USER, SMTP_PASSWORD, EMAIL_TO (separados por vírgula), EMAIL_FROM.
Uso: python vigia.py <arquivo-estado.json>
"""
import datetime as dt
import json
import os
import smtplib
import ssl
import sys
import time
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path


FUSO = dt.timezone(dt.timedelta(hours=-3), "Brasília")   # o Brasil não tem mais horário de verão
URLS = ["https://zepone.net/login", "https://zepone.net/t/indice.json"]


def responde():
    """Três tentativas, 20 s entre elas: um soluço de rede não vira alarme."""
    erro = ""
    for tentativa in range(3):
        try:
            for u in URLS:
                req = urllib.request.Request(u, headers={"User-Agent": "ZepOne-vigia"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    if r.status != 200:
                        raise RuntimeError(f"{u} respondeu {r.status}")
            return True, ""
        except Exception as e:
            erro = str(e)
            if tentativa < 2:
                time.sleep(20)
    return False, erro


def quando(iso):
    return dt.datetime.fromisoformat(iso).astimezone(FUSO).strftime("%d/%m %H:%M")


def email(assunto, texto):
    usuario, senha = os.environ.get("SMTP_USER", "").strip(), os.environ.get("SMTP_PASSWORD", "").strip()
    para = [e.strip() for e in os.environ.get("EMAIL_TO", "").split(",") if e.strip()]
    if not (usuario and senha and para):
        print("E-mail para a equipe não configurado (SMTP_USER, SMTP_PASSWORD e EMAIL_TO nos Secrets).")
        return
    msg = MIMEText(texto + "\n\n—\nVigia do ZepOne (GitHub, a cada 10 minutos)", "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = assunto, os.environ.get("EMAIL_FROM", "").strip() or usuario, ", ".join(para)
    with smtplib.SMTP(os.environ.get("SMTP_HOST", "smtp.gmail.com"), int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(usuario, senha)
        s.sendmail(msg["From"], para, msg.as_string())
    print("E-mail enviado para", ", ".join(para))


def main():
    arq = Path(sys.argv[1])
    ant = json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else {"situacao": "ok"}
    agora = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    ok, erro = responde()
    novo = dict(ant)   # sem carimbo de hora a cada rodada: só grava (commit) quando a situação muda
    caiu = False
    if not ok and ant.get("situacao") != "fora":
        novo.update(situacao="fora", desde=agora, erro=erro)
        caiu = True
        email("[ZepOne] O servidor zepone.net caiu",
              f"O zepone.net parou de responder às {quando(agora)} ({erro}).\n\n"
              "As TAGs com a linha curta nova estão usando a cópia de segurança e continuam entregando. "
              "TAGs com a linha curta antiga ficam sem carregar até o servidor voltar.\n\n"
              "Aviso de novo quando ele voltar.")
    elif ok and ant.get("situacao") == "fora":
        desde = ant.get("desde") or agora
        minutos = round((dt.datetime.fromisoformat(agora) - dt.datetime.fromisoformat(desde)).total_seconds() / 60)
        novo.update(situacao="ok", voltouEm=agora, ultimaQueda={"de": desde, "ate": agora, "minutos": minutos})
        novo.pop("desde", None)
        email("[ZepOne] O servidor zepone.net voltou",
              f"O zepone.net voltou a responder às {quando(agora)}. Ficou fora de {quando(desde)} até {quando(agora)} (cerca de {minutos} minutos).")
    arq.write_text(json.dumps(novo, ensure_ascii=False, indent=1), encoding="utf-8")
    print("situação:", novo["situacao"], "| resposta:", "ok" if ok else erro)
    if caiu:
        sys.exit(1)   # falha de propósito: o GitHub manda e-mail para o dono do repositório


if __name__ == "__main__":
    main()
