# -*- coding: utf-8 -*-
"""nlsc_wmts.build_basemap 圖磚失敗路徑(審查項 3):不連網、不用真 GDAL。

錯法:圖磚部分失敗仍回成功、缺塊靜默留白 → 六七張圖都回報成功,底圖卻有一整塊空白。
"""
import io
import sys
import urllib.request
from pathlib import Path

import pytest
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE.parent / "scripts"))

import fake_osgeo  # noqa: E402
fake_osgeo.install_stub()
import nlsc_wmts  # noqa: E402


def _png(color=(10, 20, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), color).save(buf, "PNG")
    return buf.getvalue()


# 台南一小塊,zoom 固定 → 磚數可預期
EXT = (170000, 2540000, 171500, 2541500)
Z = 16


@pytest.fixture
def env(monkeypatch):
    g, o = fake_osgeo.FakeGdal(), fake_osgeo.FakeOsr()
    monkeypatch.setattr(nlsc_wmts, "gdal", g)
    monkeypatch.setattr(nlsc_wmts, "osr", o)
    monkeypatch.setattr(nlsc_wmts.time, "sleep", lambda s: None)
    return g


def _patch_urlopen(monkeypatch, behaviour):
    """behaviour(url, n_call) → bytes 或 raise。"""
    calls = {"n": 0}

    class _R:
        def __init__(self, b):
            self.b = b

        def read(self):
            return self.b

    def fake(rq, timeout=30):
        calls["n"] += 1
        return _R(behaviour(rq.full_url, calls["n"]))

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return calls


def _grid():
    m = nlsc_wmts._tf_3826_to_3857(*EXT)
    t = 2 * nlsc_wmts.ORIGIN / (2 ** Z)
    nc = int((m[2] + nlsc_wmts.ORIGIN) / t) - int((m[0] + nlsc_wmts.ORIGIN) / t) + 1
    nr = int((nlsc_wmts.ORIGIN - m[1]) / t) - int((nlsc_wmts.ORIGIN - m[3]) / t) + 1
    return nc * nr


def test_全部成功_回字串路徑且無缺磚(env, monkeypatch, tmp_path):
    _patch_urlopen(monkeypatch, lambda u, n: _png())
    out = str(tmp_path / "a.tif")
    res = nlsc_wmts.build_basemap(EXT, out, z=Z)
    assert res == out and isinstance(res, str)          # 既有呼叫方式不變
    assert res.failed == 0 and res.total == _grid()


def test_少量失敗_警告且仍回路徑並可查缺磚數(env, monkeypatch, tmp_path, capsys):
    assert _grid() >= 8

    def beh(u, n):
        if n <= 3:                                       # 第一塊三次重試全失敗
            raise OSError("timeout")
        return _png()
    _patch_urlopen(monkeypatch, beh)
    res = nlsc_wmts.build_basemap(EXT, str(tmp_path / "b.tif"), z=Z)
    assert res is not None and res.failed == 1
    assert "圖磚 1/" in capsys.readouterr().err         # 錯法:靜默


def test_失敗比例逾25pct_視同失敗回None(env, monkeypatch, tmp_path):
    total = _grid()
    quota = {"left": total - int(total * 0.25) - 2}      # 成功磚數不足 → 失敗 >25%

    def beh(u, n):
        if quota["left"] > 0:
            quota["left"] -= 1
            return _png()
        raise OSError("boom")
    _patch_urlopen(monkeypatch, beh)
    assert nlsc_wmts.build_basemap(EXT, str(tmp_path / "c.tif"), z=Z) is None


def test_全失敗回None(env, monkeypatch, tmp_path):
    def beh(u, n):
        raise OSError("down")
    _patch_urlopen(monkeypatch, beh)
    assert nlsc_wmts.build_basemap(EXT, str(tmp_path / "d.tif"), z=Z) is None


def test_GIF空磚不算失敗(env, monkeypatch, tmp_path):
    _patch_urlopen(monkeypatch,
                   lambda u, n: (b"GIF89a" + b"\x00" * 10) if n % 2 else _png())
    res = nlsc_wmts.build_basemap(EXT, str(tmp_path / "e.tif"), z=Z)
    assert res is not None and res.failed == 0 and res.empty > 0


def test_0bytes空回應不算失敗(env, monkeypatch, tmp_path):
    # SMAPS 坡度圖無資料區回 0 bytes:不可讓失敗比例超標而回 None
    _patch_urlopen(monkeypatch, lambda u, n: b"" if n % 2 == 0 else _png())
    res = nlsc_wmts.build_basemap(EXT, str(tmp_path / "g.tif"), z=Z)
    assert res is not None and res.failed == 0 and res.empty > 0


def test_0bytes佔多數仍不計失敗_只要有一塊有資料(env, monkeypatch, tmp_path):
    _patch_urlopen(monkeypatch, lambda u, n: _png() if n == 1 else b"")
    res = nlsc_wmts.build_basemap(EXT, str(tmp_path / "h.tif"), z=Z)
    assert res is not None and res.failed == 0 and res.empty == _grid() - 1


def test_例外與HTML仍算失敗_且比例只看真失敗(env, monkeypatch, tmp_path):
    # 一半 0 bytes(無資料)＋少量例外:失敗比例以真失敗計,<=25% 仍回路徑
    def beh(u, n):
        if n <= 3:
            raise OSError("timeout")
        return b"" if n % 2 == 0 else _png()
    _patch_urlopen(monkeypatch, beh)
    res = nlsc_wmts.build_basemap(EXT, str(tmp_path / "i.tif"), z=Z)
    assert res is not None and res.failed == 1


def test_HTML錯誤頁不入快取且算失敗(env, monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    _patch_urlopen(monkeypatch, lambda u, n: b"<html>err</html>")
    assert nlsc_wmts.build_basemap(EXT, str(tmp_path / "f.tif"), z=Z,
                                   cache_dir=str(cache)) is None
    assert list(cache.glob("*")) == []


def test_pick_zoom不超過MAX_TILES(env):
    m = nlsc_wmts._tf_3826_to_3857(*EXT)
    z = nlsc_wmts._pick_zoom(*m)
    t = 2 * nlsc_wmts.ORIGIN / (2 ** z)
    nc = int((m[2] + nlsc_wmts.ORIGIN) / t) - int((m[0] + nlsc_wmts.ORIGIN) / t) + 1
    nr = int((nlsc_wmts.ORIGIN - m[1]) / t) - int((nlsc_wmts.ORIGIN - m[3]) / t) + 1
    assert nc * nr <= nlsc_wmts.MAX_TILES
