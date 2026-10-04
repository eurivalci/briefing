#!/usr/bin/env python3
"""
Briefing Municipal — CAPAG dos municípios (Tesouro Nacional) para o briefing.

Fonte: conjunto "Capacidade de Pagamento de Municípios" no portal de dados do Tesouro
(CKAN). Não existe API da CAPAG: a única fonte é a planilha XLSX de cada posição.

O que faz:
  1. pergunta ao CKAN quais posições existem (package_show), sem URL fixa;
  2. baixa só as posições novas ou alteradas (cache por id + data de modificação);
  3. lê cada XLSX reconhecendo as colunas pelo CONTEÚDO — o layout não é documentado
     (o PDF de metadados está vazio) e mudou entre anos;
  4. grava data/capag/<uf>.json com a trajetória de cada município.

Licença: os dados da CAPAG são ODbL. Cada arquivo gerado leva o aviso de licença e a
atribuição ao Tesouro Nacional. Por decisão do produto, a CAPAG NÃO entra no todos.json.

Não recalcula a nota: lê a nota publicada. A metodologia mudou ao longo dos anos
(ajuste de critérios declarado pelo próprio Tesouro a partir da CAPAG 2023), então a
trajetória é apresentada como sequência de posições publicadas, não como série homogênea.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CKAN = "https://www.tesourotransparente.gov.br/ckan/api/3/action/package_show?id=capag-municipios"
UA = {"User-Agent": "BriefingMunicipal-ETL/1.0 (EDP Sistemas; dados publicos)", "Accept-Encoding": "gzip"}
NOTAS_FINAIS = {"A+", "A", "B+", "B", "C", "D"}
NAO_CALCULADA = {"N.D.", "ND", "N/D", "N.E.", "NE", "NAO CALCULADA", "NÃO CALCULADA", "-", "N.A.", "NA", "*"}
NOTAS_IND = {"A", "B", "C"}
MESES = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9, "out": 10,
         "nov": 11, "dez": 12}


class LayoutCapag(RuntimeError):
    """A planilha não tem as colunas mínimas reconhecíveis (código IBGE e nota final)."""


def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+%/ ]+", " ", s)).strip()


def ler_json_http(resp):
    bruto = resp.read()
    if (resp.headers.get("Content-Encoding") or "").lower() == "gzip" or bruto[:2] == b"\x1f\x8b":
        bruto = gzip.decompress(bruto)
    return json.loads(bruto.decode("utf-8"))


def http(url: str, timeout=120, tentativas=3, json_=True):
    ultimo = None
    for i in range(tentativas):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                if json_:
                    return ler_json_http(r)
                bruto = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    bruto = gzip.decompress(bruto)
                return bruto
        except urllib.error.HTTPError as e:
            ultimo = e
            if e.code in (400, 404):
                raise
        except Exception as e:  # noqa: BLE001
            ultimo = e
        time.sleep(3 * (i + 1))
    raise ultimo


# --------------------------------------------------------------------------- posições

def data_da_posicao(nome: str, url: str) -> tuple[str | None, int | None]:
    """(AAAA-MM-DD, ano da CAPAG). A data vem no título ('... - 01/09/2026') ou na URL."""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", nome or "")
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}", int(m.group(3))
    base = (url or "").rsplit("/", 1)[-1].lower()
    m = re.search(r"(20\d{2})-([a-z]{3})(?:-(\d{1,2}))?", base)      # posicao-2025-jun-10 / posicao-2026-set
    if m and m.group(2) in MESES:
        dia = int(m.group(3) or 1)
        return f"{m.group(1)}-{MESES[m.group(2)]:02d}-{dia:02d}", int(m.group(1))
    m = re.search(r"(20\d{2})(\d{2})(\d{2})capag", base)              # 20240618capag-municipios
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", int(m.group(1))
    m = re.search(r"(20\d{2})", nome or "")
    return None, (int(m.group(1)) if m else None)


def listar_posicoes(pacote: dict) -> list[dict]:
    """Recursos de dados utilizáveis, do mais antigo ao mais novo, sem duplicar revisões."""
    out = []
    for r in pacote.get("resources", []):
        nome, url = r.get("name") or "", r.get("url") or ""
        if "metadado" in nome.lower() or not url.lower().endswith((".xlsx", ".xls")):
            continue
        data, ano = data_da_posicao(nome, url)
        if not ano:
            continue
        out.append({"id": r.get("id"), "nome": nome.strip(), "url": url, "data": data, "ano": ano,
                    "modificado": r.get("last_modified") or r.get("created"), "tamanho": r.get("size"),
                    "revisao": "revis" in nome.lower()})
    # "2022 - revisão" substitui o arquivo original do mesmo ano sem data
    revisados = {p["ano"] for p in out if p["revisao"] and not p["data"]}
    out = [p for p in out if not (p["ano"] in revisados and not p["data"] and not p["revisao"])]
    return sorted(out, key=lambda p: (p["data"] or f"{p['ano']}-12-31", p["nome"]))


# --------------------------------------------------------------------------- leitura do XLSX

def _valor_nota(v) -> str | None:
    s = str(v or "").strip().upper().replace(" ", "")
    return s or None


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("%", "")
    if not s or s.upper() in NAO_CALCULADA:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _codigo(v, validos: set[str]) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if isinstance(v, float) and v.is_integer():
        s = str(int(v))
    s = re.sub(r"\D", "", s)
    if len(s) == 7 and (not validos or s in validos):
        return s
    if len(s) == 6 and validos:                       # planilhas antigas: código sem o dígito verificador
        cand = [c for c in validos if c[:6] == s]
        return cand[0] if len(cand) == 1 else None
    return None


def _eh_nao_calculada(x: str | None) -> bool:
    return bool(x) and (x in NAO_CALCULADA or x.replace(".", "") in {n.replace(".", "") for n in NAO_CALCULADA})


def _perfil(dados, c):
    vals = [r[c] for r in dados if r[c] not in (None, "")]
    if not vals:
        return {"n": 0}
    notas = [_valor_nota(v) for v in vals]
    return {"n": len(vals),
            "final": sum(1 for x in notas if x in NOTAS_FINAIS or _eh_nao_calculada(x)) / len(vals),
            "ind": sum(1 for x in notas if x in NOTAS_IND) / len(vals),
            "maisb": sum(1 for x in notas if x in {"A+", "B+"}),
            "temd": sum(1 for x in notas if x == "D"),
            "num": sum(1 for v in vals if _num(v) is not None) / len(vals)}


def _avaliar_aba(linhas, validos):
    """Devolve a leitura candidata da aba, ou None se ela não tiver código E nota final."""
    largura = max(len(r) for r in linhas)
    linhas = [list(r) + [None] * (largura - len(r)) for r in linhas]
    cont = [sum(1 for r in linhas if _codigo(r[c], validos)) for c in range(largura)]
    if not cont or max(cont) < 50:
        return None
    col_cod = cont.index(max(cont))
    dados = [r for r in linhas if _codigo(r[col_cod], validos)]
    primeira = next(i for i, r in enumerate(linhas) if _codigo(r[col_cod], validos))
    # cabeçalho: junta até 3 linhas acima dos dados (há planilhas com cabeçalho em duas linhas)
    cab = [" ".join(norm(linhas[j][c]) for j in range(max(0, primeira - 3), primeira) if linhas[j][c] is not None).strip()
           for c in range(largura)]
    perfis = [_perfil(dados, c) for c in range(largura)]
    finais = [c for c in range(largura) if c != col_cod and perfis[c]["n"] and perfis[c]["final"] >= 0.6]
    if not finais:
        return None
    com_capag = [c for c in finais if "capag" in cab[c] and "indicador" not in cab[c]]
    pool = com_capag or finais
    # só a nota final tem A+/B+ (desde a CAPAG 2023) e D; os indicadores vão de A a C
    col_nota = sorted(pool, key=lambda c: (perfis[c]["maisb"], perfis[c]["temd"], "oficial" in cab[c], -c), reverse=True)[0]
    return {"dados": dados, "cab": cab, "perfis": perfis, "col_cod": col_cod, "col_nota": col_nota, "largura": largura}


def ler_planilha(caminho_ou_bytes, validos: set[str]) -> tuple[dict[str, dict], dict]:
    """Retorna ({ibge: registro}, diagnóstico do layout reconhecido)."""
    from openpyxl import load_workbook  # import tardio: só quem roda o ETL precisa do pacote
    fonte = io.BytesIO(caminho_ou_bytes) if isinstance(caminho_ou_bytes, (bytes, bytearray)) else caminho_ou_bytes
    wb = load_workbook(fonte, read_only=True, data_only=True)
    melhor, abas_vistas = None, []
    for ws in wb.worksheets:
        linhas = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            linhas.append(row)
            if i > 12000:
                break
        cand = _avaliar_aba(linhas, validos) if linhas else None
        abas_vistas.append({"aba": ws.title, "com_nota": bool(cand), "linhas": len(cand["dados"]) if cand else 0})
        if cand and (melhor is None or len(cand["dados"]) > len(melhor["dados"])):
            melhor = {**cand, "aba": ws.title}
    wb.close()
    if not melhor:
        raise LayoutCapag(f"nenhuma aba com código IBGE e nota da CAPAG; abas vistas: {abas_vistas}")

    dados, cab, perfis, largura = melhor["dados"], melhor["cab"], melhor["perfis"], melhor["largura"]
    usados = {melhor["col_cod"], melhor["col_nota"]}

    def grupo(h: str) -> str | None:
        """Indicador a que o cabeçalho se refere, por palavra-chave ou numeral como PALAVRA INTEIRA
        ("indicador ii" não pode casar com "indicador i")."""
        if "endivid" in h or "dc/rcl" in h or "dc rcl" in h:
            return "endividamento"
        if "poupan" in h:
            return "poupanca_corrente"
        if "liquidez" in h or "obrigacoes financeiras" in h:
            return "liquidez"
        toks = set(re.split(r"[\s/_.-]+", h))
        if not toks & {"indicador", "ind", "nota", "indice"}:
            return None
        for nome, numerais in (("endividamento", {"1", "i"}), ("poupanca_corrente", {"2", "ii"}), ("liquidez", {"3", "iii"})):
            if toks & numerais:
                return nome
        m = re.search(r"\bind(?:icador)?\s*([123])\b|\bind([123])\b", h)
        if m:
            return {"1": "endividamento", "2": "poupanca_corrente", "3": "liquidez"}[m.group(1) or m.group(2)]
        return None

    mapa = {"codigo_ibge": melhor["col_cod"], "capag": melhor["col_nota"]}
    for c in range(largura):
        p = perfis[c]
        if c in usados or not p["n"] or "capag" in cab[c]:
            continue
        g = grupo(cab[c])
        if not g:
            continue
        if p["ind"] >= 0.6 and f"nota_{g}" not in mapa:
            mapa[f"nota_{g}"] = c
            usados.add(c)
        elif p["num"] >= 0.6 and g not in mapa and "nota" not in cab[c].split():
            mapa[g] = c
            usados.add(c)
    # nota sem cabeçalho próprio, logo após o valor do indicador
    for g in ("endividamento", "poupanca_corrente", "liquidez"):
        if f"nota_{g}" not in mapa and g in mapa:
            c = mapa[g] + 1
            if c < largura and c not in usados and perfis[c]["n"] and perfis[c]["ind"] >= 0.6:
                mapa[f"nota_{g}"] = c
                usados.add(c)
    q = next((c for c in range(largura) if perfis[c]["n"] and c not in usados
              and ("qualidade" in cab[c] or "ranking" in cab[c])), None)
    if q is not None:
        mapa["qualidade_informacao"] = q

    regs = {}
    for r in dados:
        cod = _codigo(r[mapa["codigo_ibge"]], validos)
        bruto = r[mapa["capag"]]
        nota = _valor_nota(bruto)
        reg = {"capag": nota if nota in NOTAS_FINAIS else None,
               "capag_publicada": None if nota in NOTAS_FINAIS else (str(bruto).strip() if bruto not in (None, "") else None)}
        for k in ("endividamento", "poupanca_corrente", "liquidez"):
            if k in mapa:
                reg[k] = _num(r[mapa[k]])
            if f"nota_{k}" in mapa:
                n = _valor_nota(r[mapa[f"nota_{k}"]])
                reg[f"nota_{k}"] = n if n in NOTAS_IND else None
        if "qualidade_informacao" in mapa:
            qv = r[mapa["qualidade_informacao"]]
            reg["qualidade_informacao"] = qv if isinstance(qv, (int, float)) else (str(qv).strip() if qv else None)
        regs[cod] = reg
    # escala é da COLUNA: mediana > 5 significa que a planilha publicou em por cento (35,2), não em fração (0,352)
    escalas = {}
    for k in ("endividamento", "poupanca_corrente", "liquidez"):
        vals = sorted(r[k] for r in regs.values() if r.get(k) is not None)
        if vals:
            mediana = vals[len(vals) // 2]
            escalas[k] = "percentual" if mediana > 5 else "fracao"
            if escalas[k] == "percentual":
                for r in regs.values():
                    if r.get(k) is not None:
                        r[k] = round(r[k] / 100, 6)
    diag = {"aba": melhor["aba"], "linhas": len(regs), "abas": abas_vistas, "escala_original": escalas,
            "colunas": {k: {"indice": c, "cabecalho": cab[c] or f"(coluna {c + 1} sem título)"} for k, c in mapa.items()}}
    return regs, diag


# --------------------------------------------------------------------------- montagem

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(RAIZ / "data" / "capag"))
    ap.add_argument("--municipios", default=str(RAIZ / "data" / "municipios" / "municipios.json"))
    ap.add_argument("--ckan", default=CKAN, help=argparse.SUPPRESS)
    ap.add_argument("--max-posicoes", type=int, default=12, help="quantas posições recentes manter na trajetória")
    args = ap.parse_args(argv)

    dest = Path(args.out)
    (dest / "posicoes").mkdir(parents=True, exist_ok=True)
    muns = json.loads(Path(args.municipios).read_text(encoding="utf-8"))["municipios"] if Path(args.municipios).exists() else {}
    validos = set(muns)
    uf_de = {c: m.get("uf") for c, m in muns.items()}
    arq_cache = dest / "_posicoes.json"
    cache = json.loads(arq_cache.read_text(encoding="utf-8")) if arq_cache.exists() else {}

    pacote = http(args.ckan)
    pacote = pacote.get("result", pacote)
    posicoes = listar_posicoes(pacote)
    relatorio = {"processadas": [], "reaproveitadas": [], "ignoradas": [], "falhas": []}

    for p in posicoes:
        chave = p["id"]
        arq_pos = dest / "posicoes" / f"{p['data'] or p['ano']}.json"
        if cache.get(chave, {}).get("modificado") == p["modificado"] and arq_pos.exists():
            relatorio["reaproveitadas"].append(p["nome"])
            continue
        if p.get("tamanho") == 0:
            relatorio["ignoradas"].append({"posicao": p["nome"], "motivo": "arquivo com 0 bytes no portal"})
            continue
        try:
            bruto = http(p["url"], timeout=300, json_=False)
            if not bruto:
                raise LayoutCapag("arquivo vazio")
            with tempfile.NamedTemporaryFile(suffix=".xlsx") as tmp:
                tmp.write(bruto)
                tmp.flush()
                regs, diag = ler_planilha(tmp.name, validos)
            doc = {"posicao": p["nome"], "data": p["data"], "ano_capag": p["ano"], "ano_base": p["ano"] - 1,
                   "fonte": p["url"], "layout": diag, "municipios": regs}
            tmp_pos = arq_pos.with_suffix(".json.tmp")
            tmp_pos.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tmp_pos.replace(arq_pos)
            cache[chave] = {"modificado": p["modificado"], "arquivo": arq_pos.name, "layout": diag}
            relatorio["processadas"].append({"posicao": p["nome"], **{k: diag[k] for k in ("aba", "linhas")},
                                             "colunas": {k: v["cabecalho"] for k, v in diag["colunas"].items()}})
            print(f"[capag] {p['nome']}: {diag['linhas']} municípios, colunas -> "
                  + "; ".join(f"{k}='{v['cabecalho']}'" for k, v in diag["colunas"].items()), flush=True)
        except Exception as exc:  # noqa: BLE001 - uma posição ruim não derruba as outras
            relatorio["falhas"].append({"posicao": p["nome"], "erro": str(exc)})
            print(f"[capag] FALHA {p['nome']}: {exc}", file=sys.stderr, flush=True)

    tmp = arq_cache.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(arq_cache)

    # trajetória por município, a partir de todas as posições em disco
    docs = []
    for f in sorted((dest / "posicoes").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        docs.append(d)
    docs = sorted(docs, key=lambda d: d["data"] or f"{d['ano_capag']}-12-31")[-args.max_posicoes:]
    if not docs:
        raise SystemExit("nenhuma posição da CAPAG disponível")

    por_uf = defaultdict(dict)
    for d in docs:
        for cod, reg in d["municipios"].items():
            uf = uf_de.get(cod)
            if not uf:
                continue
            por_uf[uf].setdefault(cod, []).append({"posicao": d["data"] or str(d["ano_capag"]), "ano_capag": d["ano_capag"],
                                                   "ano_base": d["ano_base"], **reg})
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cabecalho = {
        "gerado_em": agora,
        "fonte": "Tesouro Nacional — Capacidade de Pagamento de Municípios (tesourotransparente.gov.br)",
        "licenca": "ODbL 1.0 (Open Data Commons Open Database License). Atribuição: Tesouro Nacional. "
                   "Extrato derivado redistribuído sob a mesma licença.",
        "aviso": "O resultado apurado para a CAPAG não vincula a posição do Tesouro Nacional. Notas lidas da publicação "
                 "oficial, sem recálculo; a metodologia mudou ao longo do período (ajuste de critérios a partir da CAPAG 2023).",
        "posicoes": [{"posicao": d["data"] or str(d["ano_capag"]), "nome": d["posicao"], "ano_capag": d["ano_capag"],
                      "ano_base": d["ano_base"]} for d in docs],
    }
    for uf, mm in por_uf.items():
        arq = dest / f"{uf.lower()}.json"
        tmp = arq.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({**cabecalho, "uf": uf, "municipios": dict(sorted(mm.items()))},
                                  ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(arq)
    (dest / "_auditoria.json").write_text(json.dumps({"gerado_em": agora, **relatorio}, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    ult = docs[-1]
    print(f"OK: {len(posicoes)} posições no portal; {len(relatorio['processadas'])} processadas, "
          f"{len(relatorio['reaproveitadas'])} reaproveitadas, {len(relatorio['ignoradas'])} ignoradas, "
          f"{len(relatorio['falhas'])} com falha. Mais recente: {ult['posicao']} ({len(ult['municipios'])} municípios)")
    if relatorio["falhas"] and not relatorio["processadas"] and not relatorio["reaproveitadas"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
