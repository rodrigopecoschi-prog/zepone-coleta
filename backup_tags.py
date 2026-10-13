#!/usr/bin/env python3
"""
ZepOne — cópia de segurança das TAGs publicadas.

Baixa de https://zepone.net/t/indice.json a lista das TAGs publicadas e grava cada
/t/<id>.js na pasta t/ (branch "tags-backup" deste repositório). A linha curta que fica
no AutoZep usa essa cópia pela CDN do jsDelivr quando o zepone.net não responde:
  https://cdn.jsdelivr.net/gh/rodrigopecoschi-prog/zepone-coleta@tags-backup/t/<id>.js

TAGs com "Onde roda" chegam como um porteiro que carrega o código do zepone.net (?c=1):
aqui o código vai para t/<id>.c.js e o porteiro passa a apontar para a cópia da CDN.
Se o zepone.net estiver fora do ar, nada é apagado: a cópia anterior continua valendo.

Uso: python backup_tags.py <pasta-de-saida>   (imprime os arquivos que mudaram)
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

ORIGEM = "https://zepone.net"
CDN = "https://cdn.jsdelivr.net/gh/rodrigopecoschi-prog/zepone-coleta@tags-backup/t/"
ID_OK = re.compile(r"^[A-Za-z0-9._~@-]{1,200}$")


def baixa(url):
    req = urllib.request.Request(url, headers={"User-Agent": "zepone-backup", "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def main():
    saida = Path(sys.argv[1]) / "t"
    saida.mkdir(parents=True, exist_ok=True)
    try:
        indice = json.loads(baixa(ORIGEM + "/t/indice.json"))
    except Exception as e:
        print(f"zepone.net fora do ar ou sem índice ({e}); as cópias anteriores continuam valendo.", file=sys.stderr)
        return
    mudou = []

    def grava(nome, texto):
        arq = saida / nome
        if not arq.exists() or arq.read_text(encoding="utf-8") != texto:
            arq.write_text(texto, encoding="utf-8")
            mudou.append(nome)

    ids = [i for i in indice.get("tags", []) if ID_OK.match(str(i))]
    for tid in ids:
        try:
            texto = baixa(f"{ORIGEM}/t/{tid}.js")
            if f"/t/{tid}.js?c=1" in texto:   # porteiro do "Onde roda": guarda o código e aponta para a CDN
                grava(f"{tid}.c.js", baixa(f"{ORIGEM}/t/{tid}.js?c=1"))
                texto = re.sub(r'"https?://[^"]*/t/' + re.escape(tid) + r'\.js\?c=1[^"]*"', json.dumps(CDN + tid + ".c.js"), texto)
            grava(f"{tid}.js", texto)
        except Exception as e:
            print(f"[aviso] {tid}: {e}", file=sys.stderr)
    (saida / "LEIA.txt").write_text("Cópia de segurança automática das TAGs do ZepOne (uso da linha curta quando o zepone.net não responde).\n", encoding="utf-8")
    print(f"{len(ids)} TAGs conferidas, {len(mudou)} arquivo(s) atualizado(s).", file=sys.stderr)
    for n in mudou:
        print(n)


if __name__ == "__main__":
    main()
