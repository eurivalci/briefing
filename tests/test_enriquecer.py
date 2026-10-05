#!/usr/bin/env python3
"""Teste do coletor de dados do município (IBGE + Wikidata) com servidor local.

Cenários: (A) API aceita lote; (B) API recusa lote e o coletor divide até 1 município;
(C) fonte fora do ar mantém os valores da execução anterior; (D) cache por idade do arquivo;
(E) Wikidata: prefeito vigente = sem data de fim e início mais recente; site .gov.br preferido.
Uso: python3 tests/test_enriquecer.py
"""
import contextlib
import gzip
import http.server
import io
import json
import re
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "etl"))
import build_prefeitos as B  # noqa: E402
import consolidar_prefeitos as C  # noqa: E402
import enriquecer_municipios as E  # noqa: E402

MUN = {
    "2304400": ("Fortaleza", "CE"), "2303709": ("Caucaia", "CE"), "2507507": ("João Pessoa", "PB"),
    "5300108": ("Brasília", "DF"), "5101837": ("Boa Esperança do Norte", "MT"),
}
CTRL = {"lote": True, "falhar": set(), "chamadas": 0, "wd_falha": False, "lote_ruim": set(), "paths": [], "sidra_falha": False}


def localidades():
    out = []
    for cod, (nome, uf) in MUN.items():
        m = {"id": int(cod), "nome": nome, "microrregiao": None,
             "regiao-imediata": {"nome": f"Imediata {nome}", "regiao-intermediaria": {"nome": f"Intermediária {uf}",
                                 "UF": {"sigla": uf, "regiao": {"nome": "Nordeste" if uf in ("CE", "PB") else "Centro-Oeste"}}}}}
        out.append(m)
    return out


def valor(ind, cod):
    if ind == 29170:
        return {"2304400": "EVANDRO TESTE LEAL", "2303709": "BELTRANO SUBSTITUTO"}.get(cod, "-")
    if ind == 29169:
        return {"2304400": "fortalezense"}.get(cod, "...")
    if ind == 29171:
        return str(1000 + int(cod[-3:]))
    if ind == 30255:
        return "0.754"
    return "12,5"


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def responder(self, obj, status=200):
        # como o IBGE real: gzip SEMPRE, mesmo sem Accept-Encoding no pedido
        corpo = gzip.compress(json.dumps(obj).encode())
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        CTRL["chamadas"] += 1
        p = urllib.parse.unquote(self.path)
        CTRL["paths"].append(p)
        if p.endswith("/localidades/municipios"):
            return self.responder(localidades())
        if "/agregados/6579/periodos/-1/variaveis/9324" in p:
            if CTRL["sidra_falha"]:
                return self.responder({"erro": "fora do ar"}, 503)
            series = [{"localidade": {"id": c, "nome": n}, "serie": {"2025": valor(29171, c)}} for c, (n, _) in MUN.items()]
            return self.responder([{"id": "9324", "variavel": "População residente estimada",
                                    "resultados": [{"classificacoes": [], "series": series}]}])
        if p.endswith("/pesquisas/33/indicadores"):
            return self.responder([{"id": 1, "indicador": "Grupo", "children": [
                {"id": 29169, "indicador": "Gentílico", "children": []}, {"id": 29170, "indicador": "Prefeito", "children": []}]}])
        m = re.search(r"/pesquisas/(\d+)/indicadores/(\d+)/resultados/([\d|]+)$", p)
        if m:
            ind, cods = int(m.group(2)), m.group(3).split("|")
            if ind in CTRL["falhar"]:
                return self.responder({"erro": "fora do ar"}, 503)
            if len(cods) > 1 and (not CTRL["lote"] or ind in CTRL["lote_ruim"]):
                return self.responder({"erro": "lote não suportado"}, 500)
            # o IBGE devolve série por ano; o mais recente pode vir vazio
            if ind in (29169, 29170):   # texto: Caucaia ainda só tem o prefeito do mandato anterior
                serie = lambda c: {"2021": "PREFEITO ANTERIOR", "2025": "-"} if c == "2303709" else {"2025": valor(ind, c)}  # noqa: E731
            else:
                serie = lambda c: {"2010": "1", "2022": valor(ind, c), "2025": "-" if ind != 29171 else valor(ind, c)}  # noqa: E731
            res = [{"localidade": c, "res": serie(c)} for c in cods]
            return self.responder([{"id": ind, "res": res}])
        return self.responder({"erro": "?"}, 404)

    def do_POST(self):
        CTRL["chamadas"] += 1
        if CTRL["wd_falha"]:
            return self.responder({"erro": "sobrecarga"}, 503)
        b = lambda v: {"value": v}  # noqa: E731
        linhas = [
            {"cod": b("2304400"), "mun": b("http://www.wikidata.org/entity/Q43463"),
             "site": b("http://exemplo.com/"), "artigo": b("https://pt.wikipedia.org/wiki/Fortaleza"),
             "prefeitoLabel": b("Antigo Sem Fim"), "inicio": b("2017-01-01T00:00:00Z")},
            {"cod": b("2304400"), "mun": b("http://www.wikidata.org/entity/Q43463"),
             "site": b("https://www.fortaleza.ce.gov.br/"), "artigo": b("https://pt.wikipedia.org/wiki/Fortaleza"),
             "prefeitoLabel": b("Evandro Leal"), "inicio": b("2025-01-01T00:00:00Z")},
            {"cod": b("2303709"), "mun": b("http://www.wikidata.org/entity/Q1"), "prefeitoLabel": b("Q999999")},
            {"cod": b("123"), "mun": b("http://www.wikidata.org/entity/Q2")},   # código inválido ignorado
        ]
        return self.responder({"results": {"bindings": linhas}})


