#!/usr/bin/env python3
"""Teste do ETL da CAPAG. Uso: python3 tests/test_capag.py

Layouts: (1) atual — cabeçalho em duas linhas, aba auxiliar MAIOR sem notas, leia-me, A+/B+, n.d.;
(2) antigo — código IBGE de 6 dígitos, numerais romanos, sem A+; (3) sem "CAPAG" no cabeçalho.
Também: datas a partir dos títulos/URLs REAIS do portal, revisão substituindo original, arquivo
de 0 bytes ignorado, cache entre execuções, posição corrompida não derrubando as outras.
"""
import contextlib
import gzip
import http.server
import io
import json
import sys
import tempfile
import threading
from pathlib import Path

from openpyxl import Workbook

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "etl"))
import capag as K  # noqa: E402

# 120 municípios fictícios com dígito verificador "válido" (só precisam constar do cadastro)
CODS = [f"23{i:04d}{i % 10}" for i in range(120)]
FORT, CAUC = CODS[0], CODS[1]


def xlsx(abas: dict) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for nome, linhas in abas.items():
        ws = wb.create_sheet(nome)
        for ln in linhas:
            ws.append(ln)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def layout_atual(nota_fort="B", nota_cauc="n.d."):
    cab1 = ["", "", "", "Indicador 1", "", "Indicador 2", "", "Indicador 3", "", "", ""]
    cab2 = ["UF", "Município", "Código IBGE", "Endividamento (DC/RCL)", "Nota 1", "Poupança Corrente", "Nota 2",
            "Liquidez", "Nota 3", "CAPAG", "Ranking da Qualidade da Informação"]
    linhas = [["CAPAG Municípios — posição 01/09/2026"], [], cab1, cab2]
    finais = ["A+", "A", "B+", "B", "C", "D"]
    for i, c in enumerate(CODS):
        nota = nota_fort if c == FORT else nota_cauc if c == CAUC else finais[i % 6]
        linhas.append(["CE", f"Município {i}", int(c), f"{0.1 + i / 1000:.4f}".replace(".", ","), "A",
                       0.85 + i / 10000, "B", 0.5, "C", nota, i % 5 + 1])
    base = [["Código", "Receita", "Despesa", "Outra"]] + [[c, 1000.0 + i, 900.0, 1.0] for i, c in enumerate(CODS + CODS[:30])]
    return xlsx({"Leia-me": [["Arquivo da CAPAG"], ["texto"]], "Base de dados": base, "CAPAG": linhas})


def layout_antigo():
    cab = ["Código Município", "UF", "Nome", "Indicador I", "Nota I", "Indicador II", "Nota II", "Indicador III",
           "Nota III", "Classificação CAPAG"]
    linhas = [cab]
    for i, c in enumerate(CODS):
        linhas.append([int(c[:6]), "CE", f"M{i}", 0.2, "B", 0.9, "B", 0.4, "A",
                       "C" if c == FORT else ("n.d." if i % 7 == 0 else ["A", "B", "C", "D"][i % 4])])
    return xlsx({"Planilha1": linhas})


def layout_sem_capag():
    cab = ["Cod IBGE", "Ente", "Ind 1 valor", "Nota 1", "Ind 2 valor", "Nota 2", "Ind 3 valor", "Nota 3", "Nota Final"]
    # indicadores publicados em POR CENTO (35,2 em vez de 0,352)
    linhas = [cab] + [[c, f"M{i}", 35.2, "A", 92.5, "C", 0.3 * 100, "B", ["A", "B", "C", "D"][i % 4]] for i, c in enumerate(CODS)]
    return xlsx({"Dados": linhas})


def layout_oficial():
    """Ordem de colunas da planilha oficial da STN: cod_ibge, municipio, uf, CAPAG, Indicador 1, Nota 1,
    Indicador 2, Nota 2, Indicador 3, Nota 3, ICF, Observação, Origem da Nota Final, ReF."""
    cab = ["cod_ibge", "municipio", "uf", "CAPAG", "Indicador 1", "Nota 1", "Indicador 2", "Nota 2", "Indicador 3",
           "Nota 3", "ICF", "Observação", "Origem da Nota Final", "ReF"]
    linhas = [cab]
    for i, c in enumerate(CODS):
        obs = "Nota rebaixada em razão do ICF" if c == FORT else ("" if i % 3 else "-")
        linhas.append([c, f"M{i}", "CE", "B" if c == FORT else ["A+", "A", "B+", "C", "D"][i % 5], 0.41, "A", 0.93, "B", 0.6, "C",
                       "A" if c == FORT else "B", obs, "ICF" if c == FORT else "Indicadores", 1])
    return xlsx({"CAPAG_Municipios": linhas})


