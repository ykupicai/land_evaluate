# -*- coding: utf-8 -*-
"""版面幾何核心：最大化地圖 A4 橫式版面常數、比例尺/範圍計算，以及「自動避讓角落疊圖」。

刻意**不 import qgis 或 shapely**，只做純數學，讓它能：
  * 在一般 python 下用 shapely 幾何做單元測試（tests/test_overlay_placement.py）；
  * 在 QGIS 內建 python 下由 build_layout.py 以 QgsGeometry 呼叫。

呼叫端只要提供一個 `intersect_area_fn(x1, y1, x2, y2) -> float`：回傳「基地」幾何落在
該地面矩形內的面積（>0 表示會壓到基地）。幾何函式庫由呼叫端決定，這裡不綁定。

座標慣例：extent = (L, R, B, T) = (xmin, xmax, ymin, ymax) 地面座標（公尺）。
版面 y 軸向下、地面 y 軸向上，故「頁面下緣(B)↔地面 ymin」、「頁面上緣(T)↔地面 ymax」。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# --- 最大化地圖 A4 橫式版面（mm；與設計討論定案值一致）---------------------------
# 2026-07 使用者定案：不放案名/圖名標題列（檔名與 05 出圖清單已載明），地圖上緣
# 直接頂到頁邊 5mm，高度因此從 191 增為 199。
PAGE_W, PAGE_H = 297.0, 210.0
MAP_X, MAP_Y, MAP_W, MAP_H = 5.0, 5.0, 240.0, 199.0    # 地圖框：右緣 245mm、下緣 204mm
LEGEND_X, LEGEND_Y, LEGEND_W, LEGEND_H = 247.0, 5.0, 45.0, 199.0  # 右欄只留圖例

# 角落疊圖框尺寸（mm）。名稱→(寬, 高)。可依內容微調。
OVERLAY_SCALE_NORTH = ("scale_north", 66.0, 26.0)   # 指北針＋比例尺文字＋比例尺
OVERLAY_LOC_PARCEL = ("loc_parcel", 95.0, 20.0)     # 位置＋涵蓋地號（收合成一行）
OVERLAY_INSET = 2.0                                  # 疊圖框距地圖內邊的留白（mm）

# ── 訓練專案「地圖出圖版面規範.yaml」定案版面（maxmap v2，2026-08-19）─────────────
# 附圖版面：地圖近滿版、指北針/比例尺固定「左上」、圖例固定「右下」方框（取代右欄）。
# 舊常數（MAP_X.. / LEGEND_X.. / OVERLAY_* / place_overlays）全數保留，供單元測試與
# 舊 compact 相容；build_layout 改用下列 MAP2_* + place_fixed()。
MAP2_X, MAP2_Y, MAP2_W, MAP2_H = 5.0, 5.0, 287.0, 200.0   # 近滿版地圖框（右緣292/下緣205）
OVERLAY_INSET2 = 3.0                                       # in-map 疊圖框距地圖內邊留白
NORTH_SCALE_BOX = ("north_scale", 62.0, 22.0)   # 左上：指北針＋比例尺文字＋比例尺
LEGEND_BOX = ("legend", 56.0, 58.0)             # 右下：圖例白框（高為上限，內容較短時留白）
LOC_PARCEL_BOX = ("loc_parcel", 95.0, 16.0)     # 左下：位置＋涵蓋地號（選用）
# 固定角落對照（YAML 定案；place_fixed 用來算各框的地面矩形以判避讓）
FIXED_CORNER = {"north_scale": "TL", "legend": "BR", "loc_parcel": "BL"}

# 比例尺階梯：就近取整，超出最大值則進位到千位（與參考案 make_maps.py 一致）。
SCALE_LEVELS = [500, 750, 1000, 1250, 1500, 2000, 2500, 3000, 4000, 5000]

# 角落優先序：右下→左下→右上→左上（多角皆淨空時的預設擺放偏好）。
CORNER_PRIORITY = ["BR", "BL", "TR", "TL"]


@dataclass
class Placement:
    """一次擺放的結果。assign: {疊圖名: 角落}；scale/extent 為（必要時退階後的）最終值。"""
    assign: dict          # {name: "BR"/"BL"/"TR"/"TL"}
    scale: int
    extent: tuple         # (L, R, B, T)
    zoomed_out: bool      # 是否為了避讓而退了比例尺
    outside: bool         # True=四角仍無法避讓，需退回右欄外置（呼叫端處理）


def next_scale(scale: int) -> int:
    """退一級比例尺（放大地面範圍、縮小基地占比，以挪出淨空角落）。"""
    for s in SCALE_LEVELS:
        if s > scale:
            return s
    return int(math.ceil((scale + 1) / 1000.0) * 1000)


def compute_scale_extent(bbox: tuple, expand: float = 1.12,
                         map_w: float = None, map_h: float = None) -> tuple[int, tuple]:
    """基地 bbox → (比例尺, extent)。bbox=(minx,miny,maxx,maxy)。
    先把 bbox 外擴 `expand`，取符合圖框長寬比的最小比例尺階梯，再以 bbox 中心置中。
    map_w/map_h 未給時用舊 MAP_W/MAP_H（相容）；maxmap v2 由 place_fixed 帶入 MAP2_*。"""
    mw = map_w or MAP_W
    mh = map_h or MAP_H
    minx, miny, maxx, maxy = bbox
    w = max(maxx - minx, 30.0) * expand
    h = max(maxy - miny, 30.0) * expand
    need = max(w / (mw / 1000.0), h / (mh / 1000.0))
    scale = next((s for s in SCALE_LEVELS if s >= need), None)
    if scale is None:
        scale = int(math.ceil(need / 1000.0) * 1000)
    return scale, extent_for(bbox, scale, mw, mh)


def extent_for(bbox: tuple, scale: int, map_w: float = None,
               map_h: float = None) -> tuple:
    """給定比例尺，回傳以 bbox 中心置中、符合圖框大小的 extent=(L,R,B,T)。"""
    mw = map_w or MAP_W
    mh = map_h or MAP_H
    minx, miny, maxx, maxy = bbox
    half_w = mw / 1000.0 * scale / 2.0
    half_h = mh / 1000.0 * scale / 2.0
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    return (cx - half_w, cx + half_w, cy - half_h, cy + half_h)


def corner_rect_ground(corner: str, wmm: float, hmm: float, extent: tuple,
                       inset: float = OVERLAY_INSET, map_w: float = None,
                       map_h: float = None) -> tuple:
    """疊圖框(mm，貼某頁角) → 地面矩形 (x1,y1,x2,y2)。"""
    mw = map_w or MAP_W
    mh = map_h or MAP_H
    L, R, B, T = extent
    fx = (R - L) / mw   # 地面公尺 / mm
    fy = (T - B) / mh
    if "R" in corner:
        x2 = R - inset * fx
        x1 = x2 - wmm * fx
    else:
        x1 = L + inset * fx
        x2 = x1 + wmm * fx
    if "B" in corner:
        y1 = B + inset * fy
        y2 = y1 + hmm * fy
    else:
        y2 = T - inset * fy
        y1 = y2 - hmm * fy
    return (x1, y1, x2, y2)


def _try_place(intersect_area_fn, extent, overlays, tol) -> dict | None:
    """在單一 extent 下，把每個疊圖貪婪指派到優先序中第一個「不壓基地」的未用角落。
    全部指派成功回傳 {name: corner}，否則 None。"""
    used: set[str] = set()
    assign: dict = {}
    for name, wmm, hmm in overlays:
        placed = False
        for corner in CORNER_PRIORITY:
            if corner in used:
                continue
            x1, y1, x2, y2 = corner_rect_ground(corner, wmm, hmm, extent)
            if intersect_area_fn(x1, y1, x2, y2) <= tol:
                assign[name] = corner
                used.add(corner)
                placed = True
                break
        if not placed:
            return None
    return assign


def place_overlays(intersect_area_fn, bbox: tuple, overlays=None,
                   max_zoomout: int = 6, tol: float = 1e-6,
                   expand: float = 1.12) -> Placement:
    """主入口：算比例尺/範圍，並自動避讓角落擺放疊圖。

    intersect_area_fn(x1,y1,x2,y2) -> 基地落在該地面矩形內的面積。
    先在初始比例尺嘗試；四角無法全部避開就退一級比例尺重試（基地略縮、範圍變大，
    角落留白變多），最多 `max_zoomout` 次；仍失敗則 outside=True，交呼叫端退回右欄。
    """
    if overlays is None:
        overlays = [OVERLAY_SCALE_NORTH, OVERLAY_LOC_PARCEL]
    scale, extent = compute_scale_extent(bbox, expand=expand)
    zoomed = False
    for _ in range(max_zoomout + 1):
        assign = _try_place(intersect_area_fn, extent, overlays, tol)
        if assign is not None:
            return Placement(assign, scale, extent, zoomed, outside=False)
        scale = next_scale(scale)
        extent = extent_for(bbox, scale)
        zoomed = True
    # 四角始終壓基地（基地幾乎填滿圖框）：交呼叫端外置到右欄，不強行遮蓋。
    return Placement({}, scale, extent, zoomed, outside=True)


def place_fixed(intersect_area_fn, bbox: tuple, boxes=None,
                max_zoomout: int = 6, tol: float = 1e-6, expand: float = 1.12,
                map_w: float = None, map_h: float = None) -> Placement:
    """maxmap v2 固定版面（YAML 定案）：疊圖框位置固定（左上/右下/左下），不改角落。
    保留避讓「儀下」：若基地落入任一固定框對應的地面矩形，退一級比例尺（基地縮小、
    範圍變大→框內留白變多）重試，最多 `max_zoomout` 次；仍相交則接受並回報
    outside=True（交呼叫端知悉，但仍照固定位置畫，不外移——使用者定案「固定位置」）。

    boxes：[(corner, wmm, hmm), ...]，corner ∈ TL/TR/BL/BR。預設＝左上指北針比例尺框
    ＋右下圖例框。assign 回傳 {corner: True} 表示該框最終仍壓到基地（供呼叫端提高底框
    不透明度或記 05 清單備註）。"""
    mw = map_w or MAP2_W
    mh = map_h or MAP2_H
    if boxes is None:
        boxes = [(FIXED_CORNER["north_scale"], NORTH_SCALE_BOX[1], NORTH_SCALE_BOX[2]),
                 (FIXED_CORNER["legend"], LEGEND_BOX[1], LEGEND_BOX[2])]
    scale, extent = compute_scale_extent(bbox, expand=expand, map_w=mw, map_h=mh)
    zoomed = False
    collided = {}
    for _ in range(max_zoomout + 1):
        collided = {}
        for corner, wmm, hmm in boxes:
            x1, y1, x2, y2 = corner_rect_ground(corner, wmm, hmm, extent,
                                                inset=OVERLAY_INSET2, map_w=mw, map_h=mh)
            if intersect_area_fn(x1, y1, x2, y2) > tol:
                collided[corner] = True
        if not collided:
            return Placement({}, scale, extent, zoomed, outside=False)
        scale = next_scale(scale)
        extent = extent_for(bbox, scale, mw, mh)
        zoomed = True
    return Placement(collided, scale, extent, zoomed, outside=bool(collided))
