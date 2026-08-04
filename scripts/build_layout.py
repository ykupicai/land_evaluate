# -*- coding: utf-8 -*-
"""QGIS 版面組裝：最大化地圖 A4 橫式 ＋ 自動避讓角落疊圖 ＋ 只留圖例的右欄。

必須用 QGIS 內建 python 執行（PowerShell）：
  & "C:\\Program Files\\QGIS 3.40.12\\bin\\python-qgis-ltr.bat" your_driver.py

角落擺放決策委由 scripts/overlay_placement.py（純幾何、已單元測試）計算；本檔只負責
把決策落成 QgsLayoutItem。與參考案 大量圖資轉換工具/scripts/make_maps.py 的 build_layout
相同的成熟作法（字型、圖例依範圍過濾、wrapString、比例尺）予以沿用，差異只在版面配置。

呼叫端須先備妥圖層與樣式（見 SKILL.md），並確保：
  * 基地層有 地號 label、且疊粗紅框；
  * **周邊地籍層關閉 label**（本版設計不在周邊宗地上地號，維持圖面乾淨）；
  * 傳入 base_geom = 該案場基地的 dissolve 幾何（EPSG:3826, QgsGeometry），供避讓判定。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import overlay_placement as op

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsGeometry,
    QgsLayerTree,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutSize,
    QgsLegendRenderer,
    QgsLegendStyle,
    QgsMapLayerLegendUtils,
    QgsPrintLayout,
    QgsRasterLayer,
    QgsRectangle,
    QgsTextFormat,
    QgsUnitTypes,
)
from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtGui import QColor, QFont

CRS_3826 = "EPSG:3826"
FONT_NAME = "Microsoft JhengHei"
NORTH_SVG = ":/images/north_arrows/layout_default_north_arrow.svg"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _font_label(layout, text, x, y, w, h, size, bold=False, color=None,
                bg=False, halign=None):
    it = QgsLayoutItemLabel(layout)
    it.setText(text)
    f = QFont(FONT_NAME, int(size))
    f.setBold(bold)
    try:
        fmt = QgsTextFormat()
        fmt.setFont(f)
        fmt.setSize(size)
        if color:
            fmt.setColor(QColor(*color))
        it.setTextFormat(fmt)
    except Exception:
        it.setFont(f)
    if bg:
        it.setBackgroundEnabled(True)
        it.setBackgroundColor(QColor(255, 255, 255, 210))
    if halign is not None:
        try:
            it.setHAlign(halign)
        except Exception:
            pass
    it.attemptSetSceneRect(QRectF(x, y, w, h))
    layout.addLayoutItem(it)
    return it


def corner_rect_mm(corner: str, wmm: float, hmm: float,
                   inset: float = op.OVERLAY_INSET) -> tuple:
    """疊圖框貼地圖某頁角 → 版面 mm 矩形 (x, y, w, h)。"""
    if "R" in corner:
        x = op.MAP_X + op.MAP_W - inset - wmm
    else:
        x = op.MAP_X + inset
    if "B" in corner:
        y = op.MAP_Y + op.MAP_H - inset - hmm
    else:
        y = op.MAP_Y + inset
    return (x, y, wmm, hmm)


def parcel_note(section: str, first_landno: str, total: int, extra_sections: int = 0) -> str:
    """涵蓋地號收合：'段 第一筆 等 N 筆'（跨段時補「(跨M段)」）。"""
    tail = f"（跨{extra_sections + 1}段）" if extra_sections else ""
    return f"{section} {first_landno} 等 {total} 筆{tail}"


# ---------------------------------------------------------------------------
# 疊圖群組
# ---------------------------------------------------------------------------
def _add_scale_north(layout, m, corner, scale):
    """右下(或避讓後角落)：半透明白底框 ＋ 指北針 ＋ 比例尺文字 ＋ 比例尺。"""
    name, w, h = op.OVERLAY_SCALE_NORTH
    x, y, w, h = corner_rect_mm(corner, w, h)
    # 底框（先加，位於底層）
    _font_label(layout, "", x, y, w, h, 1, bg=True)
    # 指北針（框內左上）
    na = QgsLayoutItemPicture(layout)
    na.setPicturePath(NORTH_SVG)
    na.attemptSetSceneRect(QRectF(x + 3, y + 3, 9, 13))
    na.setLinkedMap(m)
    layout.addLayoutItem(na)
    # 比例尺文字（指北針右側）
    _font_label(layout, f"比例尺　1 / {scale:,}".replace(",", " "),
                x + 15, y + 3, w - 17, 6, 10, bold=True)
    # 比例尺（框內下半）
    sb = QgsLayoutItemScaleBar(layout)
    sb.setStyle("Single Box")
    sb.setLinkedMap(m)
    sb.applyDefaultSettings()
    sb.applyDefaultSize()
    try:
        fmt = QgsTextFormat()
        fmt.setFont(QFont(FONT_NAME, 8))
        fmt.setSize(8.0)
        sb.setTextFormat(fmt)
    except Exception:
        pass
    sb.attemptSetSceneRect(QRectF(x + 3, y + 12, w - 6, 12))
    layout.addLayoutItem(sb)


def _add_loc_parcel(layout, corner, location, parcel_note_text):
    """左下(或避讓後角落)：半透明白底框 ＋ 位置 ＋ 涵蓋地號(收合)。"""
    name, w, h = op.OVERLAY_LOC_PARCEL
    x, y, w, h = corner_rect_mm(corner, w, h)
    _font_label(layout, "", x, y, w, h, 1, bg=True)
    _font_label(layout, f"位置：{location}", x + 3, y + 2, w - 6, 6, 9, bold=True)
    _font_label(layout, f"涵蓋地號：{parcel_note_text}", x + 3, y + 10, w - 6, 8, 9)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def build_sheet_layout(project, name, layers, legend_layers,
                       legend_hide_title, base_geom: QgsGeometry, bbox: tuple,
                       location: str, parcel_note_text: str, keep_alive: list,
                       map_overlay: str | None = None, show_loc_parcel: bool = True):
    """組一張最大化地圖版面。base_geom/bbox 用來算比例尺並自動避讓角落疊圖。
    回傳 (layout, placement)；placement.scale/extent 為最終（必要時退階後）值。
    版面不放案名/圖名標題列（使用者定案）——圖名資訊由檔名與 05 出圖清單承載。

    show_loc_parcel=False：不畫「位置／涵蓋地號」角落框（使用者定案 2026-07-29：
    簡報頁面本文已交代位置與地號，圖上再寫一次是重複，且會遮住圖面）。
    比例尺與指北針不受影響，仍照畫。"""

    # 1) 幾何決策：比例尺、範圍、疊圖角落（純幾何、已測）
    def intersect_fn(x1, y1, x2, y2):
        rect = QgsGeometry.fromRect(QgsRectangle(x1, y1, x2, y2))
        inter = base_geom.intersection(rect)
        return 0.0 if inter.isEmpty() else inter.area()

    placement = op.place_overlays(intersect_fn, bbox)
    scale, extent = placement.scale, placement.extent
    L, R, B, T = extent

    # 2) 版面骨架
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(name)
    layout.pageCollection().page(0).setPageSize(
        QgsLayoutSize(op.PAGE_W, op.PAGE_H, QgsUnitTypes.LayoutMillimeters))

    m = QgsLayoutItemMap(layout)
    m.setCrs(QgsCoordinateReferenceSystem(CRS_3826))
    m.setLayers(layers)
    m.setKeepLayerSet(True)
    m.setBackgroundEnabled(True)
    m.setBackgroundColor(QColor(255, 255, 255))
    m.setFrameEnabled(True)
    m.attemptSetSceneRect(QRectF(op.MAP_X, op.MAP_Y, op.MAP_W, op.MAP_H))
    m.setExtent(QgsRectangle(L, B, R, T))
    m.setScale(float(scale))
    layout.addLayoutItem(m)

    # 3) 圖面加註橫幅（如：都市土地無使用地編定），置地圖上緣置中，避開角落疊圖
    if map_overlay:
        _font_label(layout, map_overlay, op.MAP_X + 10, op.MAP_Y + 4, op.MAP_W - 20, 10,
                    13, bold=True, color=(200, 0, 0), bg=True, halign=Qt.AlignHCenter)

    # 4) 右欄圖例（依地圖範圍動態過濾；長標籤依「／」換行）
    leg = QgsLayoutItemLegend(layout)
    leg.setLinkedMap(m)
    leg.setAutoUpdateModel(False)
    root = QgsLayerTree()
    keep_alive.append(root)
    for lyr in legend_layers:
        node = root.addLayer(lyr)
        if lyr.name() in legend_hide_title:
            QgsLegendRenderer.setNodeLegendStyle(node, QgsLegendStyle.Hidden)
    leg.model().setRootGroup(root)
    # 光柵圖例會自動多一行「Band 1 (Gray)」（波段名）——對讀者毫無意義，又在只有
    # 45mm 寬的右欄白占一行。逐層挑掉該節點（2026-08-04 坡度分段圖實需）。
    for lyr in legend_layers:
        if not isinstance(lyr, QgsRasterLayer):
            continue
        node = root.findLayer(lyr.id())
        if node is None:
            continue
        nodes = leg.model().layerLegendNodes(node)
        keep_idx = [i for i, n in enumerate(nodes)
                    if not re.match(r"^Band \d+", str(n.data(Qt.DisplayRole) or ""))]
        if 0 < len(keep_idx) < len(nodes):
            QgsMapLayerLegendUtils.setLegendNodeOrder(node, keep_idx)
            leg.model().refreshLayerLegend(node)
    leg.setLegendFilterByMapEnabled(True)
    leg.setTitle("圖例")
    try:
        leg.setWrapString("／")
    except Exception:
        pass
    for style, size, bold in ((QgsLegendStyle.Title, 11, True),
                              (QgsLegendStyle.Subgroup, 9, False),
                              (QgsLegendStyle.SymbolLabel, 9, False)):
        f = QFont(FONT_NAME, size)
        f.setBold(bold)
        try:
            fmt = QgsTextFormat()
            fmt.setFont(f)
            fmt.setSize(float(size))
            leg.rstyle(style).setTextFormat(fmt)
        except Exception:
            try:
                leg.setStyleFont(style, f)
            except Exception:
                pass
    leg.attemptSetSceneRect(QRectF(op.LEGEND_X, op.LEGEND_Y, op.LEGEND_W, op.LEGEND_H))
    layout.addLayoutItem(leg)

    # 5) 角落疊圖（或無法避讓時退回右欄底部）
    if placement.outside:
        # 病態案：基地幾乎填滿圖框，四角皆會遮基地 → 資訊改置右欄圖例下方，不強遮地圖
        _font_label(layout, f"比例尺　1 / {scale:,}".replace(",", " "),
                    op.LEGEND_X, op.LEGEND_Y + op.LEGEND_H - 24, op.LEGEND_W, 6, 9, bold=True)
        if show_loc_parcel:
            _font_label(layout, f"位置：{location}\n涵蓋地號：{parcel_note_text}",
                        op.LEGEND_X, op.LEGEND_Y + op.LEGEND_H - 16, op.LEGEND_W, 16, 8)
    else:
        a = placement.assign
        _add_scale_north(layout, m, a[op.OVERLAY_SCALE_NORTH[0]], scale)
        if show_loc_parcel:
            _add_loc_parcel(layout, a[op.OVERLAY_LOC_PARCEL[0]], location, parcel_note_text)

    project.layoutManager().addLayout(layout)
    return layout, placement
