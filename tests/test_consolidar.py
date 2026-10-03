#!/usr/bin/env python3
"""Teste do consolidado nacional (todos.json / todos.csv). Uso: python3 tests/test_consolidar.py"""
import csv
import io
import json
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "etl"))
import consolidar_prefeitos as C  # noqa: E402


def reg(nm_ue, sq, urna, nome, partido, nasc, turno=1, sup=False, votos=None, pct=None, bens=None, vice=None, foto=None):
    return {"tse": {"sg_ue": "1", "nm_ue": nm_ue},
            "eleicao": {"ano": 2024, "data": "2024-10-06", "turno": turno, "suplementar": sup},
            "prefeito": {"sq": sq, "nome": nome, "nome_urna": urna, "partido": partido, "partido_nome": "PARTIDO X",
                         "genero": "FEMININO", "nascimento": nasc, "instrucao": "SUPERIOR COMPLETO", "ocupacao": "MÉDICO",
                         "votos": votos, "votos_pct": pct, "bens_total": bens, "foto": foto},
            "vice": vice}


def main():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "al.json").write_text(json.dumps({"uf": "AL", "municipios": {
        "2700300": reg("ARAPIRACA", "1", "=HYPERLINK(\"http://x\")", "MARIA DA SILVA", "MDB", "1980-10-03", votos=1000, pct=51.237, bens=1234.5,
                       vice={"nome": "JOAO VICE", "nome_urna": "JOÃO", "partido": "PP"}, foto="fotos/2700300.webp?v=1"),
        "2700102": reg("ÁGUA BRANCA", "2", "-ZECA", "JOSE DOS SANTOS", "PT", "1980-10-04", turno=2, sup=True),
    }}), encoding="utf-8")
    (tmp / "ce.json").write_text(json.dumps({"uf": "CE", "municipios": {
        "2304400": reg("FORTALEZA", "3", "EVANDRO", "EVANDRO LEAL", "PT", None),
    }}), encoding="utf-8")
    (tmp / "_indice.json").write_text("{}")          # não pode ser lido como UF
    (tmp / "todos.json").write_text("{}")            # saída anterior não pode ser relida como UF
    ibge = [{"id": 2700300, "nome": "Arapiraca"}, {"id": 2304400, "nome": "Fortaleza"}]  # Água Branca ausente: fallback
    (tmp / "ibge.json").write_text(json.dumps(ibge), encoding="utf-8")

    assert C.main(["--prefeitos", str(tmp), "--ibge", str(tmp / "ibge.json"), "--hoje", "2026-10-03",
                   "--municipios", str(tmp / "nao-existe.json")]) == 0
    doc = json.loads((tmp / "todos.json").read_text(encoding="utf-8"))
    assert doc["schema"] == 1 and doc["total"] == 3 and doc["colunas"] == C.COLUNAS
    r = doc["registros"]
    assert [x["municipio"] for x in r] == ["Água Branca", "Arapiraca", "Fortaleza"], "ordem por UF e nome sem acento"
    ag, ar, fo = r
    assert ag["municipio"] == "Água Branca" and ag["prefeito"] == "-Zeca", "fallback do nome do TSE em caixa de título"
    assert ar["idade"] == 46 and ag["idade"] == 45, "idade na data de referência (aniversário no dia conta)"
    assert ar["nome_completo"] == "Maria da Silva" and ar["vice"] == "João" and ar["vice_partido"] == "PP"
    assert ar["regiao"] == "Nordeste" and ar["briefing"] == "index.html#/AL/2700300"
    assert fo["idade"] is None and fo["vice"] is None
    assert ag["turno"] == 2 and ag["suplementar"] is True

    bruto = (tmp / "todos.csv").read_bytes()
    assert bruto.startswith(b"\xef\xbb\xbf"), "BOM para o Excel"
    linhas = list(csv.reader(io.StringIO(bruto.decode("utf-8-sig")), delimiter=";"))
    assert linhas[0] == C.COLUNAS and len(linhas) == 4
    col = {c: i for i, c in enumerate(linhas[0])}
    l_ar, l_ag = linhas[2], linhas[1]
    assert l_ar[col["prefeito"]].startswith("'="), "injeção de fórmula neutralizada"
    assert l_ag[col["prefeito"]] == "'-Zeca"
    assert l_ar[col["votos_pct"]] == "51,24" and l_ar[col["bens_declarados"]] == "1234,50", "decimal brasileiro"
    assert l_ag[col["suplementar"]] == "sim" and l_ar[col["suplementar"]] == "nao"
    assert linhas[3][col["idade"]] == "" and linhas[3][col["vice"]] == ""
    assert not list(tmp.glob("*.tmp")), "gravação atômica não deixa resto"
    # ================= com dados do município: uma linha por município + reconciliação
    t2 = Path(tempfile.mkdtemp())
    (t2 / "ce.json").write_text(json.dumps({"uf": "CE", "municipios": {
        "2304400": reg("FORTALEZA", "3", "EVANDRO", "EVANDRO TESTE LEAL", "PT", "1970-03-15"),
        "2303709": reg("CAUCAIA", "4", "FULANO", "FULANO ORIGINAL SILVA", "PSB", "1980-01-01"),
    }}), encoding="utf-8")
    (t2 / "pb.json").write_text(json.dumps({"uf": "PB", "municipios": {
        "2507507": reg("JOÃO PESSOA", "5", "CÍCERO", "CÍCERO LUCENA FILHO", "PP", "1957-01-01"),
    }}), encoding="utf-8")
    base = lambda nome, uf, **k: {"nome": nome, "uf": uf, "regiao_imediata": "Imediata", "regiao_intermediaria": "Inter",  # noqa: E731
                                  "populacao": 2428708.0, "populacao_ano": "2025", "idhm": 0.754, "idhm_ano": "2010", **k}
    muns = {"municipios": {
        "2304400": base("Fortaleza", "CE", prefeito_ibge="EVANDRO TESTE LEAL", prefeito_ibge_ano="2025",
                        prefeito_wikidata="Evandro Leal", site_oficial="https://www.fortaleza.ce.gov.br/", gentilico="fortalezense"),
        "2303709": base("Caucaia", "CE", prefeito_ibge="BELTRANO SUBSTITUTO", prefeito_ibge_ano="2025"),
        "2507507": base("João Pessoa", "PB", prefeito_ibge="LUCIANO CARTAXO", prefeito_ibge_ano="2021"),   # mandato anterior
        "5300108": base("Brasília", "DF"),
        "5101837": base("Boa Esperança do Norte", "MT", prefeito_wikidata="Alguém"),                   # sem eleito no TSE
    }}
    (t2 / "municipios.json").write_text(json.dumps(muns), encoding="utf-8")
    assert C.main(["--prefeitos", str(t2), "--municipios", str(t2 / "municipios.json"), "--hoje", "2026-10-03"]) == 0
    d2 = json.loads((t2 / "todos.json").read_text(encoding="utf-8"))
    assert d2["total"] == 5 and d2["com_prefeito_tse"] == 3 and d2["dados_municipio"] is True
    assert d2["colunas"][:len(C.COLUNAS)] == C.COLUNAS and d2["colunas"].index("codigo_tse") < d2["colunas"].index("populacao"), \
        "colunas novas só ao final (compatível com schema 1)"
    por = {r["codigo_ibge"]: r for r in d2["registros"]}
    assert por["2304400"]["situacao_mandato"] == "consistente"
    assert por["2303709"]["situacao_mandato"] == "divergente"
    assert por["2507507"]["situacao_mandato"] == "parcial", "IBGE de 2021 é do mandato anterior: não compara"
    assert por["2507507"]["prefeito_ibge"] == "Luciano Cartaxo" and por["2507507"]["prefeito_ibge_ano"] == 2021, "mas fica na coluna"
    assert por["5300108"]["situacao_mandato"] == "nao_se_aplica" and por["5300108"]["prefeito"] is None
    mt = por["5101837"]
    assert mt["tem_prefeito_tse"] is False and mt["situacao_mandato"] == "parcial" and mt["regiao"] == "Centro-Oeste"
    assert mt["briefing"] == "index.html#/MT/5101837" and mt["suplementar"] is None
    f = por["2304400"]
    assert f["populacao"] == 2428708 and isinstance(f["populacao"], int) and f["populacao_ano"] == 2025, "inteiros como inteiros"
    assert f["idhm"] == 0.754 and f["gentilico"] == "fortalezense" and f["site_oficial"].endswith(".gov.br/")
    assert [r["uf"] for r in d2["registros"]] == ["CE", "CE", "DF", "MT", "PB"], "ordem por UF"
    l2 = list(csv.reader(io.StringIO((t2 / "todos.csv").read_bytes().decode("utf-8-sig")), delimiter=";"))
    col2 = {c: i for i, c in enumerate(l2[0])}
    lf = next(l for l in l2 if l[0] == "2304400")
    assert lf[col2["idhm"]] == "0,754", "IDHM com 3 casas"
    assert lf[col2["populacao"]] == "2428708" and lf[col2["tem_prefeito_tse"]] == "sim"

    print("TODOS OS TESTES DO CONSOLIDADO PASSARAM")


if __name__ == "__main__":
    main()
