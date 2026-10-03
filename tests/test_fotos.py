#!/usr/bin/env python3
"""Teste do ETL de fotos com servidor HTTP local.

Cobre: leitura por Range trafegando só uma fração do ZIP; fallback para download completo
quando o servidor ignora Range; WebP 240x300 compacto; idempotência (segunda execução não
baixa nada); troca de prefeito (foto substituída); foto ausente e ZIP ausente na auditoria.
Uso: python3 tests/test_fotos.py
"""
import http.server
import io
import json
import os
import re
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

from PIL import Image

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "etl"))
import fotos_prefeitos as F  # noqa: E402

TRAFEGO = {"bytes": 0}


def servidor(raiz: Path, aceita_range: bool):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(raiz), **k)

        def log_message(self, *a):
            pass

        def do_GET(self):
            caminho = Path(self.translate_path(self.path))
            if not caminho.is_file():
                self.send_error(404)
                return
            dados = caminho.read_bytes()
            m = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range", "")) if aceita_range else None
            if m:
                ini = int(m.group(1))
                fim = int(m.group(2)) if m.group(2) else len(dados) - 1
                corpo = dados[ini:fim + 1]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {ini}-{fim}/{len(dados)}")
            else:
                corpo = dados
                self.send_response(200)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            try:
                self.wfile.write(corpo)
                TRAFEGO["bytes"] += len(corpo)
            except (ConnectionResetError, BrokenPipeError):
                pass  # o cliente fecha após ler só os cabeçalhos da sondagem: esperado

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def jpeg(cor, tamanho=(161, 225)):
    b = io.BytesIO()
    Image.new("RGB", tamanho, cor).save(b, "JPEG", quality=90)
    return b.getvalue()


def prefeitos(dirp: Path, uf: str, muns: dict):
    doc = {"uf": uf, "gerado_em": "x", "fontes": {}, "municipios": {
        ibge: {"eleicao": {"ano": 2024}, "prefeito": {"sq": sq, "nome": "X"}} for ibge, sq in muns.items()}}
    (dirp / f"{uf.lower()}.json").write_text(json.dumps(doc), encoding="utf-8")


