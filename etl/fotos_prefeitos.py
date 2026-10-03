#!/usr/bin/env python3
"""
Briefing Municipal — fotos oficiais dos prefeitos (TSE -> WebP compactos).

Fonte: Portal de Dados Abertos do TSE, "Fotos de candidatos", um ZIP por UF:
  https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes<ANO>/fotos/foto_cand<ANO>_<UF>_div.zip
Licença: Creative Commons Atribuição.

Cada ZIP traz as fotos de TODOS os candidatos da UF (vários GB no total). Para não
baixar tudo, o ZIP é lido por HTTP Range: só o diretório central e as fotos dos
prefeitos trafegam. Sem suporte a Range, cai para download completo, uma UF por vez.

Entrada : data/prefeitos/<uf>.json (gerados por build_prefeitos.py)
Saída   : data/fotos/<codigo_ibge>.webp  (240x300, ~8 KB)
          data/fotos/_manifest.json      (ibge -> sq, para pular o que não mudou)
          campo prefeito.foto nos JSON por UF ("fotos/<ibge>.webp?v=<sq>")
"""
from __future__ import annotations

import argparse
import io
import struct
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
import json
import re
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageOps

RAIZ = Path(__file__).resolve().parent.parent
URL_PADRAO = "https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes{ano}/fotos/foto_cand{ano}_{uf}_div.zip"
TAMANHO = (240, 300)  # 4:5, o mesmo recorte do card
QUALIDADE = 72
UA = {"User-Agent": "briefing-municipal-etl"}


# --------------------------------------------------------------------------- leitura remota por Range

class ArquivoHTTP(io.RawIOBase):
    """Arquivo somente leitura sobre HTTP Range, com cache de blocos (para zipfile)."""

    BLOCO = 256 * 1024

    def __init__(self, url: str, tamanho: int, timeout: int = 60):
        self.url, self.tamanho, self.timeout = url, tamanho, timeout
        self.pos = 0
        self.cache: dict[int, bytes] = {}
        self.bytes_baixados = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.tamanho}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def _bloco(self, i: int) -> bytes:
        if i not in self.cache:
            ini = i * self.BLOCO
            fim = min(ini + self.BLOCO, self.tamanho) - 1
            req = urllib.request.Request(self.url, headers={**UA, "Range": f"bytes={ini}-{fim}"})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                if r.status != 206:
                    raise OSError("servidor ignorou o cabeçalho Range")
                dados = r.read()
            self.bytes_baixados += len(dados)
            if len(self.cache) > 64:  # limita memória: ~16 MB de cache
                self.cache.pop(next(iter(self.cache)))
            self.cache[i] = dados
        return self.cache[i]

    def read(self, n=-1):
        if self.pos >= self.tamanho:
            return b""
        if n is None or n < 0:
            n = self.tamanho - self.pos
        n = min(n, self.tamanho - self.pos)
        partes, restante, pos = [], n, self.pos
        while restante > 0:
            i, off = divmod(pos, self.BLOCO)
            b = self._bloco(i)[off:off + restante]
            if not b:
                break
            partes.append(b)
            pos += len(b)
            restante -= len(b)
        self.pos = pos
        return b"".join(partes)

    def readinto(self, buf):
        dados = self.read(len(buf))
        buf[:len(dados)] = dados
        return len(dados)


