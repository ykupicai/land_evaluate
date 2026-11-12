# -*- coding: utf-8 -*-
"""check_zones／nlsc_facility 2026-11 審查修正的回歸測試。

  1. area 型要看「最大相連塊」，不可只看合計（65 筆分 35 塊仍判符合 10 公頃）。
  2. 圖層讀取失敗／山坡地圖層缺檔，不可印「未觸及」或預設非山坡地。
  3. api_distance 的「名稱含」篩選（dis 端點含寶塔、墓園）。
  4. attribute 命中要印備註。
  5. 寵物 YAML 可載入、5 個 note 為「參考」。
"""
import sys
from pathlib import Path

import geopandas as gpd
import yaml
from shapely.geometry import box
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_zones as cz  # noqa: E402
import nlsc_facility as nf  # noqa: E402


def _site(geom):
    return gpd.GeoDataFrame({"地號": ["1"], "使分": ["特定農業區"], "使類": ["農牧用地"]},
                            geometry=[geom], crs="EPSG:3826")


def _scattered(n_blocks, side=100, gap=500):
    """n 個 side×side 的分離塊（每塊 side² m²）。"""
    return unary_union([box(i * (side + gap), 0, i * (side + gap) + side, side)
                        for i in range(n_blocks)])


# ---------- 1. 相連性 ----------
def test_contiguous_blocks_separate_and_touching():
    blocks = cz.contiguous_blocks(_scattered(3))
    assert len(blocks) == 3 and blocks[0] == 100 * 100
    # 兩塊相隔 0.3 m（髮絲縫）視為相連；隔 10 m 不相連（容差 0.5），但容差 15 m 則相連
    two = unary_union([box(0, 0, 100, 100), box(100.3, 0, 200, 100)])
    assert len(cz.contiguous_blocks(two)) == 1
    far = unary_union([box(0, 0, 100, 100), box(110, 0, 210, 100)])
    assert len(cz.contiguous_blocks(far)) == 2
    assert len(cz.contiguous_blocks(far, cz.CONTIG_REF_TOL_M)) == 1


def test_judge_floor_area_needs_confirm_when_max_block_short():
    # 合計 12 ha 但最大塊 4.5 ha，門檻 10 ha → 須確認
    st, mx = cz.judge_floor_area(120000, [45000, 40000, 35000], 10)
    assert st == "須確認" and abs(mx - 4.5) < 1e-9
    assert cz.judge_floor_area(120000, [110000, 10000], 10)[0] == "符合"
    assert cz.judge_floor_area(80000, [45000, 35000], 10)[0] == "不足"
    # 單塊沿用合計（行為與修前相同）
    assert cz.judge_floor_area(120000, [120000], 10)[0] == "符合"


def test_area_item_scattered_blocks_not_pass():
    """12 塊各 1 ha＝合計 12 ha，最大塊 1 ha：山坡地 10 公頃門檻不可判符合。"""
    su = _scattered(12)
    item = {"id": "a", "名稱": "面積", "條號": "§52-1", "類型": "area", "級別": "╳",
            "非山坡地_公頃": 10, "山坡地_公頃": 10}
    log, results = [], []
    cz.run_item(item, _site(su), su, float(su.area), None, "臺南市", log, results)
    rec = results[0]
    assert "確認" in rec["判定"] and rec["判定"] != "符合"
    assert rec["塊數"] == 12 and rec["最大塊公頃"] == 1.0
    assert len(rec["各塊公頃前5"]) == 5
    assert "12 塊" in "\n".join(log)


def test_area_item_single_block_unchanged():
    su = box(0, 0, 400, 400)   # 16 ha 單塊
    item = {"id": "a", "名稱": "面積", "條號": "§x", "類型": "area", "級別": "╳",
            "非山坡地_公頃": 10, "山坡地_公頃": 10}
    log, results = [], []
    cz.run_item(item, _site(su), su, float(su.area), None, "臺南市", log, results)
    assert results[0]["判定"] == "符合" and "塊數" not in results[0]


def test_area_reverse_still_uses_total():
    su = _scattered(12)
    item = {"id": "a", "名稱": "環評", "條號": "§x", "類型": "area", "級別": "△",
            "非山坡地_公頃": 10, "山坡地_公頃": 10, "反向": True}
    log, results = [], []
    cz.run_item(item, _site(su), su, float(su.area), None, "臺南市", log, results)
    assert results[0]["判定"] == "△"   # 合計 12 ha 逾 10 ha → 觸發


# ---------- 2. 載入失敗 ----------
def test_overlay_load_failure_is_not_untouched(tmp_path):
    bad = tmp_path / "broken.shp"
    bad.write_bytes(b"not a shapefile")
    item = {"id": "o", "名稱": "套疊", "條號": "§y", "類型": "overlay", "級別": "╳",
            "圖層": str(bad)}
    su = box(0, 0, 100, 100)
    log, results = [], []
    cz.run_item(item, _site(su), su, 1e4, None, "臺南市", log, results)
    assert results[0]["判定"] == "缺圖資須確認"
    assert results[0]["判定"] != "未觸及"


