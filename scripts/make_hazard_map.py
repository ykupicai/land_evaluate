# -*- coding: utf-8 -*-
"""坡地災害套疊圖：山崩／崩塌／順向坡／土石流（正射為底）。

由 2026-09-13 臺北市內湖保護區變更住宅區案的一次性腳本通用化（結案七問第 3 問）。
補上本機 `環境敏感相關/` **沒有**的坡地災害圖層——順向坡區、土石流潛勢溪流／影響
範圍／集水區、大規模崩塌潛勢區、全臺崩塌地、事件型崩塌目錄。

資料來源與所有參數坑見 `docs/崩塌土石流圖資的兩條API.md`。摘要：
  ① 山崩雲（經濟部地質調查及礦業管理中心）
     `https://landslide.geologycloud.tw/map/api/opendata/lsdata/<ep>`
     ⚠ 參數是 coordinate／filetype／limit，**limit 不帶只回 10 筆**。TWD97 即 EPSG:3826。
  ② 農村發展及水土保持署 MVT 向量圖磚
     `https://gis.ardswc.gov.tw/api/ardswc/vectortiles/shp/<layer>/{z}/{y}/{x}.pbf`
     ⚠ 是 `{z}/{y}/{x}`（y 在 x 前）；以 GDAL 的 MVT 驅動解碼；跨磚切裂要依 debrisno
       dissolve，**必須 dropna=False**（屬性有 None 時 groupby 會整列丟掉）。

⚠⚠ **交付口徑**：兩平臺皆聲明其資料「僅可用於調查、研究、分析、規劃，**不可應用於
   法規作業**」→ 成果只作交叉核對與現況說明，法定認定仍以 NGIS／eland／主管機關為準。
   警語**不印在圖面上**（使用者 2026-09-13 定案），由交付文字承載。

須用 QGIS 內建 python（要 osgeo 解 MVT、要 QGIS 出圖）：
  & "<QGIS>\\bin\\python-qgis-ltr.bat" scripts\\make_hazard_map.py --case <案名> [--county 臺北市]

產出：
  <案>/崩塌土石流/*.geojson          各圖層（EPSG:3826，已裁到圖框附近）
  <案>/崩塌土石流/坡地災害檢核.csv    逐圖層件數／距基地／與基地交集
  <案>/圖檔_maxmap/001_<案>_崩塌土石流套疊.png
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
import urllib.parse
import urllib.request
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import geopandas as gpd  # noqa: E402
from osgeo import gdal  # noqa: E402
from shapely.geometry import MultiPolygon, Polygon, shape  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from qgis.core import (  # noqa: E402
    QgsApplication, QgsCoordinateReferenceSystem, QgsFillSymbol, QgsGeometry,
    QgsLayoutExporter, QgsLineSymbol, QgsProject, QgsRasterLayer, QgsRectangle,
    QgsVectorLayer,
)

import build_layout as sheet  # noqa: E402
import overlay_placement as op  # noqa: E402
from nlsc_wmts import build_basemap  # noqa: E402

CRS = "EPSG:3826"
DPI = 300
UA = {"User-Agent": "Mozilla/5.0"}

# ── 山崩雲（回 GeoJSON，coordinate=TWD97 即 3826）──────────────────
LS_BASE = "https://landslide.geologycloud.tw/map/api/opendata/lsdata"
LS_EPS = {
    "history": "歷史山崩目錄",
    "lidar": "光達判釋潛在崩塌",
    "geologicalsensitiveareas": "山崩與地滑地質敏感區",
    "dislope": "順向坡區",
}
# 該 API 的縣市參數只吃這 17 個值（新竹、嘉義為縣市合併；無離島）
LS_COUNTIES = ("臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市", "基隆市",
               "新竹縣市", "苗栗縣", "南投縣", "彰化縣", "雲林縣", "嘉義縣市",
               "屏東縣", "宜蘭縣", "花蓮縣", "臺東縣")

# ── 水保署 MVT（開放資料圖層）─────────────────────────────────
MVT_BASE = "https://gis.ardswc.gov.tw/api/ardswc/vectortiles/shp"
MVT_HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://gis.ardswc.gov.tw/"}
MVT_LAYERS = {
    "debris_flow": "土石流潛勢溪流",
    "debris_affect": "土石流潛勢溪流影響範圍",
    "debris_watershed": "土石流潛勢溪流集水區",
    "potential_landslide": "大規模崩塌潛勢區",
    "potential_landslide_affect": "大規模崩塌潛勢區影響範圍",
    "landslide": "全臺崩塌地",
    "event_landslide_inventories": "事件型崩塌目錄",
}
MVT_Z = 16

# 出圖樣式：(圖例標籤, RGB, 樣式)。面積小的排前面（＝圖面上層）才不會被蓋掉。
# 「山崩與地滑地質敏感區」用 outline：它通常罩住順向坡與歷史山崩，三層半透明填色
# 疊起來會糊成同一片（.claude/rules/出圖上色與分區.md「每層必須可區辨」）。
STYLE = [
    ("土石流潛勢溪流", "土石流潛勢溪流", (0, 90, 220), "line"),
    ("歷史山崩目錄", "歷史山崩目錄", (200, 30, 30), "fill"),
    ("順向坡區", "順向坡區", (140, 60, 200), "fill"),
    ("光達判釋潛在崩塌", "光達判釋／潛在崩塌", (230, 60, 120), "fill"),
    ("大規模崩塌潛勢區", "大規模崩塌潛勢區", (150, 30, 90), "fill"),
    ("事件型崩塌目錄", "事件型崩塌目錄", (220, 90, 60), "fill"),
    ("全臺崩塌地", "全臺崩塌地", (170, 70, 40), "fill"),
    ("山崩與地滑地質敏感區", "山崩與地滑／地質敏感區", (235, 120, 0), "outline"),
    ("土石流潛勢溪流影響範圍", "土石流潛勢溪流／影響範圍", (60, 150, 220), "fill"),
    ("大規模崩塌潛勢區影響範圍", "大規模崩塌潛勢區／影響範圍", (110, 60, 160), "fill"),
    ("土石流潛勢溪流集水區", "土石流潛勢溪流／集水區範圍", (60, 150, 220), "outline"),
]
ALPHA = 0.40


def tile_xy(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.log(math.tan(math.radians(lat))
                          + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def fetch_landslide_cloud(county: str, frame, out_dir: Path) -> None:
    """山崩雲四個向量端點；只留與 frame 相交者。"""
    if county not in LS_COUNTIES:
        print(f"  ⚠ 山崩雲不支援縣市「{county}」（僅 {len(LS_COUNTIES)} 個本島縣市）→ 略過")
        return
    for ep, nm in LS_EPS.items():
        q = urllib.parse.urlencode({"filename": county, "coordinate": "TWD97",
                                    "filetype": "json", "limit": 999999})
        try:
            raw = urllib.request.urlopen(
                urllib.request.Request(f"{LS_BASE}/{ep}?{q}", headers=UA), timeout=300).read()
            d = json.loads(raw.decode("utf-8-sig", errors="replace"))
        except Exception as exc:  # noqa: BLE001 — 單層失敗不阻斷其他層
            print(f"  ⚠ {nm}：取得失敗 {type(exc).__name__}: {exc}")
            continue
        fs = [f for f in d.get("features", []) if f.get("geometry")]
        if not fs:
            print(f"  ─ {nm}：全市 0 筆")
            continue
        g = gpd.GeoDataFrame([f.get("properties", {}) for f in fs],
                             geometry=[shape(f["geometry"]) for f in fs], crs=3826)
        g["geometry"] = g.geometry.buffer(0)
        near = g[g.intersects(frame)]
        if not len(near):
            print(f"  ─ {nm}：全市 {len(g):,} 筆，圖框內 0 筆")
            continue
        near.to_file(out_dir / f"{nm}.geojson", driver="GeoJSON")
        print(f"  ✔ {nm}：全市 {len(g):,} 筆 → 圖框內 {len(near)} 筆")


def fetch_ardswc_mvt(frame, out_dir: Path) -> None:
    """水保署 MVT 向量圖磚。⚠ 網址是 {z}/{y}/{x}，且跨磚物件要 dissolve(dropna=False)。"""
    tmp = Path(os.environ.get("TEMP", "/tmp")) / "ardswc_mvt"
    w, s, e, n = gpd.GeoSeries([frame], crs=3826).to_crs(4326).iloc[0].bounds
    x0, y1 = tile_xy(w, s, MVT_Z)
    x1, y0 = tile_xy(e, n, MVT_Z)
    for lay, nm in MVT_LAYERS.items():
        geoms, props = [], []
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                try:
                    raw = urllib.request.urlopen(urllib.request.Request(
                        f"{MVT_BASE}/{lay}/{MVT_Z}/{y}/{x}.pbf", headers=MVT_HDR),
                        timeout=60).read()
                except Exception:  # noqa: BLE001 — 空磚／404 是正常
                    continue
                if not raw:
                    continue
                p = tmp / lay / str(MVT_Z) / str(x)
                p.mkdir(parents=True, exist_ok=True)
                fp = p / f"{y}.pbf"
                fp.write_bytes(raw)
                ds = gdal.OpenEx("MVT:" + str(fp), gdal.OF_VECTOR)
                if ds is None:
                    continue
                for li in range(ds.GetLayerCount()):
                    for feat in ds.GetLayer(li):
                        gg = feat.GetGeometryRef()
                        if gg is None:
                            continue
                        geoms.append(shape(json.loads(gg.ExportToJson())))
                        props.append(feat.items())
                ds = None
        if not geoms:
            print(f"  ─ {nm}：範圍內無資料")
            continue
        gdf = gpd.GeoDataFrame(props, geometry=geoms, crs=3857).to_crs(3826)
        if gdf.geom_type.iloc[0].endswith("Polygon"):
            gdf["geometry"] = gdf.geometry.buffer(0)
        key = next((c for c in ("debrisno", "dbno_old", "id", "gid", "mvt_id")
                    if c in gdf.columns), None)
        n_raw = len(gdf)
        if key:
            # ⚠ dropna=False 不可省：屬性有 None 時 pandas groupby 會把整列丟掉
            gdf = gdf.dissolve(by=key, as_index=False, dropna=False)
        gdf.to_file(out_dir / f"{nm}.geojson", driver="GeoJSON")
        print(f"  ✔ {nm}：{len(gdf)} 件／{n_raw} 片")


def summarise(out_dir: Path, site) -> list:
    rows = []
    for _nm, label, _rgb, _kind in STYLE:
        p = out_dir / f"{_nm}.geojson"
        if not p.exists():
            rows.append([_nm, 0, "", "範圍內無資料"])
            continue
        g = gpd.read_file(p, engine="pyogrio")
        if g.crs is None:
            g = g.set_crs(3826)
        u = unary_union(list(g.geometry))
        inter = u.intersection(site)
        ia = getattr(inter, "area", 0.0)
        rows.append([_nm, len(g), round(u.distance(site), 1),
                     f"{ia:,.1f} ㎡（{ia / site.area * 100:.2f}%）" if ia else "未及基地"])
    with io.open(out_dir / "坡地災害檢核.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["圖層", "件數", "距基地_m", "與基地交集"])
        w.writerows(rows)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True, help="案名（outputs/<案名>）")
    ap.add_argument("--joined", default=None, help="基地 shp（預設 <案>/cadastre/基地.shp）")
    ap.add_argument("--county", default=None, help="山崩雲縣市參數（預設從基地 shp 讀）")
    ap.add_argument("--margin", type=float, default=260.0,
                    help="出圖框外擴公尺（預設 260；土石流常在數百公尺外，"
                         "以基地為準的框裝不下就整組畫不出來）")
    ap.add_argument("--skip-fetch", action="store_true", help="沿用既有 geojson，只重出圖")
    a = ap.parse_args()

    case_dir = REPO / "outputs" / a.case
    site_shp = Path(a.joined) if a.joined else (case_dir / "cadastre" / "基地.shp")
    if not site_shp.exists():
        sys.exit(f"找不到基地 shp：{site_shp}")
    out_dir = case_dir / "崩塌土石流"
    out_dir.mkdir(parents=True, exist_ok=True)

    b = gpd.read_file(site_shp, engine="pyogrio")
    if b.crs is None:
        b = b.set_crs(3826)
    b = b.to_crs(3826)
    site = unary_union(list(b.geometry))
    county = a.county or next((str(b.iloc[0][c]).strip() for c in ("縣市", "縣市名稱")
                               if c in b.columns), "")
    frame = site.buffer(a.margin + 200)

    if not a.skip_fetch:
        print(f"[坡地災害] 縣市={county}　圖框外擴 {a.margin:.0f} m")
        fetch_landslide_cloud(county, frame, out_dir)
        fetch_ardswc_mvt(frame, out_dir)

    print("\n[實算] 與基地之關係")
    for nm, n, d, rel in summarise(out_dir, site):
        print(f"  {nm:<26s}{n:>4} 件　距基地 {str(d):>7s} m　{rel}")

    # ── 出圖 ────────────────────────────────────────────
    qgs = QgsApplication([], False)
    qgs.initQgis()
    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem(CRS))
    keep = []
    lyr_site = QgsVectorLayer(str(site_shp), "基地地籍", "ogr")
    lyr_site.renderer().setSymbol(QgsFillSymbol.createSimple({
        "color": "255,255,255,0", "outline_color": "200,0,0,255", "outline_width": "1.0"}))
    project.addMapLayer(lyr_site, False)
    keep.append(lyr_site)

    haz = []
    for nm, label, rgb, kind in STYLE:
        p = out_dir / f"{nm}.geojson"
        if not p.exists():
            continue
        lyr = QgsVectorLayer(str(p), label, "ogr")
        if not lyr.isValid() or lyr.featureCount() == 0:
            continue
        if not lyr.crs().isValid():
            lyr.setCrs(QgsCoordinateReferenceSystem(CRS))
        r, g, bl = rgb
        if kind == "line":
            lyr.renderer().setSymbol(QgsLineSymbol.createSimple({
                "color": f"{r},{g},{bl},255", "width": "1.2"}))
        elif kind == "outline":
            lyr.renderer().setSymbol(QgsFillSymbol.createSimple({
                "color": "255,255,255,0", "outline_color": f"{r},{g},{bl},255",
                "outline_width": "0.9", "outline_style": "dash"}))
        else:
            dark = tuple(int(c * 0.6) for c in rgb)
            lyr.renderer().setSymbol(QgsFillSymbol.createSimple({
                "color": f"{r},{g},{bl},{int(ALPHA * 255)}",
                "outline_color": "%d,%d,%d,255" % dark, "outline_width": "0.3"}))
        project.addMapLayer(lyr, False)
        keep.append(lyr)
        haz.append(lyr)

    geom = QgsGeometry.unaryUnion([f.geometry() for f in lyr_site.getFeatures()])
    frame_geom = geom.buffer(a.margin, 8)
    fbb = frame_geom.boundingBox()
    bbox = (fbb.xMinimum(), fbb.yMinimum(), fbb.xMaximum(), fbb.yMaximum())

    def _inter(x1, y1, x2, y2):
        rr = frame_geom.intersection(QgsGeometry.fromRect(QgsRectangle(x1, y1, x2, y2)))
        return 0.0 if rr.isEmpty() else rr.area()

    fl, fr, fb, ft = op.place_fixed(_inter, bbox, map_w=op.MAP2_W, map_h=op.MAP2_H).extent
    bm_dir = case_dir / "底圖"
    bm_dir.mkdir(parents=True, exist_ok=True)
    tif = build_basemap((fl, fb, fr, ft), str(bm_dir / "hazard_PHOTO2.tif"),
                        layer="PHOTO2", cache_dir=str(bm_dir / "_tilecache"),
                        buffer_ratio=0.02)
    photo = QgsRasterLayer(tif, "正射影像(NLSC PHOTO2)") if tif else None
    if photo is None or not photo.isValid():
        sys.exit("正射底圖下載失敗——本張以空照為底，沒有底圖不該出圖")
    project.addMapLayer(photo, False)
    keep.append(photo)

    f0 = next(iter(lyr_site.getFeatures()))

    def fld(name):
        i = lyr_site.fields().indexFromName(name)
        return str(f0[i]).strip() if i >= 0 and f0[i] not in (None, "") else ""

    i_ln = lyr_site.fields().indexFromName("地號")
    lns = [str(f[i_ln]).strip() for f in lyr_site.getFeatures()] if i_ln >= 0 else []
    pnote = sheet.parcel_note(fld("段"), lns[0] if lns else "", len(lns))

    img_dir = case_dir / "圖檔_maxmap"
    img_dir.mkdir(parents=True, exist_ok=True)
    layout, _pl = sheet.build_sheet_layout(
        project, "001_崩塌土石流套疊", [lyr_site] + haz + [photo], haz + [lyr_site],
        set(), frame_geom, bbox, fld("縣市") + fld("鄉鎮"), pnote, keep,
        show_loc_parcel=False)
    st = QgsLayoutExporter.ImageExportSettings()
    st.dpi = DPI
    out = img_dir / f"001_{a.case}_崩塌土石流套疊.png"
    ok = QgsLayoutExporter(layout).exportToImage(str(out), st) == QgsLayoutExporter.Success
    print(("\n已輸出 " if ok else "\n輸出失敗 ") + str(out))
    print("⚠ 交付口徑：兩平臺皆聲明僅供調查研究分析規劃，不得應用於法規作業；"
          "法定認定仍以 NGIS／主管機關查復為準。")
    project.clear()
    qgs.exitQgis()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
