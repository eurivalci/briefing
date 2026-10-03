#!/usr/bin/env python3
"""
Briefing Municipal — ETL de prefeitos (TSE -> JSON por UF, chave = código IBGE).

Entrada (arquivos do Portal de Dados Abertos do TSE, ZIP ou CSV):
  --cand      consulta_cand_AAAA.zip   (obrigatório; aceita vários: ordinária + suplementares)
  --votos     votacao_candidato_munzona_AAAA.zip   (opcional; aceita vários)
  --bens      bem_candidato_AAAA.zip   (opcional; aceita vários)
  --historico consulta_cand_AAAA.zip de outros anos (opcional; trajetória eleitoral)

Junção IBGE<->TSE:
  1. tabela de-para (etl/municipios_brasileiros_tse.csv);
  2. fallback por nome normalizado + UF contra a lista oficial do IBGE
     (cobre municípios novos, como Boa Esperança do Norte/MT, ausente do de-para).

Saída:
  data/prefeitos/<uf>.json  — um arquivo por UF
  data/prefeitos/_indice.json — metadados e cobertura
  data/prefeitos/_auditoria.json — tudo que não casou ou ficou ambíguo

Princípios:
  * leitura em streaming (os arquivos de votação passam de 1 GB descompactados);
  * colunas resolvidas pelo nome do cabeçalho, nunca por posição;
  * falha explícita se faltar coluna obrigatória (o layout do TSE muda entre anos);
  * dados sensíveis (cor/raça, e-mail, CPF, título) nunca são exportados.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import sys
import unicodedata
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

csv.field_size_limit(10_000_000)

CARGO_PREFEITO = 11
CARGO_VICE = 12
NULOS = {"", "#NULO#", "#NE#", "#NULO", "-1", "-3", "-4"}
IBGE_MUNICIPIOS_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"
DEPARA_PADRAO = Path(__file__).with_name("municipios_brasileiros_tse.csv")


def ler_json_http(resp) -> object:
    """Lê JSON de uma resposta HTTP, descompactando gzip quando vier comprimido.

    A API do IBGE responde SEMPRE em gzip, mesmo sem o cabeçalho Accept-Encoding,
    e o urllib não descompacta sozinho (o erro aparece como byte 0x8b na posição 1).
    """
    bruto = resp.read()
    if (resp.headers.get("Content-Encoding") or "").lower() == "gzip" or bruto[:2] == b"\x1f\x8b":
        bruto = gzip.decompress(bruto)
    return json.loads(bruto.decode("utf-8"))


class LayoutError(RuntimeError):
    """Coluna obrigatória ausente: o layout do arquivo do TSE mudou."""


# --------------------------------------------------------------------------- utilidades

def norm_nome(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", s).upper()
    return re.sub(r"\s+", " ", s).strip()


def limpo(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    return None if v.upper() in NULOS else v


def to_int(v: str | None) -> int | None:
    v = limpo(v)
    if v is None:
        return None
    try:
        return int(float(v.replace(",", ".")))
    except ValueError:
        return None


def to_money(v: str | None) -> float | None:
    v = limpo(v)
    if v is None:
        return None
    if "," in v:
        v = v.replace(".", "").replace(",", ".")
    try:
        return round(float(v), 2)
    except ValueError:
        return None


def to_iso_date(v: str | None) -> str | None:
    v = limpo(v)
    if not v:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def iter_csv_rows(path: Path, prefixo: str):
    """Itera linhas (dict com chaves em MAIÚSCULAS) de um ZIP ou CSV do TSE.

    Em ZIPs com arquivos por UF e um consolidado *_BRASIL.csv, usa só o consolidado
    para não duplicar linhas. Sem consolidado, usa todos os CSV por UF.
    """
    def ler(fh, nome):
        texto = io.TextIOWrapper(fh, encoding="latin-1", newline="")
        leitor = csv.reader(texto, delimiter=";", quotechar='"')
        try:
            cab = [c.strip().upper() for c in next(leitor)]
        except StopIteration:
            return
        for linha in leitor:
            if len(linha) != len(cab):
                continue
            yield dict(zip(cab, linha)), nome

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            csvs = [n for n in z.namelist() if n.lower().endswith(".csv") and Path(n).name.lower().startswith(prefixo)]
            brasil = [n for n in csvs if "_brasil" in n.lower()]
            alvo = brasil or csvs
            if not alvo:
                raise FileNotFoundError(f"{path.name}: nenhum CSV '{prefixo}*' dentro do ZIP")
            for nome in sorted(alvo):
                with z.open(nome) as fh:
                    yield from ler(fh, nome)
    else:
        with open(path, "rb") as fh:
            yield from ler(fh, path.name)


def exigir(linha: dict, colunas: list[str], arquivo: str):
    faltando = [c for c in colunas if c not in linha]
    if faltando:
        raise LayoutError(f"{arquivo}: colunas ausentes {faltando}. Confira o leiame.pdf do TSE para o ano.")


# --------------------------------------------------------------------------- de-para

def carregar_depara(path: Path) -> dict[int, dict]:
    if not path.exists():
        raise FileNotFoundError(f"Tabela de-para não encontrada: {path}")
    out = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            out[int(r["codigo_tse"])] = {"ibge": r["codigo_ibge"], "uf": r["uf"], "nome": r["nome_municipio"]}
    return out


def carregar_ibge(fonte: str | None) -> list[dict]:
    """Lista oficial de municípios do IBGE (arquivo local ou API)."""
    try:
        if fonte and Path(fonte).exists():
            dados = json.loads(Path(fonte).read_text(encoding="utf-8"))
        else:
            req = urllib.request.Request(IBGE_MUNICIPIOS_URL, headers={"User-Agent": "briefing-municipal-etl",
                                                                       "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                dados = ler_json_http(resp)
    except Exception as exc:  # noqa: BLE001 - fallback é opcional, registramos e seguimos
        print(f"[aviso] lista do IBGE indisponível ({exc}); fallback por nome desativado", file=sys.stderr)
        return []
    out = []
    for m in dados:
        uf = (
            ((m.get("microrregiao") or {}).get("mesorregiao") or {}).get("UF", {}).get("sigla")
            or (((m.get("regiao-imediata") or {}).get("regiao-intermediaria") or {}).get("UF") or {}).get("sigla")
        )
        if uf:
            out.append({"ibge": str(m["id"]), "uf": uf, "nome": m["nome"]})
    return out


class Resolvedor:
    def __init__(self, depara: dict[int, dict], ibge: list[dict]):
        self.depara = depara
        self.por_nome = defaultdict(list)
        for m in ibge:
            self.por_nome[(m["uf"], norm_nome(m["nome"]))].append(m["ibge"])
        self.auditoria = {"via_depara": 0, "via_nome": [], "nao_resolvidos": []}
        self._cache = {}

    def resolver(self, sg_ue: str, uf: str, nome_ue: str) -> str | None:
        chave = (sg_ue, uf)
        if chave in self._cache:
            return self._cache[chave]
        ibge = None
        try:
            cod = int(sg_ue)
        except (TypeError, ValueError):
            cod = None
        if cod is not None and cod in self.depara and self.depara[cod]["uf"] == uf:
            ibge = self.depara[cod]["ibge"]
            self.auditoria["via_depara"] += 1
        else:
            candidatos = self.por_nome.get((uf, norm_nome(nome_ue)), [])
            if len(candidatos) == 1:
                ibge = candidatos[0]
                self.auditoria["via_nome"].append({"sg_ue": sg_ue, "uf": uf, "nome": nome_ue, "ibge": ibge})
            else:
                self.auditoria["nao_resolvidos"].append(
                    {"sg_ue": sg_ue, "uf": uf, "nome": nome_ue, "candidatos_ibge": candidatos}
                )
        self._cache[chave] = ibge
        return ibge


# --------------------------------------------------------------------------- candidaturas

COLS_CAND = [
    "ANO_ELEICAO", "NR_TURNO", "CD_ELEICAO", "DS_ELEICAO", "DT_ELEICAO", "SG_UF", "SG_UE", "NM_UE",
    "CD_CARGO", "SQ_CANDIDATO", "NR_CANDIDATO", "NM_CANDIDATO", "NM_URNA_CANDIDATO",
    "SG_PARTIDO", "NM_PARTIDO", "DT_NASCIMENTO", "DS_SIT_TOT_TURNO",
]


def ler_candidaturas(arquivos: list[Path]):
    """Retorna (prefeitos_eleitos, vices) — listas de dicts já normalizados."""
    eleitos, vices = [], []
    for arq in arquivos:
        primeira = True
        for linha, nome in iter_csv_rows(arq, "consulta_cand"):
            if primeira:
                exigir(linha, COLS_CAND, nome)
                primeira = False
            cargo = to_int(linha.get("CD_CARGO"))
            if cargo not in (CARGO_PREFEITO, CARGO_VICE):
                continue
            reg = {
                "ano": to_int(linha["ANO_ELEICAO"]),
                "turno": to_int(linha["NR_TURNO"]) or 1,
                "cd_eleicao": limpo(linha["CD_ELEICAO"]),
                "ds_eleicao": limpo(linha["DS_ELEICAO"]),
                "tipo_eleicao": limpo(linha.get("NM_TIPO_ELEICAO")),
                "dt_eleicao": to_iso_date(linha["DT_ELEICAO"]),
                "uf": limpo(linha["SG_UF"]),
                "sg_ue": limpo(linha["SG_UE"]),
                "nm_ue": limpo(linha["NM_UE"]),
                "cargo": cargo,
                "sq": limpo(linha["SQ_CANDIDATO"]),
                "numero": limpo(linha["NR_CANDIDATO"]),
                "nome": limpo(linha["NM_CANDIDATO"]),
                "nome_urna": limpo(linha["NM_URNA_CANDIDATO"]),
                "partido": limpo(linha["SG_PARTIDO"]),
                "partido_nome": limpo(linha["NM_PARTIDO"]),
                "federacao": limpo(linha.get("SG_FEDERACAO")) or limpo(linha.get("NM_FEDERACAO")),
                "coligacao": limpo(linha.get("NM_COLIGACAO")),
                "nascimento": to_iso_date(linha["DT_NASCIMENTO"]),
                "genero": limpo(linha.get("DS_GENERO")),
                "instrucao": limpo(linha.get("DS_GRAU_INSTRUCAO")),
                "ocupacao": limpo(linha.get("DS_OCUPACAO")),
                "situacao_candidatura": limpo(linha.get("DS_SITUACAO_CANDIDATURA")),
                "sit_turno": (limpo(linha["DS_SIT_TOT_TURNO"]) or "").upper(),
            }
            if cargo == CARGO_PREFEITO and reg["sit_turno"] == "ELEITO":
                eleitos.append(reg)
            elif cargo == CARGO_VICE:
                vices.append(reg)
    return eleitos, vices


def escolher_vigente(regs: list[dict]) -> tuple[dict, list[dict]]:
    """Eleição mais recente vence (suplementar substitui ordinária); empate -> maior turno."""
    ordenados = sorted(regs, key=lambda r: (r["dt_eleicao"] or "", r["turno"]), reverse=True)
    vigente = ordenados[0]
    anteriores = [r for r in ordenados[1:] if r["cd_eleicao"] != vigente["cd_eleicao"]]
    return vigente, anteriores


def eh_suplementar(reg: dict) -> bool:
    txt = f"{reg.get('tipo_eleicao') or ''} {reg.get('ds_eleicao') or ''}".upper()
    return "SUPLEMENTAR" in txt or "NOVA ELEI" in txt


# --------------------------------------------------------------------------- votos e bens

def ler_votos(arquivos: list[Path], alvo_eleicoes: set[str]):
    """Soma votos nominais válidos por candidato e por (eleição, UE, turno) para prefeito."""
    por_cand = defaultdict(int)
    total_ue = defaultdict(int)
    for arq in arquivos:
        primeira = True
        col_votos = None
        for linha, nome in iter_csv_rows(arq, "votacao_candidato_munzona"):
            if primeira:
                exigir(linha, ["CD_CARGO", "SQ_CANDIDATO", "NR_TURNO", "SG_UE", "CD_ELEICAO"], nome)
                col_votos = "QT_VOTOS_NOMINAIS_VALIDOS" if "QT_VOTOS_NOMINAIS_VALIDOS" in linha else "QT_VOTOS_NOMINAIS"
                exigir(linha, [col_votos], nome)
                primeira = False
            if to_int(linha["CD_CARGO"]) != CARGO_PREFEITO:
                continue
            cd = limpo(linha["CD_ELEICAO"])
            if alvo_eleicoes and cd not in alvo_eleicoes:
                continue
            v = to_int(linha[col_votos]) or 0
            turno = to_int(linha["NR_TURNO"]) or 1
            por_cand[(limpo(linha["SQ_CANDIDATO"]), turno)] += v
            total_ue[(cd, limpo(linha["SG_UE"]), turno)] += v
    return por_cand, total_ue


def ler_bens(arquivos: list[Path], alvo_sq: set[str]):
    soma = defaultdict(float)
    qtd = defaultdict(int)
    for arq in arquivos:
        primeira = True
        for linha, nome in iter_csv_rows(arq, "bem_candidato"):
            if primeira:
                exigir(linha, ["SQ_CANDIDATO", "VR_BEM_CANDIDATO"], nome)
                primeira = False
            sq = limpo(linha["SQ_CANDIDATO"])
            if sq not in alvo_sq:
                continue
            v = to_money(linha["VR_BEM_CANDIDATO"])
            if v is not None:
                soma[sq] += v
                qtd[sq] += 1
    return soma, qtd


# --------------------------------------------------------------------------- trajetória

def chave_pessoa(nome: str | None, nascimento: str | None) -> str | None:
    if not nome or not nascimento:
        return None
    return f"{norm_nome(nome)}|{nascimento}"


def ler_historico(arquivos: list[Path], pessoas: set[str]):
    """Trajetória por nome completo + data de nascimento (o TSE oculta CPF desde 2024)."""
    hist = defaultdict(dict)
    for arq in arquivos:
        primeira = True
        for linha, nome in iter_csv_rows(arq, "consulta_cand"):
            if primeira:
                exigir(linha, ["ANO_ELEICAO", "NM_CANDIDATO", "DT_NASCIMENTO", "DS_CARGO", "DS_SIT_TOT_TURNO"], nome)
                primeira = False
            k = chave_pessoa(limpo(linha["NM_CANDIDATO"]), to_iso_date(linha["DT_NASCIMENTO"]))
            if k not in pessoas:
                continue
            ano = to_int(linha["ANO_ELEICAO"])
            cargo = (limpo(linha["DS_CARGO"]) or "").title()
            item_k = (ano, cargo, limpo(linha.get("NM_UE")) or limpo(linha.get("SG_UF")))
            sit = (limpo(linha["DS_SIT_TOT_TURNO"]) or "").upper()
            atual = hist[k].get(item_k)
            # mantém o resultado mais informativo entre turnos (ELEITO > 2º TURNO > outros)
            peso = {"ELEITO": 3, "ELEITO POR QP": 3, "ELEITO POR MÉDIA": 3, "SUPLENTE": 2, "2º TURNO": 1}.get(sit, 0)
            if atual is None or peso > atual["_peso"]:
                hist[k][item_k] = {
                    "ano": ano, "cargo": cargo, "local": item_k[2], "uf": limpo(linha.get("SG_UF")),
                    "partido": limpo(linha.get("SG_PARTIDO")), "resultado": sit or None, "_peso": peso,
                }
    return {
        k: sorted(
            ({kk: vv for kk, vv in it.items() if kk != "_peso"} for it in v.values()),
            key=lambda x: (x["ano"] or 0), reverse=True,
        )
        for k, v in hist.items()
    }


# --------------------------------------------------------------------------- montagem

def montar(args) -> dict:
    depara = carregar_depara(Path(args.depara))
    ibge = carregar_ibge(args.ibge)
    resolvedor = Resolvedor(depara, ibge)

    eleitos, vices = ler_candidaturas([Path(p) for p in args.cand])
    if not eleitos:
        raise SystemExit("Nenhum prefeito com DS_SIT_TOT_TURNO=ELEITO encontrado nos arquivos de candidatura.")

    por_mun = defaultdict(list)
    for r in eleitos:
        ibge_cod = resolvedor.resolver(r["sg_ue"], r["uf"], r["nm_ue"])
        if ibge_cod:
            por_mun[ibge_cod].append(r)

    vices_idx = defaultdict(list)
    for v in vices:
        vices_idx[(v["cd_eleicao"], v["sg_ue"], v["numero"])].append(v)

    vigentes = {}
    for ibge_cod, regs in por_mun.items():
        vig, anteriores = escolher_vigente(regs)
        vigentes[ibge_cod] = (vig, anteriores)

    alvo_eleicoes = {v[0]["cd_eleicao"] for v in vigentes.values() if v[0]["cd_eleicao"]}
    por_cand, total_ue = ler_votos([Path(p) for p in args.votos or []], alvo_eleicoes) if args.votos else ({}, {})
    alvo_sq = {v[0]["sq"] for v in vigentes.values() if v[0]["sq"]}
    bens_soma, bens_qtd = ler_bens([Path(p) for p in args.bens or []], alvo_sq) if args.bens else ({}, {})

    pessoas = {chave_pessoa(v[0]["nome"], v[0]["nascimento"]) for v in vigentes.values()} - {None}
    historico = ler_historico([Path(p) for p in args.historico or []], pessoas) if args.historico else {}

    saida = defaultdict(dict)
    for ibge_cod, (vig, anteriores) in vigentes.items():
        votos = pct = None
        if por_cand:
            votos = por_cand.get((vig["sq"], vig["turno"]))
            tot = total_ue.get((vig["cd_eleicao"], vig["sg_ue"], vig["turno"]))
            if votos is not None and tot:
                pct = round(100 * votos / tot, 2)
        vice = None
        cands_vice = vices_idx.get((vig["cd_eleicao"], vig["sg_ue"], vig["numero"]), [])
        if cands_vice:
            v = sorted(cands_vice, key=lambda x: (x["sit_turno"] == "ELEITO", x["turno"]), reverse=True)[0]
            vice = {"nome": v["nome"], "nome_urna": v["nome_urna"], "partido": v["partido"]}
        k = chave_pessoa(vig["nome"], vig["nascimento"])
        reg = {
            "tse": {"sg_ue": vig["sg_ue"], "nm_ue": vig["nm_ue"]},
            "eleicao": {
                "ano": vig["ano"], "cd_eleicao": vig["cd_eleicao"], "descricao": vig["ds_eleicao"],
                "data": vig["dt_eleicao"], "turno": vig["turno"], "suplementar": eh_suplementar(vig),
            },
            "prefeito": {
                "sq": vig["sq"], "nome": vig["nome"], "nome_urna": vig["nome_urna"], "numero": vig["numero"],
                "partido": vig["partido"], "partido_nome": vig["partido_nome"], "federacao": vig["federacao"],
                "coligacao": vig["coligacao"], "nascimento": vig["nascimento"], "genero": vig["genero"],
                "instrucao": vig["instrucao"], "ocupacao": vig["ocupacao"],
                "votos": votos, "votos_pct": pct,
                "bens_total": round(bens_soma[vig["sq"]], 2) if vig["sq"] in bens_soma else None,
                "bens_qtd": bens_qtd.get(vig["sq"]),
            },
            "vice": vice,
            "eleicoes_anteriores_no_mandato": [
                {"cd_eleicao": a["cd_eleicao"], "data": a["dt_eleicao"], "nome_urna": a["nome_urna"], "partido": a["partido"]}
                for a in anteriores
            ],
            "trajetoria": historico.get(k, []),
        }
        saida[vig["uf"]][ibge_cod] = reg

    sem_eleito = []
    if ibge:
        cobertos = set(vigentes)
        sem_eleito = [m for m in ibge if m["ibge"] not in cobertos and m["uf"] != "DF"]

    return {
        "por_uf": saida,
        "auditoria": {
            **resolvedor.auditoria,
            "municipios_sem_eleito_nos_arquivos": sem_eleito,
            "total_municipios_com_prefeito": len(vigentes),
            "votos_carregados": bool(por_cand),
            "bens_carregados": bool(bens_soma),
            "historico_carregado": bool(historico),
        },
    }


def gravar(resultado: dict, destino: Path, fontes: dict):
    destino.mkdir(parents=True, exist_ok=True)
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    indice = {"gerado_em": agora, "fontes": fontes, "ufs": {}}
    for uf, muns in sorted(resultado["por_uf"].items()):
        doc = {"uf": uf, "gerado_em": agora, "fontes": fontes, "municipios": dict(sorted(muns.items()))}
        (destino / f"{uf.lower()}.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        indice["ufs"][uf] = len(muns)
    (destino / "_indice.json").write_text(json.dumps(indice, ensure_ascii=False, indent=1), encoding="utf-8")
    (destino / "_auditoria.json").write_text(json.dumps(resultado["auditoria"], ensure_ascii=False, indent=1), encoding="utf-8")
    return indice


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cand", nargs="+", required=True)
    p.add_argument("--votos", nargs="*")
    p.add_argument("--bens", nargs="*")
    p.add_argument("--historico", nargs="*")
    p.add_argument("--depara", default=str(DEPARA_PADRAO))
    p.add_argument("--ibge", help="JSON local da API de municípios do IBGE (senão baixa da API)")
    p.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "data" / "prefeitos"))
    args = p.parse_args(argv)

    fontes = {
        "candidaturas": [Path(x).name for x in args.cand],
        "votos": [Path(x).name for x in args.votos or []],
        "bens": [Path(x).name for x in args.bens or []],
        "historico": [Path(x).name for x in args.historico or []],
        "orgao": "Tribunal Superior Eleitoral — Portal de Dados Abertos",
    }
    resultado = montar(args)
    indice = gravar(resultado, Path(args.out), fontes)
    a = resultado["auditoria"]
    print(f"OK: {a['total_municipios_com_prefeito']} municípios em {len(indice['ufs'])} UFs -> {args.out}")
    print(f"    de-para: {a['via_depara']} | por nome: {len(a['via_nome'])} | não resolvidos: {len(a['nao_resolvidos'])}"
          f" | sem eleito: {len(a['municipios_sem_eleito_nos_arquivos'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
