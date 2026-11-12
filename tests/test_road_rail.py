# -*- coding: utf-8 -*-
"""road_access:鐵路不算道路(2026-10-20 裁示)、國土利用圖磚缺漏、旗標接通(審查項 1/2/4/5)。

不連網、不用真 GDAL(用 tests/fixtures/fake_osgeo 的空殼 import)。
"""
import sys
import urllib.request
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import shapely
from shapely.geometry import box

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE.parent / "scripts"))

import fake_osgeo  # noqa: E402
fake_osgeo.install_stub()
import road_access as ra  # noqa: E402


# ── 類別對應 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["一般鐵路", "一般鐵路及相關設", "高速鐵路", "高速鐵路及相關設"])
def test_rail_names_are_not_road(name):
    assert ra._map_category(name) == "鐵路"


@pytest.mark.parametrize("name", ["國道", "省道", "快速公路", "一般道路", "道路相關設施",
                                  "道路及相關設施", "防汛道路"])
def test_real_road_names_still_road(name):
    assert ra._map_category(name) == "道路"


def test_generic_traffic_parent_is_not_road():
    # 03「交通利用土地」是父類(含機場/鐵路/港口),不得再被「交通」關鍵字吞成道路
    assert ra._map_category("交通利用土地") != "道路"


def test_all_colortable_03_road_codes_unchanged():
    """色表內 030501~030505、0305、0405/040500 全為道路;0302xx/0303xx 全為鐵路。"""
    import csv
    p = HERE.parent / "config" / "luimap_colortable.csv"
    with open(p, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        c, n = r["lu_code"], r["lu_name"]
        if c.startswith(("030501", "030502", "030503", "030504", "030505", "0305", "0405", "040500")):
            assert ra._map_category(n) == "道路", (c, n)
        if c.startswith(("0302", "0303")):
            assert ra._map_category(n) == "鐵路", (c, n)


# ── 臨路判定 ────────────────────────────────────────────────────────────
def _feats(items):
    return gpd.GeoDataFrame(
        [{"geometry": g, "category": cat, "lu_name": name} for g, cat, name in items],
        geometry="geometry", crs="EPSG:3826")


SITE = box(0, 0, 50, 50)


def _site():
    return {"geometry": SITE, "area_m2": SITE.area, "site_id": "S001", "landno_list": "1"}


def test_rail_adjacent_is_not_adjacent_road():
    f = _feats([(box(50, 0, 60, 50), "鐵路", "一般鐵路")])
    row = ra._analyze_site(_site(), f, ra.Cfg())
    assert row["臨路狀態"] == "不臨路"
    assert "未臨路" in row["異常旗標"]
    assert "鐵路不計" in row["臨路註記"]
    assert row["鄰接路寬中位數_m"] is None          # 鐵路不參與路寬量測


def test_road_adjacent_still_direct():
    f = _feats([(box(50, 0, 60, 50), "道路", "一般道路")])
    row = ra._analyze_site(_site(), f, ra.Cfg())
    assert row["臨路狀態"] == "直接臨路"
    assert "鐵路" not in row["臨路註記"]


def test_rail_and_road_road_wins_no_rail_note_when_road_nearer():
    f = _feats([(box(50, 0, 60, 50), "道路", "一般道路"),
                (box(-30, 0, -10, 50), "鐵路", "一般鐵路")])
    row = ra._analyze_site(_site(), f, ra.Cfg())
    assert row["臨路狀態"] == "直接臨路"
    assert "鐵路不計" not in row["臨路註記"]


# ── 圖磚缺漏 ────────────────────────────────────────────────────────────
def test_tile_gap_marks_rerun_and_flag():
    f = _feats([(box(50, 0, 60, 50), "道路", "一般道路")])
    gap = {"n": 2, "polys": [box(40, -10, 80, 60)]}      # 與 buffer 相交
    row = ra._analyze_site(_site(), f, ra.Cfg(), gap)
    assert "須重跑" in row["臨路狀態"]
    assert "圖磚缺漏（2塊）" in row["異常旗標"]


def test_tile_gap_far_away_keeps_status_and_no_flag():
    f = _feats([(box(50, 0, 60, 50), "道路", "一般道路")])
    gap = {"n": 1, "polys": [box(5000, 5000, 5100, 5100)]}
    row = ra._analyze_site(_site(), f, ra.Cfg(), gap)
    assert row["臨路狀態"] == "直接臨路"
    assert "圖磚缺漏" not in row["異常旗標"]      # 錯法:旗標加在所有案場


def test_no_gap_output_unchanged():
    f = _feats([(box(50, 0, 60, 50), "道路", "一般道路")])
    row = ra._analyze_site(_site(), f, ra.Cfg())
    assert "缺漏" not in row["異常旗標"]


class _R:
    def __init__(self, b):
        self.b = b

    def read(self):
        return self.b


def _png():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), (230, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def test_fetch_mosaic_reports_missing_and_uses_nodata_not_white(monkeypatch):
    captured = {}

    def fake_write(out, canvas, epsg, ox, oy, pix):
        captured["canvas"] = canvas.copy()
        return out

    monkeypatch.setattr(ra, "_write_rgb_geotiff", fake_write)
    monkeypatch.setattr(ra.time, "sleep", lambda s: None)
    n = {"i": 0}

    def fake_open(rq, timeout=30):
        n["i"] += 1
        # 第一個磚的 3 次重試都失敗,其餘正常
        if n["i"] <= 3:
            raise OSError("boom")
        return _R(_png())

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    t = 2 * ra.ORIGIN / (2 ** 18)
    x0 = 20000.0
    bbox = (x0, 2500000.0, x0 + t * 1.5, 2500000.0 + t * 0.5)   # 約 2 欄 x 1~2 列
    tif, missing = ra._fetch_landuse_mosaic_ex(bbox, 18, "x.tif")
    assert tif == "x.tif" and len(missing) == 1
    cv = captured["canvas"]
    assert not (cv == 255).all(axis=0).any(), "缺磚不可留白色"
    assert (cv == np.array(ra.NODATA_RGB)[:, None, None]).all(axis=0).any()
    # 相容舊介面
    assert ra._fetch_landuse_mosaic(bbox, 18, "y.tif") == "y.tif"


def _mosaic_with(monkeypatch, behaviour):
    cap = {}

    def fake_write(out, canvas, *a):
        cap["canvas"] = canvas.copy()
        return out

    monkeypatch.setattr(ra, "_write_rgb_geotiff", fake_write)
    monkeypatch.setattr(ra.time, "sleep", lambda s: None)

    def fake_open(rq, timeout=30):
        return _R(behaviour())

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    t = 2 * ra.ORIGIN / (2 ** 18)
    bbox = (20000.0, 2500000.0, 20000.0 + t / 2, 2500000.0 + t / 2)
    _, missing = ra._fetch_landuse_mosaic_ex(bbox, 18, "z.tif")
    return missing, cap["canvas"]


def test_fetch_mosaic_gif_empty_tile_is_nodata_not_missing(monkeypatch):
    # 外海/境外的 GIF 空磚是正常回應:不計缺磚、不標須重跑,畫布維持白色(同修前)
    missing, cv = _mosaic_with(monkeypatch, lambda: b"GIF89a")
    assert missing == []
    assert (cv == 255).all()


def test_fetch_mosaic_zero_bytes_is_nodata_not_missing(monkeypatch):
    missing, cv = _mosaic_with(monkeypatch, lambda: b"")
    assert missing == [] and (cv == 255).all()


def test_fetch_mosaic_html_error_page_counts_missing(monkeypatch):
    missing, cv = _mosaic_with(monkeypatch, lambda: b"<html>err</html>")
    assert len(missing) >= 1
    assert (cv == np.array(ra.NODATA_RGB)[:, None, None]).all(axis=0).any()


def test_fetch_mosaic_exception_counts_missing(monkeypatch):
    def boom():
        raise OSError("timeout")
    missing, _ = _mosaic_with(monkeypatch, boom)
    assert len(missing) >= 1


def test_tile_gap_flag_only_on_intersecting_site():
    f = _feats([(box(50, 0, 60, 50), "道路", "一般道路")])
    gap = {"n": 1, "polys": [box(40, -10, 80, 60)]}
    near = ra._analyze_site(_site(), f, ra.Cfg(), gap)
    far_site = {"site_id": "S2", "geometry": box(9000, 9000, 9010, 9010),
                "area_m2": 100.0, "landno_list": "x"}
    far = ra._analyze_site(far_site, f, ra.Cfg(), gap)
    assert "圖磚缺漏" in near["異常旗標"] and "須重跑" in near["臨路狀態"]
    assert "圖磚缺漏" not in far["異常旗標"] and "須重跑" not in far["臨路狀態"]


def test_missing_polys_in_crs_returns_boxes():
    polys = ra.missing_polys_in_crs([(13000000.0, 2800000.0, 13000100.0, 2800100.0)], "EPSG:3826")
    assert len(polys) == 1 and polys[0].area > 0
    assert ra.missing_polys_in_crs([], "EPSG:3826") == []


# ── 旗標接通(項 4) ─────────────────────────────────────────────────────
def test_cli_width_flags_reach_cfg(monkeypatch, tmp_path):
    seen = {}

    def boom(*a, **k):
        raise SystemExit("stop")

    real_cfg = ra.Cfg

    class Spy(real_cfg):
        def __init__(self, **kw):
            seen.update(kw)
            super().__init__(**kw)

    monkeypatch.setattr(ra, "Cfg", Spy)
    monkeypatch.setattr(ra, "_build_sites", boom)
    monkeypatch.setattr(sys, "argv", ["road_access.py", "--joined", "j.shp", "--sitemap", "s.csv",
                                      "--out", str(tmp_path), "--width-sample", "2.5",
                                      "--width-search", "30", "--width-gap", "4"])
    with pytest.raises(SystemExit):
        ra.main()
    assert seen["width_sample"] == 2.5 and seen["width_search"] == 30 and seen["width_gap"] == 4
