# -*- coding: utf-8 -*-
"""面積基準（登記面積優先）與權屬判定的回歸測試。

★ 2026-09-11 使用者定案：交付文件的面積一律以**登記面積**為準，圖面積只作參考。
危險在於「無條件改用登記面積」——未登記土地（母號 ≥9000）的登記面積是 0，
逕改會生出 0 面積的案場。下面把這個錯法寫成斷言鎖住。
"""
import os
import sys

import pytest
from shapely.geometry import Polygon

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))
import persite_stats as ps  # noqa: E402

gpd = pytest.importorskip("geopandas")


def _gdf(rows):
    """rows: [(登面積, 邊長)] → GeoDataFrame（正方形，圖面積＝邊長²）。"""
    recs, geoms = [], []
    for i, (reg, side) in enumerate(rows):
        recs.append({"地號": str(i + 1), "登面積": reg})
        x = i * 1000.0
        geoms.append(Polygon([(x, 0), (x + side, 0), (x + side, side), (x, side)]))
    return gpd.GeoDataFrame(recs, geometry=geoms, crs=3826)


# ── registered_area ────────────────────────────────────────────────
def test_registered_area_取正值():
    assert ps.registered_area({"登面積": 615.0}) == 615.0
    assert ps.registered_area({"登面積": "615"}) == 615.0


def test_registered_area_未登記地回None():
    """★ 未登記土地（母號 ≥9000）登記面積為 0——必須回 None 讓呼叫端退回圖面積，
    不可回 0.0（那會讓該筆面積憑空消失）。"""
    assert ps.registered_area({"登面積": 0}) is None
    assert ps.registered_area({"登面積": 0.0}) is None


def test_registered_area_欄位缺或壞值回None():
    assert ps.registered_area({}) is None
    assert ps.registered_area({"登面積": None}) is None
    assert ps.registered_area({"登面積": ""}) is None
    assert ps.registered_area({"登面積": "無"}) is None


def test_registered_area_認得欄名別名():
    assert ps.registered_area({"登記面積": 100.0}) == 100.0
    assert ps.registered_area({"標示面積": 100.0}) == 100.0


# ── area_of ────────────────────────────────────────────────────────
def test_area_of_全部有登記面積時用登記面積():
    df = _gdf([(615.0, 10.0), (231.0, 20.0)])      # 圖面積 100 + 400 = 500
    area, basis, reg, geo = ps.area_of(df)
    assert area == pytest.approx(846.0)             # 615 + 231
    assert basis == "登記面積"
    assert geo == pytest.approx(500.0)              # 圖面積另計，不影響採用值


def test_area_of_未登記地該筆退回圖面積():
    """★ 混用時只有那一筆退回圖面積，其餘仍用登記面積；基準字串要講清楚有幾筆。"""
    df = _gdf([(615.0, 10.0), (0, 20.0)])           # 第二筆未登記，圖面積 400
    area, basis, _reg, _geo = ps.area_of(df)
    assert area == pytest.approx(1015.0)            # 615 + 400
    assert basis == "登記面積（1 筆未登記以圖面積計）"


def test_area_of_全未登記時退回圖面積並標明():
    df = _gdf([(0, 10.0), (0, 20.0)])
    area, basis, _reg, _geo = ps.area_of(df)
    assert area == pytest.approx(500.0)
    assert basis == "圖面積（無登記面積）"


def test_area_of_空集合不炸():
    df = _gdf([])
    assert ps.area_of(df) == (0.0, "無宗地", 0.0, 0.0)


def test_area_of_未登記地不得計為零面積():
    """把錯法寫成斷言：若實作改成『無條件取登記面積』，未登記地會變 0 面積。"""
    df = _gdf([(0, 30.0)])                          # 圖面積 900
    area, _b, _r, _g = ps.area_of(df)
    assert area > 0, "未登記地被計為 0 面積——登記面積不可無條件取用"
    assert area == pytest.approx(900.0)


# ── classify_ownership（既有判準，一併鎖住）────────────────────────
@pytest.mark.parametrize("v,want", [
    ("私(推論)", "私"), ("私", "私"),
    ("公", "公"), ("公/國有", "公"), ("國有", "公"), ("直轄市有", "公"),
    ("縣市有", "公"), ("中華民國", "公"),
    ("", None), (None, None), ("未登記", None),
])
def test_classify_ownership(v, want):
    assert ps.classify_ownership(v) == want
