# -*- coding: utf-8 -*-
"""QGIS 版面組裝：最大化地圖 A4 橫式 ＋ 自動避讓角落疊圖 ＋ 只留圖例的右欄。

必須用 QGIS 內建 python 執行（PowerShell）：
  .\\scripts\\qgis_python.ps1 your_driver.py

角落擺放決策委由 scripts/overlay_placement.py（純幾何、已單元測試）計算；本檔只負責
把決策落成 QgsLayoutItem。與參考案 大量圖資轉換工具/scripts/make_maps.py 的 build_layout
相同的成熟作法（字型、圖例依範圍過濾、wrapString、比例尺）予以沿用，差異只在版面配置。

呼叫端須先備妥圖層與樣式（見 SKILL.md），並確保：
  * 基地層有 地號 label、且疊粗紅框；
  * 周邊地籍層與基地層皆標地號（2026-08-06 使用者定案；先前版本周邊地籍不標地號，
    詳 scripts/make_maps.py 該行沿革）；
  * 傳入 base_geom = 該案場基地的 dissolve 幾何（EPSG:3826, QgsGeometry），供避讓判定。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import overlay_placement as op

# 圖例鎖右下、離圖框邊界的內縮距（使用者定案 2026-08-19：5mm；有別於其他疊圖的 3mm）
LEGEND_INSET_MM = 5.0

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsGeometry,
    QgsLayerTree,
    QgsLayoutItem,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutPoint,
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
                   inset: float = op.OVERLAY_INSET2) -> tuple:
    """疊圖框貼地圖某頁角 → 版面 mm 矩形 (x, y, w, h)。maxmap v2 用近滿版 MAP2_* 框。"""
    if "R" in corner:
        x = op.MAP2_X + op.MAP2_W - inset - wmm
    else:
        x = op.MAP2_X + inset
    if "B" in corner:
        y = op.MAP2_Y + op.MAP2_H - inset - hmm
    else:
        y = op.MAP2_Y + inset
    return (x, y, wmm, hmm)


def parcel_note(section: str, first_landno: str, total: int, extra_sections: int = 0) -> str:
    """涵蓋地號收合：'段 第一筆 等 N 筆'（跨段時補「(跨M段)」）。"""
    tail = f"（跨{extra_sections + 1}段）" if extra_sections else ""
    return f"{section} {first_landno} 等 {total} 筆{tail}"


# ---------------------------------------------------------------------------
# 疊圖群組
# ---------------------------------------------------------------------------
def _add_scale_north(layout, m, scale, opaque=False):
    """左上（YAML 定案，固定）：半透明白底框 ＋ 指北針 ＋ 比例尺文字 ＋ 比例尺。
    opaque=True 時底框改高不透明（避讓失敗、框壓到基地時提升可讀性）。"""
    name, w, h = op.NORTH_SCALE_BOX
    x, y, w, h = corner_rect_mm("TL", w, h)
    bx = _font_label(layout, "", x, y, w, h, 1, bg=True)
    if opaque:
        bx.setBackgroundColor(QColor(255, 255, 255, 245))
    # 指北針（框內左上）
    na = QgsLayoutItemPicture(layout)
    na.setPicturePath(NORTH_SVG)
    na.attemptSetSceneRect(QRectF(x + 2, y + 2, 8, 11))
    na.setLinkedMap(m)
    layout.addLayoutItem(na)
    # 比例尺文字（指北針右側）
    _font_label(layout, f"比例尺　1 / {scale:,}".replace(",", " "),
                x + 13, y + 2, w - 15, 6, 9, bold=True)
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
    sb.attemptSetSceneRect(QRectF(x + 2, y + 10, w - 4, 10))
    layout.addLayoutItem(sb)


def _add_loc_parcel(layout, location, parcel_note_text):
    """左下（YAML 定案，固定，選用）：半透明白底框 ＋ 位置 ＋ 涵蓋地號(收合)。"""
    name, w, h = op.LOC_PARCEL_BOX
    x, y, w, h = corner_rect_mm("BL", w, h)
    _font_label(layout, "", x, y, w, h, 1, bg=True)
    _font_label(layout, f"位置：{location}", x + 3, y + 2, w - 6, 6, 9, bold=True)
    _font_label(layout, f"涵蓋地號：{parcel_note_text}", x + 3, y + 9, w - 6, 6, 9)


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

    placement = op.place_fixed(intersect_fn, bbox)
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
    m.attemptSetSceneRect(QRectF(op.MAP2_X, op.MAP2_Y, op.MAP2_W, op.MAP2_H))
    m.setExtent(QgsRectangle(L, B, R, T))
    m.setScale(float(scale))
    layout.addLayoutItem(m)

    # 3) 圖面加註橫幅（如：都市土地無使用地編定），置地圖上緣置中，避開左上疊圖框
    if map_overlay:
        _font_label(layout, map_overlay, op.MAP2_X + 70, op.MAP2_Y + 4,
                    op.MAP2_W - 140, 10, 13, bold=True, color=(200, 0, 0), bg=True,
                    halign=Qt.AlignHCenter)

    # 4) 圖例：右下方框（YAML 定案，取代右欄）；依地圖範圍動態過濾、長標籤依「／」換行
    leg = QgsLayoutItemLegend(layout)
    leg.setLinkedMap(m)
    # ⚠ 圖例清單要改在圖例「自己的」自訂樹上（setAutoUpdateModel(False) 時 QGIS 會
    #   複製一棵存在圖例內部），**不可另建 QgsLayerTree() 再 setRootGroup 換進去**：
    #   那樣畫圖時有效，但存 .qgz 時 QGIS 序列化的是圖例內部那棵（空的），重開後圖例
    #   退回「列出整個專案圖層樹」——多出群組標題、光柵波段，原本挑好的項目不見
    #   （2026-09-24 阿里山案重開 qgz 實見；內湖案 make_maps 產的 qgz 同樣受害）。
    leg.setAutoUpdateModel(False)
    root = leg.model().rootGroup()
    root.removeAllChildren()
    for lyr in legend_layers:
        node = root.addLayer(lyr)
        if lyr.name() in legend_hide_title:
            QgsLegendRenderer.setNodeLegendStyle(node, QgsLegendStyle.Hidden)
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
    # 右下白框（半透明；框壓到基地時提高不透明度）＋細灰框；**fit-to-content**：
    # 依圖例內容縮放(adjustBoxSize)，再依實際尺寸把右下角貼齊地圖右下角(留 inset)。
    leg.setBackgroundEnabled(True)
    leg.setBackgroundColor(QColor(255, 255, 255, 245 if "BR" in placement.assign else 210))
    try:
        leg.setFrameEnabled(True)
        leg.setFrameStrokeColor(QColor(128, 128, 128))
    except Exception:
        pass
    try:
        leg.setResizeToContents(True)
    except Exception:
        pass
    # 先給右下暫置錨點，加入版面後縮到內容大小，再依實際尺寸重新貼齊右下角。
    lx, ly, lw0, lh0 = corner_rect_mm("BR", op.LEGEND_BOX[1], op.LEGEND_BOX[2])
    leg.attemptSetSceneRect(QRectF(lx, ly, lw0, lh0))
    layout.addLayoutItem(leg)
    try:
        leg.adjustBoxSize()
        # 圖例框「右下角」直接釘在離圖框右下 5mm（使用者定案 2026-08-19，參考圖）。
        # 用 LowerRight 參考點＋attemptMove，不依賴 sizeWithUnits 反推左上角——先前
        # 反推做法會因縮框後尺寸取值不準，讓圖例浮在圖框中間偏內側（右/下各留約 20mm）。
        leg.setReferencePoint(QgsLayoutItem.LowerRight)
        leg.attemptMove(QgsLayoutPoint(
            op.MAP2_X + op.MAP2_W - LEGEND_INSET_MM,
            op.MAP2_Y + op.MAP2_H - LEGEND_INSET_MM,
            QgsUnitTypes.LayoutMillimeters))
    except Exception:
        pass

    # 5) 固定疊圖（YAML 定案）：左上指北針/比例尺、左下位置/涵蓋地號（選用）。
    #    place_fixed 已以退比例尺盡量避讓；placement.assign 內的角落＝仍壓到基地者，
    #    提高該框底色不透明度（不移位，使用者定案「固定位置」）。
    _add_scale_north(layout, m, scale, opaque=("TL" in placement.assign))
    if show_loc_parcel:
        _add_loc_parcel(layout, location, parcel_note_text)

    project.layoutManager().addLayout(layout)
    return layout, placement