def testar_layouts():
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as t:
        t.write(layout_oficial()); t.flush()
        regs, diag = K.ler_planilha(t.name, set(CODS))
    cols = {k: v["cabecalho"] for k, v in diag["colunas"].items()}
    assert cols["capag"] == "capag" and cols["endividamento"] == "indicador 1" and cols["nota_liquidez"] == "nota 3", cols
    assert cols["qualidade_informacao"] == "icf" and cols["observacao"] == "observacao" and cols["origem_nota"] == "origem da nota final", cols
    f = regs[FORT]
    assert f["capag"] == "B" and f["endividamento"] == 0.41 and f["nota_poupanca_corrente"] == "B" and f["qualidade_informacao"] == "A"
    assert f["observacao"] == "Nota rebaixada em razão do ICF" and f["origem_nota"] == "ICF"
    assert regs[CODS[3]]["observacao"] is None, "'-' e vazio viram ausência"

    validos = set(CODS)
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as t:
        t.write(layout_atual()); t.flush()
        regs, diag = K.ler_planilha(t.name, validos)
    assert diag["aba"] == "CAPAG", f"aba auxiliar maior sem notas não pode vencer: {diag['abas']}"
    assert set(diag["colunas"]) >= {"codigo_ibge", "capag", "endividamento", "nota_endividamento", "poupanca_corrente",
                                    "nota_poupanca_corrente", "liquidez", "nota_liquidez", "qualidade_informacao"}, diag["colunas"]
    assert diag["colunas"]["capag"]["cabecalho"] == "capag"
    f = regs[FORT]
    assert f["capag"] == "B" and f["endividamento"] == 0.1 and f["nota_endividamento"] == "A", f
    assert f["nota_poupanca_corrente"] == "B" and f["nota_liquidez"] == "C" and f["qualidade_informacao"] == 1
    assert regs[CAUC]["capag"] is None and regs[CAUC]["capag_publicada"] == "n.d.", "n.d. preservado como publicado"
    assert len(regs) == 120

    with tempfile.NamedTemporaryFile(suffix=".xlsx") as t:
        t.write(layout_antigo()); t.flush()
        regs, diag = K.ler_planilha(t.name, validos)
    assert len(regs) == 120, "código de 6 dígitos casado com o cadastro"
    assert regs[FORT]["capag"] == "C"
    assert regs[FORT]["nota_endividamento"] == "B" and regs[FORT]["nota_liquidez"] == "A", \
        f"'Nota III' não pode ser lida como 'Nota I': {diag['colunas']}"
    assert diag["colunas"]["nota_poupanca_corrente"]["cabecalho"] == "nota ii"

    with tempfile.NamedTemporaryFile(suffix=".xlsx") as t:
        t.write(layout_sem_capag()); t.flush()
        regs, diag = K.ler_planilha(t.name, validos)
    assert diag["colunas"]["capag"]["cabecalho"] == "nota final", f"desempate pela presença de D: {diag['colunas']}"
    assert regs[CODS[3]]["capag"] == "D" and regs[CODS[3]]["nota_poupanca_corrente"] == "C"
    assert regs[CODS[3]]["endividamento"] == 0.352 and diag["escala_original"]["endividamento"] == "percentual", \
        "planilha em por cento normalizada para fração"
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as t:
        t.write(layout_atual()); t.flush()
        _, d1 = K.ler_planilha(t.name, set(CODS))
    assert d1["escala_original"]["endividamento"] == "fracao"

    # planilha sem nota reconhecível: falha explícita
    with tempfile.NamedTemporaryFile(suffix=".xlsx") as t:
        t.write(xlsx({"x": [["Código", "Valor"]] + [[c, 1.0] for c in CODS]})); t.flush()
        try:
            K.ler_planilha(t.name, validos)
            raise AssertionError("deveria falhar sem nota")
        except K.LayoutCapag as e:
            assert "nenhuma aba" in str(e)


def testar_datas():
    casos = [  # títulos e URLs reais do portal do Tesouro (out/2026)
        ("Capag Municípios 2026 - 01/09/2026", "x/capag-municipios-posicao-2026-set.xlsx", ("2026-09-01", 2026)),
        ("Capag Municípios 2025 - 09/11/2025", "x/capag-municipios-posicao-2025-nov-09---processamento-2025-nov-10.xlsx", ("2025-11-09", 2025)),
        ("CAPAG Municípios 2024 - 17/06/2024", "x/20240618capag-municipios.xlsx", ("2024-06-17", 2024)),
        ("CAPAG Municípios 2023", "x/capag-municipios-2023.xlsx", (None, 2023)),
        ("Capag sem data no título", "x/capag-municipios-posicao-2025-jun-10.xlsx", ("2025-06-10", 2025)),
        ("Capag sem data no título", "x/20241015capag-municipios.xlsx", ("2024-10-15", 2024)),
    ]
    for nome, url, esperado in casos:
        assert K.data_da_posicao(nome, url) == esperado, (nome, K.data_da_posicao(nome, url))
    pacote = {"resources": [
        {"id": "m", "name": "Metadados", "url": "x/metadados.pdf"},
        {"id": "a", "name": "CAPAG Municípios 2022 ", "url": "x/capag-municipios-2022.xlsx"},
        {"id": "b", "name": "Capag Municípios 2022 - revisão", "url": "x/capag-oficial-municipios-2023-02-23-corrigido.xlsx"},
        {"id": "c", "name": "Capag Municípios 2026 - 01/09/2026", "url": "x/capag-municipios-posicao-2026-set.xlsx"},
        {"id": "d", "name": "CAPAG Municípios 2021", "url": "x/capag-municipios---novembro-2021.xlsx"},
    ]}
    ids = [p["id"] for p in K.listar_posicoes(pacote)]
    assert ids == ["d", "b", "c"], f"metadados fora, revisão substitui o original, ordem cronológica: {ids}"


