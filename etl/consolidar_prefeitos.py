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
import io
import json
import sys
import unicodedata
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SCHEMA = 1
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
]
PARTICULAS = {"da", "de", "do", "das", "dos", "e"}


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
            req = urllib.request.Request("https://servicodados.ibge.gov.br/api/v1/localidades/municipios",
                                         headers={"User-Agent": "briefing-municipal-etl"})
            with urllib.request.urlopen(req, timeout=60) as r:
                dados = json.load(r)
        return {str(m["id"]): m["nome"] for m in dados}
    except Exception as exc:  # noqa: BLE001 - sem IBGE, usa o nome do TSE em caixa de título
        print(f"[aviso] nomes do IBGE indisponíveis ({exc}); usando nome do TSE", file=sys.stderr)
        return {}


def seguro_csv(v):
    """Neutraliza injeção de fórmula em planilhas (=, +, -, @, tab, CR no início)."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def registros(dir_pref: Path, nomes: dict[str, str], hoje: date) -> list[dict]:
    out = []
    for arq in sorted(dir_pref.glob("[a-z][a-z].json")):
        doc = json.loads(arq.read_text(encoding="utf-8"))
        uf = doc["uf"]
        for ibge, reg in doc["municipios"].items():
            p, e, v = reg.get("prefeito") or {}, reg.get("eleicao") or {}, reg.get("vice") or {}
            out.append({
                "codigo_ibge": ibge,
                "uf": uf,
                "regiao": REGIAO.get(uf, ""),
                "municipio": nomes.get(ibge) or titulo((reg.get("tse") or {}).get("nm_ue")),
                "prefeito": titulo(p.get("nome_urna") or p.get("nome")),
                "nome_completo": titulo(p.get("nome")),
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
                "suplementar": bool(e.get("suplementar")),
                "votos": p.get("votos"),
                "votos_pct": p.get("votos_pct"),
                "bens_declarados": p.get("bens_total"),
                "vice": titulo(v.get("nome_urna") or v.get("nome")) or None,
                "vice_partido": v.get("partido"),
                "foto": p.get("foto"),
                "briefing": f"index.html#/{uf}/{ibge}",
                "codigo_tse": (reg.get("tse") or {}).get("sg_ue"),
            })
    def chave(r):
        nome = unicodedata.normalize("NFKD", r["municipio"]).encode("ascii", "ignore").decode().lower()
        return (r["uf"], nome)
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
    args = ap.parse_args(argv)

    dir_pref = Path(args.prefeitos)
    hoje = date.fromisoformat(args.hoje) if args.hoje else date.today()
    regs = registros(dir_pref, nomes_ibge(args.ibge), hoje)
    if not regs:
        raise SystemExit("nenhum registro encontrado: rode build_prefeitos.py antes")

    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    doc = {
        "schema": SCHEMA, "gerado_em": agora, "idade_referencia": hoje.isoformat(),
        "fonte": "TSE (dados abertos) + IBGE; consolidado por Briefing Municipal — EDP Sistemas",
        "licenca": "Dados públicos do TSE e do IBGE (CC BY). Cite as fontes.",
        "total": len(regs), "colunas": COLUNAS, "registros": regs,
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
                v = f"{v:.2f}".replace(".", ",")  # decimal brasileiro, como o Excel pt-BR espera
            linha.append("" if v is None else seguro_csv(v))
        w.writerow(linha)
    gravar_atomico(dir_pref / "todos.csv", ("\ufeff" + buf.getvalue()).encode("utf-8"))

    print(f"OK: {len(regs)} prefeitos -> todos.json ({(dir_pref / 'todos.json').stat().st_size / 1e6:.2f} MB) "
          f"e todos.csv ({(dir_pref / 'todos.csv').stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