def sondar(url: str, timeout: int = 60) -> tuple[int | None, bool]:
    """Retorna (tamanho, aceita_range). Tamanho None = arquivo inexistente."""
    req = urllib.request.Request(url, headers={**UA, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 206:
                total = r.headers.get("Content-Range", "").rsplit("/", 1)[-1]
                return (int(total) if total.isdigit() else None), total.isdigit()
            tam = r.headers.get("Content-Length")
            return (int(tam) if tam and tam.isdigit() else -1), False
    except urllib.error.HTTPError as e:
        if e.code in (404, 403, 410):
            return None, False
        raise


def abrir_zip(url: str, tmpdir: Path):
    """Abre o ZIP remoto por Range; sem Range, baixa para disco temporário."""
    tamanho, range_ok = sondar(url)
    if tamanho is None:
        return None, None, "ausente"
    if range_ok:
        f = ArquivoHTTP(url, tamanho)
        return zipfile.ZipFile(io.BufferedReader(f, buffer_size=ArquivoHTTP.BLOCO)), f, "range"
    destino = tmpdir / "fotos.zip"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600) as r, open(destino, "wb") as out:
        while True:
            pedaco = r.read(4 * 1024 * 1024)
            if not pedaco:
                break
            out.write(pedaco)
    return zipfile.ZipFile(destino), None, "download"


def ler_membro_remoto(url: str, info: zipfile.ZipInfo, timeout: int = 60) -> bytes:
    """Baixa só os bytes de um membro do ZIP (cabeçalho local + dados) e descompacta.

    Cada chamada é independente, então dá para rodar várias em paralelo. A integridade
    é conferida pelo CRC32 do diretório central.
    """
    folga = 30 + len(info.orig_filename.encode("utf-8", "replace")) + 2048  # cabeçalho local + extra
    ini = info.header_offset
    fim = ini + folga + info.compress_size
    req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={ini}-{fim}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status != 206:
            raise OSError("servidor ignorou o cabeçalho Range")
        bruto = r.read()
    if bruto[:4] != b"PK\x03\x04":
        raise ValueError("cabeçalho local inválido")
    n, m = struct.unpack("<HH", bruto[26:30])
    dados = bruto[30 + n + m: 30 + n + m + info.compress_size]
    if len(dados) != info.compress_size:
        raise ValueError("membro truncado")
    if info.compress_type == zipfile.ZIP_STORED:
        conteudo = dados
    elif info.compress_type == zipfile.ZIP_DEFLATED:
        conteudo = zlib.decompressobj(-15).decompress(dados)
    else:
        raise ValueError(f"compressão não suportada: {info.compress_type}")
    if zlib.crc32(conteudo) & 0xFFFFFFFF != info.CRC:
        raise ValueError("CRC divergente")
    return conteudo


# --------------------------------------------------------------------------- imagens

def para_webp(dados: bytes) -> bytes:
    with Image.open(io.BytesIO(dados)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im = ImageOps.fit(im, TAMANHO, method=Image.LANCZOS, centering=(0.5, 0.35))  # preserva o rosto
        out = io.BytesIO()
        im.save(out, "WEBP", quality=QUALIDADE, method=6)
        return out.getvalue()


SQ_RE = re.compile(r"(\d{9,})")


def indexar_membros(z: zipfile.ZipFile) -> dict[str, str]:
    """sq -> nome do membro. O padrão do TSE é F<UF><SQ>_div.jpg; casamos pelo número."""
    idx = {}
    for nome in z.namelist():
        if not nome.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        for sq in SQ_RE.findall(Path(nome).name):
            idx.setdefault(sq, nome)
    return idx


# --------------------------------------------------------------------------- processo

def carregar_alvos(dir_prefeitos: Path) -> dict[tuple[str, int], dict[str, str]]:
    """(uf, ano) -> {ibge: sq}"""
    alvos = defaultdict(dict)
    for arq in sorted(dir_prefeitos.glob("[a-z][a-z].json")):
        doc = json.loads(arq.read_text(encoding="utf-8"))
        for ibge, reg in doc["municipios"].items():
            sq, ano = reg["prefeito"].get("sq"), reg["eleicao"].get("ano")
            if sq and ano:
                alvos[(doc["uf"], int(ano))][ibge] = sq
    return alvos


def salvar_estado(dir_out: Path, fotos: dict, relatorio: dict):
    manifest = {"fonte": "TSE — Fotos de candidatos (CC BY)", "fotos": dict(sorted(fotos.items())),
                "ultima_execucao": {k: v for k, v in relatorio.items() if k != "sem_foto"},
                "sem_foto": relatorio["sem_foto"]}
    tmp = dir_out / "_manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(dir_out / "_manifest.json")  # gravação atômica: nunca fica pela metade


def atualizar_jsons(dir_pref: Path, fotos: dict, ufs: set[str] | None = None):
    """Grava prefeito.foto nos JSON por UF (o "v" invalida cache quando o prefeito muda)."""
    for arq in sorted(dir_pref.glob("[a-z][a-z].json")):
        if ufs and arq.stem.upper() not in ufs:
            continue
        doc = json.loads(arq.read_text(encoding="utf-8"))
        mudou = False
        for ibge, reg in doc["municipios"].items():
            alvo = f"fotos/{ibge}.webp?v={fotos[ibge]}" if fotos.get(ibge) == reg["prefeito"].get("sq") else None
            if "foto" not in reg["prefeito"] or reg["prefeito"]["foto"] != alvo:
                reg["prefeito"]["foto"] = alvo
                mudou = True
        if mudou:
            tmp = arq.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            tmp.replace(arq)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--prefeitos", default=str(RAIZ / "data" / "prefeitos"))
    p.add_argument("--out", default=str(RAIZ / "data" / "fotos"))
    p.add_argument("--url", default=URL_PADRAO, help="modelo com {ano} e {uf}")
    p.add_argument("--uf", nargs="*", help="limitar a algumas UFs")
    p.add_argument("--paralelo", type=int, default=12, help="downloads simultâneos por UF")
    p.add_argument("--limite-minutos", type=float, default=0,
                   help="encerra de forma limpa ao atingir o tempo, salvando o progresso (0 = sem limite)")
    args = p.parse_args(argv)
    t0 = time.monotonic()
    def estourou() -> bool:
        return bool(args.limite_minutos) and (time.monotonic() - t0) > args.limite_minutos * 60

    dir_pref, dir_out = Path(args.prefeitos), Path(args.out)
    dir_out.mkdir(parents=True, exist_ok=True)
    arq_manifest = dir_out / "_manifest.json"
    manifest = json.loads(arq_manifest.read_text(encoding="utf-8")) if arq_manifest.exists() else {}
    fotos = manifest.get("fotos", {})

    alvos = carregar_alvos(dir_pref)
    if args.uf:
        filtro = {u.upper() for u in args.uf}
        alvos = {k: v for k, v in alvos.items() if k[0] in filtro}

    relatorio = {"novas": 0, "mantidas": 0, "sem_foto": [], "ufs": {}, "interrompido": False}
    for (uf, ano), muns in sorted(alvos.items()):
        if estourou():
            relatorio["interrompido"] = True
            print(f"[limite] tempo atingido; progresso salvo, continua na próxima execução (parou antes de {uf})")
            break
        pendentes = {i: sq for i, sq in muns.items() if not (fotos.get(i) == sq and (dir_out / f"{i}.webp").exists())}
        relatorio["mantidas"] += len(muns) - len(pendentes)
        chave = f"{uf}/{ano}"
        if not pendentes:
            relatorio["ufs"][chave] = "sem alterações"
            continue
        url = args.url.format(ano=ano, uf=uf)
        t_uf = time.monotonic()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                z, remoto, modo = abrir_zip(url, Path(tmp))
            except Exception as exc:  # noqa: BLE001 - uma UF com falha não derruba as outras
                print(f"[{chave}] falha ao abrir {url}: {exc}", file=sys.stderr)
                relatorio["ufs"][chave] = f"erro: {exc}"
                relatorio["sem_foto"] += [{"ibge": i, "sq": s, "motivo": "zip indisponível"} for i, s in pendentes.items()]
                continue
            if z is None:
                relatorio["ufs"][chave] = "zip ausente no TSE"
                relatorio["sem_foto"] += [{"ibge": i, "sq": s, "motivo": "zip ausente"} for i, s in pendentes.items()]
                continue
            with z:
                idx = indexar_membros(z)
                tarefas = []
                for ibge, sq in sorted(pendentes.items()):
                    membro = idx.get(sq)
                    if membro:
                        tarefas.append((ibge, sq, z.getinfo(membro)))
                    else:
                        relatorio["sem_foto"].append({"ibge": ibge, "sq": sq, "motivo": "foto não encontrada no zip"})

                def processar(t):
                    ibge, sq, info = t
                    bruto = ler_membro_remoto(url, info) if remoto else z.read(info)
                    (dir_out / f"{ibge}.webp").write_bytes(para_webp(bruto))
                    return ibge, sq

                ok, trafego = 0, sum(i.compress_size for _, _, i in tarefas)
                # no modo download o arquivo é local: zipfile não é seguro para leitura concorrente
                workers = max(1, args.paralelo) if remoto else 1
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    for t, fut in [(t, ex.submit(processar, t)) for t in tarefas]:
                        try:
                            ibge, sq = fut.result()
                            fotos[ibge] = sq
                            ok += 1
                        except Exception as exc:  # noqa: BLE001 - imagem ruim não derruba a UF
                            relatorio["sem_foto"].append({"ibge": t[0], "sq": t[1], "motivo": f"imagem inválida: {exc}"})
                relatorio["novas"] += ok
                extra = f", ~{(remoto.bytes_baixados + trafego) / 1e6:.1f} MB" if remoto else ""
                relatorio["ufs"][chave] = f"{ok}/{len(pendentes)} via {modo}{extra}, {time.monotonic() - t_uf:.0f}s"
                print(f"[{chave}] {relatorio['ufs'][chave]}", flush=True)
        # checkpoint por UF: uma interrupção depois daqui não perde este trabalho
        atualizar_jsons(dir_pref, fotos, {uf})
        salvar_estado(dir_out, fotos, relatorio)

    # remove fotos de quem deixou de ser prefeito (só numa execução completa e sem filtro)
    if not args.uf and not relatorio["interrompido"]:
        vigentes = {i for muns in carregar_alvos(dir_pref).values() for i in muns}
        for i in list(fotos):
            if i not in vigentes:
                (dir_out / f"{i}.webp").unlink(missing_ok=True)
                fotos.pop(i)

    atualizar_jsons(dir_pref, fotos)
    salvar_estado(dir_out, fotos, relatorio)
    print(f"OK: {relatorio['novas']} novas, {relatorio['mantidas']} mantidas, {len(relatorio['sem_foto'])} sem foto"
          f"{' (interrompido pelo limite de tempo)' if relatorio['interrompido'] else ''} em {time.monotonic() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
