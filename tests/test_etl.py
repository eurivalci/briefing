#!/usr/bin/env python3
"""Teste de ponta a ponta do ETL com arquivos sintéticos no layout do TSE (latin-1, ';', aspas).

Casos cobertos:
  * 2º turno (Fortaleza): linha do 1º turno "2º TURNO" + linha do 2º turno "ELEITO";
  * código TSE com zero à esquerda ("01120" -> Acrelândia);
  * eleição suplementar posterior substitui a ordinária (Cametá);
  * município ausente do de-para resolvido pelo nome (Boa Esperança do Norte/MT);
  * UE inexistente vai para auditoria, sem derrubar o processo;
  * dados sensíveis (cor/raça, CPF, e-mail) nunca chegam à saída;
  * valores monetários com vírgula decimal e separador de milhar;
  * trajetória ligada por nome + nascimento, com resultado mais informativo entre turnos.
Uso: python3 tests/test_etl.py
"""
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "etl"))
import build_prefeitos as etl  # noqa: E402

CAB_CAND = (
    "DT_GERACAO;HH_GERACAO;ANO_ELEICAO;CD_TIPO_ELEICAO;NM_TIPO_ELEICAO;NR_TURNO;CD_ELEICAO;DS_ELEICAO;DT_ELEICAO;"
    "TP_ABRANGENCIA;SG_UF;SG_UE;NM_UE;CD_CARGO;DS_CARGO;SQ_CANDIDATO;NR_CANDIDATO;NM_CANDIDATO;NM_URNA_CANDIDATO;"
    "NM_SOCIAL_CANDIDATO;NR_CPF_CANDIDATO;DS_EMAIL;CD_SITUACAO_CANDIDATURA;DS_SITUACAO_CANDIDATURA;TP_AGREMIACAO;"
    "NR_PARTIDO;SG_PARTIDO;NM_PARTIDO;NR_FEDERACAO;NM_FEDERACAO;SG_FEDERACAO;DS_COMPOSICAO_FEDERACAO;SQ_COLIGACAO;"
    "NM_COLIGACAO;DS_COMPOSICAO_COLIGACAO;SG_UF_NASCIMENTO;DT_NASCIMENTO;NR_TITULO_ELEITORAL_CANDIDATO;CD_GENERO;"
    "DS_GENERO;CD_GRAU_INSTRUCAO;DS_GRAU_INSTRUCAO;CD_ESTADO_CIVIL;DS_ESTADO_CIVIL;CD_COR_RACA;DS_COR_RACA;"
    "CD_OCUPACAO;DS_OCUPACAO;CD_SIT_TOT_TURNO;DS_SIT_TOT_TURNO"
).split(";")


def cand(ano, turno, cd_el, ds_el, dt, uf, ue, nm_ue, cargo, sq, nr, nome, urna, partido, nasc, sit, tipo="ELEIÇÃO ORDINÁRIA"):
    d = {c: "#NULO#" for c in CAB_CAND}
    d.update({
        "ANO_ELEICAO": str(ano), "NM_TIPO_ELEICAO": tipo, "NR_TURNO": str(turno), "CD_ELEICAO": cd_el,
        "DS_ELEICAO": ds_el, "DT_ELEICAO": dt, "SG_UF": uf, "SG_UE": ue, "NM_UE": nm_ue, "CD_CARGO": str(cargo),
        "DS_CARGO": {11: "PREFEITO", 12: "VICE-PREFEITO", 13: "VEREADOR"}[cargo], "SQ_CANDIDATO": sq,
        "NR_CANDIDATO": nr, "NM_CANDIDATO": nome, "NM_URNA_CANDIDATO": urna, "NR_CPF_CANDIDATO": "-4",
        "DS_EMAIL": "segredo@exemplo.com", "DS_SITUACAO_CANDIDATURA": "APTO", "SG_PARTIDO": partido,
        "NM_PARTIDO": f"Partido {partido}", "DT_NASCIMENTO": nasc, "DS_GENERO": "MASCULINO",
        "DS_GRAU_INSTRUCAO": "SUPERIOR COMPLETO", "DS_COR_RACA": "SENSIVEL", "DS_OCUPACAO": "EMPRESÁRIO",
        "DS_SIT_TOT_TURNO": sit,
    })
    return d


