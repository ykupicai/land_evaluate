# -*- coding: utf-8 -*-
"""區位圖（簡報第2頁用）：基地在區域中的位置

以 NLSC 通用電子地圖（EMAP）為底、外擴 N 公尺，疊各宗基地並標註 site_id，
版面沿用與六主題相同的 maxmap 版面（build_layout.build_sheet_layout）。

為什麼要獨立一張：有了區位圖承擔「周邊道路與聚落脈絡」，臨路判定圖就不必外擴，
可與其他主題維持同一比例尺（使用者定案 2026-07-29）。

須用 QGIS 內建 python：
  & "<QGIS>\\bin\\python-qgis-ltr.bat" scripts\\make_locality_map.py `
      --joined <基地.shp> --case <案名> [--sites <臨路評估/sites.shp>] [--margin 1500]
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
    QgsFeature, QgsFeatureRequest, QgsFillSymbol, QgsGeometry, QgsLayoutExporter,
    QgsPalLayerSettings, QgsProject, QgsProperty, QgsRasterLayer, QgsRectangle,
    QgsRendererCategory, QgsSymbolLayer, QgsTextBufferSettings, QgsTextFormat,
    QgsVectorLayer, QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor, QFont  # noqa: E402

import build_layout as sheet  # noqa: E402
from nlsc_wmts import build_basemap  # noqa: E402

CRS = "EPSG:3826"
EXPORT_DPI = 300
URBAN_COLOR_FIELD = "Color1"   # 都市計畫 shp 的官方數值化 RGB 欄
# 宗基地分色盤（2026-08-01）：**刻意避開紅色**——紅色已固定給「基地地籍」圖層，
# 單一案場時若宗基地也用紅色，兩層會長得一模一樣、圖例失去辨識作用。
SITE_PALETTE = [
    (0, 90, 220), (240, 140, 0), (20, 150, 60), (140, 40, 180), (0, 150, 150),
    (200, 20, 140), (140, 90, 40), (100, 130, 20), (20, 60, 140), (230, 180, 0),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True)
    ap.add_argument("--case", required=True)
    ap.add_argument("--sites", default=None, help="案場面 shp（有則標 site_id）")
    ap.add_argument("--margin", type=float, default=None,
                    help="外擴公尺數。未給時優先讀 <案>/區位圖_道路基準.json"
                         "（scripts/road_network.py 算出的「外擴到第一條國道/省道/"
                         "市道/縣道/鄉道/市區快速道路為止」距離），沒有該檔才退回"
                         "固定 1500m 舊制")
    ap.add_argument("--basemap-dir", default=None)
    ap.add_argument("--site-outline", type=float, default=0.5,
                    help="基地範圍框線粗度(mm)；使用者定案 2026-07-29 由 1.1 減為 0.5")
    ap.add_argument("--no-urban", action="store_true",
                    help="不疊都市計畫土地使用分區")
    ap.add_argument("--urban-alpha", type=float, default=0.45,
                    help="都市計畫分區不透明度 0~1（預設 0.45，讓電子地圖路名透出）")
    ap.add_argument("--no-loc-parcel", action="store_true",
                    help="（相容旗標，已是預設值）不畫圖上「位置／涵蓋地號」角落框")
    ap.add_argument("--loc-parcel", action="store_true",
                    help="畫出「位置／涵蓋地號」角落框（舊版面；預設不畫）")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    out_dir = Path(a.out) if a.out else (REPO_ROOT / "outputs" / a.case)
    qgs = QgsApplication([], False)
    qgs.initQgis()
    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem(CRS))
    keep = []

    site = QgsVectorLayer(a.joined, "基地地籍", "ogr")
    if not site.isValid():
        raise SystemExit(f"基地 shp 讀取失敗：{a.joined}")
    site.renderer().setSymbol(QgsFillSymbol.createSimple({
        "color": "255,0,0,60", "outline_color": "200,0,0,255",
        "outline_width": str(a.site_outline)}))
    project.addMapLayer(site, False); keep.append(site)

    def fld(feat, *names):
        for n in names:
            i = site.fields().indexFromName(n)
            if i >= 0 and feat[i] not in (None, ""):
                return str(feat[i]).strip()
        return ""

    f0 = next(iter(site.getFeatures()))
    county = fld(f0, "縣市名稱", "縣市")
    town = fld(f0, "鄉鎮市區", "鄉鎮區", "鄉鎮")
    section = re.sub(r"^\[\d+\]", "", fld(f0, "地段名稱", "段"))
    loc = f"{county}{town}"

    geom = QgsGeometry.unaryUnion([f.geometry() for f in site.getFeatures()])

    margin = a.margin
    road_note = ""
    if margin is None:
        # ★ 2026-08-06 使用者定案：外擴基準改用「碰到第一條夠格公路為止」，不用固定
        #   距離。這裡不直接算（QGIS 內建 python 沒有 geopandas），改讀
        #   scripts/road_network.py 系統 python 預先算好的結果——它已由
        #   run_case.py 的 ⓪a 流程自動產出；獨立跑本腳本時若沒有該檔，
        #   先跑一次 `python scripts/road_network.py --joined ... --out ...`。
        road_json = out_dir / "區位圖_道路基準.json"
        if road_json.exists():
            import json
            rd = json.loads(road_json.read_text(encoding="utf-8"))
            margin = rd["margin_m"]
            if rd.get("road_name"):
                road_note = (f"（外擴基準：{rd['road_type']}「{rd['road_name']}」"
                             f"距基地 {rd.get('distance_m', 0):,.0f} m，來源 {rd['source']}）")
        else:
            margin = 1500.0
            road_note = (f"（⚠ 找不到 {road_json.name}，退回固定 1500m 舊制——"
                         "建議先跑 scripts/road_network.py 產生道路基準）")

    bb = geom.boundingBox()
    bb.grow(margin)
    bbox = (bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum())
    print(f"位置：{loc}　地段：{section}　外擴 {margin:.0f} m{road_note}")

    layers = [site]
    legend = [site]

    # 案場面：**依 site_id 分色，靠圖例辨識，不在圖上標註**
    # （★2026-08-01 使用者定案：區位圖外擴後基地很小，標籤會整個蓋掉基地本身；
    #   改為每宗基地一色並列入圖例，多宗基地時顏色即區分依據。）
    if a.sites and os.path.exists(a.sites):
        sl = QgsVectorLayer(a.sites, "宗基地", "ogr")
        if sl.isValid():
            idx = sl.fields().indexFromName("site_id")
            if idx >= 0:
                ids = sorted({str(f["site_id"] or "") for f in sl.getFeatures()} - {""})
                cats = []
                for i, sid in enumerate(ids):
                    r, g, b = SITE_PALETTE[i % len(SITE_PALETTE)]
                    cats.append(QgsRendererCategory(sid, QgsFillSymbol.createSimple({
                        "color": f"{r},{g},{b},110",
                        "outline_color": f"{r},{g},{b},255",
                        "outline_width": "0.9"}), sid))
                sl.setRenderer(QgsCategorizedSymbolRenderer("site_id", cats))
                print(f"  宗基地 {len(ids)} 宗依 site_id 分色（圖例辨識，圖面不標註）："
                      + "／".join(ids))
            else:
                sl.renderer().setSymbol(QgsFillSymbol.createSimple({
                    "color": "220,30,30,110", "outline_color": "220,30,30,255",
                    "outline_width": "0.9"}))
            sl.setLabelsEnabled(False)
            project.addMapLayer(sl, False); keep.append(sl)
            layers.insert(0, sl)
            legend.insert(0, sl)

    # 都市計畫土地使用分區（★2026-07-29 使用者要求：區位圖同時套疊都市計畫分區）
    # 用 Color1 官方色 data-defined 上色（同 make_maps 的做法），半透明疊在 EMAP 上。
    if not a.no_urban:
        up = REPO_ROOT / "都市計畫" / "全台都市計畫土地使用分區.shp"
        if up.exists():
            ul = QgsVectorLayer(str(up), "都市計畫土地使用分區", "ogr")
            if ul.isValid():
                # ⚠️ 實測（2026-07-29）：fillColor 的運算式**直接給 "Color1"** 即可
                # （QGIS 會把 "r,g,b" 字串解析成顏色，styles/都市計畫.qml 就是這樣）。
                # 別自己拆欄位——`split_part` 是 PostgreSQL 函式，QGIS 沒有，
                # 運算式會靜默失效、整片變成基礎符號的灰色（實際踩過）。
                # 透明度改用「圖層不透明度」控制，才不會被 data-defined 覆蓋。
                sym = QgsFillSymbol.createSimple({
                    "color": "200,200,200,255",
                    "outline_color": "120,120,120,180", "outline_width": "0.15"})
                sym.symbolLayer(0).setDataDefinedProperty(
                    QgsSymbolLayer.PropertyFillColor,
                    QgsProperty.fromExpression(f'"{URBAN_COLOR_FIELD}"'))
                ul.renderer().setSymbol(sym)
                ul.setOpacity(a.urban_alpha)
                project.addMapLayer(ul, False); keep.append(ul)
                layers.append(ul)

                # 圖例層：data-defined 單一符號在圖例只會顯示一格灰，故另建分類記憶體層
                # （不進 layers、只進 legend），標籤取圖框內實際出現的分區名。
                i_c = ul.fields().indexOf(URBAN_COLOR_FIELD)
                i_z = ul.fields().indexOf("zone")
                rect = QgsRectangle(bbox[0], bbox[1], bbox[2], bbox[3])
                seen = {}
                if i_c >= 0:
                    for f in ul.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
                        col = str(f[i_c] or "").strip()
                        z = str(f[i_z] or "").strip() if i_z >= 0 else ""
                        if col:
                            seen.setdefault(col, [])
                            if z and z not in seen[col]:
                                seen[col].append(z)
                if seen:
                    leg = QgsVectorLayer(f"Polygon?crs={CRS}&field=cat:string",
                                         "都市計畫土地使用分區", "memory")
                    # ⚠️ 每個類別都要放一個「位於圖框中心的極小假幾何」，否則
                    # build_sheet_layout 的 setLegendFilterByMapEnabled 會因該層無要素
                    # 而把整層從圖例濾掉（實際踩過：分區有上色但圖例空白）。
                    pr = leg.dataProvider()
                    cx, cy = rect.center().x(), rect.center().y()
                    half = max(rect.width(), rect.height()) * 0.0005
                    cats, feats = [], []
                    for col, zs in seen.items():
                        try:
                            r, g, b = (int(x) for x in col.split(","))
                        except ValueError:
                            continue
                        cats.append(QgsRendererCategory(col, QgsFillSymbol.createSimple({
                            "color": f"{r},{g},{b},{int(a.urban_alpha * 255)}",
                            "outline_color": "120,120,120,200", "outline_width": "0.1"}),
                            "／".join(zs[:3]) + ("　等" if len(zs) > 3 else "")))
                        ft = QgsFeature(leg.fields())
                        ft.setGeometry(QgsGeometry.fromRect(
                            QgsRectangle(cx - half, cy - half, cx + half, cy + half)))
                        ft.setAttributes([col])
                        feats.append(ft)
                    if cats:
                        pr.addFeatures(feats); leg.updateExtents()
                        leg.setRenderer(QgsCategorizedSymbolRenderer("cat", cats))
                        project.addMapLayer(leg, False); keep.append(leg)
                        legend.append(leg)
                print(f"  都市計畫分區：Color1 官方色、圖層不透明度 {a.urban_alpha:.0%}；"
                      f"圖框內分區 {len(seen)} 色 "
                      f"{[('／'.join(v[:2]) or k) for k, v in seen.items()]}")
            else:
                print("  ⚠ 都市計畫 shp 讀取失敗，區位圖不疊分區")
        else:
            print(f"  ⚠ 找不到 {up}，區位圖不疊分區")

    # 通用電子地圖：★2026-07-29 使用者定案「透明電子地圖置於最上層」
    #   底層 EMAP（不透明）→ 提供全幅底圖（含都市計畫範圍外的河川、鄰區）
    #   頂層 EMAP2（透明版，僅道路與註記）→ 路名與地名壓在都市計畫分區色之上，
    #     否則分區色會把路名蓋掉、區位圖失去「看得出在哪裡」的功能。
    # QGIS setLayers 的順序：**清單第一個畫在最上層**。
    bm = Path(a.basemap_dir) if a.basemap_dir else (out_dir / "底圖")
    bm.mkdir(parents=True, exist_ok=True)
    cache = str((out_dir / "底圖") / "_tilecache")
    try:
        tif = build_basemap(bbox, str(bm / "locality_EMAP.tif"), layer="EMAP",
                            cache_dir=cache, buffer_ratio=0.02)
        if tif:
            rl = QgsRasterLayer(tif, "通用電子地圖(NLSC EMAP)")
            if rl.isValid():
                project.addMapLayer(rl, False); keep.append(rl)
                layers.append(rl)          # 置底
    except Exception as exc:  # noqa: BLE001 — 底圖失敗仍出圖
        print(f"  ⚠ 底圖(EMAP)失敗：{type(exc).__name__}: {exc}")
    try:
        tif2 = build_basemap(bbox, str(bm / "locality_EMAP2.tif"), layer="EMAP2",
                             cache_dir=cache, buffer_ratio=0.02, keep_alpha=True)
        if tif2:
            rl2 = QgsRasterLayer(tif2, "通用電子地圖透明版(NLSC EMAP2)")
            if rl2.isValid():
                project.addMapLayer(rl2, False); keep.append(rl2)
                layers.insert(0, rl2)      # ★置頂
                print("  透明電子地圖(EMAP2)已置於最上層")
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠ 底圖(EMAP2)失敗：{type(exc).__name__}: {exc}")

    idx_ln = site.fields().indexFromName("地號")
    lns = [str(f[idx_ln]).strip() for f in site.getFeatures()] if idx_ln >= 0 else []
    pnote = sheet.parcel_note(section, lns[0] if lns else "", len(lns))

    img_dir = out_dir / "圖檔_maxmap"
    img_dir.mkdir(parents=True, exist_ok=True)
    fname = f"001_{a.case}_區位圖.png"
    layout, pl = sheet.build_sheet_layout(project, "001_區位圖", layers, legend, set(),
                                          geom, bbox, loc, pnote, keep,
                                          show_loc_parcel=a.loc_parcel)
    exporter = QgsLayoutExporter(layout)
    st = QgsLayoutExporter.ImageExportSettings()
    st.dpi = EXPORT_DPI
    code = exporter.exportToImage(str(img_dir / fname), st)
    ok = code == QgsLayoutExporter.Success
    print(("已輸出 " if ok else "輸出失敗 ") + str(img_dir / fname)
          + f"（比例尺 1/{getattr(pl, 'scale', '?')}）")
    project.clear(); qgs.exitQgis()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

