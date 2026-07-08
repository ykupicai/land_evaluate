# -*- coding: utf-8 -*-
"""
階段3出圖：02_比對結果.xlsx → 每案場 4 張 A4 橫式 PNG ＋ 全案 .qgz ＋ 05_出圖清單.xlsx

執行（必須用 QGIS 內建 Python，PowerShell 呼叫）：
  & "C:\\Program Files\\QGIS 3.40.12\\bin\\python-qgis-ltr.bat" scripts\\make_maps.py \
      [案子名] [--cases 030,004] [--exclude 021,023,038,063,086]

流程：
1. 讀 02_比對結果.xlsx（僅取「比對狀態=成功」列；--exclude 案場整案跳過）
2. 依「圖資FID」直接從縣市地籍 shp 取宗地幾何（免重新比對），寫入 outputs/<案子>/圖層.gpkg
   - 基地（含 案場ID/地號/使分/使類；都市土地以「都市土地（使用分區另查）」表示）
   - 周邊地籍（出圖範圍再外擴內的**所有**宗地，含 地號/使分/使類/是否基地——
     使用分區圖與使用地編定圖靠它讓周邊宗地也上底色）
   - 功能分區（各縣市功能分區 shp 於出圖範圍之要素，分區名以 zone_utils.normalize_zone 標準化；
     載入清單/CRS 修正邏輯與 scripts/match_parcels.py 的 load_county_zones() 一致）
3. 每案場 4 張主題圖（A4 橫式、EPSG:3826）：
   1 使用分區、2 使用地編定（此二張套 styles/使用分區.qml、使用地編定.qml——由
     scripts/build_legend_styles.py 從使用者 QGIS 樣式庫 symbology-style.db 產出，
     含 LinePatternFill 紋理；周邊宗地與基地都上色、基地另疊粗紅框）、
   3 國土功能分區（配色依 國土功能分區/圖例.xml）、
   4 NLSC WMTS 正射影像 PHOTO2 ＋ EMAP 半透明 50% 套疊
4. 比例尺：案場 bbox 外擴 25%（BBOX_EXPAND 可調）後就近取整
   （1/500、1/1000、1/2500、1/5000，超出取實際值進位至千）；4 張圖同一範圍
5. 輸出：圖檔/<案場ID>_<案場名>_<主題>.png、<案子>.qgz（圖層依案場分組＋每張圖一個 Layout）、
   05_出圖清單.xlsx；單張失敗記入清單續跑不中斷。
"""

from __future__ import annotations

import csv
import math
import re
import sqlite3
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import yaml
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font

from zone_utils import normalize_zone
from nlsc_wmts import build_basemap

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsFillSymbol,
    QgsGeometry,
    QgsLayerTree,
    QgsLayoutExporter,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutSize,
    QgsLegendRenderer,
    QgsLegendStyle,
    QgsMapRendererSequentialJob,
    QgsMapSettings,
    QgsPalLayerSettings,
    QgsPrintLayout,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsRendererCategory,
    QgsSettings,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsUnitTypes,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import QRectF, QSize, Qt
from qgis.PyQt.QtGui import QColor, QFont

# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------

QGIS_PREFIX = r"C:\Program Files\QGIS 3.40.12\apps\qgis-ltr"
CRS_3826 = "EPSG:3826"
TODAY = datetime.now().strftime("%Y-%m-%d")

# 圖面（A4 橫式 297x210mm）
MAP_X, MAP_Y, MAP_W, MAP_H = 6.0, 14.0, 200.0, 190.0  # mm
PANEL_X = 210.0
SCALE_LEVELS = [500, 750, 1000, 1250, 1500, 2000, 2500, 3000, 4000, 5000]
BBOX_EXPAND = 1.12  # 基地 dissolve 範圍外擴約 12%（盡量放大基地以利辨識）
NEIGH_EXPAND = 0.08  # 周邊地籍再外擴比例（相對出圖範圍寬度）
EXPORT_DPI = 300
FONT_NAME = "Microsoft JhengHei"  # 微軟正黑體

WMTS_URL = "https://wmts.nlsc.gov.tw/wmts"
URBAN_LABEL = "都市土地（使用分區另查）"
WRITE_QGZ = True  # 是否輸出 .qgz（可用 --no-qgz 關閉；大批次崩潰時先關）

# 都市計畫土地使用分區（補都市土地 使分/使類=None 的缺口，見 config/urban_plan_legend.yaml）
URBAN_SHP = "都市計畫/全台都市計畫土地使用分區.shp"
URBAN_ZONE_FIELD = "zone"
URBAN_COLOR_FIELD = "Color1"
URBAN_QML = "都市計畫.qml"
URBAN_LEGEND_YAML = "config/urban_plan_legend.yaml"
URBAN_LAYER_NAME = "都市計畫土地使用分區"
URBAN_LANDTYPE_OPACITY = 1.0  # 使用地編定圖也完整呈現都市計畫分區（依使用者圖說）

# 主題（key=檔名用、title=圖名/圖說）
THEMES = ["使用分區", "使用地編定", "國土功能分區", "正射套疊"]
THEME_TITLES = {
    "使用分區": "非都市土地使用分區及都市計畫土地使用分區圖",
    "使用地編定": "非都市土地使用地變更編定及都市計畫土地使用分區圖",
    "國土功能分區": "國土功能分區圖",
    "正射套疊": "正射影像及通用電子地圖套疊圖",
}

# 有「總圖/合併檔」的縣市只用合併檔（與 scripts/match_parcels.py 的 PREFERRED_ZONE_FILES 一致）
PREFERRED_ZONE_FILES = {
    "苗栗縣": ["國土功能分區1091208修"],
    "臺中市": ["功能分區總圖"],
    "臺東縣": ["臺東陸域功能分區D", "臺東海域功能分區D"],
}

