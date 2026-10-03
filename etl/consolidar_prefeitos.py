#!/usr/bin/env python3
"""
Briefing Municipal — consolidado nacional de prefeitos para consumo externo.

Lê data/prefeitos/<uf>.json e gera, no mesmo diretório:
  todos.json  {"schema": 1, "gerado_em", "fonte", "total", "colunas", "registros": [...]}
  todos.csv   separador ';', UTF-8 com BOM (abre direto no Excel em pt-BR)

O esquema é plano e estável (versão em "schema"): colunas novas podem ser acrescentadas
ao final; renomear ou remover exige subir a versão. Pensado para ser lido por outros
sistemas via URL fixa: /data/prefeitos/todos.json e /data/prefeitos/todos.csv.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import sys
import unicodedata
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SCHEMA = 1
IBGE_MUNICIPIOS_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"
REGIAO = {
    "AC": "Norte", "AM": "Norte", "AP": "Norte", "PA": "Norte", "RO": "Norte", "RR": "Norte", "TO": "Norte",
    "AL": "Nordeste", "BA": "Nordeste", "CE": "Nordeste", "MA": "Nordeste", "PB": "Nordeste", "PE": "Nordeste",
    "PI": "Nordeste", "RN": "Nordeste", "SE": "Nordeste",
    "DF": "Centro-Oeste", "GO": "Centro-Oeste", "MS": "Centro-Oeste", "MT": "Centro-Oeste",
    "ES": "Sudeste", "MG": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
    "PR": "Sul", "RS": "Sul", "SC": "Sul",
}
COLUNAS = [
    "codigo_ibge", "uf", "regiao", "municipio", "prefeito", "nome_completo", "partido", "partido_nome",
    "federacao", "genero", "nascimento", "idade", "instrucao", "ocupacao", "eleicao_data", "eleicao_ano",
    "turno", "suplementar", "votos", "votos_pct", "bens_declarados", "vice", "vice_partido",
    "foto", "briefing", "codigo_tse",
    # schema 1, acréscimo compatível (out/2026): dados do município e reconciliação do mandato
    "tem_prefeito_tse", "regiao_intermediaria", "regiao_imediata", "gentilico",
    "populacao", "populacao_ano", "area_km2", "densidade", "pib_per_capita", "pib_per_capita_ano",
    "idhm", "idhm_ano", "salario_medio_sm", "salario_medio_sm_ano", "escolarizacao_6_14", "escolarizacao_6_14_ano",
    "mortalidade_infantil", "mortalidade_infantil_ano",
    "prefeito_ibge", "prefeito_ibge_ano", "prefeito_wikidata", "prefeito_wikidata_desde", "situacao_mandato",
    "site_oficial", "wikidata", "wikipedia",
]
CAMPOS_MUNICIPIO = [
    "regiao_intermediaria", "regiao_imediata", "gentilico", "populacao", "populacao_ano", "area_km2", "densidade",
    "pib_per_capita", "pib_per_capita_ano", "idhm", "idhm_ano", "salario_medio_sm", "salario_medio_sm_ano",
    "escolarizacao_6_14", "escolarizacao_6_14_ano", "mortalidade_infantil", "mortalidade_infantil_ano",
    "prefeito_ibge", "prefeito_ibge_ano", "prefeito_wikidata", "prefeito_wikidata_desde",
    "site_oficial", "wikidata", "wikipedia",
]
INTEIROS = {"populacao", "populacao_ano", "pib_per_capita_ano", "idhm_ano", "salario_medio_sm_ano",
            "escolarizacao_6_14_ano", "mortalidade_infantil_ano", "prefeito_ibge_ano"}
PARTICULAS = {"da", "de", "do", "das", "dos", "e"}


def ler_json_http(resp) -> object:
    """Lê JSON de uma resposta HTTP, descompactando gzip quando vier comprimido.

    A API do IBGE responde SEMPRE em gzip, mesmo sem o cabeçalho Accept-Encoding,
    e o urllib não descompacta sozinho (o erro aparece como byte 0x8b na posição 1).
    """
    bruto = resp.read()
    if (resp.headers.get("Content-Encoding") or "").lower() == "gzip" or bruto[:2] == b"\x1f\x8b":
        bruto = gzip.decompress(bruto)
    return json.loads(bruto.decode("utf-8"))


def _tokens(s: str | None) -> list[str]:
    n = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()
    n = "".join(ch if ch.isalnum() else " " for ch in n)
    return [t for t in n.split() if t not in {"DA", "DE", "DO", "DAS", "DOS", "E", "DI", "DU"}]


def nomes_compativeis(a: str | None, b: str | None) -> bool:
    A, B = _tokens(a), _tokens(b)
    if not A or not B:
        return False
    comum = len([t for t in A if t in set(B)])
    menor = min(len(A), len(B))
    if menor == 1:
        return comum == 1 and (B.count(A[0]) > 0 if len(A) == 1 else A.count(B[0]) > 0)
    return comum >= 2 or comum == menor


def inicio_mandato(eleicao: dict) -> int | None:
    """Ordinária de 2024 -> mandato desde 2025; suplementar -> desde o próprio ano."""
    ano = eleicao.get("ano")
    if not ano:
        return None
    return int(ano) if eleicao.get("suplementar") else int(ano) + 1


def ibge_vigente(nome: str | None, ano, eleicao: dict) -> str | None:
    """Prefeito do painel do IBGE só conta se a referência for do mandato vigente."""
    ini = inicio_mandato(eleicao)
    try:
        return nome if (nome and (ini is None or ano is None or int(ano) >= ini)) else None
    except (TypeError, ValueError):
        return nome


def situacao_mandato(nomes_tse: list, ibge: str | None, wd: str | None) -> str:
    """consistente | divergente | parcial | sem_dados — mesma leitura do painel do briefing.
    Quem chama já descartou o valor do IBGE anterior ao mandato (ibge_vigente)."""
    nomes_tse = [n for n in nomes_tse if n]
    outras = [x for x in (ibge, wd) if x]
    if not nomes_tse:
        return "parcial" if outras else "sem_dados"
    if not outras:
        return "parcial"
    confere = [any(nomes_compativeis(o, n) for n in nomes_tse) for o in outras]
    return "consistente" if all(confere) else "divergente"


def titulo(s: str | None) -> str:
    if not s:
        return ""
    out = []
    for i, w in enumerate(s.lower().split()):
        out.append(w if (i and w in PARTICULAS) else "-".join(p[:1].upper() + p[1:] for p in w.split("-")))
    return " ".join(out)


def idade(nasc: str | None, hoje: date) -> int | None:
    try:
        n = date.fromisoformat(nasc)
    except (TypeError, ValueError):
        return None
    return hoje.year - n.year - ((hoje.month, hoje.day) < (n.month, n.day))


def nomes_ibge(fonte: str | None) -> dict[str, str]:
    """Nomes oficiais com acentuação correta (o TSE publica em maiúsculas)."""
    try:
        if fonte and Path(fonte).exists():
            dados = json.loads(Path(fonte).read_text(encoding="utf-8"))
        else:
            req = urllib.request.Request(IBGE_MUNICIPIOS_URL,
                                         headers={"User-Agent": "briefing-municipal-etl", "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=60) as r:
                dados = ler_json_http(r)
        return {str(m["id"]): m["nome"] for m in dados}
    except Exception as exc:  # noqa: BLE001 - sem IBGE, usa o nome do TSE em caixa de título
        print(f"[aviso] nomes do IBGE indisponíveis ({exc}); usando nome do TSE", file=sys.stderr)
        return {}


def seguro_csv(v):
    """Neutraliza injeção de fórmula em planilhas (=, +, -, @, tab, CR no início)."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def carregar_municipios(caminho: Path | None) -> dict[str, dict]:
    if caminho and caminho.exists():
        return json.loads(caminho.read_text(encoding="utf-8")).get("municipios", {})
    return {}


