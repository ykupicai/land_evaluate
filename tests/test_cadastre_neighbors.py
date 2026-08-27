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


def test_frame_envelope_exceeds_buffer_for_elongated_base():
    """鄰地涵蓋改由出圖框決定（2026-08-23 永康三民段385 案）：細長基地的 maxmap 出圖框
    遠大於固定 50m buffer，故框篩選才能填滿框、不留白。鎖住「框寬 > buffer 寬」。"""
    import overlay_placement as op
    from shapely.geometry import Polygon
    base = Polygon([(0, 0), (20, 230), (40, 0)])   # 約 230m 長、40m 寬的細長三角形

    def _isect(x1, y1, x2, y2, _g=base):
        it = _g.intersection(box(x1, y1, x2, y2))
        return 0.0 if it.is_empty else it.area

    pl = op.place_fixed(_isect, base.bounds, map_w=op.MAP2_W, map_h=op.MAP2_H)
    fx1, fx2, fy1, fy2 = pl.extent
    buf = base.buffer(50).bounds
    assert (fx2 - fx1) > (buf[2] - buf[0]), "出圖框寬應大於 50m buffer 寬（否則框內留白）"
    assert (fy2 - fy1) >= (buf[3] - buf[1]) * 0.9   # 高度方向亦不小於 buffer


def test_fetch_all_survives_one_failed_group(tmp_path, monkeypatch):
    """單段查詢失敗不得阻斷其他成功段的合併輸出（2026-08-19 湖口祥湖段案）。

    坑：fetch_all 迴圈裡 cmd_fetch 對 0 成功筆數會 raise SystemExit；若失敗段
    （常見＝未登錄地段 9999-xxx，線上無此段名）排最後，未包 try 會讓迴圈後的
    concat/寫檔整段沒跑到——前面成功段只留 _鄰地_<段>.shp、最終 鄰地.shp 沒產出。
    """
    good = (CO, TOWN, "祥湖段")
    bad = (CO, TOWN, "未登錄地段")   # 排最後，重現原始崩潰順序
    groups = {good: ["41", "42"], bad: ["9999-558"]}

    def fake_cmd_fetch(a):
        if a.section == "未登錄地段":
            raise SystemExit("✖ 無任何成功筆數，未產出 shp")
        gpd.GeoDataFrame(
            {"地號": a.landnos, "geometry": [box(0, 0, 1, 1), box(1, 0, 2, 1)]},
            crs=3826).to_file(str(Path(a.out) / f"{a.name}.shp"), engine="pyogrio")

    monkeypatch.setattr(cn, "cmd_fetch", fake_cmd_fetch)
    out_shp, merged = cn.fetch_all(groups, str(tmp_path))
    assert Path(out_shp).exists(), "失敗段不得阻斷合併，鄰地.shp 必須產出"
    assert len(merged) == 2 and set(merged["地號"]) == {"41", "42"}


def test_current_town_maps_renamed_towns(monkeypatch):
    """本機快照的鄉鎮名可能是改制前舊名，線上 API 只認現行名 → 整段查詢全滅
    （2026-08-26 員林案：快照「員林鎮」，2015 已改制「員林市」，鄰地一筆都抓不到）。

    鎖住三件事：①舊名要對回現行名 ②仍是原後綴者不可被亂改 ③對不到一律原名回傳。
    去尾比對只在同一縣市內做——後綴由行政層級決定，同縣市不會同時有「員林鎮」與
    「員林市」。⚠ 此寬鬆僅限鄉鎮，段名去尾會把「民權段」配到「民權一段」（別人的地）。
    """
    import types
    fake = types.SimpleNamespace(
        counties=lambda: ["彰化縣", "苗栗縣", "新北市"],
        towns=lambda c: {"彰化縣": ["員林市", "和美鎮", "彰化市"],
                         "苗栗縣": ["頭份市", "竹南鎮"],
                         "新北市": ["板橋區", "三重區"]}[c],
        _norm=lambda s: (s or "").replace("臺", "台").replace(" ", "").strip(),
        _pick=lambda names, want: next(
            (n for n in names if n.replace("臺", "台") == (want or "").replace("臺", "台")), None),
    )
    monkeypatch.setitem(sys.modules, "moa", fake)
    monkeypatch.setattr(cn, "SKILL", str(Path(__file__).resolve().parent))  # 讓 isdir 成立
    cn._TOWN_CACHE.clear()

    assert cn._current_town("彰化縣", "員林鎮") == "員林市"   # 鎮→市 改制
    assert cn._current_town("苗栗縣", "頭份鎮") == "頭份市"   # 同型
    assert cn._current_town("新北市", "板橋市") == "板橋區"   # 六都升格 市→區
    assert cn._current_town("彰化縣", "和美鎮") == "和美鎮"   # 仍是鎮，不可被亂改
    assert cn._current_town("彰化縣", "員林市") == "員林市"   # 已是現行名，原樣
    assert cn._current_town("彰化縣", "不存在鄉") == "不存在鄉"  # 對不到→原名，不阻斷
