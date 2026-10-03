#!/usr/bin/env python3
"""
Briefing Municipal — dados do município para o Brasil inteiro (IBGE + Wikidata).

Gera data/municipios/municipios.json com, para cada um dos ~5.571 municípios:
  * IBGE Localidades: região, região intermediária, região imediata;
  * IBGE Pesquisas (painel Cidades): população estimada, área, densidade, PIB per capita,
    IDHM, salário médio formal, escolarização 6–14, mortalidade infantil, gentílico e o
    nome do prefeito publicado no painel — cada valor com o ano de referência;
  * Wikidata (CC0): item, site oficial, verbete na Wikipedia e chefe de governo vigente.

Decisões:
  * Lote adaptativo: pede até 100 municípios por chamada (códigos separados por "|");
    se o lote falhar, divide ao meio até chegar a um município. Funciona com ou sem
    suporte a lote, só muda a velocidade.
  * Retenção: fonte que falha mantém os valores da execução anterior, marcados como tal.
    Uma segunda-feira com o IBGE fora do ar não publica colunas vazias.
  * O texto da Wikipedia NÃO entra (CC BY-SA exigiria a mesma licença no arquivo derivado);
    entra só o endereço do verbete.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
IBGE = "https://servicodados.ibge.gov.br/api/v1"
WDQS = "https://query.wikidata.org/sparql"
UA = {"User-Agent": "BriefingMunicipal-ETL/1.0 (EDP Sistemas; dados publicos)"}
VAZIOS = {"", "-", "...", "X", "x", "..", "None", "null"}

# Mesmos indicadores do briefing (index.html). unidade = como o painel do IBGE publica.
INDICADORES = [
    ("populacao", 33, 29171, "pessoas"),
    ("area_km2", 33, 29167, "km²"),
    ("densidade", 33, 29168, "hab/km²"),
    ("pib_per_capita", 38, 47001, "R$"),
    ("idhm", 37, 30255, ""),
    ("salario_medio_sm", 33, 29765, "salários mínimos"),
    ("escolarizacao_6_14", 40, 60045, "%"),
    ("mortalidade_infantil", 39, 30279, "óbitos por mil nascidos vivos"),
]


def sem_acento(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def get_json(url: str, timeout: int = 60, tentativas: int = 3, dados: bytes | None = None, headers: dict | None = None):
    ultimo = None
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, data=dados, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            ultimo = e
            if e.code in (400, 404):      # erro do pedido: repetir não muda nada
                raise
            espera = int(e.headers.get("Retry-After", "0") or 0) if e.code == 429 else 0
            time.sleep(max(espera, 2 * (i + 1)))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            ultimo = e
            time.sleep(2 * (i + 1))
    raise ultimo


# --------------------------------------------------------------------------- IBGE

def localidades() -> dict[str, dict]:
    out = {}
    for m in get_json(f"{IBGE}/localidades/municipios", timeout=120):
        ri = m.get("regiao-imediata") or {}
        rint = ri.get("regiao-intermediaria") or {}
        uf = rint.get("UF") or ((m.get("microrregiao") or {}).get("mesorregiao") or {}).get("UF") or {}
        out[str(m["id"])] = {
            "nome": m["nome"], "uf": uf.get("sigla"), "regiao": (uf.get("regiao") or {}).get("nome"),
            "regiao_intermediaria": rint.get("nome"), "regiao_imediata": ri.get("nome"),
        }
    return out


def ultimo_valor(serie: dict, numerico: bool):
    for ano in sorted(serie or {}, reverse=True):
        v = serie[ano]
        if v is None or str(v).strip() in VAZIOS:
            continue
        if not numerico:
            return str(v).strip(), ano
        try:
            return float(str(v).replace(",", ".")), ano
        except ValueError:
            continue
    return None, None


def resultados_em_lote(pesquisa: int, indicador: int, codigos: list[str], numerico: bool,
                       lote: int = 100, paralelo: int = 6) -> tuple[dict, dict]:
    """{codigo: (valor, ano)} para todos os códigos, com divisão adaptativa do lote."""
    resultado, estat = {}, {"chamadas": 0, "falhas": 0, "divisoes": 0}

    def pedir(grupo: list[str]):
        url = f"{IBGE}/pesquisas/{pesquisa}/indicadores/{indicador}/resultados/{'|'.join(grupo)}"
        estat["chamadas"] += 1
        try:
            d = get_json(url, timeout=90, tentativas=2)
            res = {str(x.get("localidade")): x.get("res") for x in (d[0].get("res") if d else []) or []}
            # lote que volta sem nenhum dos pedidos é tratado como lote não suportado
            if len(grupo) > 1 and not any(c in res for c in grupo):
                raise ValueError("lote sem resultado")
            return [(c, ultimo_valor(res.get(c), numerico)) for c in grupo]
        except Exception:  # noqa: BLE001 - divide e tenta de novo
            if len(grupo) == 1:
                estat["falhas"] += 1
                return [(grupo[0], (None, None))]
            estat["divisoes"] += 1
            meio = len(grupo) // 2
            return pedir(grupo[:meio]) + pedir(grupo[meio:])

    grupos = [codigos[i:i + lote] for i in range(0, len(codigos), lote)]
    with ThreadPoolExecutor(max_workers=paralelo) as ex:
        for parte in ex.map(pedir, grupos):
            for c, va in parte:
                resultado[c] = va
    return resultado, estat


def descobrir_texto() -> dict[str, int | None]:
    """IDs do prefeito e do gentílico no painel (pesquisa 33), achados pelo nome."""
    plano = []

    def andar(nos):
        for n in nos or []:
            plano.append((n.get("id"), sem_acento(n.get("indicador") or "")))
            andar(n.get("children"))
    andar(get_json(f"{IBGE}/pesquisas/33/indicadores"))
    achar = lambda prefixo: next((i for i, nome in plano if nome.startswith(prefixo)), None)  # noqa: E731
    return {"prefeito_ibge": achar("prefeit"), "gentilico": achar("gentilico")}


# --------------------------------------------------------------------------- Wikidata

SPARQL = """SELECT ?cod ?mun ?site ?artigo ?prefeitoLabel ?inicio WHERE {
  ?mun wdt:P1585 ?cod .
  OPTIONAL { ?mun wdt:P856 ?site . }
  OPTIONAL { ?artigo schema:about ?mun ; schema:isPartOf <https://pt.wikipedia.org/> . }
  OPTIONAL { ?mun p:P6 ?st . ?st ps:P6 ?prefeito ; wikibase:rank ?rank .
             FILTER(?rank != wikibase:DeprecatedRank) FILTER NOT EXISTS { ?st pq:P582 ?fim . }
             OPTIONAL { ?st pq:P580 ?inicio . } }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "pt-br,pt,en" . }
}"""


def wikidata() -> dict[str, dict]:
    corpo = urllib.parse.urlencode({"query": SPARQL, "format": "json"}).encode()
    d = get_json(WDQS, timeout=180, dados=corpo, headers={"Content-Type": "application/x-www-form-urlencoded",
                                                         "Accept": "application/sparql-results+json"})
    out = {}
    for b in d["results"]["bindings"]:
        v = lambda k: b.get(k, {}).get("value")  # noqa: E731
        cod = v("cod")
        if not cod or len(cod) != 7 or not cod.isdigit():
            continue
        e = out.setdefault(cod, {"wikidata": v("mun").rsplit("/", 1)[-1], "sites": set(), "wikipedia": None,
                                 "prefeitos": []})
        if v("site"):
            e["sites"].add(v("site"))
        if v("artigo"):
            e["wikipedia"] = v("artigo")
        nome = v("prefeitoLabel")
        if nome and not (nome.startswith("Q") and nome[1:].isdigit()):
            e["prefeitos"].append((v("inicio") or "", nome))
    for e in out.values():
        sites = sorted(e.pop("sites"))
        e["site_oficial"] = next((s for s in sites if ".gov.br" in s), sites[0] if sites else None)
        pref = sorted(set(e.pop("prefeitos")), reverse=True)
        e["prefeito_wikidata"] = pref[0][1] if pref else None
        e["prefeito_wikidata_desde"] = pref[0][0][:10] if pref and pref[0][0] else None
    return out


# --------------------------------------------------------------------------- montagem

def main(argv=None):
    global IBGE, WDQS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(RAIZ / "data" / "municipios" / "municipios.json"))
    ap.add_argument("--max-dias", type=float, default=6, help="reaproveita o arquivo se for mais novo que isso")
    ap.add_argument("--forcar", action="store_true")
    ap.add_argument("--lote", type=int, default=100)
    ap.add_argument("--paralelo", type=int, default=6)
    ap.add_argument("--ibge-base", default=IBGE, help=argparse.SUPPRESS)       # testes
    ap.add_argument("--wikidata-url", default=WDQS, help=argparse.SUPPRESS)    # testes
    args = ap.parse_args(argv)
    IBGE, WDQS = args.ibge_base, args.wikidata_url

    destino = Path(args.out)
    destino.parent.mkdir(parents=True, exist_ok=True)
    anterior = json.loads(destino.read_text(encoding="utf-8")) if destino.exists() else {}
    if anterior and not args.forcar:
        idade = (datetime.now(timezone.utc) - datetime.fromisoformat(anterior["gerado_em"])).total_seconds() / 86400
        if idade < args.max_dias:
            print(f"arquivo com {idade:.1f} dias (< {args.max_dias}); nada a fazer. Use --forcar para refazer.")
            return 0

    ant_mun = anterior.get("municipios", {})
    status, t0 = {}, time.monotonic()

    # 1) cadastro: sem ele não há lista de códigos; se falhar, usa o anterior
    try:
        cad = localidades()
        status["localidades"] = {"ok": True, "municipios": len(cad)}
    except Exception as exc:  # noqa: BLE001
        if not ant_mun:
            raise SystemExit(f"IBGE Localidades indisponível e sem arquivo anterior: {exc}")
        cad = {c: {k: m.get(k) for k in ("nome", "uf", "regiao", "regiao_intermediaria", "regiao_imediata")}
               for c, m in ant_mun.items()}
        status["localidades"] = {"ok": False, "erro": str(exc), "retido": True}
    codigos = sorted(cad)
    mun = {c: dict(cad[c]) for c in codigos}

    def reter(campos: list[str], fonte: str, exc):
        n = 0
        for c in codigos:
            for k in campos:
                if k in ant_mun.get(c, {}):
                    mun[c][k] = ant_mun[c][k]
                    n += 1
        status[fonte] = {"ok": False, "erro": str(exc), "retido": True, "valores_retidos": n}

    # 2) indicadores numéricos
    for campo, pesquisa, ind, unidade in INDICADORES:
        try:
            res, est = resultados_em_lote(pesquisa, ind, codigos, True, args.lote, args.paralelo)
            preenchidos = sum(1 for v, _ in res.values() if v is not None)
            if preenchidos == 0:
                raise RuntimeError("nenhum valor retornado")
            for c in codigos:
                v, ano = res.get(c, (None, None))
                mun[c][campo], mun[c][f"{campo}_ano"] = v, ano
            status[campo] = {"ok": True, "preenchidos": preenchidos, "unidade": unidade, **est}
        except Exception as exc:  # noqa: BLE001
            reter([campo, f"{campo}_ano"], campo, exc)
        print(f"[ibge] {campo}: {status[campo]}", flush=True)

    # 3) campos de texto do painel (prefeito e gentílico)
    try:
        ids = descobrir_texto()
    except Exception as exc:  # noqa: BLE001
        ids = {"prefeito_ibge": None, "gentilico": None}
        status["painel_meta"] = {"ok": False, "erro": str(exc)}
    for campo, ind in ids.items():
        try:
            if not ind:
                raise RuntimeError("indicador não localizado na pesquisa 33")
            res, est = resultados_em_lote(33, ind, codigos, False, args.lote, args.paralelo)
            if not any(v for v, _ in res.values()):
                raise RuntimeError("nenhum valor retornado")   # mesma trava dos numéricos: falha não apaga
            for c in codigos:
                v, ano = res.get(c, (None, None))
                mun[c][campo] = v
                if campo == "prefeito_ibge":
                    mun[c]["prefeito_ibge_ano"] = ano
            status[campo] = {"ok": True, "indicador": ind, "preenchidos": sum(1 for v, _ in res.values() if v), **est}
        except Exception as exc:  # noqa: BLE001
            reter([campo] + (["prefeito_ibge_ano"] if campo == "prefeito_ibge" else []), campo, exc)
        print(f"[ibge] {campo}: {status[campo]}", flush=True)

    # 4) Wikidata
    campos_wd = ["wikidata", "site_oficial", "wikipedia", "prefeito_wikidata", "prefeito_wikidata_desde"]
    try:
        wd = wikidata()
        for c in codigos:
            for k in campos_wd:
                mun[c][k] = (wd.get(c) or {}).get(k)
        status["wikidata"] = {"ok": True, "itens": sum(1 for c in codigos if c in wd)}
    except Exception as exc:  # noqa: BLE001
        reter(campos_wd, "wikidata", exc)
    print(f"[wikidata] {status['wikidata']}", flush=True)

    doc = {
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fontes": {"ibge": "servicodados.ibge.gov.br (Localidades e Pesquisas)", "wikidata": "query.wikidata.org (CC0)"},
        "status": status, "total": len(mun), "municipios": mun,
    }
    tmp = destino.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(destino)
    falhas = [k for k, s in status.items() if not s.get("ok")]
    print(f"OK: {len(mun)} municípios em {time.monotonic() - t0:.0f}s"
          + (f" — fontes com falha (valores anteriores mantidos): {', '.join(falhas)}" if falhas else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