def registros(dir_pref: Path, nomes: dict[str, str], hoje: date, muns: dict[str, dict] | None = None) -> list[dict]:
    """Uma linha por município. Sem o arquivo de municípios, só os que têm prefeito no TSE."""
    muns = muns or {}
    tse = {}
    for arq in sorted(dir_pref.glob("[a-z][a-z].json")):
        doc = json.loads(arq.read_text(encoding="utf-8"))
        for ibge, reg in doc["municipios"].items():
            tse[ibge] = (doc["uf"], reg)
    out = []
    for ibge in sorted(set(tse) | set(muns)):
        m = muns.get(ibge, {})
        uf, reg = tse.get(ibge, (m.get("uf"), None))
        reg = reg or {}
        p, e, v = reg.get("prefeito") or {}, reg.get("eleicao") or {}, reg.get("vice") or {}
        r = {
            "codigo_ibge": ibge,
            "uf": uf,
            "regiao": REGIAO.get(uf or "", "") or m.get("regiao") or "",
            "municipio": m.get("nome") or nomes.get(ibge) or titulo((reg.get("tse") or {}).get("nm_ue")),
            "prefeito": titulo(p.get("nome_urna") or p.get("nome")) or None,
            "nome_completo": titulo(p.get("nome")) or None,
            "partido": p.get("partido"),
            "partido_nome": titulo(p.get("partido_nome")) or None,
            "federacao": p.get("federacao"),
            "genero": titulo(p.get("genero")) or None,
            "nascimento": p.get("nascimento"),
            "idade": idade(p.get("nascimento"), hoje),
            "instrucao": titulo(p.get("instrucao")) or None,
            "ocupacao": titulo(p.get("ocupacao")) or None,
            "eleicao_data": e.get("data"),
            "eleicao_ano": e.get("ano"),
            "turno": e.get("turno"),
            "suplementar": bool(e.get("suplementar")) if reg else None,
            "votos": p.get("votos"),
            "votos_pct": p.get("votos_pct"),
            "bens_declarados": p.get("bens_total"),
            "vice": titulo(v.get("nome_urna") or v.get("nome")) or None,
            "vice_partido": v.get("partido"),
            "foto": p.get("foto"),
            "briefing": f"index.html#/{uf}/{ibge}" if uf else None,
            "codigo_tse": (reg.get("tse") or {}).get("sg_ue"),
            "tem_prefeito_tse": bool(reg),
        }
        for k in CAMPOS_MUNICIPIO:
            val = m.get(k)
            if k in INTEIROS and val is not None:
                try:
                    val = int(float(val))
                except (TypeError, ValueError):
                    val = None
            r[k] = val
        if r["prefeito_ibge"]:
            r["prefeito_ibge"] = titulo(r["prefeito_ibge"])
        r["situacao_mandato"] = ("nao_se_aplica" if uf == "DF" else
                                 situacao_mandato([p.get("nome"), p.get("nome_urna")],
                                                  ibge_vigente(m.get("prefeito_ibge"), m.get("prefeito_ibge_ano"), e),
                                                  m.get("prefeito_wikidata")))
        out.append(r)

    def chave(r):
        nome = unicodedata.normalize("NFKD", r["municipio"] or "").encode("ascii", "ignore").decode().lower()
        return (r["uf"] or "", nome)
    return sorted(out, key=chave)


