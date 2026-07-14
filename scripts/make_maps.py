# -*- coding: utf-8 -*-
"""
階段3出圖：02_比對結果.xlsx → 每案場 4 張 A4 橫式 PNG ＋ 全案 .qgz ＋ 05_出圖清單.xlsx

執行（必須用 QGIS 內建 Python，PowerShell 呼叫）：
  & "C:\\Program Files\\QGIS 3.40.12\\bin\\python-qgis-ltr.bat" scripts\\make_maps.py \
      [案子名] [--cases 030,004] [--exclude 021,023,038,063,086]

trust-shp 模式（--trust-shp <基地.shp>，spec §5.1）：基地層幾何與屬性直接取自使用者
  shp（地號/使分/使類欄，dbf 壞欄名用 dbfread 容錯），**不讀 02、不比對官方 FID**，
  無 14 碼段號的宗地照收。single=全歸案場 001；加 --sitemap <csv> 則依 sitemap
  分案場（persite，每宗基地一組）。周邊地籍/功能分區仍按地圖範圍從官方 shp 撈。

流程：
1. 讀 02_比對結果.xlsx（僅取「比對狀態=成功」列；--exclude 案場整案跳過）
   —— trust-shp 模式改直讀 shp（見上），不經此步。
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
import shutil
import sqlite3
import subprocess
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
from shp_trust_utils import (
    UNMAPPED_ID,
    UNMAPPED_NAME,
    assign_site,
    gpkg_reuse_ok,
    load_sitemap,
    normalize_landno,
    read_dbf,
    read_shp_trust_meta,
    shp_get,
    theme6_missing_record,
)

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
MATERIALIZE = False  # --mem：把每案場向量層轉記憶體層再算圖，解大單案(周邊上千筆)
                     # 時同一 gpkg 多層平行算圖的 SQLite「unable to open」並發問題。
                     # 記憶體層不會存進 .qgz，故通常與 --no-qgz 併用。

# 都市計畫土地使用分區（補都市土地 使分/使類=None 的缺口，見 config/urban_plan_legend.yaml）
URBAN_SHP = "都市計畫/全台都市計畫土地使用分區.shp"
URBAN_ZONE_FIELD = "zone"
URBAN_COLOR_FIELD = "Color1"
URBAN_QML = "都市計畫.qml"
URBAN_LEGEND_YAML = "config/urban_plan_legend.yaml"
URBAN_LAYER_NAME = "都市計畫土地使用分區"
URBAN_LANDTYPE_OPACITY = 1.0  # 使用地編定圖也完整呈現都市計畫分區（依使用者圖說）

# 國土利用調查（LUIMAP）：fishpv 專案的官方色碼表 + features.geojson 分類結果
FISHPV_ROOT = Path(r"C:\Users\user\Documents\Claude\Projects\漁電共生評估")
LUIMAP_COLORTABLE = (FISHPV_ROOT / "qgis_plugin" / "fishpv_aquavoltaic"
                     / "resources" / "luimap_colortable.csv")
LUIMAP_SHARE_MIN = 0.005  # 圖例列入門檻（面積佔比）

# 主題（key=檔名用、title=圖名/圖說）
THEMES = ["使用分區", "使用地編定", "國土功能分區", "正射套疊", "國土利用調查", "臨路判定"]
THEME_TITLES = {
    "使用分區": "非都市土地使用分區及都市計畫土地使用分區圖",
    "使用地編定": "非都市土地使用地變更編定及都市計畫土地使用分區圖",
    "國土功能分區": "國土功能分區圖",
    "正射套疊": "正射影像及通用電子地圖套疊圖",
    "國土利用調查": "國土利用調查圖",
    "臨路判定": "臨路判定圖",
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


def to_memory(src: QgsVectorLayer) -> QgsVectorLayer:
    """把 gpkg 圖層(含 subset)materialize 成記憶體層，保留 renderer/labeling/opacity。
    用於解大單案同一 gpkg 多層平行算圖的 SQLite 並發問題。"""
    mem = src.materialize(QgsFeatureRequest())
    mem.setName(src.name())
    try:
        mem.setRenderer(src.renderer().clone())
    except Exception:
        pass
    try:
        if src.labelsEnabled() and src.labeling() is not None:
            mem.setLabeling(src.labeling().clone())
            mem.setLabelsEnabled(True)
    except Exception:
        pass
    mem.setOpacity(src.opacity())
    return mem


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


# ---------------------------------------------------------------------------
# trust-shp 模式：基地層幾何與屬性直接取自使用者 shp（不比對官方 FID）
# ---------------------------------------------------------------------------


def _write_gpkg(layers, gpkg: Path) -> None:
    """把 base/neigh/zones 三個記憶體層寫進單一 gpkg（三表，schema 與 extract_layers 一致）。"""
    gpkg.parent.mkdir(parents=True, exist_ok=True)
    if gpkg.exists():
        gpkg.unlink()
    ctx = QgsProject.instance().transformContext()
    for i, lyr in enumerate(layers):
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


def extract_layers_trust(shp_path: str, cases: dict[str, dict], registry: dict,
                         gpkg: Path, sitemap: dict, case_name: str) -> list[str]:
    """trust-shp：基地層直接取 shp 幾何+屬性（免比對官方 FID），周邊/功能分區仍按
    地圖範圍從縣市官方 shp 撈。回傳警告清單。gpkg schema 與 extract_layers 完全一致。"""
    warnings: list[str] = []
    base = mem_layer("基地", "field=案場ID:string&field=案場名稱:string&field=地號:string"
                            "&field=使分:string&field=使類:string")
    neigh = mem_layer("周邊地籍", "field=案場ID:string&field=地號:string"
                                 "&field=使分:string&field=使類:string&field=是否基地:string")
    zones = mem_layer("功能分區", "field=縣市:string&field=zone:string")

    # 1. 基地層：shp 幾何(QGIS) + dbf 屬性(read_dbf，容忍截斷欄名/依 .cpg 編碼)，按位置配對
    dbf_path = str(Path(shp_path).with_suffix(".dbf"))
    recs = read_dbf(dbf_path)
    lyr_shp = QgsVectorLayer(str(shp_path), "trust", "ogr")
    if not lyr_shp.isValid():
        raise RuntimeError(f"trust-shp 載入失敗：{shp_path}")
    feats = sorted(lyr_shp.getFeatures(), key=lambda f: f.id())
    if len(feats) != len(recs):
        warnings.append(f"trust-shp 幾何數({len(feats)})與屬性數({len(recs)})不一致，按位置配對")
    bboxes: dict[str, QgsRectangle] = {}
    landnos: dict[str, set] = defaultdict(set)
    for f, r in zip(feats, recs):
        landno = shp_get(r, ["地號", "20251201_4"])
        site = assign_site(landno, sitemap, case_name)
        if site is None:
            site = (UNMAPPED_ID, UNMAPPED_NAME)
        cid, cname = site
        if cid not in cases:
            continue
        zone = shp_get(r, ["20251201_6"])
        ltype = shp_get(r, ["20251201_7"])
        g = f.geometry()
        if g is None or g.isEmpty():
            warnings.append(f"{cid} 地號{landno} 無幾何")
            continue
        add_feat(base, g, [cid, cname, landno, zone or URBAN_LABEL, ltype or URBAN_LABEL])
        landnos[cid].add(landno)
        bb = g.boundingBox()
        bboxes[cid] = bb if cid not in bboxes else (
            bboxes[cid].combineExtentWith(bb) or bboxes[cid])

    for cid, c in cases.items():
        bb = bboxes.get(cid)
        if bb is None or bb.isEmpty():
            c["error"] = "trust-shp 無幾何"
            warnings.append(f"{cid} {c['error']}")
            continue
        c["scale"], c["extent"] = compute_scale_extent(bb)
        c["has_urban_rows"] = any(row["使分"] is None for row in c["rows"])
        c["_landnos"] = landnos[cid]

    # 2. 周邊地籍 + 功能分區：仍從官方縣市 shp 按地圖範圍撈（與比對無關）
    by_county: dict[str, list[dict]] = defaultdict(list)
    for c in cases.values():
        if "extent" in c:
            by_county[c["county"]].append(c)
    for county, clist in sorted(by_county.items()):
        cad = registry["counties"].get(county, {}).get("cadastral")
        if not cad:
            warnings.append(f"{county} 無地籍圖資（周邊地籍略過）")
            continue
        shp = REPO_ROOT / cad
        lyr = QgsVectorLayer(str(shp), county, "ogr")
        if not lyr.isValid():
            warnings.append(f"{county} 地籍 shp 載入失敗：{cad}")
            continue
        ensure_qix(lyr, shp)
        fld = lyr.fields()
        i_mother, i_child = fld.indexOf("宗地母"), fld.indexOf("宗地子")
        i_zone, i_type = fld.indexOf("使分"), fld.indexOf("使類")
        print(f"{county}：{len(clist)} 案場（trust-shp）", flush=True)
        for c in clist:
            base_set = c.get("_landnos", set())
            # 「是否基地」以正規化地號比對：基地地號=shp 原文，周邊=官方母子號 landno_text()
            # 還原，兩邊格式可能不同（前導零/全形/「之」）→ 都經 normalize_landno 再比。
            base_norm = {normalize_landno(x) for x in base_set if x}
            matched: set[str] = set()
            rect = c["extent"].buffered(c["extent"].width() * NEIGH_EXPAND)
            attrs_idx = [i for i in (i_mother, i_child, i_zone, i_type) if i >= 0]
            req = QgsFeatureRequest().setFilterRect(rect).setSubsetOfAttributes(attrs_idx)
            n = 0
            for f in lyr.getFeatures(req):
                zone = attr_str(f[i_zone]) if i_zone >= 0 else ""
                typ = attr_str(f[i_type]) if i_type >= 0 else ""
                ln = landno_text(f[i_mother], f[i_child]) if i_mother >= 0 else ""
                is_base = normalize_landno(ln) in base_norm
                if is_base:
                    matched.add(normalize_landno(ln))
                add_feat(neigh, f.geometry(), [
                    c["id"], ln, zone or URBAN_LABEL, typ or URBAN_LABEL,
                    "Y" if is_base else ""])
                n += 1
            # 基地宗地在周邊層完全找不到相符（疊字/格式問題）→ 點名警告，不靜默
            miss = base_norm - matched
            if miss:
                orig = sorted({x for x in base_set if normalize_landno(x) in miss})
                warnings.append(
                    f"{c['id']} {c['name']}：{len(orig)} 筆基地地號在周邊官方地籍層"
                    f"找不到相符（是否基地無 Y，疑地號格式/疊字不一致）：{'、'.join(orig)}")
            print(f"  {c['id']} {c['name']}：基地 {len(base_set)} 地號、1/{c['scale']}、周邊 {n} 筆", flush=True)
        rects = [c["extent"].buffered(c["extent"].width() * 0.05)
                 for c in clist if "extent" in c]
        if rects:
            n = load_zones_for_county(zones, registry, county, rects, warnings)
            print(f"  {county} 功能分區要素 {n} 筆", flush=True)

    _write_gpkg((base, neigh, zones), gpkg)
    return warnings


def parcels_text(case: dict) -> str:
    """涵蓋地號：依段/小段分組。例：更寮段更寮小段 331、331-1"""
    groups: dict[str, list[str]] = {}
    for row in case["rows"]:
        sec = row["段"] + ("段" if row["段"] and not row["段"].endswith("段") else "")
        if row["小段"] and row["小段"] not in ("", "None", "nan"):
            sec += row["小段"] + "小段"
        groups.setdefault(sec, []).append(row["地號"])
    return "；".join(f"{sec} {('、'.join(nums))}" for sec, nums in groups.items())


def collapse_parcels(case: dict) -> str:
    """maxmap 版面用的收合地號：『段 第一筆 等 N 筆』（跨多段補『(跨M段)』）。
    完整清單仍寫進 05 出圖清單的『涵蓋地號』欄，這裡只放地圖角落疊圖的一行摘要。"""
    rows = case["rows"]
    total = len(rows)
    if not total:
        return ""
    first = rows[0]
    sec = first["段"] + ("段" if first["段"] and not first["段"].endswith("段") else "")
    if first["小段"] and first["小段"] not in ("", "None", "nan"):
        sec += first["小段"] + "小段"
    secs = {r["段"] for r in rows if r["段"]}
    tail = f"（跨{len(secs)}段）" if len(secs) > 1 else ""
    return f"{sec} {first['地號']} 等 {total} 筆{tail}"


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


def ensure_gpkg_crs(layer: QgsVectorLayer, authid: str = CRS_3826) -> None:
    """最後防線：crs() 無效就強制補回本專案固定的 EPSG:3826（見 open_gpkg_layer 註解）。"""
    if not layer.crs().isValid():
        layer.setCrs(QgsCoordinateReferenceSystem(authid))


# 需要各自獨立實體檔的 gpkg 表格（見 make_table_copies 說明）
GPKG_TABLES = ["基地", "周邊地籍", "功能分區"]


def make_table_copies(gpkg: Path, out_dir: Path) -> dict[str, Path]:
    """把 圖層.gpkg 複製成「每個表格各一份獨立實體檔」，回傳 {表格名: 複本路徑}。

    根因（Task8 除錯查明，非外部程序鎖！）：GDAL/OGR 對同一個 .gpkg 檔案路徑，會在
    process 內共用底層 SQLite dataset 連線；本程序在同一支 圖層.gpkg 內對「基地」表
    setSubsetString() 之後，同 process 內任何對「周邊地籍」（或其他不同表）的
    QgsVectorLayer 開啟／setSubsetString()／extent() 查詢會持續性(非偶發、重試/等待
    完全沒用，實測 20 次、6+ 秒仍 100% 失敗)讀回 crs() 空值、extent() Null，錯誤log
    出現「unable to open database file」「layer ... is not registered in
    gpkg_geometry_columns」。屬性/幾何資料其實仍完整，但地圖渲染時因座標系統無效/
    範圍未知，該圖層填色符號整層被跳過（只剩 outline-only 的紅框/灰線正常），且仍
    回報「成功」——非常陰。已用兩份「不同實體檔」重現驗證：只要「基地」與「周邊地籍」
    分別來自不同檔案，兩者互不干擾，完全正常；同一表格（如 周邊地籍 開 3 次給
    l_nz/l_nt/l_gray）共用同一份複本沒有問題，問題只發生在「不同表格名稱」共用同一
    實體檔時。故解法：每個表格各給一份獨立實體檔，全程用複本路徑開層，不開原始
    圖層.gpkg（原檔仍保留、不受影響，仍可供桌面 QGIS 手動檢視）。"""
    copies: dict[str, Path] = {}
    for table in GPKG_TABLES:
        dst = out_dir / f"圖層_讀取_{table}.gpkg"
        shutil.copyfile(gpkg, dst)
        copies[table] = dst
    return copies


def open_gpkg_layer(uri: str, name: str, subset: str | None = None,
                    retries: int = 3) -> QgsVectorLayer:
    """開 gpkg 圖層（可選 setSubsetString）並確認 crs()/extent() 有效才回傳；當作對付
    其他偶發瞬間鎖（例如桌面 QGIS/防毒同時存取）的最後防線。注意：這個重試機制救不了
    make_table_copies() 描述的「跨表格共用連線」問題（已實測重試無效），呼叫端務必
    先用 make_table_copies() 讓每個表格各自來自獨立實體檔，這裡只處理「同一份複本、
    同一張表」仍可能發生的其他瞬時失敗。"""
    layer = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(0.2)
        layer = QgsVectorLayer(uri, name, "ogr")
        if subset:
            layer.setSubsetString(subset)
        if layer.crs().isValid() and not layer.extent().isNull():
            return layer
    ensure_gpkg_crs(layer)
    return layer


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


def luimap_legend_categories(features_geojson: Path, colortable_csv: Path,
                             extent: QgsRectangle) -> list:
    """圖面範圍內實際出現的 LUIMAP 官方類別（依 features 面積佔比）。
    回傳 [(lu_name, (r,g,b)), ...] 佔比由大到小，<LUIMAP_SHARE_MIN 不列。"""
    colors = {}
    with open(colortable_csv, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            colors[str(row["lu_code"]).strip()] = (
                row["lu_name"].strip(),
                (int(row["R"]), int(row["G"]), int(row["B"])))
    lyr = QgsVectorLayer(str(features_geojson), "f", "ogr")
    if not lyr.isValid():
        return []
    req = QgsFeatureRequest().setFilterRect(extent)
    shares: dict[str, float] = defaultdict(float)
    total = 0.0
    for f in lyr.getFeatures(req):
        code = attr_str(f["lu_code"])
        a = f.geometry().area()
        shares[code] += a
        total += a
    if total <= 0:
        return []
    out = []
    for code, a in sorted(shares.items(), key=lambda x: -x[1]):
        if a / total < LUIMAP_SHARE_MIN or code not in colors:
            continue
        out.append(colors[code])
    return out


def road_status_color(status: str) -> tuple:
    """臨路狀態文字 → 框色（純規則，見設計文件 Task6 brief）。"""
    s = str(status or "")
    if "不臨路" in s:
        return (200, 0, 0)
    if "直接臨路" in s:
        return (0, 150, 0)
    if "需搭橋" in s:
        return (255, 140, 0)
    return (120, 120, 120)


def load_sites_status(csv_path: Path) -> dict:
    """sites_status.csv → {site_id: (臨路狀態, 距離str, 註記)}。"""
    out = {}
    if not csv_path.exists():
        return out
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            out[str(row["site_id"]).strip()] = (
                str(row.get("臨路狀態", "")), str(row.get("臨路距離_m", "")),
                str(row.get("臨路註記", "")))
    return out


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


OVERVIEW_COLORS = ["166,206,227", "31,120,180", "178,223,138", "51,160,44",
                   "251,154,153", "227,26,28", "253,191,111", "255,127,0",
                   "202,178,214", "106,61,154"]


def gpkg_site_ids(gpkg: Path) -> set[str]:
    """既有 圖層.gpkg 基地表的案場ID集合（供 --skip-extract 分組一致性檢查）。
    讀不到（層缺/欄缺/檔壞）回空集合 → gpkg_reuse_ok 判不可重用、自動重抽。"""
    lyr = QgsVectorLayer(f"{gpkg}|layername=基地", "gpkg基地檢查", "ogr")
    if not lyr.isValid():
        return set()
    idx = lyr.fields().indexOf("案場ID")
    if idx < 0:
        return set()
    return {str(v) for v in lyr.uniqueValues(idx) if v is not None}


def render_overview(project: QgsProject, case_dir: Path, keep_alive: list) -> bool:
    """00_總區位圖：EMAP 底 + fishpv 宗基地上色編號。回傳成功與否。"""
    sites_shp = case_dir / "臨路評估" / "sites.shp"
    if not sites_shp.exists():
        print(f"[警告] 無 {sites_shp}，略過總區位圖", flush=True)
        return False
    lyr = QgsVectorLayer(str(sites_shp), "宗基地", "ogr")
    if not lyr.isValid():
        return False
    cats = []
    ids = sorted({str(f["site_id"]) for f in lyr.getFeatures()})
    for i, sid in enumerate(ids):
        sym = fill_symbol(tuple(int(x) for x in
                          OVERVIEW_COLORS[i % len(OVERVIEW_COLORS)].split(",")),
                          outline="60,60,60,255", width="0.4")
        sym.setOpacity(0.65)
        cats.append(QgsRendererCategory(sid, sym, sid))
    lyr.setRenderer(QgsCategorizedSymbolRenderer("site_id", cats))
    lyr.setLabeling(label_settings(
        "\"site_id\" || '\\n' || format_number(\"area_m2\"/10000, 2) || ' ha'",
        size=10.0, is_expression=True))
    lyr.setLabelsEnabled(True)
    ext = lyr.extent()
    scale, extent = compute_scale_extent(ext)
    # EMAP 底圖（不透明）
    e3826 = (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())
    bm_dir = case_dir / "底圖"
    bm_dir.mkdir(exist_ok=True)
    tif = build_basemap(e3826, str(bm_dir / "overview_EMAP.tif"), layer="EMAP",
                        cache_dir=str(bm_dir / "_tilecache"), buffer_ratio=0.05)
    layers = [lyr]
    if tif:
        rl = QgsRasterLayer(tif, "臺灣通用電子地圖")
        project.addMapLayer(rl, False)
        keep_alive.append(rl)
        layers = [lyr, rl]
    project.addMapLayer(lyr, False)
    keep_alive.append(lyr)
    layout = build_layout(project, "00_overview", "總區位圖（宗基地聚合）",
                          layers, [lyr], set(), scale, extent,
                          "宗基地＝毗鄰地號聚合（fishpv）；編號對照 臨路評估/成果.xlsx",
                          keep_alive)
    exp = QgsLayoutExporter(layout)
    st = QgsLayoutExporter.ImageExportSettings()
    st.dpi = EXPORT_DPI
    code = exp.exportToImage(str(case_dir / "00_總區位圖.png"), st)
    ok = code == QgsLayoutExporter.Success
    print(f"00_總區位圖:{'成功' if ok else '失敗'}", flush=True)
    return ok


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main() -> None:
    global WRITE_QGZ, MATERIALIZE
    args = [a for a in sys.argv[1:]]
    case_name = "20260707-清潔隊部資訊"
    only: set[str] | None = None
    exclude: set[str] = set()
    skip_extract = False
    extract_only = overview_only = False
    trust_shp: str | None = None
    sitemap_path: str | None = None
    themes = list(THEMES)
    layout_mode = "compact"
    themes_filter: set[str] | None = None
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--cases="):
            only = {x.strip() for x in a.split("=", 1)[1].split(",") if x.strip()}
        elif a.startswith("--exclude="):
            exclude = {x.strip() for x in a.split("=", 1)[1].split(",") if x.strip()}
        elif a.startswith("--trust-shp="):
            trust_shp = a.split("=", 1)[1]
        elif a == "--trust-shp":
            trust_shp = args[i + 1]; i += 1
        elif a.startswith("--sitemap="):
            sitemap_path = a.split("=", 1)[1]
        elif a == "--sitemap":
            sitemap_path = args[i + 1]; i += 1
        elif a == "--no-qgz":
            WRITE_QGZ = False
        elif a == "--mem":
            MATERIALIZE = True
        elif a == "--skip-extract":
            skip_extract = True
        elif a == "--extract-only":
            extract_only = True
        elif a == "--overview-only":
            overview_only = True
        elif a == "--basic":
            themes = THEMES[:4]
        elif a.startswith("--layout="):
            layout_mode = a.split("=", 1)[1].strip()
        elif a == "--layout":
            layout_mode = args[i + 1]; i += 1
        elif a.startswith("--themes="):
            themes_filter = {x.strip() for x in a.split("=", 1)[1].split(",") if x.strip()}
        elif not a.startswith("--"):
            case_name = a
        i += 1

    if layout_mode not in ("compact", "maxmap"):
        raise SystemExit(f"--layout 只接受 compact 或 maxmap，收到 {layout_mode!r}")
    if themes_filter:
        themes = [t for t in themes if t in themes_filter]

    out_dir = REPO_ROOT / "outputs" / case_name
    # maxmap 版面另存，全程不覆蓋既有 compact 成果（圖檔/05/qgz 皆加 _maxmap 後綴）
    sfx = "_maxmap" if layout_mode == "maxmap" else ""
    img_dir = out_dir / f"圖檔{sfx}"
    img_dir.mkdir(parents=True, exist_ok=True)
    gpkg = out_dir / "圖層.gpkg"          # 抽取結果共用，不隨版面而異
    qgz = out_dir / f"{case_name}{sfx}.qgz"
    out05 = out_dir / f"05_出圖清單{sfx}.xlsx"
    out05_csv = out_dir / f"05_出圖清單{sfx}.csv"
    styles_dir = REPO_ROOT / "styles"

    op = sheet = None
    if layout_mode == "maxmap":
        import overlay_placement as op        # 純幾何：比例尺/範圍/角落避讓（已單元測試）
        import build_layout as sheet          # 最大化版面組裝（與 parcel-map-sheet skill 同源、已 vendor）

    with (REPO_ROOT / "config" / "county_registry.yaml").open(encoding="utf-8") as f:
        registry = yaml.safe_load(f)

    sitemap = load_sitemap(sitemap_path) if (trust_shp and sitemap_path) else {}
    if trust_shp:
        cases = read_shp_trust_meta(trust_shp, sitemap, case_name, only, exclude)
        print(f"trust-shp 出圖案場 {len(cases)} 個：直取 shp 幾何、免比對"
              f"（排除 {sorted(exclude)}）", flush=True)
    else:
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
        # 00_總區位圖只吃 臨路評估/sites.shp，完全不需 圖層.gpkg → 提前短路、跳過
        # extract（兩模式皆是），避免 trust 首跑為此白掃一次整份 shp 的周邊/功能分區。
        if overview_only:
            render_overview(project, out_dir, keep_alive)
            return

        # --skip-extract 只有在既有 gpkg「分組與本次 cases 一致」才可重用：
        # 換 --mode（single 的 001 vs persite 的 S001..）沿用舊 gpkg 會讓每個案場
        # 「gpkg 基地無此案場」靜默全失敗 → 偵測不一致時自動落回完整重抽並明說原因。
        reused_gpkg = False
        if skip_extract:
            if not gpkg.exists():
                print(f"[警告] --skip-extract 但 {gpkg} 不存在，自動改為完整抽取", flush=True)
            else:
                gpkg_ids = gpkg_site_ids(gpkg)
                if gpkg_reuse_ok(gpkg_ids, set(cases)):
                    reused_gpkg = True
                    warnings = []
                    print("略過 extract_layers，重用既有 圖層.gpkg", flush=True)
                else:
                    print(f"[警告] --skip-extract：既有 圖層.gpkg 基地分組="
                          f"{sorted(gpkg_ids)} 與本次案場={sorted(cases)} 不一致"
                          "（多半是換了 --mode 沿用另一模式的舊 gpkg）→ 自動落回完整重抽",
                          flush=True)
        if not reused_gpkg:
            if trust_shp:
                warnings = extract_layers_trust(trust_shp, cases, registry, gpkg,
                                                sitemap, case_name)
            else:
                warnings = extract_layers(cases, registry, gpkg)

        if extract_only:
            print(f"--extract-only:已寫 {gpkg},結束", flush=True)
            return

        # 每個表格各給一份獨立實體檔再開層（見 make_table_copies 說明：GDAL 對同一支
        # gpkg 內不同表格的 shared dataset 連線會互相污染 crs()/extent()，跨檔案則
        # 完全正常，且已實測驗證）。
        tbl_gpkg = make_table_copies(gpkg, out_dir)

        if reused_gpkg:
            # extent 改由 gpkg 基地層算（原 extract_layers 已內建，這裡補算 skip 掉的部分）
            for cid, c in cases.items():
                bl = open_gpkg_layer(f"{tbl_gpkg['基地']}|layername=基地", "b",
                                     f"\"案場ID\" = '{cid}'")
                ext = bl.extent()
                if ext.isEmpty():
                    c["error"] = "gpkg 基地無此案場"
                    continue
                c["scale"], c["extent"] = compute_scale_extent(ext)
                c["has_urban_rows"] = any(
                    (r.get("使分") in (None, "", "None")) for r in c["rows"])

        save_styles(styles_dir)

        # 共用圖層
        zones_lyr = open_gpkg_layer(f"{tbl_gpkg['功能分區']}|layername=功能分區", "國土功能分區")
        zones_lyr.setRenderer(zone_renderer())
        zones_lyr.setOpacity(0.6)
        if MATERIALIZE:
            zones_lyr = to_memory(zones_lyr)
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
        n_done = n_fail = n_skip = 0
        for cid in sorted(cases):
            c = cases[cid]
            pt = parcels_text(c)
            if "extent" not in c:
                for theme in themes:
                    records.append([cid, c["name"], theme, "", "", pt,
                                    datetime.now().strftime("%Y-%m-%d %H:%M"), "失敗",
                                    c.get("error", "無圖面範圍")])
                    n_fail += 1
                continue

            base_uri = f"{tbl_gpkg['基地']}|layername=基地"
            neigh_uri = f"{tbl_gpkg['周邊地籍']}|layername=周邊地籍"
            subset = f"\"案場ID\" = '{cid}'"

            # 基地紅框（4 張圖共用最上層，標基地地號）
            l_red = open_gpkg_layer(base_uri, "基地（紅框標示）", subset)
            l_red.renderer().setSymbol(outline_symbol())
            l_red.setLabeling(label_settings())
            l_red.setLabelsEnabled(True)

            # maxmap：以最大化圖框(240×191)重算比例尺/範圍，並算基地 dissolve 幾何供角落
            # 避讓；覆蓋 c["scale"]/c["extent"]，讓後續 WMTS/LUIMAP 底圖與版面同用此範圍。
            mm_ctx = None
            if layout_mode == "maxmap":
                bb = l_red.extent()
                bbox = (bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum())
                geom = QgsGeometry.unaryUnion(
                    [f.geometry().makeValid() for f in l_red.getFeatures()])

                def _intersect(x1, y1, x2, y2, _g=geom):
                    it = _g.intersection(QgsGeometry.fromRect(QgsRectangle(x1, y1, x2, y2)))
                    return 0.0 if it.isEmpty() else it.area()

                mm_pl = op.place_overlays(_intersect, bbox)
                c["scale"] = mm_pl.scale
                c["extent"] = QgsRectangle(mm_pl.extent[0], mm_pl.extent[2],
                                           mm_pl.extent[1], mm_pl.extent[3])
                mm_ctx = (geom, bbox)
            # 周邊+基地宗地上色（使用分區／使用地編定，各套使用者樣式庫 QML）
            l_nz = open_gpkg_layer(neigh_uri, "宗地-使用分區", subset)
            l_nt = open_gpkg_layer(neigh_uri, "宗地-使用地編定", subset)
            for l, qml in ((l_nz, qml_zoning), (l_nt, qml_type)):
                _, ok = l.loadNamedStyle(qml)
                if not ok and not qml_warned:
                    warnings.append(f"QML 套用失敗：{qml}")
                    qml_warned = True
                # 周邊宗地不標地號（僅基地宗地由 l_red 標；依使用者指示地號只呈現基地範圍）
                l.setLabelsEnabled(False)
            # 地籍界線（使用者符號多無外框，疊一層細灰界線；主題3/4 亦作周邊淡灰線）
            l_gray = open_gpkg_layer(neigh_uri, "周邊地籍界線", subset)
            l_gray.renderer().setSymbol(outline_symbol(color="130,130,130,255", width="0.18"))

            if MATERIALIZE:
                l_red, l_nz, l_nt, l_gray = (to_memory(x) for x in (l_red, l_nz, l_nt, l_gray))

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

            # NLSC LUIMAP（國土利用調查）底圖＋圖面實際出現類別的圖例 proxy
            luimap_rl = None
            luimap_legend_lyr = None
            fishpv_dir = out_dir / "臨路評估"
            if "國土利用調查" in themes:
                try:
                    l_tif = build_basemap(ext_3826, str(basemap_dir / f"{cid}_LUIMAP.tif"),
                                          layer="LUIMAP", cache_dir=str(tile_cache),
                                          buffer_ratio=0.05)
                    if l_tif:
                        luimap_rl = QgsRasterLayer(l_tif, "國土利用調查(NLSC LUIMAP)")
                        project.addMapLayer(luimap_rl, False)
                        keep_alive.append(luimap_rl)
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"{cid} LUIMAP 底圖失敗：{exc}")
                # fishpv 產物（features.geojson）非本工具管轄，schema 可能漂移；
                # 圖例 proxy 產製失敗只降級（無圖例），不可拖垮整批出圖。
                try:
                    cats = (luimap_legend_categories(fishpv_dir / "features.geojson",
                                                     LUIMAP_COLORTABLE, c["extent"])
                            if (fishpv_dir / "features.geojson").exists() else [])
                    if cats:
                        plyr = QgsVectorLayer(f"Polygon?crs={CRS_3826}&field=cat:string",
                                              "國土利用類別", "memory")
                        cx, cy = c["extent"].center().x(), c["extent"].center().y()
                        half = c["extent"].width() * 0.001
                        rcats = []
                        for i, (name, rgb) in enumerate(cats):
                            sym = fill_symbol(rgb, outline="120,120,120,255", width="0.1")
                            rcats.append(QgsRendererCategory(str(i), sym, name))
                            add_feat(plyr, QgsGeometry.fromRect(QgsRectangle(
                                cx - half, cy - half, cx + half, cy + half)), [str(i)])
                        plyr.setRenderer(QgsCategorizedSymbolRenderer("cat", rcats))
                        keep_alive.append(plyr)
                        project.addMapLayer(plyr, False)
                        luimap_legend_lyr = plyr
                except Exception as exc:  # noqa: BLE001
                    cats = []
                    luimap_legend_lyr = None
                    warnings.append(f"{cid} LUIMAP 圖例產製失敗：{exc}")

            # 主題6「臨路判定」圖層（fishpv 臨路評估產物：sites.shp/sites_status.csv/
            # features.geojson）；同樣視為外部產物，schema 漂移只讓本主題失敗，不中斷批次。
            sites_lyr = roads_lyr = None
            road_note_lines = []
            if "臨路判定" in themes and (fishpv_dir / "sites.shp").exists():
                try:
                    status = load_sites_status(fishpv_dir / "sites_status.csv")
                    sites_lyr = QgsVectorLayer(str(fishpv_dir / "sites.shp"), "宗基地(臨路)", "ogr")
                    if not sites_lyr.isValid():
                        raise RuntimeError("sites.shp 讀取失敗")
                    scats = []
                    for f in sites_lyr.getFeatures():
                        sid = str(f["site_id"])
                        st_, dist, _n = status.get(sid, ("", "", ""))
                        rgb = road_status_color(st_)
                        sym = outline_symbol(color=f"{rgb[0]},{rgb[1]},{rgb[2]},255", width="1.1")
                        scats.append(QgsRendererCategory(sid, sym, f"{sid} {st_ or '無資料'}"))
                        road_note_lines.append(
                            f"{sid}:{st_ or '無資料'}"
                            + (f"(距{dist}m)" if dist not in ("", "0.0", "0.00") else ""))
                    sites_lyr.setRenderer(QgsCategorizedSymbolRenderer("site_id", scats))
                    sites_lyr.setLabeling(label_settings("site_id", size=10.0))
                    sites_lyr.setLabelsEnabled(True)
                    roads_lyr = QgsVectorLayer(str(fishpv_dir / "features.geojson"), "道路地物", "ogr")
                    if not roads_lyr.isValid():
                        raise RuntimeError("features.geojson 讀取失敗")
                    roads_lyr.setSubsetString("\"category\" = '道路'")
                    roads_lyr.renderer().setSymbol(fill_symbol((255, 85, 0),
                                                   outline="180,60,0,255", width="0.2"))
                    roads_lyr.setOpacity(0.75)
                    for l in (sites_lyr, roads_lyr):
                        project.addMapLayer(l, False)
                        keep_alive.append(l)
                except Exception as exc:  # noqa: BLE001
                    sites_lyr = roads_lyr = None
                    road_note_lines = []
                    warnings.append(f"{cid} 臨路判定圖層產製失敗：{exc}")

            theme_defs = {
                "使用分區": (zoning_layers, zoning_legend, {"宗地-使用分區"}, zoning_note, None),
                "使用地編定": (landtype_layers, landtype_legend, {"宗地-使用地編定"}, landtype_note,
                               None),
                "國土功能分區": ([l_red, l_gray, zones_lyr], [zones_lyr, l_red], {"國土功能分區"},
                                 common_note + "\n功能分區：各縣市政府國土功能分區圖資\n（示意性質，實際以公告為準）",
                                 None),
                "正射套疊": (overlay_layers, [l_red], set(), common_note, None),
                "國土利用調查": ([l_red] + ([luimap_legend_lyr] if luimap_legend_lyr else [])
                                 + ([luimap_rl] if luimap_rl else []),
                                 ([luimap_legend_lyr] if luimap_legend_lyr else []) + [l_red],
                                 set(), common_note + "\n國土利用調查：NLSC LUIMAP（官方色）",
                                 None),
                # photo_rl 若為 None（正射底圖失敗）：拿掉即可，仍可出道路＋宗基地白底。
                "臨路判定": (([sites_lyr, roads_lyr] + ([photo_rl] if photo_rl else []))
                             if sites_lyr else [],
                             [sites_lyr, roads_lyr] if sites_lyr else [],
                             set(),
                             common_note + "\n臨路判定(fishpv)：\n"
                             + wrap_cjk("、".join(road_note_lines), 24),
                             None),
            }

            for theme in themes:
                layers, leg_layers, hide, note, overlay = theme_defs[theme]
                fname = f"{cid}_{sanitize(c['name'])}_{theme}.png"
                ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                if theme == "正射套疊" and not case_wmts_ok:
                    records.append([cid, c["name"], theme, fname, f"1/{c['scale']}", pt, ts,
                                    "失敗", "NLSC 底圖下載失敗"])
                    n_fail += 1
                    continue
                if theme == "國土利用調查" and luimap_rl is None:
                    records.append([cid, c["name"], theme, fname, f"1/{c['scale']}", pt, ts,
                                    "失敗", "LUIMAP 底圖下載失敗"])
                    n_fail += 1
                    continue
                if theme == "臨路判定" and sites_lyr is None:
                    # 區分「本來就沒跑 fishpv（照舊手冊直跑 make_maps）＝略過」與
                    # 「fishpv 跑過但失敗＝失敗：臨路評估失敗」（見 theme6_missing_record）
                    st6, note6 = theme6_missing_record(fishpv_dir)
                    records.append([cid, c["name"], theme, fname, f"1/{c['scale']}", pt, ts,
                                    st6, note6])
                    if st6 == "失敗":
                        n_fail += 1
                    else:
                        n_skip += 1
                    continue
                try:
                    title = f"{c['name']}　{THEME_TITLES[theme]}"
                    if layout_mode == "maxmap":
                        geom, bbox = mm_ctx
                        # 主題免責說明（common_note 以外的行）移到 05 備註欄（決定 A）
                        caveat = note.replace(common_note, "", 1).strip().replace("\n", "　")
                        layout, _mmpl = sheet.build_sheet_layout(
                            project, f"{cid}_{theme}", title, layers, leg_layers, hide,
                            geom, bbox, loc, collapse_parcels(c), keep_alive,
                            map_overlay=overlay)
                    else:
                        caveat = ""
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
                                        pt, ts, "成功", caveat])
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
            print(f"[{cid}] {c['name']} 完成（累計 成功{n_done}/失敗{n_fail}"
                  f"{f'/略過{n_skip}' if n_skip else ''}）", flush=True)
            # 逐案場即時寫出 05 CSV（不依賴脆弱的迴圈後段；重出時可續用）
            try:
                with open(out05_csv, "w", newline="", encoding="utf-8-sig") as fcsv:
                    w = csv.writer(fcsv)
                    w.writerow(OUT05_COLUMNS)
                    w.writerows(records)
            except Exception:  # noqa: BLE001
                traceback.print_exc()

        # 05 出圖清單：已於迴圈中逐案場寫成 CSV。
        # 不在本進程用 openpyxl 產 xlsx——openpyxl 依賴 lxml，與 GDAL 共用 libxml2，
        # 大量圖磚(GDAL)處理後 libxml2 全域狀態損毀，openpyxl 一建立 Workbook 就
        # 存取違例硬崩潰。xlsx 交由獨立乾淨進程 scripts/csv_to_xlsx.py 產生（見 main 末）。
        print(f"05 清單(CSV)已即時寫出：{out05_csv}", flush=True)
        print(f"\n=== 完成：成功 {n_done} 張、失敗 {n_fail} 張"
              f"{f'、略過 {n_skip} 張（主題6未跑fishpv）' if n_skip else ''} ===", flush=True)
        if warnings:
            print("警告：", flush=True)
            for w in warnings:
                print(f"  - {w}", flush=True)
        sys.stdout.flush()

        # .qgz（供手動微調）：project.write() 走 Qt XML(QDomDocument)，非 libxml2，
        # 不受上述衝突影響；置於最後、單獨 try。
        if WRITE_QGZ:
            try:
                project.setFileName(str(qgz))
                print(f"已寫入 {qgz}" if project.write() else "[警告] .qgz 寫入失敗",
                      flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[警告] .qgz 寫入例外：{type(exc).__name__}: {exc}", flush=True)
        sys.stdout.flush()
    finally:
        app.exitQgis()

    # QGIS 已結束，另起乾淨子進程把 CSV 轉成正式 xlsx（該子進程不載入 GDAL，無衝突）
    _csv_to_xlsx(out05_csv, out05)


def _csv_to_xlsx(csv_path: Path, xlsx_path: Path) -> None:
    """以獨立子進程將 05 CSV 轉為格式化 xlsx（避免與 GDAL 的 libxml2 衝突）。"""
    if not csv_path.exists():
        print(f"[警告] 找不到 {csv_path}，略過 xlsx 轉換", flush=True)
        return
    conv = REPO_ROOT / "scripts" / "csv_to_xlsx.py"
    try:
        r = subprocess.run([sys.executable, str(conv), str(csv_path), str(xlsx_path)],
                           capture_output=True, text=True, timeout=120)
        print((r.stdout or "").strip() or f"已產生 {xlsx_path}", flush=True)
        if r.returncode != 0:
            print(f"[警告] xlsx 轉換非零回傳：{(r.stderr or '').strip()[:300]}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[警告] xlsx 轉換失敗：{type(exc).__name__}: {exc}（CSV 仍可用）", flush=True)


if __name__ == "__main__":
    main()