ARQS = {}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/ckan"):
            corpo = gzip.compress(json.dumps({"success": True, "result": ARQS["pacote"]}).encode())
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
        else:
            corpo = ARQS.get(self.path)
            if corpo is None:
                self.send_response(404); self.end_headers(); return
            ARQS["baixados"].append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


def testar_ponta_a_ponta():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    tmp = Path(tempfile.mkdtemp())
    muns = {"municipios": {c: {"uf": "CE" if i < 100 else "PB"} for i, c in enumerate(CODS)}}
    (tmp / "municipios.json").write_text(json.dumps(muns))
    ARQS.update({"/f/2021.xlsx": layout_antigo(), "/f/2026-jun.xlsx": layout_atual("C"), "/f/2026-set.xlsx": layout_atual("B"),
                 "/f/quebrado.xlsx": b"isto nao e um xlsx", "baixados": []})
    ARQS["pacote"] = {"resources": [
        {"id": "r21", "name": "CAPAG Municípios 2021", "url": base + "/f/2021.xlsx", "last_modified": "2024-08-01", "size": 456000},
        {"id": "r20", "name": "CAPAG Municípios 2020", "url": base + "/f/2020.xlsx", "last_modified": "2024-08-01", "size": 0},
        {"id": "rq", "name": "Capag Municípios 2025 - 19/02/2025", "url": base + "/f/quebrado.xlsx", "last_modified": "2025-02-20"},
        {"id": "rj", "name": "Capag Municípios 2026 - 01/06/2026", "url": base + "/f/2026-jun.xlsx", "last_modified": "2026-06-02"},
        {"id": "rs", "name": "Capag Municípios 2026 - 01/09/2026", "url": base + "/f/2026-set.xlsx", "last_modified": "2026-09-15"},
    ]}
    args = ["--out", str(tmp / "capag"), "--municipios", str(tmp / "municipios.json"), "--ckan", base + "/ckan"]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert K.main(args) == 0, "posição quebrada não derruba as outras"
    ce = json.loads((tmp / "capag" / "ce.json").read_text(encoding="utf-8"))
    assert "ODbL" in ce["licenca"] and "Tesouro" in ce["fonte"] and "não vincula" in ce["aviso"]
    traj = ce["municipios"][FORT]
    assert [t["posicao"] for t in traj] == ["2021", "2026-06-01", "2026-09-01"], traj
    assert [t["capag"] for t in traj] == ["C", "C", "B"] and traj[-1]["ano_base"] == 2025
    assert CODS[110] in json.loads((tmp / "capag" / "ce.json").read_text())["municipios"], "UF pelo prefixo 23"
    aud = json.loads((tmp / "capag" / "_auditoria.json").read_text(encoding="utf-8"))
    assert [x["posicao"] for x in aud["ignoradas"]] == ["CAPAG Municípios 2020"], "0 bytes ignorado"
    assert [x["posicao"] for x in aud["falhas"]] == ["Capag Municípios 2025 - 19/02/2025"]
    assert "/f/2020.xlsx" not in ARQS["baixados"]

    # segunda execução: nada novo baixado (a quebrada é tentada de novo, é a única)
    ARQS["baixados"] = []
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        K.main(args)
    assert ARQS["baixados"] == ["/f/quebrado.xlsx"], ARQS["baixados"]
    # posição republicada (last_modified mudou) é reprocessada
    ARQS["pacote"]["resources"][-1]["last_modified"] = "2026-09-20"
    ARQS["/f/2026-set.xlsx"] = layout_atual("A")
    ARQS["baixados"] = []
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        K.main(args)
    assert "/f/2026-set.xlsx" in ARQS["baixados"]
    assert json.loads((tmp / "capag" / "ce.json").read_text())["municipios"][FORT][-1]["capag"] == "A"
    assert not list((tmp / "capag").rglob("*.tmp"))
    assert sorted(json.loads((tmp / "capag" / "_auditoria.json").read_text())["arquivos_por_uf"]) == ["CE"], \
        "a UF vem do prefixo do código (23 = CE), não do cadastro"

    # sem cadastro de municípios: ainda grava os arquivos por UF (antes: "OK" sem gravar nada)
    t2 = Path(tempfile.mkdtemp())
    args2 = ["--out", str(t2 / "capag"), "--municipios", str(t2 / "nao-existe.json"), "--ckan", base + "/ckan"]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        assert K.main(args2) == 0
    assert (t2 / "capag" / "ce.json").exists(), "arquivo por UF gravado mesmo sem cadastro"
    srv.shutdown()


if __name__ == "__main__":
    testar_layouts()
    testar_datas()
    testar_ponta_a_ponta()
    print("TODOS OS TESTES DA CAPAG PASSARAM")
