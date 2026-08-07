# -*- coding: utf-8 -*-
"""check_zones.run_item——鎖住 2026-08-02 龍崎案修過的兩個判定語意。

  1. area 型的「反向」旗標：預設＝「達門檻才符合」（如山坡地 ≥10 公頃）；
     反向＝「逾門檻即觸發」（如環評法§5Ⅲ⑨ 40 公頃以上應實施環評）。
     當時 YAML 已寫「反向: true」但引擎忽略，51.9 公頃被印成「✓ 符合」——
     與「應辦環評」的真意完全相反。
  2. attribute 型的欄位候選清單（tcdgis 叫「使用分區」、⓪a 叫「使分」）。
"""
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_zones import run_item  # noqa: E402


def _site(n=1):
    g = gpd.GeoDataFrame({"地號": [f"{i+1}" for i in range(n)],
                          "使分": ["工業區"] * n},
                         geometry=[box(0, 0, 100, 100)] * n, crs="EPSG:3826")
    return g


def _run(item, tot_m2):
    log, results = [], []
    run_item(item, _site(), box(0, 0, 100, 100), tot_m2, None, "臺南市", log, results)
    return results[0], "\n".join(log)


AREA = {"id": "t", "名稱": "測試門檻", "條號": "§x", "類型": "area", "級別": "△",
        "非山坡地_公頃": 40, "山坡地_公頃": 40}


def test_area_default_threshold_is_floor():
    """預設語意：達門檻＝符合（面積下限）。"""
    rec, _ = _run(dict(AREA), 50 * 10000)          # 50 ha ≥ 40 ha
    assert rec["判定"] == "符合"
    rec, _ = _run(dict(AREA), 30 * 10000)          # 30 ha < 40 ha
    assert rec["判定"] == "△"


def test_area_reverse_threshold_is_ceiling():
    """反向語意：逾門檻＝觸發（環評 40 公頃以上必辦）——不可再印成「符合」。"""
    rec, log = _run(dict(AREA, 反向=True), 51.8753 * 10000)   # 龍崎案實際值
    assert rec["判定"] == "△" and rec["反向"] is True
    assert "逾門檻" in log and "符合" not in log
    rec, _ = _run(dict(AREA, 反向=True), 30 * 10000)
    assert rec["判定"] == "未逾門檻"


def test_attribute_field_candidates():
    """欄位候選：shp 只有「使分」時，候選清單 ['使用分區','使分'] 仍要能比對。"""
    item = {"id": "a", "名稱": "分區含值", "條號": "§y", "類型": "attribute",
            "級別": "╳", "欄位": ["使用分區", "使分"], "含值": "工業區"}
    log, results = [], []
    run_item(item, _site(), box(0, 0, 100, 100), 1e4, None, "臺南市", log, results)
    assert results[0]["判定"] == "╳"


def test_attribute_missing_field_flags_not_silent():
    """兩個候選欄都不在 → 「欄位缺失須確認」，不是靜默當成未觸及。"""
    item = {"id": "a", "名稱": "分區含值", "條號": "§y", "類型": "attribute",
            "級別": "╳", "欄位": ["不存在欄"], "含值": "工業區"}
    log, results = [], []
    run_item(item, _site(), box(0, 0, 100, 100), 1e4, None, "臺南市", log, results)
    assert results[0]["判定"] == "欄位缺失須確認"


def test_note_type_prints_remark_not_missing_layer():
    """note 型（社福設施_臺南市.yaml 實錯，2026-08-06 後壁案重跑發現）：純敘述提醒，
    不該落到通用 overlay 分支印成「找不到圖層檔案」——那看起來像資料缺失，
    實際上該條款根本沒有可套疊的圖資，是型別沒被引擎認得。"""
    item = {"id": "n", "名稱": "3年內設立期限", "條號": "§z", "類型": "note",
            "級別": "△", "備註": "許可後3年內須設立，逾期廢止。"}
    log, results = [], []
    run_item(item, _site(), box(0, 0, 100, 100), 1e4, None, "臺南市", log, results)
    assert results[0]["判定"] == "△"
    assert "找不到圖層檔案" not in "\n".join(log)
    assert "許可後3年內須設立" in "\n".join(log)
