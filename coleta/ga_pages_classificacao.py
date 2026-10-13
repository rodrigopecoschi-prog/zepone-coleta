"""
Classificacao de URLs do GA4 em: home / editoria / materia / outros.

Como usar:
1) No seu fetch do GA4 (o que o Atualizar_painel.bat ja roda), adicione um relatorio
   com a dimensao 'pagePath' (ou 'pagePathPlusQueryString' sem a query) e a metrica
   'screenPageViews', quebrado por 'date'. Ex. de linhas retornadas:
       date=20260810, pagePath='/', views=1234
       date=20260810, pagePath='/category/convite-animado/', views=210
       date=20260810, pagePath='/2023/08/convite-animado-barbie.html', views=980
2) Passe cada linha para classify_url(pagePath) e some as views por (categoria, date).
3) Guarde no site: S['pages'] = {'home':{date:views}, 'editoria':{...},
   'materia':{...}, 'outros':{...}}  -> o painel ja desenha o grafico sozinho.

Regras padrao (cobrem o padrao WordPress/blog do exemplo). Cada site pode ter um
override em REGRAS_POR_SITE (chave = id do site).
"""
import re

# Overrides opcionais por site. Ex.: se um site usa '/noticias/' como materia, ou
# '/secao/' como editoria, adicione aqui. Deixe vazio para usar o padrao.
REGRAS_POR_SITE = {
    # 'fazendoanossafesta': {
    #     'editoria': ['/category/'],
    #     'materia_regex': [r'/\d{4}/\d{2}/', r'\.html'],
    # },
}

_EDITORIA_PADRAO = ['/category/', '/categoria/', '/editoria/', '/editorias/',
                    '/section/', '/secao/', '/tag/', '/tags/', '/assunto/', '/temas/']
_MATERIA_REGEX_PADRAO = [r'/\d{4}/\d{2}/', r'\.html', r'\.htm', r'/\d{4}/\d{2}/\d{2}/']


def _norm(path):
    if not path:
        return ''
    p = str(path).split('?')[0].split('#')[0].strip()
    # tira dominio se vier URL completa
    p = re.sub(r'^https?://[^/]+', '', p)
    if not p.startswith('/'):
        p = '/' + p
    # remove barra final (menos a raiz)
    if len(p) > 1:
        p = p.rstrip('/')
    return p.lower()


def classify_url(path, site=None):
    p = _norm(path)
    if p in ('', '/'):
        return 'home'
    reg = REGRAS_POR_SITE.get(site, {})
    editoria = reg.get('editoria', _EDITORIA_PADRAO)
    materia_rx = reg.get('materia_regex', _MATERIA_REGEX_PADRAO)
    # editoria (secoes/categorias)
    for seg in editoria:
        if seg in p:
            return 'editoria'
    # materia (post: tem data no caminho, ou .html, etc.)
    for rx in materia_rx:
        if re.search(rx, p):
            return 'materia'
    # fallback: caminho com 2+ niveis costuma ser materia; 1 nivel = outros
    segs = [s for s in p.split('/') if s]
    if len(segs) >= 2:
        return 'materia'
    return 'outros'


def agrupar_pages(linhas, site=None):
    """linhas = iteravel de (date_str, pagePath, views). Retorna o dict 'pages'."""
    out = {'home': {}, 'editoria': {}, 'materia': {}, 'outros': {}}
    for d, path, v in linhas:
        cat = classify_url(path, site)
        try:
            v = int(round(float(v)))
        except (TypeError, ValueError):
            continue
        out[cat][d] = out[cat].get(d, 0) + v
    # remove categorias totalmente vazias
    return {k: val for k, val in out.items() if val}


if __name__ == '__main__':
    testes = [
        '/',
        'https://fazendoanossafesta.com.br/',
        '/category/convite-animado/',
        '/2023/08/convite-animado-barbie.html/',
        '/2021/07/caixa-envelope-dia-dos-pais.html',
        '/tag/barbie/',
        '/sobre',
    ]
    for t in testes:
        print(f'{classify_url(t, "fazendoanossafesta"):>9}  <-  {t}')
