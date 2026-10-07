# -*- coding: utf-8 -*-
"""零星土地檢核（`sporadic_land_check`）—— 把 2026-10-03 彌陀安港段587案的判準寫成斷言。

鎖住的事（全部合成幾何，不打網路）：
  1. §35 前提＝毗鄰**甲種、丙種**建築用地；只毗鄰鄉村區**乙種**建築用地者，
     即使被建築用地完全包圍、面積極小，§35 仍「不符」（彌陀案實例，這是最容易犯的錯）。
  2. 聚合：非鄉村區之六類農業用地、0.3m 容差相接；鄉村區內的農牧用地、交通用地、
     都市計畫區、相距 >0.3m 者都不併入；面積用登記面積。
     ★ 水利用地預設當水溝隔絕要素、不併入（§35 第二款、§35-1 第一三款明列水溝）。
  7. §35Ⅳ（§35-1Ⅲ 準用）的 78.04.03 前編定、道路水溝寬 4m 資料上查不到 →
     §35 第一款、§35-1 第一款判「符合」時一律降為「待查」。
  3. 平均寬度＝面積／最小外接矩形長邊。
  4. 角碰不算毗鄰。
  5. 口袋被查詢框切斷時必須標「下限值」，不可把框內筆數當成整塊面積交付。
  6. 六類農業用地與 greenbelt_ratio 同一份定義；框查原始欄名可正規化。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import geopandas as gpd  # noqa: E402
import pytest  # noqa: E402
from shapely.geometry import box  # noqa: E402

import greenbelt_ratio  # noqa: E402
import sporadic_land_check as slc  # noqa: E402

ENV = box(-500, -500, 500, 500)


def frame(rows):
    """rows: (地號, 使分, 使類, geom[, 都計區]) → 正規化後的框查 GeoDataFrame。"""
    recs, geoms = [], []
    for r in rows:
        no, lz, lt, g = r[:4]
        recs.append({"地號": no, "段": "測試段", "使分": lz, "使類": lt,
                     "都計區": r[4] if len(r) > 4 else "非都計", "登面積": g.area})
        geoms.append(g)
    return slc.normalize_frame(gpd.GeoDataFrame(recs, geometry=geoms, crs="EPSG:3826"))


def base_of(*geoms, lt="農牧用地", reg=None):
    recs = [{"地號": str(i + 1), "段": "測試段", "使分": "一般農業區", "使類": lt,
             "登面積": (reg[i] if reg else g.area)} for i, g in enumerate(geoms)]
    return gpd.GeoDataFrame(recs, geometry=list(geoms), crs="EPSG:3826")


def ring(lz, lt, b=(0, 0, 10, 10), w=10):
    """把 b 四面包起來的四塊宗地。"""
    x1, y1, x2, y2 = b
    return [("W", lz, lt, box(x1 - w, y1 - w, x1, y2 + w)),
            ("E", lz, lt, box(x2, y1 - w, x2 + w, y2 + w)),
            ("S", lz, lt, box(x1, y1 - w, x2, y1)),
            ("N", lz, lt, box(x1, y2, x2, y2 + w))]


# ---------------------------------------------------------------- 1. §35 前提
def test_only_yi_building_land_does_not_satisfy_s35():
    """★ 錯法：被鄉村區乙種建築用地完全包圍、100 ㎡ → 判 §35 成立。正解：前提不成立。"""
    res = slc.analyze(base_of(box(0, 0, 10, 10)), frame(ring("鄉村區", "乙種建築用地")), ENV)
    s35 = res["判定"]["§35"]
    assert s35["前提"]["判定"] == slc.NG
    assert s35["初判"] == slc.NG, "只毗鄰乙種建築用地，§35 不得判成立"
    assert "乙種" in s35["前提"]["說明"]
    # 同一塊地走 §35-1（鄉村區邊緣）則可能符合——兩條不可混為一談
    assert res["判定"]["§35-1"]["初判"] != slc.NG


def test_jia_building_land_satisfies_s35_premise():
    res = slc.analyze(base_of(box(0, 0, 10, 10)), frame(ring("一般農業區", "甲種建築用地")), ENV)
    s35 = res["判定"]["§35"]
    assert s35["前提"]["判定"] == slc.OK
    # ★ 錯法：直接判 OK。包圍之建築用地須 78.04.03 前編定（§35Ⅳ）資料上查不到 → 待查
    assert s35["各款"]["一"]["判定"] == slc.PEND
    assert "78.04.03" in s35["各款"]["一"]["說明"]
    assert s35["初判"] == "可能符合"


def test_bing_counts_too():
    res = slc.analyze(base_of(box(0, 0, 10, 10)), frame(ring("山坡地保育區", "丙種建築用地")), ENV)
    assert res["判定"]["§35"]["前提"]["判定"] == slc.OK


def test_corner_touch_is_not_adjacency():
    """只在角點碰到甲建（0.3m 容差下約 0.6m）不算毗鄰。"""
    rows = [("J", "一般農業區", "甲種建築用地", box(10, 10, 20, 20)),
            ("A", "一般農業區", "交通用地", box(-10, -10, 0, 20))]
    res = slc.analyze(base_of(box(0, 0, 10, 10)), frame(rows), ENV)
    assert res["判定"]["§35"]["前提"]["判定"] == slc.NG
    corner = [r for r in res["基地"]["緊鄰宗地"] if r["地號"] == "J"]
    assert corner and corner[0]["角碰"]


# ---------------------------------------------------------------- 2. 聚合
def test_pocket_aggregation_rules():
    rows = [
        ("A1", "一般農業區", "農牧用地", box(10.2, 0, 30, 10)),   # 髮絲縫 0.2m → 併入
        ("A2", "一般農業區", "水利用地", box(30, 0, 32, 10)),      # 水利用地＝水溝隔絕 → 不併
        ("A3", "一般農業區", "養殖用地", box(32, 0, 50, 10)),      # 被 A2 隔開 → 不併
        ("G1", "一般農業區", "農牧用地", box(0, 10.5, 10, 20)),    # 相距 0.5m → 不併
        ("V1", "鄉村區", "農牧用地", box(-10, 0, 0, 10)),           # 鄉村區內農牧 → 不併
        ("R1", "一般農業區", "交通用地", box(0, -4, 50, 0)),        # 道路 → 不併、且隔斷
        ("B1", "一般農業區", "農牧用地", box(0, -14, 50, -4)),      # 路的另一側 → 不併
        ("U1", None, None, box(50, 0, 60, 10), "都計"),             # 都市計畫區 → 不併
    ]
    res = slc.analyze(base_of(box(0, 0, 10, 10)), frame(rows), ENV)
    members = set(res["_geom"]["members"]["地號"])
    assert members == {"A1"}
    p = res["所在連續農業用地"]
    assert p["筆數"] == 2
    assert p["登記面積_m2"] == pytest.approx(100 + 19.8 * 10, abs=0.01)
    assert p["外圍接觸_m"]["水利用地"] > 9
    assert p["外圍接觸_m"]["交通用地"] > 29        # 口袋 x 0~30（水溝擋住 A3）
    assert p["外圍接觸_m"]["鄉村區"] > 9
    assert not p["觸及查詢框邊"]
    # 敏感度分析（水利用地併入）才會連到 A3 與都市計畫區
    res2 = slc.analyze(base_of(box(0, 0, 10, 10)), frame(rows), ENV, water_barrier=False)
    assert set(res2["_geom"]["members"]["地號"]) == {"A1", "A2", "A3"}
    assert res2["所在連續農業用地"]["外圍接觸_m"]["都市計畫區"] > 9


def test_water_barrier_sensitivity():
    rows = [("A1", "一般農業區", "水利用地", box(10, 0, 12, 10)),
            ("A2", "一般農業區", "農牧用地", box(12, 0, 40, 10))]
    assert res_members(rows, False) == {"A1", "A2"}
    assert res_members(rows, True) == set()
    # 預設＝水利用地當隔絕
    res = slc.analyze(base_of(box(0, 0, 10, 10)), frame(rows), ENV)
    assert set(res["_geom"]["members"]["地號"]) == set()


def test_ditch_isolates_base_for_s35_1_item1():
    """★ code review 錯法重現：西鄉村區、南北道路、東側 4m 水溝外為大片農地。
    舊算法把水溝併進口袋 → 口袋 2 萬㎡、§35-1 第一款「不符」。正解：水溝是隔絕要素，
    口袋只剩基地本身；但 4m 寬與 78.04.03 前編定查不到 → 「待查」而非「符合」。"""
    rows = [("V", "鄉村區", "乙種建築用地", box(-10, -4, 0, 14)),
            ("RS", "一般農業區", "交通用地", box(0, -4, 10, 0)),
            ("RN", "一般農業區", "交通用地", box(0, 10, 10, 14)),
            ("D", "一般農業區", "水利用地", box(10, -4, 14, 14)),
            ("F", "一般農業區", "農牧用地", box(14, -100, 214, 100))]
    res = slc.analyze(base_of(box(0, 0, 10, 10)), frame(rows), ENV)
    assert res["所在連續農業用地"]["登記面積_m2"] == pytest.approx(100)
    item1 = res["判定"]["§35-1"]["各款"]["一"]
    assert item1["判定"] == slc.PEND
    assert "4m" in item1["說明"] and "78.04.03" in item1["說明"]
    old = slc.analyze(base_of(box(0, 0, 10, 10)), frame(rows), ENV, water_barrier=False)
    assert old["判定"]["§35-1"]["各款"]["一"]["判定"] == slc.NG


def res_members(rows, wb):
    res = slc.analyze(base_of(box(0, 0, 10, 10)), frame(rows), ENV, water_barrier=wb)
    return set(res["_geom"]["members"]["地號"])


def test_pocket_uses_registered_area():
    rows = [("A1", "一般農業區", "農牧用地", box(10, 0, 20, 10))]
    res = slc.analyze(base_of(box(0, 0, 10, 10), reg=[123.45]), frame(rows), ENV)
    assert res["基地"]["登記面積_m2"] == pytest.approx(123.45)
    assert res["所在連續農業用地"]["登記面積_m2"] == pytest.approx(223.45)


def test_large_pocket_fails_s35_1_item3():
    """彌陀型：基地本身 <0.5 公頃，但所在連續農業用地 >0.5 公頃 → 第三款不符。"""
    rows = [("A1", "一般農業區", "農牧用地", box(30, 0, 130, 60))] + \
           ring("鄉村區", "乙種建築用地", b=(0, 0, 30, 30))[:1]
    res = slc.analyze(base_of(box(0, 0, 30, 30)), frame(rows), ENV)
    assert res["基地"]["登記面積_m2"] < slc.AREA_05
    item3 = res["判定"]["§35-1"]["各款"]["三"]
    assert item3["判定"] == slc.NG
    assert res["判定"]["§35-1"]["初判"] == slc.NG


# ---------------------------------------------------------------- 3. 平均寬度
def test_average_width_area_over_long_side():
    res = slc.analyze(base_of(box(0, 0, 60, 20)), frame([]), ENV)
    b = res["基地"]
    assert b["外接矩形長邊_m"] == pytest.approx(60)
    assert b["外接矩形短邊_m"] == pytest.approx(20)
    assert b["平均寬度_m"] == pytest.approx(20)
    assert res["判定"]["§35"]["各款"]["四"]["判定"] == slc.NG


def test_narrow_strip_is_pending_not_pass():
    res = slc.analyze(base_of(box(0, 0, 100, 8)), frame([]), ENV)
    assert res["基地"]["平均寬度_m"] == pytest.approx(8)
    assert res["判定"]["§35"]["各款"]["四"]["判定"] == slc.PEND   # 對邊須人工確認


def test_rotated_rectangle_width():
    from shapely import affinity
    g = affinity.rotate(box(0, 0, 50, 5), 37, origin="centroid")
    res = slc.analyze(base_of(g), frame([]), ENV)
    assert res["基地"]["平均寬度_m"] == pytest.approx(5, abs=0.01)


# ---------------------------------------------------------------- 4. 觸框
def test_pocket_touching_frame_edge_is_lower_bound():
    rows = [("A1", "一般農業區", "農牧用地", box(10, 0, 600, 10))]   # 伸出框外
    res = slc.analyze(base_of(box(0, 0, 10, 10)), frame(rows), ENV)
    p = res["所在連續農業用地"]
    assert p["觸及查詢框邊"] and p["面積為下限值"]
    assert res["警告"] and "下限" in res["警告"][0]
    assert "框內下限" in res["判定"]["§35-1"]["各款"]["三"]["說明"]


# ---------------------------------------------------------------- 5. 小工具
def test_area_test_plus10():
    assert slc.area_test(1200, 1200, True)[0] == slc.OK
    assert slc.area_test(1300, 1200, True)[0] == slc.PEND
    assert slc.area_test(1321, 1200, True)[0] == slc.NG
    assert slc.area_test(5001, 5000, False)[0] == slc.NG   # §35-1③ 無 10% 增加


def test_same_landuse_neighbour_blocks_item5():
    rows = [("A1", "一般農業區", "農牧用地", box(10, 0, 20, 10))]
    res = slc.analyze(base_of(box(0, 0, 10, 10)), frame(rows), ENV)
    assert res["判定"]["§35"]["各款"]["五"]["判定"] == slc.NG


def test_ag_kinds_single_source():
    assert slc.AG_KINDS is greenbelt_ratio.AG_KINDS
    assert "交通用地" not in slc.AG_KINDS


def test_normalize_raw_frame_columns():
    raw = gpd.GeoDataFrame([{
        "地號": "04830001", "段": "安港", "小段": None, "使用分區": "一般農業區",
        "使用類別": "農牧用地", "都市計畫區": "非都計", "登記面積_平方公尺": 481.03}],
        geometry=[box(0, 0, 1, 1)], crs="EPSG:3826")
    g = slc.normalize_frame(raw)
    r = g.iloc[0]
    assert (r["地號"], r["段"], r["使分"], r["使類"], r["都計區"]) == \
        ("483-1", "安港段", "一般農業區", "農牧用地", "非都計")
    assert r["登面積"] == pytest.approx(481.03)
    assert slc.normalize_frame(gpd.GeoDataFrame(
        [{"地號": "09001", "段": "x段", "使類": None}], geometry=[box(0, 0, 1, 1)],
        crs="EPSG:3826")).iloc[0]["未登記"] == "是"


def test_caveat_on_every_article():
    res = slc.analyze(base_of(box(0, 0, 10, 10)), frame(ring("鄉村區", "乙種建築用地")), ENV)
    for k in ("§35", "§35-1"):
        assert res["判定"][k]["備註"] == slc.CAVEAT