# 國土功能分區配色（自 國土功能分區/圖例.xml 轉出，RGB）
ZONE_COLORS = {
    "國土保育地區第一類": (56, 168, 0),
    "國土保育地區第二類": (8, 255, 0),
    "國土保育地區第三類": (178, 145, 255),
    "國土保育地區第四類": (221, 255, 81),
    "城鄉發展地區第一類": (217, 198, 217),
    "城鄉發展地區第二類": (235, 110, 110),      # 圖例無「城二」總類，取近似色
    "城鄉發展地區第二類之一": (251, 93, 90),
    "城鄉發展地區第二類之二": (160, 92, 89),
    "城鄉發展地區第二類之三": (213, 122, 127),
    "城鄉發展地區第三類": (251, 92, 255),
    "農業發展地區第一類": (251, 252, 96),
    "農業發展地區第二類": (255, 171, 90),
    "農業發展地區第三類": (253, 221, 172),
    "農業發展地區第四類": (252, 218, 219),
    "農業發展地區第五類": (198, 170, 96),
    "海洋資源地區第一類之一": (93, 91, 175),
    "海洋資源地區第一類之二": (93, 91, 250),
    "海洋資源地區第一類之三": (99, 195, 255),
    "海洋資源地區第二類": (85, 255, 254),
    "海洋資源地區第三類": (218, 252, 253),
}

OUT05_COLUMNS = ["案場ID", "案場名稱", "主題", "檔名", "比例尺", "涵蓋地號", "產出時間", "狀態", "備註"]


def sanitize(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "-", str(name)).strip("-")


def attr_str(v) -> str:
    """shp 屬性 → 乾淨字串（NULL/None → 空字串）。"""
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s in ("NULL", "None", "nan") else s


def landno_text(mother, child) -> str:
    """宗地母/宗地子（4 位字串）還原地號顯示字串：0331/0001 → 331-1、0331/0000 → 331。"""
    try:
        m = int(attr_str(mother) or "0")
        c = int(attr_str(child) or "0")
    except ValueError:
        return attr_str(mother)
    return f"{m}-{c}" if c else f"{m}"


# ---------------------------------------------------------------------------
# 讀 02 → 案場結構
# ---------------------------------------------------------------------------