def main():
    tmp = Path(tempfile.mkdtemp())
    cdn = tmp / "cdn" / "eleicoes2024"
    cdn.mkdir(parents=True)
    # ZIP do CE: 2 prefeitos + 60 "vereadores" com fotos pesadas (ruído incompressível)
    with zipfile.ZipFile(cdn / "foto_cand2024_CE_div.zip", "w", zipfile.ZIP_STORED) as z:
        z.writestr("FCE060000000001_div.jpg", jpeg((200, 30, 30)))
        z.writestr("FCE060000000002_div.jpg", jpeg((30, 200, 30), (300, 300)), compress_type=zipfile.ZIP_DEFLATED)
        for i in range(60):
            z.writestr(f"FCE0699999{i:05d}_div.jpg", os.urandom(200_000))
        z.writestr("FCE060000000009_div.jpg", b"isto nao e uma imagem")
    tamanho_zip = (cdn / "foto_cand2024_CE_div.zip").stat().st_size

    dirp, out = tmp / "prefeitos", tmp / "fotos"
    dirp.mkdir()
    prefeitos(dirp, "CE", {"2304400": "060000000001", "2303709": "060000000002",
                           "2312908": "060000000003",   # sem foto no zip
                           "2307650": "060000000009"})  # foto corrompida
    prefeitos(dirp, "AC", {"1200013": "010000000001"})  # zip do AC não existe

    # ---------- 1) com Range
    srv = servidor(tmp / "cdn", aceita_range=True)
    url = f"http://127.0.0.1:{srv.server_port}/eleicoes{{ano}}/foto_cand{{ano}}_{{uf}}_div.zip"
    TRAFEGO["bytes"] = 0
    assert F.main(["--prefeitos", str(dirp), "--out", str(out), "--url", url]) == 0
    trafego_range = TRAFEGO["bytes"]
    assert trafego_range < tamanho_zip * 0.10, f"Range deveria trafegar pouco: {trafego_range} de {tamanho_zip}"

    for ibge in ("2304400", "2303709"):
        f = out / f"{ibge}.webp"
        assert f.exists(), ibge
        with Image.open(f) as im:
            assert im.format == "WEBP" and im.size == (240, 300), (im.format, im.size)
        assert f.stat().st_size < 15_000, f.stat().st_size
    ce = json.loads((dirp / "ce.json").read_text(encoding="utf-8"))
    assert ce["municipios"]["2304400"]["prefeito"]["foto"] == "fotos/2304400.webp?v=060000000001"
    assert ce["municipios"]["2312908"]["prefeito"]["foto"] is None
    man = json.loads((out / "_manifest.json").read_text(encoding="utf-8"))
    motivos = {x["ibge"]: x["motivo"] for x in man["sem_foto"]}
    assert motivos["2312908"] == "foto não encontrada no zip"
    assert motivos["2307650"].startswith("imagem inválida")
    assert motivos["1200013"] == "zip ausente"

    # ---------- 2) idempotência: nada novo para baixar no CE
    TRAFEGO["bytes"] = 0
    antes = (out / "2304400.webp").stat().st_mtime_ns
    F.main(["--prefeitos", str(dirp), "--out", str(out), "--url", url, "--uf", "CE"])
    assert (out / "2304400.webp").stat().st_mtime_ns == antes, "foto inalterada não pode ser regravada"
    # só os 2 pendentes (sem foto/corrompida) são reconsultados, nunca o zip inteiro
    assert TRAFEGO["bytes"] < tamanho_zip * 0.25

    # ---------- 3) troca de prefeito em Fortaleza + município que saiu da base
    prefeitos(dirp, "CE", {"2304400": "060000000002", "2312908": "060000000003"})
    F.main(["--prefeitos", str(dirp), "--out", str(out), "--url", url])
    man = json.loads((out / "_manifest.json").read_text(encoding="utf-8"))
    assert man["fotos"]["2304400"] == "060000000002", "foto do novo prefeito"
    assert not (out / "2303709.webp").exists(), "foto de quem saiu da base é removida"
    with Image.open(out / "2304400.webp") as im:
        r, g, b = im.convert("RGB").getpixel((120, 150))
        assert g > r, "imagem nova (verde) substituiu a antiga (vermelha)"
    srv.shutdown()

    # ---------- 4) servidor sem Range: fallback para download completo
    out2 = tmp / "fotos2"
    srv2 = servidor(tmp / "cdn", aceita_range=False)
    url2 = f"http://127.0.0.1:{srv2.server_port}/eleicoes{{ano}}/foto_cand{{ano}}_{{uf}}_div.zip"
    F.main(["--prefeitos", str(dirp), "--out", str(out2), "--url", url2, "--uf", "CE"])
    assert (out2 / "2304400.webp").exists(), "fallback sem Range"
    assert "download" in json.loads((out2 / "_manifest.json").read_text())["ultima_execucao"]["ufs"]["CE/2024"]
    srv2.shutdown()

    # ---------- 5) limite de tempo: para entre UFs, salva o progresso e retoma depois
    with zipfile.ZipFile(cdn / "foto_cand2024_PB_div.zip", "w") as z:
        z.writestr("FPB150000000001_div.jpg", jpeg((30, 30, 200)))
    dir3, out3 = tmp / "pref3", tmp / "fotos3"
    dir3.mkdir()
    prefeitos(dir3, "CE", {"2304400": "060000000001"})
    prefeitos(dir3, "PB", {"2507507": "150000000001"})
    srv3 = servidor(tmp / "cdn", aceita_range=True)
    url3 = f"http://127.0.0.1:{srv3.server_port}/eleicoes{{ano}}/foto_cand{{ano}}_{{uf}}_div.zip"

    class Relogio:  # cada consulta ao relógio avança 1 minuto: deterministico
        t = -60.0
        @classmethod
        def monotonic(cls):
            cls.t += 60.0
            return cls.t
    real = F.time
    F.time = Relogio
    try:
        F.main(["--prefeitos", str(dir3), "--out", str(out3), "--url", url3, "--limite-minutos", "2.5"])
    finally:
        F.time = real
    m3 = json.loads((out3 / "_manifest.json").read_text(encoding="utf-8"))
    assert m3["ultima_execucao"]["interrompido"] is True
    assert "2304400" in m3["fotos"] and "2507507" not in m3["fotos"], m3["fotos"]
    assert json.loads((dir3 / "ce.json").read_text())["municipios"]["2304400"]["prefeito"]["foto"], "CE salvo no checkpoint"
    assert json.loads((dir3 / "pb.json").read_text())["municipios"]["2507507"]["prefeito"]["foto"] is None
    F.main(["--prefeitos", str(dir3), "--out", str(out3), "--url", url3])
    m3 = json.loads((out3 / "_manifest.json").read_text(encoding="utf-8"))
    assert m3["ultima_execucao"]["ufs"]["CE/2024"] == "sem alterações", "retomada não refaz o que já foi feito"
    assert "2507507" in m3["fotos"] and not m3["ultima_execucao"]["interrompido"]
    assert not list(out3.glob("*.tmp")) and not list(dir3.glob("*.tmp")), "sem arquivos temporários"
    srv3.shutdown()

    print(f"TODOS OS TESTES DE FOTOS PASSARAM (Range trafegou {trafego_range / 1e6:.2f} MB de um zip de {tamanho_zip / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
