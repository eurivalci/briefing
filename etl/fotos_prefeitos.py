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


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--prefeitos", default=str(RAIZ / "data" / "prefeitos"))
    p.add_argument("--out", default=str(RAIZ / "data" / "fotos"))
    p.add_argument("--url", default=URL_PADRAO, help="modelo com {ano} e {uf}")
    p.add_argument("--uf", nargs="*", help="limitar a algumas UFs")
    args = p.parse_args(argv)

    dir_pref, dir_out = Path(args.prefeitos), Path(args.out)
    dir_out.mkdir(parents=True, exist_ok=True)
    arq_manifest = dir_out / "_manifest.json"
    manifest = json.loads(arq_manifest.read_text(encoding="utf-8")) if arq_manifest.exists() else {}
    fotos = manifest.get("fotos", {})

    alvos = carregar_alvos(dir_pref)
    if args.uf:
        filtro = {u.upper() for u in args.uf}
        alvos = {k: v for k, v in alvos.items() if k[0] in filtro}

    relatorio = {"novas": 0, "mantidas": 0, "sem_foto": [], "ufs": {}}
    for (uf, ano), muns in sorted(alvos.items()):
        pendentes = {i: sq for i, sq in muns.items() if not (fotos.get(i) == sq and (dir_out / f"{i}.webp").exists())}
        relatorio["mantidas"] += len(muns) - len(pendentes)
        if not pendentes:
            relatorio["ufs"][f"{uf}/{ano}"] = "sem alterações"
            continue
        url = args.url.format(ano=ano, uf=uf)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                z, remoto, modo = abrir_zip(url, Path(tmp))
            except Exception as exc:  # noqa: BLE001 - uma UF com falha não derruba as outras
                print(f"[{uf}/{ano}] falha ao abrir {url}: {exc}", file=sys.stderr)
                relatorio["ufs"][f"{uf}/{ano}"] = f"erro: {exc}"
                relatorio["sem_foto"] += [{"ibge": i, "sq": s, "motivo": "zip indisponível"} for i, s in pendentes.items()]
                continue
            if z is None:
                relatorio["ufs"][f"{uf}/{ano}"] = "zip ausente no TSE"
                relatorio["sem_foto"] += [{"ibge": i, "sq": s, "motivo": "zip ausente"} for i, s in pendentes.items()]
                continue
            with z:
                idx = indexar_membros(z)
                ok = 0
                for ibge, sq in sorted(pendentes.items()):
                    membro = idx.get(sq)
                    if not membro:
                        relatorio["sem_foto"].append({"ibge": ibge, "sq": sq, "motivo": "foto não encontrada no zip"})
                        continue
                    try:
                        (dir_out / f"{ibge}.webp").write_bytes(para_webp(z.read(membro)))
                        fotos[ibge] = sq
                        ok += 1
                    except Exception as exc:  # noqa: BLE001 - imagem corrompida não derruba a UF
                        relatorio["sem_foto"].append({"ibge": ibge, "sq": sq, "motivo": f"imagem inválida: {exc}"})
                relatorio["novas"] += ok
                mb = f", {remoto.bytes_baixados / 1e6:.1f} MB trafegados" if remoto else ""
                relatorio["ufs"][f"{uf}/{ano}"] = f"{ok}/{len(pendentes)} via {modo}{mb}"
                print(f"[{uf}/{ano}] {relatorio['ufs'][f'{uf}/{ano}']}")

    # remove fotos de quem deixou de ser prefeito em todas as bases
    vigentes = {i for muns in carregar_alvos(dir_pref).values() for i in muns}
    if not args.uf:
        for i in list(fotos):
            if i not in vigentes:
                (dir_out / f"{i}.webp").unlink(missing_ok=True)
                fotos.pop(i)

    # grava o caminho da foto nos JSON por UF (o "v" invalida cache quando o prefeito muda)
    for arq in sorted(dir_pref.glob("[a-z][a-z].json")):
        doc = json.loads(arq.read_text(encoding="utf-8"))
        mudou = False
        for ibge, reg in doc["municipios"].items():
            alvo = f"fotos/{ibge}.webp?v={fotos[ibge]}" if fotos.get(ibge) == reg["prefeito"].get("sq") else None
            if "foto" not in reg["prefeito"] or reg["prefeito"]["foto"] != alvo:
                reg["prefeito"]["foto"] = alvo
                mudou = True
        if mudou:
            arq.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    manifest = {"fonte": "TSE — Fotos de candidatos (CC BY)", "fotos": dict(sorted(fotos.items())),
                "ultima_execucao": {k: v for k, v in relatorio.items() if k != "sem_foto"},
                "sem_foto": relatorio["sem_foto"]}
    arq_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK: {relatorio['novas']} novas, {relatorio['mantidas']} mantidas, {len(relatorio['sem_foto'])} sem foto")
    return 0


if __name__ == "__main__":
    sys.exit(main())
