# -*- coding: utf-8 -*-
"""基地自身 vs 鄰地的辨識（2026-11 審查 critical）——把舊『重疊 >=50% 整筆剔除』的錯法鎖成斷言。

舊錯法兩種：
  ① 農業部圖形把小宗地整筆包在基地大宗地內（萬里案 128 在 128-9 內）→ 被當基地剔除、鄰地消失；
  ② 申請範圍只占母地號一部分（--joined 給切片）→ 占 >=50% 整筆剔除而殘餘地消失，
     <50% 整筆留下與基地重疊。
純合成幾何，不打網路。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import geopandas as gpd  # noqa: E402
from shapely.geometry import box  # noqa: E402

import cadastre_neighbors as cn  # noqa: E402

SEG = "萬里段"


def _gdf(rows):
    """rows: (地號, geometry)"""
    return gpd.GeoDataFrame(
        [{"地號": n, "段": SEG, "登面積": 1.0, "圖面積": g.area, "geometry": g} for n, g in rows],
        crs=3826)


def _site(nos):
    return gpd.GeoDataFrame([{"地號": n, "段": SEG} for n in nos],
                            geometry=[box(0, 0, 1, 1)] * len(nos), crs=3826)


def test_small_parcel_inside_base_kept_as_contained():
    """錯法①：128 整筆在基地 128-9 內，不得被當基地剔除。"""
    su = box(0, 0, 100, 100)
    g = _gdf([("128-9", box(0, 0, 100, 100)), ("128", box(40, 40, 50, 50)),
              ("200", box(100, 0, 150, 50))])
    msgs = []
    out, rep = cn.split_self_neighbors(g, su, cn.base_keys_from_site(_site(["128-9"])),
                                       say=msgs.append)
    assert sorted(out["地號"]) == ["128", "200"]
    assert rep["contained"] == ["128"] and rep["self"] == ["128-9"]
    assert any("基地內含他人裡地" in m for m in msgs)
    assert "殘餘" not in out.columns       # 無部分重疊時輸出欄位與舊版相同


def test_old_rule_would_drop_contained_parcel():
    """對照：舊規則（ov>=0.5 剔除）確實會把 128 剔掉——證明新測試抓得到錯法。"""
    su = box(0, 0, 100, 100)
    g = _gdf([("128", box(40, 40, 50, 50))])
    ov = g.geometry.intersection(su).area / g.geometry.area
    assert (ov >= 0.5).all()


def test_partial_overlap_keeps_residual_when_over_half():
    """錯法②a：申請範圍切片占母地號 70% → 舊法整筆剔除、殘餘消失；新法保留 30% 殘餘。"""
    su = box(0, 0, 70, 100)                       # 申請範圍（切片）
    g = _gdf([("5", box(0, 0, 100, 100))])        # 母地號整筆
    out, rep = cn.split_self_neighbors(g, su, cn.base_keys_from_site(_site(["5"])),
                                       say=lambda *_: None)
    assert len(out) == 1
    assert abs(out.geometry.iloc[0].area - 3000) < 1e-6
    assert out.geometry.iloc[0].intersection(su).area < 1e-6
    assert out["殘餘"].iloc[0] == "申請範圍外" and out["登面積"].iloc[0] == 1.0
    assert rep["partial"] == ["5"]


def test_partial_overlap_under_half_no_longer_overlaps_base():
    """錯法②b：切片只占 30% → 舊法整筆留下、與基地重疊；新法只留基地外殘餘。"""
    su = box(0, 0, 30, 100)
    g = _gdf([("5", box(0, 0, 100, 100))])
    out, _ = cn.split_self_neighbors(g, su, cn.base_keys_from_site(_site(["5"])),
                                     say=lambda *_: None)
    assert out.geometry.iloc[0].intersection(su).area < 1e-6
    assert abs(out.geometry.iloc[0].area - 7000) < 1e-6


def test_tiny_residual_dropped():
    """殘餘不足 1 ㎡ 的髮絲碎片丟棄（等同整筆在基地內）。"""
    su = box(0, 0, 100, 100)
    g = _gdf([("5", box(0, 0, 100.005, 100)), ("6", box(100, 0, 150, 50))])
    out, _ = cn.split_self_neighbors(g, su, cn.base_keys_from_site(_site(["5"])),
                                     say=lambda *_: None)
    assert list(out["地號"]) == ["6"]


def test_no_base_keys_falls_back_to_legacy_geometric_rule():
    """基地欄位認不出地號／對不上 → 退回舊幾何判定，行為與修前相同。"""
    su = box(0, 0, 100, 100)
    g = _gdf([("1", box(0, 0, 100, 100)), ("2", box(100, 0, 150, 50))])
    out, rep = cn.split_self_neighbors(g, su, set(), say=lambda *_: None)
    assert list(out["地號"]) == ["2"] and rep["legacy"]
    out2, rep2 = cn.split_self_neighbors(g, su, {("別段", "999")}, say=lambda *_: None)
    assert list(out2["地號"]) == ["2"] and rep2["legacy"]


def test_plain_case_unchanged():
    """既有案件無包覆／部分重疊時：基地自身剔除、鄰地原樣（含幾何與欄位）。"""
    su = box(0, 0, 100, 100)
    g = _gdf([("1", box(0, 0, 100, 100)), ("2", box(100, 0, 150, 50))])
    out, rep = cn.split_self_neighbors(g, su, cn.base_keys_from_site(_site(["1"])))
    assert list(out["地號"]) == ["2"]
    assert list(out.columns) == list(g.columns)
    assert out.geometry.iloc[0].equals(g.geometry.iloc[1])
    assert not rep["contained"] and not rep["partial"]


def test_base_keys_include_old_landno_and_normalize():
    site = gpd.GeoDataFrame([{"地號": "0128-0009", "原地號": "50", "段": "[1234]萬里段"}],
                            geometry=[box(0, 0, 1, 1)], crs=3826)
    keys = cn.base_keys_from_site(site)
    assert ("萬里", "128-9") in keys and ("萬里", "50") in keys
    assert cn.base_keys_from_site(gpd.GeoDataFrame({"x": [1]}, geometry=[box(0, 0, 1, 1)])) == set()


# ── 第二輪：基地自身辨識的回歸 ─────────────────────────────────────────────
def _site2(rows):
    """rows: (地號, 段, geometry)"""
    return gpd.GeoDataFrame([{"地號": n, "段": s} for n, s, _ in rows],
                            geometry=[g for _, _, g in rows], crs=3826)


def _gdf_seg(rows):
    """rows: (地號, 段, geometry)"""
    return gpd.GeoDataFrame(
        [{"地號": n, "段": s, "登面積": 7.0, "圖面積": g.area, "geometry": g} for n, s, g in rows],
        crs=3826)


def test_variant_segment_base_parcel_still_dropped_by_landno():
    """基地兩筆，其中 2 號因段名異體字對不上：(a) 正規化地號相同 → 仍視為基地自身剔除。"""
    b1, b2 = box(0, 0, 50, 100), box(50, 0, 100, 100)
    site = _site2([("1", "寶段", b1), ("2", "寶段", b2)])
    su = b1.union(b2)
    g = _gdf_seg([("1", "寶段", b1), ("2", "寳段", b2), ("300", "寶段", box(100, 0, 150, 50))])
    out, rep = cn.split_self_neighbors(g, su, cn.base_keys_from_site(site),
                                       say=lambda *_: None, base_geoms=list(site.geometry))
    assert list(out["地號"]) == ["300"]
    assert not rep["contained"] and sorted(rep["self"]) == ["1", "2"]


def test_variant_segment_renumbered_base_dropped_by_iou():
    """地號也對不上（重測改號未帶原地號），但幾何與基地某筆 IoU>=0.98 → (b) 仍剔除。"""
    b1, b2 = box(0, 0, 50, 100), box(50, 0, 100, 100)
    site = _site2([("1", "寶段", b1), ("2", "寶段", b2)])
    su = b1.union(b2)
    g = _gdf_seg([("1", "寶段", b1), ("77", "新段", box(50, 0, 100, 100.5))])
    out, rep = cn.split_self_neighbors(g, su, cn.base_keys_from_site(site),
                                       say=lambda *_: None, base_geoms=list(site.geometry))
    assert len(out) == 0 and not rep["contained"]


def test_real_enclosed_other_parcel_kept_with_warning_in_mixed_base():
    """基地多筆且部分對不上時，真正被包住的他人裡地（地號不在基地、幾何也不近似）仍保留並警告。"""
    b1, b2 = box(0, 0, 50, 100), box(50, 0, 100, 100)
    site = _site2([("1", "寶段", b1), ("2", "寶段", b2)])
    su = b1.union(b2)
    g = _gdf_seg([("1", "寶段", b1), ("2", "寳段", b2), ("128", "寶段", box(40, 40, 60, 60))])
    msgs = []
    out, rep = cn.split_self_neighbors(g, su, cn.base_keys_from_site(site),
                                       say=msgs.append, base_geoms=list(site.geometry))
    assert list(out["地號"]) == ["128"] and rep["contained"] == ["128"]
    assert any("基地內含他人裡地" in m for m in msgs)


def test_unkeyed_small_overlap_kept_whole_without_residual_column():
    """未命中地號的小面積重疊（界線誤差）維持修前行為：整筆保留、不裁切、無『殘餘』欄、無殘餘訊息。"""
    b1 = box(0, 0, 100, 100)
    site = _site2([("1", "寶段", b1)])
    nb = box(98, 0, 150, 50)                     # 與基地重疊 2x50，占該筆 ~3.8%
    g = _gdf_seg([("1", "寶段", b1), ("9", "寶段", nb)])
    msgs = []
    out, rep = cn.split_self_neighbors(g, b1, cn.base_keys_from_site(site),
                                       say=msgs.append, base_geoms=list(site.geometry))
    assert list(out["地號"]) == ["9"] and out.geometry.iloc[0].equals(nb)
    assert "殘餘" not in out.columns and not rep["partial"]
    assert not any("只占部分宗地" in m for m in msgs)


def test_residual_row_area_is_residual_geometry_area():
    """殘餘列的圖面積改為殘餘幾何面積；登面積保留原值；非殘餘列不動。"""
    su = box(0, 0, 70, 100)
    g = _gdf([("5", box(0, 0, 100, 100)), ("6", box(100, 0, 150, 50))])
    out, _ = cn.split_self_neighbors(g, su, cn.base_keys_from_site(_site(["5"])),
                                     say=lambda *_: None)
    r5 = out[out["地號"] == "5"].iloc[0]
    r6 = out[out["地號"] == "6"].iloc[0]
    assert abs(r5["圖面積"] - 3000) < 0.01 and r5["登面積"] == 1.0
    assert abs(r6["圖面積"] - 2500) < 1e-6 and r6["殘餘"] == ""


def test_classify_self_rules():
    assert cn.classify_self(0.99, False, True) == "self"
    assert cn.classify_self(0.99, False) == "contained"
    assert cn.classify_self(0.5, False) == "self"        # 沿用修前幾何規則
    assert cn.classify_self(0.1, False) == "neighbor"
    assert cn.classify_self(0.5, True) == "partial"
