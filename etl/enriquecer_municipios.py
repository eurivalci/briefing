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
import gzip
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
UA = {"User-Agent": "BriefingMunicipal-ETL/1.0 (EDP Sistemas; dados publicos)", "Accept-Encoding": "gzip"}
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


def ler_json_http(resp) -> object:
    """Lê JSON de uma resposta HTTP, descompactando gzip quando vier comprimido.

    A API do IBGE responde SEMPRE em gzip, mesmo sem o cabeçalho Accept-Encoding,
    e o urllib não descompacta sozinho (o erro aparece como byte 0x8b na posição 1).
    """
    bruto = resp.read()
    if (resp.headers.get("Content-Encoding") or "").lower() == "gzip" or bruto[:2] == b"\x1f\x8b":
        bruto = gzip.decompress(bruto)
    return json.loads(bruto.decode("utf-8"))


def sem_acento(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def get_json(url: str, timeout: int = 60, tentativas: int = 3, dados: bytes | None = None, headers: dict | None = None):
    ultimo = None
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, data=dados, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return ler_json_http(r)
        except urllib.error.HTTPError as e:
            ultimo = e
            if e.code in (400, 404):      # erro do pedido: repetir não muda nada
                raise
            espera = int(e.headers.get("Retry-After", "0") or 0) if e.code == 429 else 0
            time.sleep(max(espera, 2 * (i + 1)))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
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


def suporta_lote(amostra: list[str]) -> bool:
    """Sonda UMA vez se a API de Pesquisas aceita vários municípios por chamada ("a|b")."""
    if len(amostra) < 2:
        return False
    try:
        d = get_json(f"{IBGE}/pesquisas/33/indicadores/29171/resultados/{'|'.join(amostra[:2])}", timeout=60, tentativas=2)
        locs = {str(x.get("localidade")) for x in (d[0].get("res") if d else []) or []}
        return set(amostra[:2]) <= locs
    except Exception:  # noqa: BLE001
        return False


def resultados_em_lote(pesquisa: int, indicador: int, codigos: list[str], numerico: bool,
                       lote: int = 100, paralelo: int = 6, com_lote: bool = True) -> tuple[dict, dict]:
    """{codigo: (valor, ano)}. Com lote: grupos de até 100; um lote que falhar é refeito
    município a município UMA vez (sem cascata de divisões). Sem lote: um por chamada."""
    resultado, estat = {}, {"chamadas": 0, "falhas": 0, "modo": "lote" if com_lote else "unitario"}

    def um(c: str):
        estat["chamadas"] += 1
        try:
            d = get_json(f"{IBGE}/pesquisas/{pesquisa}/indicadores/{indicador}/resultados/{c}", timeout=60, tentativas=2)
            res = {str(x.get("localidade")): x.get("res") for x in (d[0].get("res") if d else []) or []}
            return [(c, ultimo_valor(res.get(c), numerico))]
        except Exception:  # noqa: BLE001
            estat["falhas"] += 1
            return [(c, (None, None))]

    def grupo(g: list[str]):
        estat["chamadas"] += 1
        try:
            d = get_json(f"{IBGE}/pesquisas/{pesquisa}/indicadores/{indicador}/resultados/{'|'.join(g)}", timeout=90, tentativas=2)
            res = {str(x.get("localidade")): x.get("res") for x in (d[0].get("res") if d else []) or []}
            if not any(c in res for c in g):
                raise ValueError("lote sem resultado")
            return [(c, ultimo_valor(res.get(c), numerico)) for c in g]
        except Exception:  # noqa: BLE001
            out = []
            for c in g:
                out += um(c)
            return out

    if com_lote:
        tarefas, fn, workers = [codigos[i:i + lote] for i in range(0, len(codigos), lote)], grupo, paralelo
    else:
        tarefas, fn, workers = [[c] for c in codigos], (lambda g: um(g[0])), max(paralelo, 16)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for parte in ex.map(fn, tarefas):
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
    ap.add_argument("--max-dias", type=float, default=6, help="campo atualizado há menos que isso não é buscado de novo")
    ap.add_argument("--forcar", action="store_true")
    ap.add_argument("--lote", type=int, default=100)
    ap.add_argument("--paralelo", type=int, default=6)
    ap.add_argument("--limite-minutos", type=float, default=15,
                    help="encerra de forma limpa (salvando) e continua na próxima execução; 0 = sem limite")
    ap.add_argument("--ibge-base", default=IBGE, help=argparse.SUPPRESS)       # testes
    ap.add_argument("--wikidata-url", default=WDQS, help=argparse.SUPPRESS)    # testes
    args = ap.parse_args(argv)
    IBGE, WDQS = args.ibge_base, args.wikidata_url
    t0 = time.monotonic()
    estourou = lambda: bool(args.limite_minutos) and (time.monotonic() - t0) > args.limite_minutos * 60  # noqa: E731

    destino = Path(args.out)
    destino.parent.mkdir(parents=True, exist_ok=True)
    anterior = json.loads(destino.read_text(encoding="utf-8")) if destino.exists() else {}
    ant_mun = anterior.get("municipios", {})
    status = dict(anterior.get("status", {}))   # status por fonte, com a data da última atualização
    agora_iso = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731

    def fresco(fonte: str) -> bool:
        st = status.get(fonte) or {}
        if args.forcar or not st.get("ok") or not st.get("atualizado_em"):
            return False
        idade = (datetime.now(timezone.utc) - datetime.fromisoformat(st["atualizado_em"])).total_seconds() / 86400
        return idade < args.max_dias

    # 1) cadastro: sem ele não há lista de códigos; se falhar, usa o anterior
    if fresco("localidades") and ant_mun:
        cad = {c: {k: m.get(k) for k in ("nome", "uf", "regiao", "regiao_intermediaria", "regiao_imediata")} for c, m in ant_mun.items()}
    else:
        try:
            cad = localidades()
            status["localidades"] = {"ok": True, "municipios": len(cad), "atualizado_em": agora_iso()}
        except Exception as exc:  # noqa: BLE001
            if not ant_mun:
                raise SystemExit(f"IBGE Localidades indisponível e sem arquivo anterior: {exc}")
            cad = {c: {k: m.get(k) for k in ("nome", "uf", "regiao", "regiao_intermediaria", "regiao_imediata")}
                   for c, m in ant_mun.items()}
            status["localidades"] = {**status.get("localidades", {}), "ok": False, "erro": str(exc), "retido": True}
    codigos = sorted(cad)
    # começa do arquivo anterior: o que não for atualizado agora continua valendo
    mun = {c: {**ant_mun.get(c, {}), **cad[c]} for c in codigos}

    def gravar():
        doc = {"gerado_em": agora_iso(),
               "fontes": {"ibge": "servicodados.ibge.gov.br (Localidades e Pesquisas)", "wikidata": "query.wikidata.org (CC0)"},
               "status": status, "total": len(mun), "municipios": mun}
        tmp = destino.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(destino)

    def falhou(fonte, exc):
        status[fonte] = {**status.get(fonte, {}), "ok": False, "erro": str(exc), "retido": True}

    gravar()   # já salva o cadastro: mesmo que o resto falhe, o consolidado tem nome e região

    # 2) Wikidata: uma consulta para o país inteiro, rápida
    campos_wd = ["wikidata", "site_oficial", "wikipedia", "prefeito_wikidata", "prefeito_wikidata_desde"]
    if not fresco("wikidata"):
        try:
            wd = wikidata()
            for c in codigos:
                for k in campos_wd:
                    mun[c][k] = (wd.get(c) or {}).get(k)
            status["wikidata"] = {"ok": True, "itens": sum(1 for c in codigos if c in wd), "atualizado_em": agora_iso()}
        except Exception as exc:  # noqa: BLE001
            falhou("wikidata", exc)
        gravar()
        print(f"[wikidata] {status['wikidata']}", flush=True)

    # 3) indicadores (população primeiro) e campos de texto do painel — só o que estiver vencido
    tarefas = [(campo, pesquisa, ind, True, unidade) for campo, pesquisa, ind, unidade in INDICADORES if not fresco(campo)]
    texto = [c for c in ("prefeito_ibge", "gentilico") if not fresco(c)]
    com_lote = False
    if tarefas or texto:
        com_lote = suporta_lote(codigos)
        print(f"[ibge] API de Pesquisas {'aceita' if com_lote else 'NÃO aceita'} lote; modo {'lote' if com_lote else 'unitário'}", flush=True)
    if texto:
        try:
            ids_texto = descobrir_texto()
        except Exception as exc:  # noqa: BLE001
            ids_texto = {"prefeito_ibge": None, "gentilico": None}
            status["painel_meta"] = {"ok": False, "erro": str(exc)}
        tarefas += [(campo, 33, ids_texto.get(campo), False, None) for campo in texto]
    adiados = []
    for campo, pesquisa, ind, numerico, unidade in tarefas:
        if estourou():
            adiados.append(campo)
            continue
        try:
            if not ind:
                raise RuntimeError("indicador não localizado na pesquisa 33")
            res, est = resultados_em_lote(pesquisa, ind, codigos, numerico, args.lote, args.paralelo, com_lote)
            preenchidos = sum(1 for v, _ in res.values() if v not in (None, ""))
            if preenchidos == 0:
                raise RuntimeError("nenhum valor retornado")   # falha não apaga o que já existe
            for c in codigos:
                v, ano = res.get(c, (None, None))
                mun[c][campo] = v
                if numerico:
                    mun[c][f"{campo}_ano"] = ano
                elif campo == "prefeito_ibge":
                    mun[c]["prefeito_ibge_ano"] = ano
            status[campo] = {"ok": True, "preenchidos": preenchidos, "unidade": unidade, "atualizado_em": agora_iso(), **est}
        except Exception as exc:  # noqa: BLE001
            falhou(campo, exc)
        gravar()   # checkpoint por indicador
        print(f"[ibge] {campo}: {status[campo]}", flush=True)

    if adiados:
        print(f"[limite] {args.limite_minutos} min atingidos; adiados para a próxima execução: {', '.join(adiados)}", flush=True)
    falhas = [k for k, st in status.items() if not st.get("ok")]
    print(f"OK: {len(mun)} municípios em {time.monotonic() - t0:.0f}s"
          + (f" — fontes com falha (valores anteriores mantidos): {', '.join(falhas)}" if falhas else "")
          + (f" — adiados: {len(adiados)}" if adiados else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
