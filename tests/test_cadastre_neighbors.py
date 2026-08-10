# -*- coding: utf-8 -*-
"""周邊地籍發現的「母號縮水」偵測 —— 把 2026-08-10 潮州中山段20 案的坑寫成斷言。

坑：本機快照某母號在 Ver 111年2月後就地分割成母號＋數個子號，快照仍是分割前的大範圍
（五魁段13 快照10,486㎡→即時4,999㎡，分出13-1/13-2）。discover() 只認舊母號、子號整批
不在快照 → 漏。原本的「缺口鄰近所有母號」探測會把緊鄰大缺口但沒縮水的 16 個母號全收、
每個探 80 子號→逾時，且放大寫檔鎖窗口導致補正 crash。

本測試鎖住 find_shrunk_mothers 只精準抓「即時面積 < 快照×0.9」的母號（純合成幾何、
不打網路）：
  1. 縮水母號要被抓到；
  2. 沒縮水的母號不可誤觸（否則又回到探測 16 個母號的慢路徑）；
  3. 面積相近（雜訊級微差）不算縮水；
  4. _section_name 段/小段組字正確。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402
from shapely.geometry import box  # noqa: E402

import cadastre_neighbors as cn  # noqa: E402

CO, TOWN, SEG = "屏東縣", "潮州鎮", "五魁"


def _snap(rows):
    """rows: (母, 子, x0,y0,x1,y1) → 快照候選 GeoDataFrame（欄同本機 shp）。"""
    recs = [{"縣市": CO, "鄉鎮": TOWN, "段": SEG, "小段": None,
             "宗地母": m, "宗地子": c, "geometry": box(x0, y0, x1, y1)}
            for (m, c, x0, y0, x1, y1) in rows]
    return gpd.GeoDataFrame(recs, crs=3826)


def _live(rows):
    """rows: (地號, x0,y0,x1,y1) → 即時回填 GeoDataFrame（『段』是完整「五魁段」）。"""
    recs = [{"縣市": CO, "鄉鎮": TOWN, "段": f"{SEG}段", "地號": ln,
             "geometry": box(x0, y0, x1, y1)}
            for (ln, x0, y0, x1, y1) in rows]
    return gpd.GeoDataFrame(recs, crs=3826)


def test_shrunk_mother_detected():
    # 快照：母號13 = 一大塊 100×100（10,000㎡）；母號8 = 50×50（沒縮水）
    sur = _snap([(13, 0, 0, 0, 100, 100), (8, 0, 200, 0, 250, 50)])
    # 即時：13 只剩左半 40×100（4,000㎡，縮水）；8 原樣 50×50
    live = _live([("13", 0, 0, 40, 100), ("8", 200, 0, 250, 50)])
    targets = cn.find_shrunk_mothers(sur, live)
    key = (CO, TOWN, f"{SEG}段")
    assert key in targets, "縮水母號 13 應被抓到"
    assert "0013" in targets[key], "母號13(縮水 10,000→4,000)須列為探測目標"
    assert "0008" not in targets.get(key, set()), "沒縮水的母號8 不可誤觸（否則回到過度探測）"


def test_no_shrink_not_flagged():
    # 面積僅雜訊級微差（重測邊界 <GAP_MIN_AREA）不算縮水
    sur = _snap([(13, 0, 0, 0, 100, 100)])
    live = _live([("13", 0, 0, 100, 99.99)])   # 少 ~1㎡
    assert cn.find_shrunk_mothers(sur, live) == {}, "雜訊級微差不可判為縮水"


def test_mother_entirely_gone_flagged():
    # 母號在即時回填完全不在（live_area=0）也算縮水（極端分割/重測改號）
    sur = _snap([(9, 0, 0, 0, 30, 30)])
    live = _live([("8", 500, 500, 520, 520)])   # 完全沒有母號9
    targets = cn.find_shrunk_mothers(sur, live)
    assert "0009" in targets.get((CO, TOWN, f"{SEG}段"), set())


def test_section_name():
    assert cn._section_name("五魁", None) == "五魁段"
    assert cn._section_name("考試潭", "鹿寮") == "考試潭段鹿寮小段"
    assert cn._section_name("中山", float("nan")) == "中山段"