def rodar(saida, url, *extra):
    with contextlib.redirect_stdout(io.StringIO()):
        return E.main(["--out", str(saida), "--ibge-base", url + "/api/v1", "--wikidata-url", url + "/sparql",
                       "--sidra-base", url + "/api/v3/agregados", *extra])


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    E.time.sleep = lambda s: None   # sem esperas reais nas novas tentativas
    tmp = Path(tempfile.mkdtemp())

    # ---------- (A) lote aceito: poucas chamadas
    CTRL.update(lote=True, falhar=set(), chamadas=0)
    a = tmp / "a.json"
    assert rodar(a, url, "--forcar") == 0
    chamadas_lote = CTRL["chamadas"]
    d = json.loads(a.read_text(encoding="utf-8"))
    f = d["municipios"]["2304400"]
    assert d["total"] == 5 and f["nome"] == "Fortaleza" and f["uf"] == "CE" and f["regiao_imediata"] == "Imediata Fortaleza"
    assert f["populacao"] == 1400.0 and f["populacao_ano"] == "2025", "ano mais recente com valor"
    assert f["idhm"] == 0.754 and f["idhm_ano"] == "2022", "ano vazio é pulado"
    assert f["area_km2"] == 12.5, "vírgula decimal"
    assert f["prefeito_ibge"] == "EVANDRO TESTE LEAL" and f["gentilico"] == "fortalezense"
    assert d["municipios"]["5300108"]["prefeito_ibge"] is None, "'-' vira vazio"
    cau = d["municipios"]["2303709"]
    assert cau["prefeito_ibge"] == "PREFEITO ANTERIOR" and cau["prefeito_ibge_ano"] == "2021", "valor antigo vem COM o ano"
    assert d["municipios"]["5101837"]["regiao"] == "Centro-Oeste", "município de microrregião nula"
    # Wikidata
    assert f["prefeito_wikidata"] == "Evandro Leal", "vigente = início mais recente entre os sem fim"
    assert f["site_oficial"] == "https://www.fortaleza.ce.gov.br/", ".gov.br preferido"
    assert f["wikipedia"] == "https://pt.wikipedia.org/wiki/Fortaleza" and f["wikidata"] == "Q43463"
    assert d["municipios"]["2303709"]["prefeito_wikidata"] is None, "rótulo sem nome (Q999999) descartado"
    assert all(s["ok"] for s in d["status"].values()), d["status"]

    # ---------- (B) lote recusado: sonda uma vez e vai direto ao unitário, SEM cascata de divisões
    CTRL.update(lote=False, chamadas=0)
    b = tmp / "b.json"
    rodar(b, url, "--forcar")
    db = json.loads(b.read_text(encoding="utf-8"))
    assert db["status"]["populacao"]["fonte"] == "SIDRA 6579", "população em uma chamada"
    assert db["status"]["idhm"]["modo"] == "unitario"
    por_municipio = len(E.INDICADORES) - 1 + 2     # todos menos população (SIDRA), mais prefeito e gentílico
    # localidades (1) + wikidata (1) + sonda recusada com 1 nova tentativa (2) + meta do painel (1) + SIDRA (1)
    # + exatamente 1 chamada por município e indicador
    assert CTRL["chamadas"] == 6 + por_municipio * len(MUN), f"sem cascata: {CTRL['chamadas']} chamadas"
    for cod in MUN:
        for k in ("populacao", "idhm", "prefeito_ibge", "gentilico"):
            assert db["municipios"][cod][k] == d["municipios"][cod][k], (cod, k)

    # ---------- (B2) API aceita lote, mas um indicador falha em lote: refaz unitário UMA vez
    CTRL.update(lote=True, lote_ruim={29168}, chamadas=0, paths=[])
    b2 = tmp / "b2.json"
    rodar(b2, url, "--forcar")
    d2 = json.loads(b2.read_text(encoding="utf-8"))
    p29168 = [p for p in CTRL["paths"] if "/indicadores/29168/" in p]
    # lote com 1 nova tentativa (2) + 1 por município, sem cascata
    assert len(p29168) == 2 + len(MUN), f"sem cascata: {len(p29168)}"
    assert d2["municipios"]["2304400"]["densidade"] == 12.5 and d2["status"]["densidade"]["ok"]
    CTRL["lote_ruim"] = set()

    # ---------- (C) fonte fora do ar: mantém o anterior e diz que manteve
    CTRL.update(lote=True, falhar={29171, 29170}, wd_falha=True, sidra_falha=True)
    rodar(a, url, "--forcar")
    dc = json.loads(a.read_text(encoding="utf-8"))
    fc = dc["municipios"]["2304400"]
    assert fc["populacao"] == 1400.0 and fc["prefeito_ibge"] == "EVANDRO TESTE LEAL", "valores retidos"
    assert fc["prefeito_wikidata"] == "Evandro Leal" and fc["site_oficial"], "Wikidata retida"
    for fonte in ("populacao", "prefeito_ibge", "wikidata"):
        assert dc["status"][fonte]["ok"] is False and dc["status"][fonte]["retido"] is True, fonte
    assert dc["status"]["idhm"]["ok"] is True, "fonte saudável segue atualizando"

    # ---------- (D) retomada: só o que falhou é buscado de novo; depois, nada
    CTRL.update(falhar=set(), wd_falha=False, sidra_falha=False, chamadas=0, paths=[])
    rodar(a, url)
    ind_pedidos = {p.split("/indicadores/")[1].split("/")[0] for p in CTRL["paths"] if "/resultados/" in p and p.count("|") >= 3}
    assert ind_pedidos == {"29170"}, f"só o prefeito pela API de Pesquisas: {ind_pedidos}"
    assert any("/agregados/6579/" in p for p in CTRL["paths"]), "população refeita pelo SIDRA"
    assert not any(p.endswith("/localidades/municipios") for p in CTRL["paths"]), "cadastro fresco não é refeito"
    assert json.loads(a.read_text(encoding="utf-8"))["status"]["wikidata"]["ok"], "Wikidata refeita"
    CTRL.update(chamadas=0)
    rodar(a, url)
    assert CTRL["chamadas"] == 0, "tudo fresco: nenhuma chamada"
    assert not list(tmp.glob("*.tmp"))

    # ---------- (E) limite de tempo: salva a população, adia o resto, retoma depois sem refazer
    class Relogio:
        t = -1.0
        @classmethod
        def monotonic(cls):
            cls.t += 1.0
            return cls.t * 60   # cada consulta ao relógio "avança" 1 minuto
    real = E.time.monotonic
    E.time.monotonic = Relogio.monotonic
    e = tmp / "e.json"
    try:
        rodar(e, url, "--forcar", "--limite-minutos", "1.5")
    finally:
        E.time.monotonic = real
    de = json.loads(e.read_text(encoding="utf-8"))
    assert de["status"]["populacao"]["ok"] and de["municipios"]["2304400"]["populacao"] == 1400.0, "população salva primeiro"
    assert not de["status"].get("idhm", {}).get("ok"), "o resto foi adiado, não marcado como concluído"
    CTRL.update(paths=[])
    rodar(e, url)
    assert not any("/agregados/6579/" in p for p in CTRL["paths"]), "população fresca não é buscada de novo na retomada"
    assert json.loads(e.read_text(encoding="utf-8"))["status"]["idhm"]["ok"], "retomada completou o adiado"
    # ---------- (G) retomada NO MEIO de um indicador: blocos de 2 municípios, para no meio, continua do cursor
    class Relogio2:
        n = 0
        @classmethod
        def monotonic(cls):
            cls.n += 1
            return 0 if cls.n < 6 else 10_000   # libera os primeiros passos e "estoura" depois
    g = tmp / "g.json"
    E.time.monotonic = Relogio2.monotonic
    try:
        rodar(g, url, "--forcar", "--bloco", "2", "--limite-minutos", "1")
    finally:
        E.time.monotonic = real
    sg = json.loads(g.read_text(encoding="utf-8"))["status"]
    parciais = {k: v for k, v in sg.items() if v.get("parcial")}
    assert parciais and all(0 < v["cursor"] < len(MUN) for v in parciais.values()), f"parou no meio com cursor salvo: {parciais}"
    campo_parcial, cur = next(iter(parciais.items()))
    ind_id = {c: i for c, _, i, _ in E.INDICADORES}.get(campo_parcial)
    CTRL.update(paths=[])
    rodar(g, url, "--bloco", "2")
    if ind_id:
        pedidos = [p for p in CTRL["paths"] if f"/indicadores/{ind_id}/resultados/" in p]
        cods_pedidos = {c for p in pedidos for c in p.rsplit("/", 1)[1].split("|")}
        assert len(cods_pedidos) == len(MUN) - cur["cursor"], f"retomou do cursor {cur['cursor']}: pediu {sorted(cods_pedidos)}"
    sg2 = json.loads(g.read_text(encoding="utf-8"))["status"]
    assert sg2[campo_parcial]["ok"] and not sg2[campo_parcial].get("parcial"), "indicador concluído na retomada"

    # ---------- (F) as outras duas leituras do IBGE também descompactam gzip
    B.IBGE_MUNICIPIOS_URL = url + "/api/v1/localidades/municipios"
    C.IBGE_MUNICIPIOS_URL = url + "/api/v1/localidades/municipios"
    with contextlib.redirect_stderr(io.StringIO()) as err:
        lista = B.carregar_ibge(None)
        nomes = C.nomes_ibge(None)
    assert "indisponível" not in err.getvalue(), err.getvalue()
    assert {m["ibge"] for m in lista} == set(MUN), "ETL do TSE recebe a lista (fallback por nome ativo)"
    assert next(m for m in lista if m["ibge"] == "5101837")["uf"] == "MT", "município de microrregião nula"
    assert nomes["2507507"] == "João Pessoa", "consolidador recebe os nomes oficiais com acento"
    # e a leitura antiga (json.load direto) quebraria com o mesmo erro visto no GitHub
    import urllib.request
    with urllib.request.urlopen(url + "/api/v1/localidades/municipios") as r:
        try:
            json.load(r)
            raise AssertionError("o servidor de teste deveria estar respondendo em gzip")
        except UnicodeDecodeError as e:
            assert "0x8b" in str(e), e

    srv.shutdown()
    print(f"TODOS OS TESTES DO COLETOR PASSARAM (lote: {chamadas_lote} chamadas; sem lote: unitário, sem cascata)")


if __name__ == "__main__":
    main()