def read_cases(xlsx: Path, only: set[str] | None, exclude: set[str]) -> dict[str, dict]:
    wb = load_workbook(xlsx, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = [str(c) for c in next(rows)]
    idx = {h: i for i, h in enumerate(header)}
    cases: dict[str, dict] = {}
    for r in rows:
        if r is None or r[idx["案場ID"]] is None:
            continue
        cid = str(r[idx["案場ID"]]).strip()
        if cid in exclude or (only and cid not in only):
            continue
        if str(r[idx["比對狀態"]]) != "成功":
            continue
        fid_txt = str(r[idx["圖資FID"]] or "")
        fids = [int(x) for x in fid_txt.split(";") if x.strip()]
        c = cases.setdefault(cid, {
            "id": cid,
            "name": str(r[idx["案場名稱"]]).strip(),
            "county": str(r[idx["縣市"]]).strip(),
            "town": str(r[idx["鄉鎮區"]] or "").strip(),
            "rows": [],
        })
        c["rows"].append({
            "地號": str(r[idx["地號原文"]]).strip(),
            "段": str(r[idx["段"]] or "").strip(),
            "小段": str(r[idx["小段"]] or "").strip(),
            "宗地母": str(r[idx["宗地母"]] or "").strip(),
            "宗地子": str(r[idx["宗地子"]] or "").strip(),
            "使分": (str(r[idx["圖資使分"]]).strip() if r[idx["圖資使分"]] else None),
            "使類": (str(r[idx["圖資使類"]]).strip() if r[idx["圖資使類"]] else None),
            "fids": fids,
        })
    wb.close()
    return cases


def parcels_text(case: dict) -> str:
    """涵蓋地號：依段/小段分組。例：更寮段更寮小段 331、331-1"""
    groups: dict[str, list[str]] = {}
    for row in case["rows"]:
        sec = row["段"] + ("段" if row["段"] and not row["段"].endswith("段") else "")
        if row["小段"] and row["小段"] not in ("", "None", "nan"):
            sec += row["小段"] + "小段"
        groups.setdefault(sec, []).append(row["地號"])
    return "；".join(f"{sec} {('、'.join(nums))}" for sec, nums in groups.items())


def wrap_cjk(s: str, width: int = 22) -> str:
    """中文長字串（以 、；空白 斷點）強制換行，避免版面標籤超出邊框被裁切。"""
    out_lines, cur = [], ""
    token = ""
    for ch in s:
        token += ch
        if ch in "、； ":
            if len(cur) + len(token) > width and cur:
                out_lines.append(cur)
                cur = token
            else:
                cur += token
            token = ""
    cur += token
    if len(cur) > width and out_lines:
        # 最後殘段仍過長就再拆
        while len(cur) > width:
            out_lines.append(cur[:width])
            cur = cur[width:]
    out_lines.append(cur)
    return "\n".join(x for x in out_lines if x)


# ---------------------------------------------------------------------------
# 比例尺與圖面範圍
# ---------------------------------------------------------------------------


def compute_scale_extent(bbox: QgsRectangle) -> tuple[int, QgsRectangle]:
    w = max(bbox.width(), 30.0) * BBOX_EXPAND
    h = max(bbox.height(), 30.0) * BBOX_EXPAND
    need = max(w / (MAP_W / 1000.0), h / (MAP_H / 1000.0))
    scale = next((s for s in SCALE_LEVELS if s >= need), None)
    if scale is None:
        scale = int(math.ceil(need / 1000.0) * 1000)
    half_w = MAP_W / 1000.0 * scale / 2.0
    half_h = MAP_H / 1000.0 * scale / 2.0
    cx, cy = bbox.center().x(), bbox.center().y()
    return scale, QgsRectangle(cx - half_w, cy - half_h, cx + half_w, cy + half_h)


# ---------------------------------------------------------------------------
# 資料抽取 → 圖層.gpkg
# ---------------------------------------------------------------------------


def mem_layer(name: str, fields: str) -> QgsVectorLayer:
    lyr = QgsVectorLayer(f"MultiPolygon?crs={CRS_3826}&{fields}", name, "memory")
    assert lyr.isValid()
    return lyr


def add_feat(layer: QgsVectorLayer, geom, attrs: list) -> None:
    f = QgsFeature(layer.fields())
    f.setGeometry(geom)
    f.setAttributes(attrs)
    layer.dataProvider().addFeature(f)


def ensure_qix(layer: QgsVectorLayer, shp_path: Path) -> None:
    qix = shp_path.with_suffix(".qix")
    if not qix.exists():
        try:
            layer.dataProvider().createSpatialIndex()
            print(f"  已建空間索引 {qix.name}", flush=True)
        except Exception:
            pass


def fix_zone_crs(layer: QgsVectorLayer) -> str:
    """依 registry 探勘結論修 CRS（同 match_parcels._fix_crs）。回傳實際來源 CRS authid。"""
    crs = layer.crs()
    if not crs.isValid():
        layer.setCrs(QgsCoordinateReferenceSystem(CRS_3826))
        return CRS_3826
    authid = crs.authid()
    if authid == "EPSG:3825":
        return "EPSG:3825"  # 需轉換
    if authid != CRS_3826:
        # 自訂 PROJCS（中央經線121、比例0.9999、假東250000）實為 TWD97 TM2
        layer.setCrs(QgsCoordinateReferenceSystem(CRS_3826))
    return CRS_3826


def extract_layers(cases: dict[str, dict], registry: dict, gpkg: Path) -> list[str]:
    """抽基地/周邊/功能分區 → gpkg。回傳警告清單。"""
    warnings: list[str] = []
    base = mem_layer("基地", "field=案場ID:string&field=案場名稱:string&field=地號:string"
                            "&field=使分:string&field=使類:string")
    neigh = mem_layer("周邊地籍", "field=案場ID:string&field=地號:string"
                                 "&field=使分:string&field=使類:string&field=是否基地:string")
    zones = mem_layer("功能分區", "field=縣市:string&field=zone:string")

    by_county: dict[str, list[dict]] = defaultdict(list)
    for c in cases.values():
        by_county[c["county"]].append(c)

    for county, clist in sorted(by_county.items()):
        cad = registry["counties"][county].get("cadastral")
        if not cad:
            for c in clist:
                c["error"] = f"{county} 無地籍圖資"
                warnings.append(f"{c['id']} {c['error']}")
            continue
        shp = REPO_ROOT / cad
        lyr = QgsVectorLayer(str(shp), county, "ogr")
        if not lyr.isValid():
            for c in clist:
                c["error"] = f"地籍 shp 載入失敗：{cad}"
                warnings.append(f"{c['id']} {c['error']}")
            continue
        ensure_qix(lyr, shp)
        fld = lyr.fields()
        i_mother, i_child = fld.indexOf("宗地母"), fld.indexOf("宗地子")
        i_zone, i_type = fld.indexOf("使分"), fld.indexOf("使類")
        print(f"{county}：{len(clist)} 案場", flush=True)

        for c in clist:
            fids = sorted({f for row in c["rows"] for f in row["fids"]})
            feats = {f.id(): f for f in lyr.getFeatures(QgsFeatureRequest().setFilterFids(fids))}
            bbox = None
            for row in c["rows"]:
                for fid in row["fids"]:
                    f = feats.get(fid)
                    if f is None:
                        warnings.append(f"{c['id']} 地號{row['地號']} FID {fid} 取不到幾何")
                        continue
                    if i_mother >= 0 and str(f[i_mother]) != row["宗地母"]:
                        warnings.append(
                            f"{c['id']} 地號{row['地號']} FID {fid} 宗地母不符"
                            f"（shp={f[i_mother]} 02={row['宗地母']}），仍照 FID 出圖")
                    g = f.geometry()
                    add_feat(base, g, [c["id"], c["name"], row["地號"],
                                       row["使分"] or URBAN_LABEL, row["使類"] or URBAN_LABEL])
                    bbox = g.boundingBox() if bbox is None else bbox.combineExtentWith(g.boundingBox()) or bbox
            if bbox is None or bbox.isEmpty():
                c["error"] = "全部地號取不到幾何"
                warnings.append(f"{c['id']} {c['error']}")
                continue
            c["scale"], c["extent"] = compute_scale_extent(bbox)
            c["has_urban_rows"] = any(row["使分"] is None for row in c["rows"])
            # 周邊地籍：出圖範圍再外擴 NEIGH_EXPAND 內的所有宗地，帶 地號/使分/使類；
            # 基地宗地也在內（是否基地=Y，供標註規則排除、避免與基地紅框層重複標地號）
            case_fids = set(fids)
            rect = c["extent"].buffered(c["extent"].width() * NEIGH_EXPAND)
            attrs_idx = [i for i in (i_mother, i_child, i_zone, i_type) if i >= 0]
            req = QgsFeatureRequest().setFilterRect(rect).setSubsetOfAttributes(attrs_idx)
            n = 0
            for f in lyr.getFeatures(req):
                zone = attr_str(f[i_zone]) if i_zone >= 0 else ""
                typ = attr_str(f[i_type]) if i_type >= 0 else ""
                add_feat(neigh, f.geometry(), [
                    c["id"],
                    landno_text(f[i_mother], f[i_child]) if i_mother >= 0 else "",
                    zone or URBAN_LABEL,
                    typ or URBAN_LABEL,
                    "Y" if f.id() in case_fids else "",
                ])
                n += 1
            print(f"  {c['id']} {c['name']}：{len(fids)} 宗地、1/{c['scale']}、周邊 {n} 筆", flush=True)

        # 功能分區（該縣市全部案場範圍）
        rects = [c["extent"].buffered(c["extent"].width() * 0.05)
                 for c in clist if "extent" in c]
        if rects:
            n = load_zones_for_county(zones, registry, county, rects, warnings)
            print(f"  {county} 功能分區要素 {n} 筆", flush=True)

    # 寫 gpkg
    gpkg.parent.mkdir(parents=True, exist_ok=True)
    if gpkg.exists():
        gpkg.unlink()
    ctx = QgsProject.instance().transformContext()
    for i, lyr in enumerate((base, neigh, zones)):
        opt = QgsVectorFileWriter.SaveVectorOptions()
        opt.driverName = "GPKG"
        opt.layerName = lyr.name()
        opt.fileEncoding = "UTF-8"
        opt.actionOnExistingFile = (
            QgsVectorFileWriter.CreateOrOverwriteFile if i == 0
            else QgsVectorFileWriter.CreateOrOverwriteLayer)
        res = QgsVectorFileWriter.writeAsVectorFormatV3(lyr, str(gpkg), ctx, opt)
        if res[0] != QgsVectorFileWriter.NoError:
            raise RuntimeError(f"gpkg 寫入失敗 {lyr.name()}: {res}")
    print(f"已寫入 {gpkg}", flush=True)
    return warnings


def load_zones_for_county(zones_mem: QgsVectorLayer, registry: dict, county: str,
                          rects: list[QgsRectangle], warnings: list[str]) -> int:
    entries = registry["counties"].get(county, {}).get("functional_zones", [])
    preferred = PREFERRED_ZONE_FILES.get(county)
    if preferred:
        entries = [e for e in entries if any(p in e["path"] for p in preferred)]
    if not entries:
        warnings.append(f"{county} 無功能分區圖資（主題3將無分區面）")
        return 0
    total = 0
    crs3826 = QgsCoordinateReferenceSystem(CRS_3826)
    for e in entries:
        path = REPO_ROOT / e["path"]
        stem_label = normalize_zone(Path(e["path"]).stem)
        zone_field = e.get("zone_field")
        if zone_field is None and stem_label is None:
            continue  # 非四大分區圖層（行政區界、未來發展、纜線…）
        lyr = QgsVectorLayer(str(path), Path(e["path"]).stem, "ogr")
        if not lyr.isValid():
            warnings.append(f"{county} 功能分區讀取失敗：{e['path']}")
            continue
        src_auth = fix_zone_crs(lyr)
        xform = None
        rect_xform = None
        if src_auth == "EPSG:3825":
            src = QgsCoordinateReferenceSystem("EPSG:3825")
            xform = QgsCoordinateTransform(src, crs3826, QgsProject.instance())
            rect_xform = QgsCoordinateTransform(crs3826, src, QgsProject.instance())
        fld_idx = lyr.fields().indexOf(zone_field) if zone_field else -1
        seen: set[int] = set()
        for rect in rects:
            r = rect_xform.transformBoundingBox(rect) if rect_xform else rect
            for f in lyr.getFeatures(QgsFeatureRequest().setFilterRect(r)):
                if f.id() in seen:
                    continue
                seen.add(f.id())
                if fld_idx >= 0:
                    z = normalize_zone(f[fld_idx]) or stem_label
                else:
                    z = stem_label
                if z is None:
                    continue
                g = f.geometry()
                if xform:
                    g = type(g)(g)
                    g.transform(xform)
                add_feat(zones_mem, g, [county, z])
                total += 1
    return total


# ---------------------------------------------------------------------------
# 樣式
# ---------------------------------------------------------------------------


def fill_symbol(rgb, outline="35,35,35,255", width="0.2") -> QgsFillSymbol:
    r, g, b = rgb
    return QgsFillSymbol.createSimple({
        "color": f"{r},{g},{b},255", "style": "solid",
        "outline_color": outline, "outline_style": "solid", "outline_width": width,
    })


def outline_symbol(color="255,0,0,255", width="0.9") -> QgsFillSymbol:
    return QgsFillSymbol.createSimple({
        "color": "0,0,0,0", "style": "no",
        "outline_color": color, "outline_style": "solid", "outline_width": width,
    })


def zone_renderer() -> QgsCategorizedSymbolRenderer:
    cats = []
    for val, rgb in ZONE_COLORS.items():
        cats.append(QgsRendererCategory(val, fill_symbol(rgb, outline="35,35,35,60", width="0.1"), val))
    other = QgsFillSymbol.createSimple({
        "color": "210,210,210,255", "style": "solid",
        "outline_color": "35,35,35,60", "outline_width": "0.1"})
    cats.append(QgsRendererCategory(None, other, "其他"))
    return QgsCategorizedSymbolRenderer("zone", cats)


def label_settings(field: str = "地號", size: float = 9.0, is_expression: bool = False,
                   color: tuple[int, int, int] = (0, 0, 0)) -> QgsVectorLayerSimpleLabeling:
    pal = QgsPalLayerSettings()
    pal.fieldName = field
    pal.isExpression = is_expression
    fmt = QgsTextFormat()
    fmt.setFont(QFont(FONT_NAME))
    fmt.setSize(size)
    fmt.setColor(QColor(*color))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(1.0)
    buf.setColor(QColor(255, 255, 255))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    return QgsVectorLayerSimpleLabeling(pal)


# 周邊宗地標註：基地宗地交給紅框層標（避免重複）；PAL 自動避讓，擁擠處自動略label
NEIGH_LABEL_EXPR = "CASE WHEN \"是否基地\" = 'Y' THEN NULL ELSE \"地號\" END"


def load_urban_color_names(yaml_path: Path) -> dict[str, list[str]]:
    """都市計畫官方圖例 Color1(\"R,G,B\") → 分區/用地名清單（同色多名時全列出）。"""
    with yaml_path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    out: dict[str, list[str]] = {}
    for name, rgb in cfg.get("zones", {}).items():
        key = ",".join(str(x) for x in rgb)
        out.setdefault(key, []).append(name)
    return out


def urban_zone_legend_layer(urban_lyr: QgsVectorLayer, extent: QgsRectangle,
                            color_to_names: dict[str, list[str]],
                            keep_alive: list) -> QgsVectorLayer | None:
    """建圖例專用小圖層：案場出圖範圍內實際出現的都市計畫 Color1 → 分區名清單。
    不掛進地圖圖層清單（不渲染），只放進 legend_layers 供圖例面板顯示；
    每類別放 1 個位於 extent 中心的假幾何，確保 setLegendFilterByMapEnabled 不會濾掉它。"""
    if not urban_lyr.isValid():
        return None
    i_color = urban_lyr.fields().indexOf(URBAN_COLOR_FIELD)
    i_zone = urban_lyr.fields().indexOf(URBAN_ZONE_FIELD)
    if i_color < 0:
        return None
    req = QgsFeatureRequest().setFilterRect(extent)
    attrs = [i for i in (i_color, i_zone) if i >= 0]
    req.setSubsetOfAttributes(attrs)
    seen: dict[str, str] = {}
    for f in urban_lyr.getFeatures(req):
        color = attr_str(f[i_color])
        if not color or color in seen:
            continue
        names = color_to_names.get(color)
        if names:
            # 圖例只列代表名（同色多分區取前2個＋等），完整分區名已標在各 polygon 上
            label = "／".join(names[:2]) + ("　等" if len(names) > 2 else "")
        else:
            label = attr_str(f[i_zone]) if i_zone >= 0 else color
        seen[color] = label
    if not seen:
        return None
    lyr = QgsVectorLayer(f"Polygon?crs={CRS_3826}&field=cat:string", URBAN_LAYER_NAME, "memory")
    cx, cy = extent.center().x(), extent.center().y()
    half = max(extent.width(), extent.height()) * 0.001
    cats = []
    for i, (color, label) in enumerate(seen.items()):
        r, g, b = (int(x) for x in color.split(","))
        sym = fill_symbol((r, g, b), outline="120,120,120,255", width="0.1")
        cats.append(QgsRendererCategory(str(i), sym, label))
        geom = QgsGeometry.fromRect(QgsRectangle(cx - half, cy - half, cx + half, cy + half))
        add_feat(lyr, geom, [str(i)])
    lyr.setRenderer(QgsCategorizedSymbolRenderer("cat", cats))
    keep_alive.append(lyr)
    return lyr


def save_styles(styles_dir: Path) -> None:
    """輸出 QML 樣式檔供 QGIS 手動微調時套用。
    注意：使用分區.qml／使用地編定.qml 由 scripts/build_legend_styles.py 從使用者
    樣式庫產出，這裡**不可**覆寫。"""
    styles_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        ("國土功能分區.qml", "field=zone:string", zone_renderer()),
        ("基地紅框.qml", "field=地號:string", None),
        ("周邊地籍.qml", "field=案場ID:string", None),
    ]
    for fname, fields, renderer in specs:
        tmp = QgsVectorLayer(f"MultiPolygon?crs={CRS_3826}&{fields}", "tmp", "memory")
        if renderer is not None:
            tmp.setRenderer(renderer.clone())
        elif fname.startswith("基地紅框"):
            tmp.renderer().setSymbol(outline_symbol())
            tmp.setLabeling(label_settings())
            tmp.setLabelsEnabled(True)
        else:
            tmp.renderer().setSymbol(outline_symbol(color="130,130,130,255", width="0.18"))
        tmp.saveNamedStyle(str(styles_dir / fname))
    print(f"樣式檔已輸出至 {styles_dir}（使用分區/使用地編定.qml 由 build_legend_styles.py 管理）", flush=True)


