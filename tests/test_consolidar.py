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

    assert C.main(["--prefeitos", str(tmp), "--ibge", str(tmp / "ibge.json"), "--hoje", "2026-10-03"]) == 0
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
    print("TODOS OS TESTES DO CONSOLIDADO PASSARAM")


if __name__ == "__main__":
    main()
