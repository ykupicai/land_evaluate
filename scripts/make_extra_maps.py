# -*- coding: utf-8 -*-
"""兩張補充主題圖（2026-07-29 使用者要求）

  --theme 權屬    依「公私別」分色（公有／私有），並標地號；正射為底
  --theme 環域    以 buffer 畫法定距離環域（預設 200m），環域內外一目瞭然
  --theme 隔離綠帶 綠帶＋現況建築物輪廓＋兩者交集（應拆除範圍），正射為底

版面沿用 build_layout.build_sheet_layout（與六主題同框）。
須用 QGIS 內建 python 執行。

  & "<QGIS>\\bin\\python-qgis-ltr.bat" scripts\\make_extra_maps.py `
      --joined <基地.shp> --case <案名> --theme 權屬 [--no-loc-parcel]
  & "<QGIS>\\bin\\python-qgis-ltr.bat" scripts\\make_extra_maps.py `
      --joined <基地.shp> --case <案名> --theme 環域 --buffer 200
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from qgis.core import (  # noqa: E402
    QgsApplication, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
    QgsFeature, QgsFillSymbol, QgsGeometry, QgsLayoutExporter, QgsPalLayerSettings,
    QgsProject, QgsRasterLayer, QgsRendererCategory, QgsTextBufferSettings,
    QgsTextFormat, QgsVectorLayer, QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor, QFont  # noqa: E402

import build_layout as sheet  # noqa: E402
from nlsc_wmts import build_basemap  # noqa: E402

CRS = "EPSG:3826"
EXPORT_DPI = 300
# ⚠️ 一律用 114 年版（2025-02-20、有 .prj、62 個生產區）。
# 同資料夾內的 BREED47.shp 是 **2016-01-20 舊版、僅 47 個、無 .prj**，
# 用它會漏掉後來新增的生產區——本案「安南港西區」就是新增的，
# 舊版算出「未涉及、最近 6,453m」，新版才看出**基地 100% 在生產區內**（2026-07-29 實錯）。
BREED_DEFAULT = (r"\\192.168.1.134\Public-TN\3. 圖資\gis圖資\產業相關"
                 r"\一級產業\養殖漁業生產區\養殖漁業生產區_114_SHP\養殖漁業生產區.shp")
# 權屬配色（公有偏暖、私有偏冷，圖例一眼可分）
OWNER_STYLE = {
    "公": ((255, 140, 0), "公有（國有／公有）"),
    "私": ((80, 140, 255), "私有"),
}


def label_field(lyr, field, size=11):
    if lyr.fields().indexFromName(field) < 0:
        return
    pal = QgsPalLayerSettings()
    pal.fieldName = field
    tf = QgsTextFormat()
    tf.setFont(QFont("Microsoft JhengHei", size, QFont.Bold))
    tf.setSize(size)
    buf = QgsTextBufferSettings(); buf.setEnabled(True); buf.setSize(1.2)
    buf.setColor(QColor(255, 255, 255)); tf.setBuffer(buf)
    pal.setFormat(tf)
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    lyr.setLabelsEnabled(True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True)
    ap.add_argument("--case", required=True)
    ap.add_argument("--theme", required=True,
                    choices=["權屬", "環域", "養殖漁業生產區", "隔離綠帶"])
    ap.add_argument("--breed-shp", default=BREED_DEFAULT,
                    help="養殖漁業生產區 shp（預設 NAS 一級產業圖資）")
    ap.add_argument("--breed-km", type=float, default=10.0,
                    help="養殖漁業生產區主題：圖框納入基地周邊 N 公里內之生產區")
    ap.add_argument("--buffer", type=float, default=200.0, help="環域主題的距離(公尺)")
    ap.add_argument("--greenbelt", default=None,
                    help="隔離綠帶主題：綠帶 shp（預設 <案>/隔離綠帶/隔離綠帶或設施.shp）")
    ap.add_argument("--building", default=None,
                    help="隔離綠帶主題：現況建築物 shp（預設 <案>/建蔽率判讀/現況建築物_判讀.shp）")
    ap.add_argument("--alpha", type=float, default=0.55, help="面填色不透明度 0~1")
    ap.add_argument("--basemap-dir", default=None)
    ap.add_argument("--no-loc-parcel", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    out_dir = Path(a.out) if a.out else (REPO_ROOT / "outputs" / a.case)
    qgs = QgsApplication([], False); qgs.initQgis()
    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem(CRS))
    keep = []

    site = QgsVectorLayer(a.joined, "基地地籍", "ogr")
    if not site.isValid():
        raise SystemExit(f"基地 shp 讀取失敗：{a.joined}")

    def fld(feat, *names):
        for n in names:
            i = site.fields().indexFromName(n)
            if i >= 0 and feat[i] not in (None, ""):
                return str(feat[i]).strip()
        return ""

    f0 = next(iter(site.getFeatures()))
    loc = fld(f0, "縣市名稱", "縣市") + fld(f0, "鄉鎮市區", "鄉鎮區", "鄉鎮")
    section = re.sub(r"^\[\d+\]", "", fld(f0, "地段名稱", "段"))
    i_ln = site.fields().indexFromName("地號")
    lns = [str(f[i_ln]).strip() for f in site.getFeatures()] if i_ln >= 0 else []
    pnote = sheet.parcel_note(section, lns[0] if lns else "", len(lns))

    geom = QgsGeometry.unaryUnion([f.geometry() for f in site.getFeatures()])
    A = int(a.alpha * 255)
    layers, legend = [], []

    if a.theme == "權屬":
        i_pub = site.fields().indexFromName("公私別")
        if i_pub < 0:
            raise SystemExit("基地 shp 無「公私別」欄，無法做權屬分色圖")
        vals = []
        for f in site.getFeatures():
            v = str(f[i_pub] or "").strip()
            if v and v not in vals:
                vals.append(v)
        cats = []
        for v in vals:
            rgb, lab = OWNER_STYLE.get(v, ((150, 150, 150), v or "未載"))
            cats.append(QgsRendererCategory(v, QgsFillSymbol.createSimple({
                "color": "%d,%d,%d,%d" % (rgb + (A,)),
                "outline_color": "60,60,60,255", "outline_width": "0.4"}), lab))
        site.setRenderer(QgsCategorizedSymbolRenderer("公私別", cats))
        label_field(site, "地號", 11)
        print("權屬類別：", vals)
        bb = geom.boundingBox()
        ext_geom = geom
    elif a.theme == "養殖漁業生產區":
        # 法源：農業主管機關同意農業用地變更使用審查作業要點**第七點**
        #   「經政府核定之養殖漁業生產區範圍內之農業用地，不同意變更使用」（五款例外）
        # 114 年版有 .prj（EPSG:3826）、dbf 為 Big5；欄位 TITLE/CITY/COUNTRY。
        site.renderer().setSymbol(QgsFillSymbol.createSimple({
            "color": "255,0,0,90", "outline_color": "200,0,0,255", "outline_width": "0.8"}))
        if not os.path.exists(a.breed_shp):
            raise SystemExit(f"找不到養殖漁業生產區圖資：{a.breed_shp}")
        bl = QgsVectorLayer(a.breed_shp, "養殖漁業生產區", "ogr")
        if not bl.isValid():
            raise SystemExit("養殖漁業生產區 shp 讀取失敗")
        bl.setProviderEncoding("BIG5")
        if not bl.crs().isValid():
            bl.setCrs(QgsCoordinateReferenceSystem(CRS))
            print("  BREED47 無 .prj → 依 bounds 驗證結果設為 EPSG:3826")
        bl.renderer().setSymbol(QgsFillSymbol.createSimple({
            "color": "0,150,200,%d" % int(0.45 * 255),
            "outline_color": "0,90,140,255", "outline_width": "0.4"}))
        label_field(bl, "TITLE", 10)
        project.addMapLayer(bl, False); keep.append(bl)
        layers.append(bl); legend.append(bl)
        # 圖框：納入基地周邊 breed_km 公里內的生產區，證明「不在範圍內」而非只放大基地
        near, nearest_d, nearest_t = [], None, ""
        for f in bl.getFeatures():
            g2 = f.geometry()
            if g2.isEmpty():
                continue
            d = geom.distance(g2)
            if d <= a.breed_km * 1000.0:
                near.append(g2)
            if nearest_d is None or d < nearest_d:
                nearest_d, nearest_t = d, str(f["TITLE"] or "")
        inter = [g2 for g2 in near if geom.intersects(g2)]
        print(f"  {a.breed_km:.0f} km 內生產區 {len(near)} 個；"
              f"最近「{nearest_t}」{nearest_d:,.0f} m；"
              f"與基地相交 {len(inter)} 個 → "
              f"{'★★涉及（基地落在生產區內）' if inter else '未涉及'}")
        # 基地落在生產區內時，圖框以「所屬生產區」為主（不必納入 10km 內其他區）
        if inter:
            near = inter
        ext_geom = geom
        for g2 in near:
            ext_geom = ext_geom.combine(g2)
        bb = ext_geom.boundingBox()
        bb.grow(max(bb.width(), bb.height()) * 0.05)
        # 基地與生產區南北並列時 bbox 會很瘦長，A4 橫式框會留下左右空白帶。
        # 把較短的一邊補到版面長寬比（op.MAP_W/MAP_H），畫面才填滿。
        import overlay_placement as op
        want = op.MAP_W / op.MAP_H
        got = bb.width() / bb.height() if bb.height() else want
        if got < want:
            pad = (bb.height() * want - bb.width()) / 2.0
            bb.setXMinimum(bb.xMinimum() - pad); bb.setXMaximum(bb.xMaximum() + pad)
        elif got > want:
            pad = (bb.width() / want - bb.height()) / 2.0
            bb.setYMinimum(bb.yMinimum() - pad); bb.setYMaximum(bb.yMaximum() + pad)
    elif a.theme == "隔離綠帶":
        # 隔離綠帶配置圖：綠帶 + 現況建築物輪廓 + 兩者交集（＝應拆除範圍）
        # 法源：特定工廠變更編定審查辦法§6①(一)（寬≥1.5m 且面積≥30%）、§8（綠帶內建物應拆）
        # 幾何由 scripts/greenbelt_ratio.py 與 scripts/building_coverage.py 產出
        site.renderer().setSymbol(QgsFillSymbol.createSimple({
            "color": "255,255,255,0", "outline_color": "200,0,0,255",
            "outline_width": "0.9"}))
        label_field(site, "地號", 10)
        gb_p = a.greenbelt or str(out_dir / "隔離綠帶" / "隔離綠帶或設施.shp")
        bd_p = a.building or str(out_dir / "建蔽率判讀" / "現況建築物_判讀.shp")
        if not os.path.exists(gb_p):
            raise SystemExit(f"找不到綠帶 shp：{gb_p}（先跑 greenbelt_ratio.py）")
        gl = QgsVectorLayer(gb_p, "隔離綠帶或設施", "ogr")
        if not gl.isValid():
            raise SystemExit(f"綠帶 shp 讀取失敗：{gb_p}")
        gl.renderer().setSymbol(QgsFillSymbol.createSimple({
            "color": "56,168,0,%d" % int(a.alpha * 255),
            "outline_color": "30,110,0,255", "outline_width": "0.5"}))
        gg = QgsGeometry.unaryUnion([f.geometry() for f in gl.getFeatures()])
        print(f"  隔離綠帶 {gg.area():,.2f} m²（占基地 {gg.area()/geom.area()*100:.2f}%）")

        stack = [gl]
        if os.path.exists(bd_p):
            bd = QgsVectorLayer(bd_p, "現況建築物", "ogr")
            if bd.isValid():
                # 圖例名稱取自 shp 的 name 欄（含來源：航照判讀／buildx 分棟建物框）
                f_bd = next(iter(bd.getFeatures()), None)
                i_nm = bd.fields().indexFromName("name")
                if f_bd is not None and i_nm >= 0 and f_bd[i_nm]:
                    nm = str(f_bd[i_nm]).replace("(buildx)", "(分棟建物框)") \
                                        .replace("(ortho)", "(航照判讀)")
                    bd.setName(nm)
                bd.renderer().setSymbol(QgsFillSymbol.createSimple({
                    "color": "255,255,255,0", "outline_color": "40,40,40,255",
                    "outline_width": "0.6", "outline_style": "dash"}))
                bg = QgsGeometry.unaryUnion([f.geometry() for f in bd.getFeatures()])
                cut = bg.intersection(gg)
                if not cut.isEmpty():
                    cl = QgsVectorLayer(f"Polygon?crs={CRS}&field=lab:string",
                                        "應拆除範圍(綠帶內建築物)", "memory")
                    ft = QgsFeature(cl.fields()); ft.setGeometry(cut)
                    ft.setAttributes([f"應拆除 {cut.area():,.0f} m²"])
                    cl.dataProvider().addFeature(ft); cl.updateExtents()
                    sym = QgsFillSymbol.createSimple({
                        "color": "220,0,0,255", "style": "diagonal_x",
                        "outline_color": "150,0,0,255", "outline_width": "0.5"})
                    cl.renderer().setSymbol(sym)
                    project.addMapLayer(cl, False); keep.append(cl)
                    stack.insert(0, cl)
                    print(f"  應拆除（綠帶內建築物）{cut.area():,.2f} m²"
                          f"／現況建築 {bg.area():,.2f} m²")
                project.addMapLayer(bd, False); keep.append(bd)
                stack.insert(1 if len(stack) > 1 else 0, bd)
        for lyr in stack:
            if lyr not in keep:
                project.addMapLayer(lyr, False); keep.append(lyr)
            layers.append(lyr); legend.append(lyr)
        ext_geom = QgsGeometry(geom).buffer(8, 8)
        bb = ext_geom.boundingBox()
    else:
        # 環域：基地紅框 + buffer 環（環域面用半透明、邊界虛線）
        site.renderer().setSymbol(QgsFillSymbol.createSimple({
            "color": "255,0,0,40", "outline_color": "200,0,0,255", "outline_width": "1.0"}))
        label_field(site, "地號", 10)
        ring = QgsGeometry(geom).buffer(a.buffer, 24).difference(geom)
        bl = QgsVectorLayer(f"Polygon?crs={CRS}&field=lab:string",
                            f"{a.buffer:.0f} 公尺環域", "memory")
        pr = bl.dataProvider()
        ft = QgsFeature(bl.fields()); ft.setGeometry(ring)
        ft.setAttributes([f"距基地 {a.buffer:.0f} 公尺範圍"])
        pr.addFeature(ft); bl.updateExtents()
        bl.renderer().setSymbol(QgsFillSymbol.createSimple({
            "color": "255,200,0,%d" % int(0.35 * 255),
            "outline_color": "230,120,0,255", "outline_width": "0.6",
            "outline_style": "dash"}))
        project.addMapLayer(bl, False); keep.append(bl)
        layers.append(bl); legend.append(bl)
        ext_geom = QgsGeometry(geom).buffer(a.buffer * 1.15, 8)
        bb = ext_geom.boundingBox()
        print(f"環域 {a.buffer:.0f} m 已建；環面積 {ring.area():,.0f} m²")

    project.addMapLayer(site, False); keep.append(site)
    layers.append(site); legend.append(site)
    bbox = (bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum())

    bm = Path(a.basemap_dir) if a.basemap_dir else (out_dir / "底圖")
    bm.mkdir(parents=True, exist_ok=True)
    try:
        base = "EMAP" if a.theme == "養殖漁業生產區" else "PHOTO2"
        tif = build_basemap(bbox, str(bm / f"extra_{a.theme}_{base}.tif"), layer=base,
                            cache_dir=str((out_dir / "底圖") / "_tilecache"),
                            buffer_ratio=0.1)
        if tif:
            rl = QgsRasterLayer(tif, f"{'通用電子地圖' if base == 'EMAP' else '正射影像'}"
                                     f"(NLSC {base})")
            if rl.isValid():
                project.addMapLayer(rl, False); keep.append(rl); layers.append(rl)
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠ 底圖失敗：{type(exc).__name__}: {exc}")

    img_dir = out_dir / "圖檔_maxmap"
    img_dir.mkdir(parents=True, exist_ok=True)
    fname = f"001_{a.case}_{a.theme}.png"
    layout, pl = sheet.build_sheet_layout(
        project, f"001_{a.theme}", layers, legend, set(), ext_geom, bbox, loc, pnote,
        keep, show_loc_parcel=not a.no_loc_parcel)
    exporter = QgsLayoutExporter(layout)
    st = QgsLayoutExporter.ImageExportSettings(); st.dpi = EXPORT_DPI
    code = exporter.exportToImage(str(img_dir / fname), st)
    ok = code == QgsLayoutExporter.Success
    print(("已輸出 " if ok else "輸出失敗 ") + str(img_dir / fname)
          + f"（比例尺 1/{getattr(pl, 'scale', '?')}）")
    project.clear(); qgs.exitQgis()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