def gravar_atomico(caminho: Path, conteudo: bytes):
    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    tmp.write_bytes(conteudo)
    tmp.replace(caminho)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prefeitos", default=str(RAIZ / "data" / "prefeitos"))
    ap.add_argument("--ibge", help="JSON local da API de municípios do IBGE (senão baixa)")
    ap.add_argument("--hoje", help="data de referência para a idade (AAAA-MM-DD); padrão: hoje")
    ap.add_argument("--municipios", default=str(RAIZ / "data" / "municipios" / "municipios.json"),
                    help="saída de enriquecer_municipios.py (opcional)")
    args = ap.parse_args(argv)

    dir_pref = Path(args.prefeitos)
    hoje = date.fromisoformat(args.hoje) if args.hoje else date.today()
    muns = carregar_municipios(Path(args.municipios))
    nomes = {} if muns else nomes_ibge(args.ibge)   # com o arquivo de municípios, o nome oficial já vem nele
    regs = registros(dir_pref, nomes, hoje, muns)
    if not regs:
        raise SystemExit("nenhum registro encontrado: rode build_prefeitos.py antes")

    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    doc = {
        "schema": SCHEMA, "gerado_em": agora, "idade_referencia": hoje.isoformat(),
        "fonte": "TSE (dados abertos) + IBGE; consolidado por Briefing Municipal — EDP Sistemas",
        "licenca": "Dados públicos do TSE e do IBGE (CC BY). Cite as fontes.",
        "total": len(regs), "com_prefeito_tse": sum(1 for r in regs if r["tem_prefeito_tse"]),
        "dados_municipio": bool(muns), "colunas": COLUNAS, "registros": regs,
    }
    gravar_atomico(dir_pref / "todos.json", json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(COLUNAS)
    for r in regs:
        linha = []
        for c in COLUNAS:
            v = r[c]
            if isinstance(v, bool):
                v = "sim" if v else "nao"
            elif isinstance(v, float):
                casas = 3 if c == "idhm" else 2
                v = f"{v:.{casas}f}".replace(".", ",")  # decimal brasileiro, como o Excel pt-BR espera
            linha.append("" if v is None else seguro_csv(v))
        w.writerow(linha)
    gravar_atomico(dir_pref / "todos.csv", ("\ufeff" + buf.getvalue()).encode("utf-8"))

    print(f"OK: {len(regs)} municípios ({doc['com_prefeito_tse']} com prefeito no TSE; dados do município: "
          f"{'sim' if muns else 'não'}) -> todos.json ({(dir_pref / 'todos.json').stat().st_size / 1e6:.2f} MB) "
          f"e todos.csv ({(dir_pref / 'todos.csv').stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