# ---------------------------------------------------------------------------
# 版面
# ---------------------------------------------------------------------------


def _set_label_font(item: QgsLayoutItemLabel, size: float, bold: bool = False) -> None:
    f = QFont(FONT_NAME, int(size))
    f.setBold(bold)
    try:
        fmt = QgsTextFormat()
        fmt.setFont(f)
        fmt.setSize(size)
        item.setTextFormat(fmt)
    except Exception:
        item.setFont(f)


def build_layout(project: QgsProject, name: str, title: str, layers: list,
                 legend_layers: list, legend_hide_title: set, scale: int,
                 extent: QgsRectangle, notes: str, keep_alive: list,
                 map_overlay: str | None = None) -> QgsPrintLayout:
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(name)
    page = layout.pageCollection().page(0)
    page.setPageSize(QgsLayoutSize(297, 210, QgsUnitTypes.LayoutMillimeters))

    # 地圖
    m = QgsLayoutItemMap(layout)
    m.setCrs(QgsCoordinateReferenceSystem(CRS_3826))
    m.setLayers(layers)
    m.setKeepLayerSet(True)
    m.setBackgroundEnabled(True)
    m.setBackgroundColor(QColor(255, 255, 255))
    m.setFrameEnabled(True)
    m.attemptSetSceneRect(QRectF(MAP_X, MAP_Y, MAP_W, MAP_H))
    m.setExtent(extent)
    m.setScale(float(scale))
    layout.addLayoutItem(m)

    # 標題
    t = QgsLayoutItemLabel(layout)
    t.setText(title)
    _set_label_font(t, 15, bold=True)
    t.attemptSetSceneRect(QRectF(MAP_X, 3.5, 280, 9))
    layout.addLayoutItem(t)

    # 指北針
    na = QgsLayoutItemPicture(layout)
    na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
    na.attemptSetSceneRect(QRectF(276, 16, 11, 17))
    na.setLinkedMap(m)
    layout.addLayoutItem(na)

    # 圖面加註橫幅（如：都市土地無使用地編定）
    if map_overlay:
        ov = QgsLayoutItemLabel(layout)
        ov.setText(map_overlay)
        ov.setBackgroundEnabled(True)
        ov.setBackgroundColor(QColor(255, 255, 255, 210))
        try:
            ov.setHAlign(Qt.AlignHCenter)
        except Exception:
            pass
        _set_label_font(ov, 13, bold=True)
        try:
            fmt = ov.textFormat()
            fmt.setColor(QColor(200, 0, 0))
            ov.setTextFormat(fmt)
        except Exception:
            pass
        ov.attemptSetSceneRect(QRectF(MAP_X + 10, MAP_Y + MAP_H - 20, MAP_W - 20, 10))
        layout.addLayoutItem(ov)

    # 圖例
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
    leg.setLegendFilterByMapEnabled(True)
    leg.setTitle("圖例")
    try:
        leg.setWrapString("／")   # 長標籤依「／」換行，避免超出邊框
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
    leg.attemptSetSceneRect(QRectF(PANEL_X, 16, 84, 118))
    layout.addLayoutItem(leg)

    # 比例尺文字＋比例尺
    st = QgsLayoutItemLabel(layout)
    st.setText(f"比例尺　1 / {scale:,}".replace(",", " "))
    _set_label_font(st, 10, bold=True)
    st.attemptSetSceneRect(QRectF(PANEL_X, 138, 80, 6))
    layout.addLayoutItem(st)

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
    sb.attemptSetSceneRect(QRectF(PANEL_X, 145, 70, 12))
    layout.addLayoutItem(sb)

    # 註記
    n = QgsLayoutItemLabel(layout)
    n.setText(notes)
    _set_label_font(n, 8)
    n.setMode(QgsLayoutItemLabel.ModeFont) if hasattr(QgsLayoutItemLabel, "ModeFont") else None
    n.attemptSetSceneRect(QRectF(PANEL_X, 160, 84, 47))
    layout.addLayoutItem(n)

    project.layoutManager().addLayout(layout)
    return layout


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main() -> None:
    global WRITE_QGZ
    args = [a for a in sys.argv[1:]]
    case_name = "20260707-清潔隊部資訊"
    only: set[str] | None = None
    exclude: set[str] = set()
    for a in args:
        if a.startswith("--cases="):
            only = {x.strip() for x in a.split("=", 1)[1].split(",") if x.strip()}
        elif a.startswith("--exclude="):
            exclude = {x.strip() for x in a.split("=", 1)[1].split(",") if x.strip()}
        elif a == "--no-qgz":
            WRITE_QGZ = False
        elif not a.startswith("--"):
            case_name = a

    out_dir = REPO_ROOT / "outputs" / case_name
    img_dir = out_dir / "圖檔"
    img_dir.mkdir(parents=True, exist_ok=True)
    gpkg = out_dir / "圖層.gpkg"
    qgz = out_dir / f"{case_name}.qgz"
    out05 = out_dir / "05_出圖清單.xlsx"
    out05_csv = out_dir / "05_出圖清單.csv"
    styles_dir = REPO_ROOT / "styles"

    with (REPO_ROOT / "config" / "county_registry.yaml").open(encoding="utf-8") as f:
        registry = yaml.safe_load(f)

    cases = read_cases(out_dir / "02_比對結果.xlsx", only, exclude)
    print(f"出圖案場 {len(cases)} 個（排除 {sorted(exclude)}）", flush=True)

    QgsApplication.setPrefixPath(QGIS_PREFIX, True)
    app = QgsApplication([], True)
    app.initQgis()
    QgsSettings().setValue("qgis/networkAndProxy/networkTimeout", 120000)

    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem(CRS_3826))
    project.setTitle(case_name)

    records: list[list] = []
    keep_alive: list = []
    try:
        warnings = extract_layers(cases, registry, gpkg)
        save_styles(styles_dir)

        # 共用圖層
        zones_lyr = QgsVectorLayer(f"{gpkg}|layername=功能分區", "國土功能分區", "ogr")
        zones_lyr.setRenderer(zone_renderer())
        zones_lyr.setOpacity(0.6)
        # NLSC 正射/電子地圖底圖改用圖磚拼接（nlsc_wmts.build_basemap），逐案場產 GeoTIFF；
        # 不再用 QGIS WMS provider（會卡住/逾時）。這裡只準備快取資料夾。
        basemap_dir = out_dir / "底圖"
        basemap_dir.mkdir(parents=True, exist_ok=True)
        tile_cache = basemap_dir / "_tilecache"
        wmts_ok = True  # 逐案場嘗試，失敗於該案場 05 清單標記

        # 都市計畫土地使用分區（補都市土地 使分/使類=None 缺口；Color1 官方色 data-defined）
        urban_shp_path = REPO_ROOT / URBAN_SHP
        urban_qml_path = str(styles_dir / URBAN_QML)
        urban_lyr_zoning = QgsVectorLayer(str(urban_shp_path), URBAN_LAYER_NAME, "ogr")
        urban_lyr_landtype = QgsVectorLayer(str(urban_shp_path), URBAN_LAYER_NAME, "ogr")
        urban_ok = urban_lyr_zoning.isValid() and urban_lyr_landtype.isValid()
        if urban_ok:
            ensure_qix(urban_lyr_zoning, urban_shp_path)
            for lyr in (urban_lyr_zoning, urban_lyr_landtype):
                _, ok = lyr.loadNamedStyle(urban_qml_path)
                if not ok:
                    warnings.append(f"都市計畫 QML 套用失敗：{urban_qml_path}")
            urban_lyr_landtype.setOpacity(URBAN_LANDTYPE_OPACITY)
            # 都市計畫每個 polygon 標其使用分區名（zone 欄）；PAL 自動避讓，僅標範圍內
            urban_lyr_zoning.setLabeling(
                label_settings(URBAN_ZONE_FIELD, size=7.5, color=(40, 40, 40)))
            urban_lyr_zoning.setLabelsEnabled(True)
        else:
            warnings.append(f"都市計畫圖層載入失敗：{urban_shp_path}（使用分區/使用地編定圖將無都市底色）")
        color_to_names = load_urban_color_names(REPO_ROOT / URBAN_LEGEND_YAML)

        for lyr in (zones_lyr, urban_lyr_zoning, urban_lyr_landtype):
            project.addMapLayer(lyr, False)
        shared_grp = project.layerTreeRoot().addGroup("共用圖層")
        for lyr in (zones_lyr, urban_lyr_zoning, urban_lyr_landtype):
            node = shared_grp.addLayer(lyr)
            node.setItemVisibilityChecked(False)
        shared_grp.setExpanded(False)

        qml_zoning = str(styles_dir / "使用分區.qml")
        qml_type = str(styles_dir / "使用地編定.qml")
        for p in (qml_zoning, qml_type):
            if not Path(p).exists():
                raise RuntimeError(f"缺樣式檔 {p}，請先跑 python -X utf8 scripts/build_legend_styles.py")

        qml_warned = False
        n_done = n_fail = 0
        for cid in sorted(cases):
            c = cases[cid]
            pt = parcels_text(c)
            if "extent" not in c:
                for theme in THEMES:
                    records.append([cid, c["name"], theme, "", "", pt,
                                    datetime.now().strftime("%Y-%m-%d %H:%M"), "失敗",
                                    c.get("error", "無圖面範圍")])
                    n_fail += 1
                continue

            base_uri = f"{gpkg}|layername=基地"
            neigh_uri = f"{gpkg}|layername=周邊地籍"
            subset = f"\"案場ID\" = '{cid}'"

            # 基地紅框（4 張圖共用最上層，標基地地號）
            l_red = QgsVectorLayer(base_uri, "基地（紅框標示）", "ogr")
            l_red.setSubsetString(subset)
            l_red.renderer().setSymbol(outline_symbol())
            l_red.setLabeling(label_settings())
            l_red.setLabelsEnabled(True)
            # 周邊+基地宗地上色（使用分區／使用地編定，各套使用者樣式庫 QML）
            l_nz = QgsVectorLayer(neigh_uri, "宗地-使用分區", "ogr")
            l_nt = QgsVectorLayer(neigh_uri, "宗地-使用地編定", "ogr")
            for l, qml in ((l_nz, qml_zoning), (l_nt, qml_type)):
                l.setSubsetString(subset)
                _, ok = l.loadNamedStyle(qml)
                if not ok and not qml_warned:
                    warnings.append(f"QML 套用失敗：{qml}")
                    qml_warned = True
                # 周邊宗地不標地號（僅基地宗地由 l_red 標；依使用者指示地號只呈現基地範圍）
                l.setLabelsEnabled(False)
            # 地籍界線（使用者符號多無外框，疊一層細灰界線；主題3/4 亦作周邊淡灰線）
            l_gray = QgsVectorLayer(neigh_uri, "周邊地籍界線", "ogr")
            l_gray.setSubsetString(subset)
            l_gray.renderer().setSymbol(outline_symbol(color="130,130,130,255", width="0.18"))

            for l in (l_red, l_nz, l_nt, l_gray):
                project.addMapLayer(l, False)
            grp = project.layerTreeRoot().addGroup(f"{cid} {c['name']}")
            for l in (l_red, l_gray, l_nz, l_nt):
                node = grp.addLayer(l)
                node.setItemVisibilityChecked(False)
            grp.setExpanded(False)

            loc = f"{c['county']}{c['town']}"
            common_note = f"位置：{loc}\n涵蓋地號：\n{wrap_cjk(pt)}"

            is_urban = bool(c.get("has_urban_rows")) and urban_ok
            urban_legend_lyr = (urban_zone_legend_layer(urban_lyr_zoning, c["extent"],
                                                        color_to_names, keep_alive)
                                if is_urban else None)
            if urban_legend_lyr is not None:
                # 必須註冊進專案，圖例面板才能解析並顯示都市計畫分區節點
                project.addMapLayer(urban_legend_lyr, False)

            zoning_layers = [l_red, l_gray, l_nz]
            zoning_legend = [l_nz, l_red]
            zoning_note = common_note + "\n（使分空值＝都市土地，詳都市計畫分區）"
            if is_urban:
                # urban_legend_lyr 置於地圖最底層：被 urban_lyr_zoning 全覆蓋而不可見，
                # 但納入地圖圖層清單，setLegendFilterByMapEnabled 才不會把它濾掉。
                zoning_layers = [l_red, l_gray, l_nz, urban_lyr_zoning]
                if urban_legend_lyr is not None:
                    zoning_layers.append(urban_legend_lyr)
                    zoning_legend = [l_nz, urban_legend_lyr, l_red]
                zoning_note += "\n都市計畫圖資：全台都市計畫土地使用分區（Color1 官方色）"

            landtype_layers = [l_red, l_gray, l_nt]
            landtype_legend = [l_nt, l_red]
            landtype_note = common_note + "\n（使類空值＝都市土地，詳都市計畫分區）"
            if is_urban:
                landtype_layers = [l_red, l_gray, l_nt, urban_lyr_zoning]
                if urban_legend_lyr is not None:
                    landtype_layers.append(urban_legend_lyr)
                    landtype_legend = [l_nt, urban_legend_lyr, l_red]
                landtype_note += "\n都市計畫圖資：全台都市計畫土地使用分區（Color1 官方色）"

            # NLSC 底圖（逐案場圖磚拼接）：正射(PHOTO2)為底＋電子地圖(EMAP)半透明50%
            ext = c["extent"]
            ext_3826 = (ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum())
            photo_rl = emap_rl = None
            try:
                p_tif = build_basemap(ext_3826, str(basemap_dir / f"{cid}_PHOTO2.tif"),
                                      layer="PHOTO2", cache_dir=str(tile_cache),
                                      buffer_ratio=0.05)
                e_tif = build_basemap(ext_3826, str(basemap_dir / f"{cid}_EMAP2.tif"),
                                      layer="EMAP2", cache_dir=str(tile_cache),
                                      buffer_ratio=0.05, keep_alpha=True)
                if p_tif:
                    photo_rl = QgsRasterLayer(p_tif, "正射影像(NLSC PHOTO2)")
                if e_tif:
                    emap_rl = QgsRasterLayer(e_tif, "通用電子地圖透明版(NLSC EMAP2)")
                    emap_rl.setOpacity(0.85)  # EMAP2 背景已透明，僅道路/註記；略降不遮擋影像
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{cid} 底圖下載失敗：{type(exc).__name__}: {exc}")
            case_wmts_ok = photo_rl is not None and photo_rl.isValid()
            overlay_layers = [l_red]
            if emap_rl is not None and emap_rl.isValid():
                overlay_layers.append(emap_rl)
            if photo_rl is not None:
                overlay_layers.append(photo_rl)
            for rl in (photo_rl, emap_rl):
                if rl is not None:
                    project.addMapLayer(rl, False)
                    keep_alive.append(rl)

            theme_defs = {
                "使用分區": (zoning_layers, zoning_legend, {"宗地-使用分區"}, zoning_note, None),
                "使用地編定": (landtype_layers, landtype_legend, {"宗地-使用地編定"}, landtype_note,
                               None),
                "國土功能分區": ([l_red, l_gray, zones_lyr], [zones_lyr, l_red], {"國土功能分區"},
                                 common_note + "\n功能分區：各縣市政府國土功能分區圖資\n（示意性質，實際以公告為準）",
                                 None),
                "正射套疊": (overlay_layers, [l_red], set(), common_note, None),
            }

            for theme in THEMES:
                layers, leg_layers, hide, note, overlay = theme_defs[theme]
                fname = f"{cid}_{sanitize(c['name'])}_{theme}.png"
                ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                if theme == "正射套疊" and not case_wmts_ok:
                    records.append([cid, c["name"], theme, fname, f"1/{c['scale']}", pt, ts,
                                    "失敗", "NLSC 底圖下載失敗"])
                    n_fail += 1
                    continue
                try:
                    title = f"{c['name']}　{THEME_TITLES[theme]}"
                    layout = build_layout(project, f"{cid}_{theme}", title, layers,
                                          leg_layers, hide, c["scale"], c["extent"],
                                          note, keep_alive, map_overlay=overlay)
                    exporter = QgsLayoutExporter(layout)
                    st = QgsLayoutExporter.ImageExportSettings()
                    st.dpi = EXPORT_DPI
                    # WMTS 偶發逾時 → 正射套疊圖最多重試 3 次
                    attempts = 3 if theme == "正射套疊" else 1
                    code = last_err = None
                    for att in range(attempts):
                        try:
                            code = exporter.exportToImage(str(img_dir / fname), st)
                        except Exception as exc:  # noqa: BLE001
                            code, last_err = None, f"{type(exc).__name__}: {exc}"
                        if code == QgsLayoutExporter.Success:
                            break
                        if att + 1 < attempts:
                            print(f"  [{cid}] {theme} 第{att + 1}次輸出失敗，重試…", flush=True)
                    if code == QgsLayoutExporter.Success:
                        records.append([cid, c["name"], theme, fname, f"1/{c['scale']}",
                                        pt, ts, "成功", ""])
                        n_done += 1
                    else:
                        records.append([cid, c["name"], theme, fname, f"1/{c['scale']}",
                                        pt, ts, "失敗",
                                        last_err or f"exportToImage 回傳 {code}"])
                        n_fail += 1
                except Exception as exc:  # noqa: BLE001 — 單張失敗續跑
                    records.append([cid, c["name"], theme, fname, f"1/{c['scale']}", pt, ts,
                                    "失敗", f"{type(exc).__name__}: {exc}"])
                    n_fail += 1
                    traceback.print_exc()
            print(f"[{cid}] {c['name']} 完成（累計 成功{n_done}/失敗{n_fail}）", flush=True)
            # 逐案場即時寫出 05 CSV（不依賴脆弱的迴圈後段；重出時可續用）
            try:
                with open(out05_csv, "w", newline="", encoding="utf-8-sig") as fcsv:
                    w = csv.writer(fcsv)
                    w.writerow(OUT05_COLUMNS)
                    w.writerows(records)
            except Exception:  # noqa: BLE001
                traceback.print_exc()

        # 05 清單（優先寫出並確保落地；即使後續 .qgz 崩潰也已保存）
        print("開始寫 05 出圖清單…", flush=True)
        try:
            wb = Workbook()
            ws = wb.active
            ws.append(OUT05_COLUMNS)
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for r in records:
                ws.append(r)
            for i, w in enumerate([7, 18, 12, 40, 9, 50, 17, 8, 40], start=1):
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
            wrap = Alignment(wrap_text=True, vertical="top")
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    cell.alignment = wrap
            ws.freeze_panes = "A2"
            wb.save(out05)
            print(f"已寫入 {out05}", flush=True)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            sys.stdout.flush()
        print(f"\n=== 完成：成功 {n_done} 張、失敗 {n_fail} 張 ===", flush=True)
        if warnings:
            print("警告：", flush=True)
            for w in warnings:
                print(f"  - {w}", flush=True)
        sys.stdout.flush()

        # .qgz（供手動微調；大批次含數百記憶體圖層時 project.write 可能崩潰，
        # 故置於 05 之後並個別 try，崩潰也不影響已寫出的圖與 05）
        if WRITE_QGZ:
            try:
                project.setFileName(str(qgz))
                print(f"已寫入 {qgz}" if project.write() else "[警告] .qgz 寫入失敗",
                      flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[警告] .qgz 寫入例外：{type(exc).__name__}: {exc}", flush=True)
    finally:
        app.exitQgis()


if __name__ == "__main__":
    main()
