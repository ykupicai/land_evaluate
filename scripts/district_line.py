# -*- coding: utf-8 -*-
r"""出圖端：把 `<案>/行政區界/行政區界.shp` 畫成「區界線＋區名」圖層。

設計原則
  ‧ **檔案存在就畫，不存在就回 None**——各出圖腳本只要呼叫一次 `district_layers()`，
    不必各自加旗標；沒跑過 `district_fetch.py` 的舊案完全不受影響。
  ‧ 畫**界線不畫面**：行政區是全覆蓋的面，填色會把底下所有圖層蓋掉
    （`.claude/rules/出圖上色與分區.md`「全覆蓋圖層畫外框等於沒畫」的反面——
    這裡要的就是界線本身，故用粗虛線外框＋白色 halo，任何底圖上都看得見）。
  ‧ 區名標註另開一層（點層），才能**只標圖框內、且每區只標一次**；直接標在面層上，
    PAL 會把標註放在整個行政區的質心——那通常在圖框外，圖上等於沒標。

須 QGIS python。呼叫端範例：
    import district_line
    dist = district_line.district_layers(CASE_DIR, project, keep, extent)
    layers = [site, *dist.layers, photo]      # dist.layers 已由上而下排好
    legend = [*dist.legend, site]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsFeature, QgsFeatureRequest, QgsFillSymbol,
    QgsGeometry, QgsMarkerSymbol, QgsPalLayerSettings, QgsPointXY, QgsRectangle,
    QgsTextBufferSettings, QgsTextFormat, QgsVectorLayer, QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor, QFont

CRS = "EPSG:3826"
LINE_RGB = "90,0,110,255"      # 深紫：與基地紅框、都市計畫界線（洋紅）都區隔得開
LINE_W = "1.1"
LABEL_SIZE = 14


@dataclass
class DistrictLayers:
    layers: list = field(default_factory=list)   # 由上而下（第一個最上層）
    legend: list = field(default_factory=list)
    names: list = field(default_factory=list)

    def __bool__(self):
        return bool(self.layers)


def district_layers(case_dir, project, keep_alive: list, extent: QgsRectangle,
                    title: str = "行政區界", label: bool = True) -> DistrictLayers:
    """回傳可直接併進 layers／legend 的行政區界圖層組；無檔案時回空的 DistrictLayers。"""
    out = DistrictLayers()
    shp = Path(case_dir) / "行政區界" / "行政區界.shp"
    if not shp.exists():
        return out
    lyr = QgsVectorLayer(str(shp), title, "ogr")
    if not lyr.isValid():
        print(f"  ⚠ 行政區界讀取失敗（略過）：{shp}")
        return out
    if not lyr.crs().isValid():
        lyr.setCrs(QgsCoordinateReferenceSystem(CRS))
    lyr.renderer().setSymbol(QgsFillSymbol.createSimple({
        "color": "0,0,0,0", "style": "no",
        "outline_color": LINE_RGB, "outline_width": LINE_W,
        "outline_style": "dash"}))
    project.addMapLayer(lyr, False)
    keep_alive.append(lyr)
    out.layers.append(lyr)
    out.legend.append(lyr)

    if not label:
        return out
    # 區名：每區取「該區 ∩ 圖框」最大塊的內部可容點，確保標在圖框內看得到的地方
    frame = QgsGeometry.fromRect(extent)
    pts = QgsVectorLayer(f"Point?crs={CRS}&field=區名:string", f"{title}標註", "memory")
    dp = pts.dataProvider()
    i_name = lyr.fields().indexFromName("區名")
    for f in lyr.getFeatures(QgsFeatureRequest().setFilterRect(extent)):
        name = str(f[i_name]).strip() if i_name >= 0 else ""
        if not name:
            continue
        g = f.geometry().intersection(frame)
        if g.isEmpty():
            continue
        parts = g.asGeometryCollection() or [g]
        big = max(parts, key=lambda p: p.area())
        # 稍微往內縮，避免標在圖框邊緣被裁掉
        inner = big.buffer(-min(extent.width(), extent.height()) * 0.03, 6)
        pt = (inner if not inner.isEmpty() else big).pointOnSurface().asPoint()
        feat = QgsFeature(pts.fields())
        feat.setAttribute("區名", name)
        feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(pt)))
        dp.addFeature(feat)
        out.names.append(name)
    pts.updateExtents()
    if pts.featureCount() == 0:
        return out
    # 點本身不要出現：透明小記號（size=0 會讓整個 feature 不進渲染管線、連標註一起消失）
    pts.renderer().setSymbol(QgsMarkerSymbol.createSimple({
        "size": "0.4", "color": "0,0,0,0", "outline_style": "no"}))
    pal = QgsPalLayerSettings()
    pal.fieldName = "區名"
    pal.placement = Qgis.LabelPlacement.OverPoint
    pal.displayAll = True          # 周邊分區標註密集，預設會因碰撞被丟掉
    pal.priority = 10
    tf = QgsTextFormat()
    tf.setFont(QFont("Microsoft JhengHei", LABEL_SIZE, QFont.Bold))
    tf.setSize(LABEL_SIZE)
    tf.setColor(QColor(90, 0, 110))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(2.0)
    buf.setColor(QColor(255, 255, 255))
    tf.setBuffer(buf)
    pal.setFormat(tf)
    pts.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    pts.setLabelsEnabled(True)     # setLabeling 不會自己開啟標註
    project.addMapLayer(pts, False)
    keep_alive.append(pts)
    out.layers.insert(0, pts)      # 標註層放最上層
    return out
