#!/usr/bin/env python3
"""Teste do coletor de dados do município (IBGE + Wikidata) com servidor local.

Cenários: (A) API aceita lote; (B) API recusa lote e o coletor divide até 1 município;
(C) fonte fora do ar mantém os valores da execução anterior; (D) cache por idade do arquivo;
(E) Wikidata: prefeito vigente = sem data de fim e início mais recente; site .gov.br preferido.
Uso: python3 tests/test_enriquecer.py
"""
import contextlib
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
import enriquecer_municipios as E  # noqa: E402

MUN = {
    "2304400": ("Fortaleza", "CE"), "2303709": ("Caucaia", "CE"), "2507507": ("João Pessoa", "PB"),
    "5300108": ("Brasília", "DF"), "5101837": ("Boa Esperança do Norte", "MT"),
}
CTRL = {"lote": True, "falhar": set(), "chamadas": 0, "wd_falha": False}


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
        corpo = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        CTRL["chamadas"] += 1
        p = urllib.parse.unquote(self.path)
        if p.endswith("/localidades/municipios"):
            return self.responder(localidades())
        if p.endswith("/pesquisas/33/indicadores"):
            return self.responder([{"id": 1, "indicador": "Grupo", "children": [
                {"id": 29169, "indicador": "Gentílico", "children": []}, {"id": 29170, "indicador": "Prefeito", "children": []}]}])
        m = re.search(r"/pesquisas/(\d+)/indicadores/(\d+)/resultados/([\d|]+)$", p)
        if m:
            ind, cods = int(m.group(2)), m.group(3).split("|")
            if ind in CTRL["falhar"]:
                return self.responder({"erro": "fora do ar"}, 503)
            if len(cods) > 1 and not CTRL["lote"]:
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
        return E.main(["--out", str(saida), "--ibge-base", url + "/api/v1", "--wikidata-url", url + "/sparql", *extra])


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

    # ---------- (B) lote recusado: divide até 1 e chega no mesmo resultado
    CTRL.update(lote=False, chamadas=0)
    b = tmp / "b.json"
    rodar(b, url, "--forcar")
    db = json.loads(b.read_text(encoding="utf-8"))
    assert CTRL["chamadas"] > chamadas_lote * 3, "sem lote, mais chamadas"
    assert db["status"]["populacao"]["divisoes"] > 0
    for cod in MUN:
        for k in ("populacao", "idhm", "prefeito_ibge", "gentilico"):
            assert db["municipios"][cod][k] == d["municipios"][cod][k], (cod, k)

    # ---------- (C) fonte fora do ar: mantém o anterior e diz que manteve
    CTRL.update(lote=True, falhar={29171, 29170}, wd_falha=True)
    rodar(a, url, "--forcar")
    dc = json.loads(a.read_text(encoding="utf-8"))
    fc = dc["municipios"]["2304400"]
    assert fc["populacao"] == 1400.0 and fc["prefeito_ibge"] == "EVANDRO TESTE LEAL", "valores retidos"
    assert fc["prefeito_wikidata"] == "Evandro Leal" and fc["site_oficial"], "Wikidata retida"
    for fonte in ("populacao", "prefeito_ibge", "wikidata"):
        assert dc["status"][fonte]["ok"] is False and dc["status"][fonte]["retido"] is True, fonte
    assert dc["status"]["idhm"]["ok"] is True, "fonte saudável segue atualizando"

    # ---------- (D) cache: arquivo novo não é refeito sem --forcar
    CTRL.update(falhar=set(), wd_falha=False, chamadas=0)
    rodar(a, url)
    assert CTRL["chamadas"] == 0, "arquivo recente reaproveitado"
    assert not list(tmp.glob("*.tmp"))
    srv.shutdown()
    print(f"TODOS OS TESTES DO COLETOR PASSARAM (lote: {chamadas_lote} chamadas; sem lote: divisão adaptativa)")


if __name__ == "__main__":
    main()