def escrever_zip(path, nome_csv, cab, linhas):
    buf = io.StringIO()
    buf.write(";".join(f'"{c}"' for c in cab) + "\r\n")
    for ln in linhas:
        buf.write(";".join(f'"{ln.get(c, "")}"' for c in cab) + "\r\n")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(nome_csv, buf.getvalue().encode("latin-1"))
        z.writestr("leiame.pdf", b"%PDF fake")


def main():
    tmp = Path(tempfile.mkdtemp())
    E24, D24 = "2045202024", "06/10/2024"
    linhas24 = [
        # Fortaleza: 2º turno
        cand(2024, 1, E24, "Eleições Municipais 2024", D24, "CE", "13897", "FORTALEZA", 11, "60001", "13", "EVANDRO TESTE LEAL", "EVANDRO", "PT", "15/03/1970", "2º TURNO"),
        cand(2024, 2, E24, "Eleições Municipais 2024", "27/10/2024", "CE", "13897", "FORTALEZA", 11, "60001", "13", "EVANDRO TESTE LEAL", "EVANDRO", "PT", "15/03/1970", "ELEITO"),
        cand(2024, 2, E24, "Eleições Municipais 2024", "27/10/2024", "CE", "13897", "FORTALEZA", 11, "60002", "22", "ANDRE OPOSICAO", "ANDRE", "PL", "01/01/1975", "NÃO ELEITO"),
        cand(2024, 2, E24, "Eleições Municipais 2024", "27/10/2024", "CE", "13897", "FORTALEZA", 12, "60003", "13", "GABRIELLA VICE", "GABRIELLA", "PT", "01/01/1980", "ELEITO"),
        cand(2024, 1, E24, "Eleições Municipais 2024", D24, "CE", "13897", "FORTALEZA", 13, "60009", "13123", "VEREADOR X", "X", "PT", "01/01/1990", "ELEITO"),
        # Acrelândia: zero à esquerda
        cand(2024, 1, E24, "Eleições Municipais 2024", D24, "AC", "01120", "ACRELÂNDIA", 11, "10001", "55", "PREFEITA ACRE", "PREFEITA", "PSD", "02/02/1980", "ELEITO"),
        # Cametá: ordinária (depois cassada)
        cand(2024, 1, E24, "Eleições Municipais 2024", D24, "PA", "04413", "CAMETÁ", 11, "20001", "15", "PRIMEIRO ELEITO", "PRIMEIRO", "MDB", "03/03/1965", "ELEITO"),
        # Boa Esperança do Norte: fora do de-para
        cand(2024, 1, E24, "Eleições Municipais 2024", D24, "MT", "90999", "BOA ESPERANÇA DO NORTE", 11, "30001", "44", "PREFEITO NOVO", "NOVO", "UNIÃO", "04/04/1972", "ELEITO"),
        # UE inexistente
        cand(2024, 1, E24, "Eleições Municipais 2024", D24, "SP", "88888", "CIDADE FANTASMA", 11, "40001", "10", "FANTASMA", "FANTASMA", "REP", "05/05/1960", "ELEITO"),
    ]
    escrever_zip(tmp / "consulta_cand_2024.zip", "consulta_cand_2024_BRASIL.csv", CAB_CAND, linhas24)
    sup = [cand(2025, 1, "2049252025", "Eleição Suplementar Cametá", "01/06/2025", "PA", "04413", "CAMETÁ", 11, "20501", "45", "SEGUNDO ELEITO SUP", "SEGUNDO", "PSDB", "06/06/1970", "ELEITO", tipo="ELEIÇÃO SUPLEMENTAR")]
    escrever_zip(tmp / "consulta_cand_2025.zip", "consulta_cand_2025_BRASIL.csv", CAB_CAND, sup)
    hist = [
        cand(2020, 1, "426", "Eleições Municipais 2020", "15/11/2020", "CE", "13897", "FORTALEZA", 13, "70001", "13777", "EVANDRO TESTE LEAL", "EVANDRO", "PT", "15/03/1970", "ELEITO POR QP"),
        cand(2016, 1, "220", "Eleições Municipais 2016", "02/10/2016", "CE", "13897", "FORTALEZA", 13, "70002", "13777", "EVANDRO TESTE LEAL", "EVANDRO", "PT", "15/03/1970", "SUPLENTE"),
        # homônimo com outra data de nascimento NÃO pode ser ligado
        cand(2020, 1, "426", "Eleições Municipais 2020", "15/11/2020", "CE", "13897", "FORTALEZA", 13, "70003", "13888", "EVANDRO TESTE LEAL", "EVANDRO", "PT", "16/03/1970", "ELEITO"),
    ]
    escrever_zip(tmp / "consulta_cand_2020.zip", "consulta_cand_2020_BRASIL.csv", CAB_CAND, hist)

    cab_v = "ANO_ELEICAO;NR_TURNO;CD_ELEICAO;SG_UF;SG_UE;NM_UE;NR_ZONA;CD_CARGO;SQ_CANDIDATO;QT_VOTOS_NOMINAIS;QT_VOTOS_NOMINAIS_VALIDOS".split(";")
    v = lambda t, ue, z, cargo, sq, n: {"ANO_ELEICAO": "2024", "NR_TURNO": str(t), "CD_ELEICAO": E24, "SG_UF": "CE", "SG_UE": ue, "NM_UE": "X", "NR_ZONA": z, "CD_CARGO": str(cargo), "SQ_CANDIDATO": sq, "QT_VOTOS_NOMINAIS": str(n), "QT_VOTOS_NOMINAIS_VALIDOS": str(n)}
    votos = [
        v(2, "13897", "1", 11, "60001", 400000), v(2, "13897", "2", 11, "60001", 250000),
        v(2, "13897", "1", 11, "60002", 300000), v(2, "13897", "2", 11, "60002", 50000),
        v(1, "13897", "1", 11, "60001", 999),  # 1º turno não pode contaminar o 2º
        v(2, "13897", "1", 13, "60009", 777777),  # vereador ignorado
    ]
    escrever_zip(tmp / "votacao_candidato_munzona_2024.zip", "votacao_candidato_munzona_2024_BRASIL.csv", cab_v, votos)

    cab_b = "ANO_ELEICAO;SQ_CANDIDATO;NR_ORDEM_BEM_CANDIDATO;DS_TIPO_BEM_CANDIDATO;VR_BEM_CANDIDATO".split(";")
    bens = [
        {"ANO_ELEICAO": "2024", "SQ_CANDIDATO": "60001", "NR_ORDEM_BEM_CANDIDATO": "1", "DS_TIPO_BEM_CANDIDATO": "Casa", "VR_BEM_CANDIDATO": "350000,00"},
        {"ANO_ELEICAO": "2024", "SQ_CANDIDATO": "60001", "NR_ORDEM_BEM_CANDIDATO": "2", "DS_TIPO_BEM_CANDIDATO": "Carro", "VR_BEM_CANDIDATO": "1.500,50"},
        {"ANO_ELEICAO": "2024", "SQ_CANDIDATO": "60009", "NR_ORDEM_BEM_CANDIDATO": "1", "DS_TIPO_BEM_CANDIDATO": "Casa", "VR_BEM_CANDIDATO": "9,99"},
    ]
    escrever_zip(tmp / "bem_candidato_2024.zip", "bem_candidato_2024_BRASIL.csv", cab_b, bens)

    ibge_fixture = [
        {"id": 2304400, "nome": "Fortaleza", "microrregiao": {"mesorregiao": {"UF": {"sigla": "CE"}}}},
        {"id": 1200013, "nome": "Acrelândia", "microrregiao": {"mesorregiao": {"UF": {"sigla": "AC"}}}},
        {"id": 1502103, "nome": "Cametá", "microrregiao": {"mesorregiao": {"UF": {"sigla": "PA"}}}},
        # município novo: microrregiao nula (caso real que quebra muitos sistemas)
        {"id": 5101837, "nome": "Boa Esperança do Norte", "microrregiao": None,
         "regiao-imediata": {"regiao-intermediaria": {"UF": {"sigla": "MT"}}}},
        {"id": 3550308, "nome": "São Paulo", "microrregiao": {"mesorregiao": {"UF": {"sigla": "SP"}}}},
        {"id": 5300108, "nome": "Brasília", "microrregiao": {"mesorregiao": {"UF": {"sigla": "DF"}}}},
    ]
    (tmp / "ibge.json").write_text(json.dumps(ibge_fixture), encoding="utf-8")

    out = tmp / "out"
    rc = etl.main([
        "--cand", str(tmp / "consulta_cand_2024.zip"), str(tmp / "consulta_cand_2025.zip"),
        "--votos", str(tmp / "votacao_candidato_munzona_2024.zip"),
        "--bens", str(tmp / "bem_candidato_2024.zip"),
        "--historico", str(tmp / "consulta_cand_2020.zip"),
        "--ibge", str(tmp / "ibge.json"), "--out", str(out),
    ])
    assert rc == 0

    ce = json.loads((out / "ce.json").read_text(encoding="utf-8"))
    f = ce["municipios"]["2304400"]
    assert f["prefeito"]["nome_urna"] == "EVANDRO", f
    assert f["eleicao"]["turno"] == 2 and not f["eleicao"]["suplementar"]
    assert f["prefeito"]["votos"] == 650000, f["prefeito"]["votos"]
    assert f["prefeito"]["votos_pct"] == 65.0, f["prefeito"]["votos_pct"]
    assert f["prefeito"]["bens_total"] == 351500.50 and f["prefeito"]["bens_qtd"] == 2
    assert f["vice"]["nome_urna"] == "GABRIELLA"
    traj = f["trajetoria"]
    assert [t["ano"] for t in traj] == [2020, 2016], traj
    assert traj[0]["resultado"] == "ELEITO POR QP"

    ac = json.loads((out / "ac.json").read_text(encoding="utf-8"))
    assert "1200013" in ac["municipios"]

    pa = json.loads((out / "pa.json").read_text(encoding="utf-8"))["municipios"]["1502103"]
    assert pa["prefeito"]["nome_urna"] == "SEGUNDO" and pa["eleicao"]["suplementar"] is True
    assert pa["eleicoes_anteriores_no_mandato"][0]["nome_urna"] == "PRIMEIRO"

    mt = json.loads((out / "mt.json").read_text(encoding="utf-8"))
    assert "5101837" in mt["municipios"]

    aud = json.loads((out / "_auditoria.json").read_text(encoding="utf-8"))
    assert [x["nome"] for x in aud["via_nome"]] == ["BOA ESPERANÇA DO NORTE"]
    assert [x["sg_ue"] for x in aud["nao_resolvidos"]] == ["88888"]
    assert [x["ibge"] for x in aud["municipios_sem_eleito_nos_arquivos"]] == ["3550308"]  # DF excluído

    bruto = "".join(p.read_text(encoding="utf-8") for p in out.glob("*.json"))
    for proibido in ("SENSIVEL", "segredo@", "-4\"", "NR_TITULO"):
        assert proibido not in bruto, f"vazamento: {proibido}"

    # layout quebrado precisa falhar explicitamente
    escrever_zip(tmp / "quebrado.zip", "consulta_cand_2024_BRASIL.csv", ["ANO_ELEICAO", "SG_UF"], [{"ANO_ELEICAO": "2024", "SG_UF": "CE"}])
    try:
        etl.main(["--cand", str(tmp / "quebrado.zip"), "--ibge", str(tmp / "ibge.json"), "--out", str(tmp / "o2")])
        raise AssertionError("layout quebrado deveria falhar")
    except etl.LayoutError as e:
        assert "colunas ausentes" in str(e)

    print("TODOS OS TESTES DO ETL PASSARAM")


if __name__ == "__main__":
    main()