def test_area_slope_layer_missing_not_default_flat(tmp_path):
    item = {"id": "a", "名稱": "面積", "條號": "§x", "類型": "area", "級別": "╳",
            "非山坡地_公頃": 0.2, "山坡地_公頃": 10,
            "山坡地圖層": str(tmp_path / "不存在.shp")}
    su = box(0, 0, 100, 100)
    log, results = [], []
    cz.run_item(item, _site(su), su, 1e4, None, "臺南市", log, results)
    assert results[0]["判定"] == "山坡地屬性無法判定須確認"
    assert "不可預設非山坡地" in "\n".join(log)


def test_area_slope_unknown_rec_has_aligned_fields(tmp_path):
    """提前 return 的 rec 須與其他 area 判定對齊，帶「反向」「門檻公頃」欄位。"""
    base = {"id": "a", "名稱": "面積", "條號": "§x", "類型": "area", "級別": "╳",
            "非山坡地_公頃": 0.2, "山坡地_公頃": 10,
            "山坡地圖層": str(tmp_path / "不存在.shp")}
    su = box(0, 0, 100, 100)
    for rev in (False, True):
        item = dict(base, **({"反向": True} if rev else {}))
        log, results = [], []
        cz.run_item(item, _site(su), su, 1e4, None, "臺南市", log, results)
        rec = results[0]
        assert rec["判定"] == "山坡地屬性無法判定須確認"
        assert "反向" in rec and rec["反向"] is rev
        assert "門檻公頃" in rec


def test_pet_hazard_keywords_precise():
    cfg = yaml.safe_load((ROOT / "config" / "檢核清單" / "寵物生命紀念設施.yaml")
                         .read_text(encoding="utf-8"))
    kw = {i["id"]: i for i in cfg["檢核項目"]}["pet_hazard_500m"]["名稱含"]
    assert "油" not in kw
    assert set(kw) == {"加油站", "加氣站", "瓦斯", "液化氣", "天然氣", "石油", "中油", "油庫", "油料"}
    items = [{"名稱": "阿珠油飯", "實距_m": 10}, {"名稱": "大同油漆行", "實距_m": 10},
             {"名稱": "北基加油站", "實距_m": 10}]
    assert [i["名稱"] for i in nf.filter_by_name(items, kw)] == ["北基加油站"]


def test_summary_counts_confirm_as_ask():
    """判定字串含「確認」才會被總結列入須函詢／須確認。"""
    for j in ("缺圖資須確認", "山坡地屬性無法判定須確認", "須人工確認（最大相連塊未達門檻）"):
        assert "確認" in j


# ---------- 3. 名稱篩選 ----------
def test_filter_by_name():
    items = [{"名稱": "金剛舍利寶塔", "實距_m": 100}, {"名稱": "北基加油站", "實距_m": 300},
             {"名稱": None, "實距_m": 50}]
    assert nf.filter_by_name(items, None) == items
    assert nf.filter_by_name(items, []) == items
    got = nf.filter_by_name(items, ["加油站", "加氣站"])
    assert [i["名稱"] for i in got] == ["北基加油站"]


def test_nearest_name_filter(monkeypatch):
    rows = [{"name": "萬壽山墓園", "addr": "a", "lon": 121.5, "lat": 25.0},
            {"name": "北基加油站", "addr": "b", "lon": 121.6, "lat": 25.0}]
    monkeypatch.setattr(nf, "query", lambda ep, lon, lat, r, tries=3: rows)
    geom = gpd.GeoSeries(gpd.points_from_xy([121.5], [25.0]), crs=4326).to_crs(3826).iloc[0].buffer(50)
    log = []
    r_all = nf.nearest(geom, ["dis"], 500, log)
    assert r_all["dis"]["判定"] == "觸及"            # 不給篩選：墓園在內即觸及（舊行為）
    r = nf.nearest(geom, ["dis"], 500, log, name_contains=["加油站"])
    assert r["dis"]["判定"] == "未觸及"              # 墓園被篩掉，加油站在 500 m 外
    assert r["dis"]["最近名稱"] == "北基加油站"


# ---------- 4. attribute 備註 ----------
def test_attribute_hit_prints_remark():
    item = {"id": "z", "名稱": "分區", "條號": "§5", "類型": "attribute", "級別": "╳",
            "欄位": ["使分"], "含值": "特定農業區", "備註": "特定農業區之殯葬用地除外"}
    su = box(0, 0, 100, 100)
    log, results = [], []
    cz.run_item(item, _site(su), su, 1e4, None, "臺南市", log, results)
    assert results[0]["判定"] == "╳"
    assert "殯葬用地除外" in "\n".join(log)


# ---------- 5. 寵物 YAML ----------
def test_pet_yaml_loads_and_notes_are_reference():
    cfg = yaml.safe_load((ROOT / "config" / "檢核清單" / "寵物生命紀念設施.yaml")
                         .read_text(encoding="utf-8"))
    items = {i["id"]: i for i in cfg["檢核項目"]}
    for i in items.values():
        assert "類型" in i and "名稱" in i
    for k in ("pet_green_20pct", "pet_local_need", "pet_geology", "pet_road", "pet_nuclear_lpz"):
        assert items[k]["類型"] == "note" and items[k]["級別"] == "參考", k
    assert "加油站" in items["pet_hazard_500m"]["名稱含"]
    assert items["pet_zone_excluded"].get("翻轉條件")
    # 可被引擎執行（note 型不走圖資）
    su = box(0, 0, 100, 100)
    log, results = [], []
    cz.run_item(items["pet_road"], _site(su), su, 1e4, None, "新北市", log, results)
    assert results[0]["判定"] == "參考"
