# -*- coding: utf-8 -*-
"""環境敏感地套疊圖（簡報第6頁用）

依 `環境敏感套疊.csv` 找出「實際有套疊」的敏感圖層，解析到本機 `環境敏感相關/` 的 shp，
用與基本主題圖相同的 maxmap 版面（build_layout.build_sheet_layout）出一張
「基地×該敏感圖層」的套疊圖，並把逐類套疊面積寫成 csv 供簡報引用。

為什麼不放國土利用調查圖：環境敏感地那一頁要證明的是「基地與敏感範圍的空間關係」，
放國土利用調查圖答非所問（使用者定案 2026-07-29）。

須用 QGIS 內建 python 執行：
  .\\scripts\\qgis_python.ps1 scripts\\make_env_map.py `
      --joined <基地.shp> --case <案名> [--env-csv <環境敏感套疊.csv>]

無任何套疊時：不出圖、回傳 exit 0 並印「無套疊」，由簡報端決定不做該頁（使用者定案）。
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from qgis.core import (  # noqa: E402
    Qgis, QgsApplication, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
    QgsFeatureRequest, QgsFillSymbol, QgsGeometry, QgsLayoutExporter,
    QgsLinePatternFillSymbolLayer, QgsLineSymbol, QgsMarkerSymbol,
    QgsPointPatternFillSymbolLayer, QgsProject, QgsRasterLayer,
    QgsRectangle, QgsRendererCategory, QgsSimpleFillSymbolLayer, QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor  # noqa: E402

import build_layout as sheet  # noqa: E402
import district_line  # noqa: E402
from nlsc_wmts import build_basemap  # noqa: E402

CRS = "EPSG:3826"
ENV_ROOT = REPO_ROOT / "環境敏感相關"
EXPORT_DPI = 300

# 圖層登錄表與解析已抽至 env_registry.py（純模組、可單測；REGISTRY 的自然保留區
# 1152→1061 順序由 tests/test_env_registry.py 鎖住）。本檔只留 QGIS 出圖端。
from env_color_assign import assign_layer_colors, is_full_coverage  # noqa: E402
from env_registry import (  # noqa: E402
    DEFAULT_ALPHA, GENERIC_PALETTE, REGISTRY, resolve_layer,
)


def hatch_symbol(rgb, angle: float = 45.0, spacing: float = 5.0,
                 width: float = 0.35) -> QgsFillSymbol:
    """45° 斜線紋理填色（透明底＋同色外框）。

    ★2026-09-13 使用者定案：全覆蓋圖層改以 45° 斜線表示，取代原本的「只畫虛線外框」。
    斜線同時交代**範圍**與**存在**，又不像實心填色那樣把底下的圖層與正射整片染掉——
    這正是 .claude/rules/出圖上色與分區.md 記的「要用填色紋理不是邊框」那條的延伸
    （特定目的事業用地的紅底綠斜線是同一個手法）。
    ⚠ 間距 5.0mm 是**全幅尺度**的值，不是宗地符號的 2.2mm：這層通常涵蓋整個圖框，
      2.2mm 會密到把正射與底下圖層糊成一片粉紅（2026-09-13 實見），等於沒有退讓。
    """
    sym = QgsFillSymbol()
    base = QgsSimpleFillSymbolLayer()
    base.setColor(QColor(0, 0, 0, 0))                 # 透明底
    base.setStrokeColor(QColor(*rgb))
    base.setStrokeWidth(0.5)
    sym.changeSymbolLayer(0, base)
    lp = QgsLinePatternFillSymbolLayer()
    lp.setLineAngle(angle)
    lp.setDistance(spacing)
    lp.setDistanceUnit(QgsUnitTypes.RenderMillimeters)
    lp.setLineWidth(width)
    lp.setLineWidthUnit(QgsUnitTypes.RenderMillimeters)
    lp.setColor(QColor(*rgb))
    # 子線符號的顏色要另外設，只設 layer 的 color 會留下預設色
    lp.setSubSymbol(QgsLineSymbol.createSimple({
        "color": "%d,%d,%d,255" % tuple(rgb), "width": str(width)}))
    sym.appendSymbolLayer(lp)
    return sym


def real_coverage(shp: Path, rule: dict, geom: QgsGeometry, bb: QgsRectangle) -> float:
    """本層與基地的實際重疊面積：全部要素先 dissolve 再求交集，重疊要素不重複計算。"""
    lyr = QgsVectorLayer(str(shp), "_cover_probe", "ogr")
    if not lyr.isValid():
        return 0.0
    if not lyr.crs().isValid() and rule.get("crs"):
        lyr.setCrs(QgsCoordinateReferenceSystem(f"EPSG:{rule['crs']}"))
    parts = [geom.intersection(f.geometry())
             for f in lyr.getFeatures(QgsFeatureRequest().setFilterRect(bb))]
    parts = [p for p in parts if not p.isEmpty()]
    return QgsGeometry.unaryUnion(parts).area() if parts else 0.0


# ★全覆蓋層紋理依序換「樣式」（2026-10-20 使用者指正，阿里山案）：五個全覆蓋層都畫
#   45° 斜線時線條平行疊在一起分不出；只換角度仍是一片線網。改為斜線／圓點／方塊／
#   三角／交叉點／反斜線／十字／橫線輪替，點類各自錯開偏移，避免不同層的點疊在同一處。
#   每項：("line", 角度) 或 ("point", 標記形狀, 是否實心)
FILL_PATTERNS = (
    ("line", 45.0), ("point", "circle", True), ("point", "square", False),
    ("point", "triangle", True), ("line", 135.0), ("point", "cross2", True),
    ("point", "diamond", False), ("line", 0.0),
)
HATCH_ANGLES = tuple(p[1] for p in FILL_PATTERNS if p[0] == "line")   # 相容舊引用


# 點紋各層的格內位置（以網格間距的比例表示）：5 個點紋層落在方格四角＋中心，
# 兩兩中心距 ≥0.35×間距（18mm 時 6.4mm），遠大於標記 2.2mm，保證不同層的點不會疊在同一處。
# 間距 18mm：使用者兩度要求放寬（12mm 時畫面仍滿，2026-10-20）。
POINT_SPACING = 18.0
POINT_SLOTS = ((0.0, 0.0), (0.5, 0.0), (0.0, 0.5), (0.5, 0.5), (0.25, 0.25))


def pattern_symbol(rgb, idx: int, spacing: float = POINT_SPACING,
                   legend: bool = False) -> QgsFillSymbol:
    """第 idx 個全覆蓋層的紋理填色（透明底＋同色外框＋線紋或點紋）。

    legend=True：圖例小方格用的較密版本（間距 3.5mm、以方格自身為原點），
    圖面那份 12mm 間距在約 8mm 寬的方格內常常一個點都看不到。
    """
    kind = FILL_PATTERNS[idx % len(FILL_PATTERNS)]
    if kind[0] == "line":
        return hatch_symbol(rgb, angle=kind[1])   # 線紋維持 5mm：拉寬後圖例小方格內看不到線
    _, shape, solid = kind
    # 本層是第幾個點紋（只數點紋），決定落在網格的哪個位置
    n_pt = sum(1 for p in (FILL_PATTERNS * (idx // len(FILL_PATTERNS) + 1))[:idx] if p[0] == "point")
    fx, fy = POINT_SLOTS[n_pt % len(POINT_SLOTS)]
    sx, sy = fx * spacing, fy * spacing
    sym = QgsFillSymbol()
    base = QgsSimpleFillSymbolLayer()
    base.setColor(QColor(0, 0, 0, 0))
    base.setStrokeColor(QColor(*rgb))
    base.setStrokeWidth(0.5)
    sym.changeSymbolLayer(0, base)
    pp = QgsPointPatternFillSymbolLayer()
    pp.setDistanceX(spacing); pp.setDistanceY(spacing)
    pp.setDistanceXUnit(QgsUnitTypes.RenderMillimeters)
    pp.setDistanceYUnit(QgsUnitTypes.RenderMillimeters)
    if legend:
        # 圖例方格約 8×4mm：橫向 3.5mm 一顆、縱向只排一列並置中，點才不會被方格邊切半
        sx, sy = 1.75, 2.0
        pp.setDistanceX(3.5); pp.setDistanceY(20.0)
        pp.setDisplacementX(0); pp.setDisplacementY(0)
        pp.setDisplacementXUnit(QgsUnitTypes.RenderMillimeters)
    else:
        pp.setDisplacementX(0); pp.setDisplacementY(0)  # 規則方格，位置才算得準
        # ⚠ 網格原點要對齊「畫面」而非各圖層要素外框，否則各層網格起點不同，偏移量失去意義
        pp.setCoordinateReference(Qgis.SymbolCoordinateReference.Viewport)
    pp.setOffsetX(sx); pp.setOffsetY(sy)
    pp.setOffsetXUnit(QgsUnitTypes.RenderMillimeters)
    pp.setOffsetYUnit(QgsUnitTypes.RenderMillimeters)
    col = "%d,%d,%d,255" % tuple(rgb)
    pp.setSubSymbol(QgsMarkerSymbol.createSimple({
        "name": shape, "size": "1.6" if legend else "2.2", "size_unit": "MM",
        "color": col if solid else "0,0,0,0",
        "outline_color": col, "outline_width": "0.35", "outline_width_unit": "MM"}))
    sym.appendSymbolLayer(pp)
    return sym

# 圖例只留項目名稱：去掉 NGIS 圖層名前的環敏編號（「1-23-1-1國有林事業區」→「國有林事業區」）
_CODE_PREFIX = re.compile(r"^\s*\d+(?:-\d+)+\s*")


def strip_env_code(name: str) -> str:
    return _CODE_PREFIX.sub("", name) or name


def load_env_layer(shp: Path, rule: dict, name: str, alpha: float = DEFAULT_ALPHA,
                   outline_only: bool = False, fallback_rgb: tuple = (127, 127, 127),
                   pattern_idx: int = 0):
    lyr = QgsVectorLayer(str(shp), name, "ogr")
    if not lyr.isValid():
        return None
    if not lyr.crs().isValid() and rule.get("crs"):
        lyr.setCrs(QgsCoordinateReferenceSystem(f"EPSG:{rule['crs']}"))
    # 本層代表色：規則層指定的語意色優先，否則用呼叫端算好的「保證不撞色」的配色
    # （見 assign_layer_colors；不要在這裡用 color_idx 對 GENERIC_PALETTE 取模——
    # 語意色本身可能剛好等於色環某格，索引式取色會撞色，見該函式 docstring）
    base = tuple(rule.get("rgb") or fallback_rgb)
    fld = rule.get("field")
    if fld and lyr.fields().indexFromName(fld) >= 0:
        cats = []
        colors = rule.get("colors") or {}
        labels = rule.get("labels") or {}
        vals = sorted({f[fld] for f in lyr.getFeatures()},
                      key=lambda v: list(colors).index(v) if v in colors else 99)
        for v in vals:
            rgb = colors.get(v, base)
            sym = pattern_symbol(rgb, pattern_idx) if outline_only else QgsFillSymbol.createSimple({
                "color": "%d,%d,%d,%d" % (rgb + (int(alpha * 255),)),
                "outline_color": "%d,%d,%d,255" % rgb,
                "outline_width": "0.15", "outline_style": "solid"})
            cats.append(QgsRendererCategory(v, sym, labels.get(v, str(v))))
        lyr.setRenderer(QgsCategorizedSymbolRenderer(fld, cats))
    else:
        dark = tuple(int(c * 0.6) for c in base)   # 外框用同色調暗版，維持可區辨
        sym = pattern_symbol(base, pattern_idx) if outline_only else QgsFillSymbol.createSimple({
            "color": "%d,%d,%d,%d" % (base + (int(alpha * 255),)),
            "outline_color": "%d,%d,%d,255" % dark,
            "outline_width": "0.3", "outline_style": "solid"})
        lyr.setRenderer(lyr.renderer().__class__(sym) if False else lyr.renderer())
        lyr.renderer().setSymbol(sym)
        if outline_only and FILL_PATTERNS[pattern_idx % len(FILL_PATTERNS)][0] == "point":
            sheet.LEGEND_SYMBOLS[lyr.id()] = pattern_symbol(base, pattern_idx, legend=True)
    return lyr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True, help="基地 shp（②的 joined）")
    ap.add_argument("--case", required=True, help="案名（outputs/<案名>）")
    ap.add_argument("--env-csv", default=None, help="環境敏感套疊.csv（預設案件根目錄）")
    ap.add_argument("--extra-csv", default=None,
                    help="環境敏感套疊_本機補充.csv：平台53項清單外、但本機圖資查出的真限制"
                         "（例如地下水管制區第二級——平台清單只有第一級）")
    ap.add_argument("--county", default=None, help="縣市名（預設從基地 shp 讀）")
    ap.add_argument("--basemap-dir", default=None, help="底圖 tif 輸出資料夾（避開 QGIS 檔案鎖）")
    ap.add_argument("--alpha", type=float, default=DEFAULT_ALPHA,
                    help="敏感圖層不透明度 0~1（預設 0.40，讓正射底圖透出來）")
    ap.add_argument("--no-loc-parcel", action="store_true",
                    help="（相容旗標，已是預設值）不畫圖上「位置／涵蓋地號」角落框")
    ap.add_argument("--loc-parcel", action="store_true",
                    help="畫出「位置／涵蓋地號」角落框（舊版面；預設不畫）")
    ap.add_argument("--out", default=None, help="輸出資料夾（預設 outputs/<案名>）")
    ap.add_argument("--site", default=None,
                    help="案場ID（如 S001）：依 csv「案場」欄過濾命中、基地只取該案場宗地、"
                         "輸出檔名帶案場ID；需搭配 --sitemap。不給＝整份基地（舊行為）")
    ap.add_argument("--sitemap", default=None,
                    help="案場定義/sitemap.csv（--site 用；預設 outputs/<案>/案場定義/sitemap.csv）")
    a = ap.parse_args()
    import env_map_args as ema  # noqa: PLC0415 — 純邏輯（可單測）

    out_dir = Path(a.out) if a.out else (REPO_ROOT / "outputs" / a.case)
    # 案件根目錄：環敏/*.geojson 覆蓋幾何一律從這裡解析，不隨 --out 改變
    case_root = REPO_ROOT / "outputs" / a.case
    env_csv, _why = ema.pick_env_csv([out_dir, case_root], a.env_csv)
    if env_csv is None:
        raise SystemExit(_why)
    print(f"環敏 csv：{_why}")
    site_keys = None
    if a.site:
        sm = Path(a.sitemap) if a.sitemap else (case_root / "案場定義" / "sitemap.csv")
        if not sm.exists():
            raise SystemExit(f"--site 需要 sitemap：找不到 {sm}")
        site_keys = ema.load_site_keys(sm, a.site)
        if not site_keys:
            raise SystemExit(f"sitemap 中查無案場 {a.site} 的地號：{sm}")
        if not ema.has_site_column(env_csv):
            print(f"  ⚠ {env_csv.name} 無「案場」欄（非 --per-site 產物），命中項無法依案場過濾，"
                  f"以全部列計")

    def read_hits(path):
        return ema.read_hits(path, a.site)[0]

    hits, n_total, _warns = ema.read_hits_ex(env_csv, a.site)
    for _w in _warns:
        print(f"  ⚠ {_w}")
    print(f"平台環敏檢核 {n_total} 項，套疊 {len(hits)} 項"
          + (f"（案場 {a.site}）" if a.site else ""))
    extra = Path(a.extra_csv) if a.extra_csv else (out_dir / "環境敏感套疊_本機補充.csv")
    if extra.exists():
        ex = read_hits(extra)
        print(f"本機補充（平台清單外）{len(ex)} 項")
        seen = {n for n, _ in hits}
        hits += [(n, ar) for n, ar in ex if n not in seen]
    # ★同一圖層名在 NGIS 會逐面各回一列（2-31 捷運禁建限建回 3 列），照單全收會把同一份
    #   圖資載入三次、圖例與逐類面積各出現三次。依圖層名去重、保留最大那筆的面積供排序；
    #   真正的面積由下方 dissolve 後重算，不靠這裡的值。
    _seen: dict = {}
    for n, ar in hits:
        _seen[n] = max(_seen.get(n, 0.0), ar)
    if len(_seen) != len(hits):
        print(f"  （同名圖層去重：{len(hits)} 列 → {len(_seen)} 層）")
    hits = sorted(_seen.items(), key=lambda t: -t[1])
    for n, ar in hits:
        print(f"  - {n}：{ar:,.2f} m²")
    if not hits:
        print("無套疊 → 不出圖（簡報請免作環境敏感頁，於總表載明無涉環境敏感地）")
        return 0

    qgs = QgsApplication([], False)
    qgs.initQgis()
    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem(CRS))
    keep_alive = []

    site = QgsVectorLayer(a.joined, "基地地籍", "ogr")
    if not site.isValid():
        raise SystemExit(f"基地 shp 讀取失敗：{a.joined}")
    if site_keys:
        # 只留該案場的宗地（段＋地號比對）；以記憶體層承接，後續流程完全不變
        def _val(feat, *names):
            for n in names:
                i = site.fields().indexFromName(n)
                if i >= 0 and feat[i] not in (None, ""):
                    return str(feat[i]).strip()
            return ""
        picked = [f for f in site.getFeatures()
                  if ema.in_site(site_keys, _val(f, "地段名稱", "段"), _val(f, "地號"))]
        if not picked:
            raise SystemExit(f"基地 shp 內找不到案場 {a.site} 的宗地（sitemap {len(site_keys)} 筆）")
        mem = QgsVectorLayer(f"MultiPolygon?crs={site.crs().authid() or 'EPSG:3826'}",
                             "基地地籍", "memory")
        mem.dataProvider().addAttributes(site.fields().toList())
        mem.updateFields()
        mem.dataProvider().addFeatures(picked)
        print(f"案場 {a.site}：基地宗地 {len(picked)} / {site.featureCount()} 筆")
        site = mem
    def _fld(feat, *names):
        """欄名容錯（tcdgis 匯出用「縣市名稱/鄉鎮市區/地段名稱」，其他來源可能用短名）。"""
        for n in names:
            i = site.fields().indexFromName(n)
            if i >= 0 and feat[i] not in (None, ""):
                return str(feat[i]).strip()
        return ""

    f0 = next(iter(site.getFeatures()))
    county = (a.county or _fld(f0, "縣市名稱", "縣市") or "").strip()
    town = _fld(f0, "鄉鎮市區", "鄉鎮區", "鄉鎮")
    # 地段名稱形如「[5161]州海段」→ 去掉代碼前綴
    section = re.sub(r"^\[\d+\]", "", _fld(f0, "地段名稱", "段"))
    loc = f"{county}{town}"
    print(f"位置：{loc}　地段：{section}")

    red = QgsFillSymbol.createSimple({
        "color": "255,255,255,0", "outline_color": "200,0,0,255", "outline_width": "0.9"})
    site.renderer().setSymbol(red)
    # 基地標地號：地圖出圖版面規範 v2「基地疊粗紅框並標地號」，基本主題圖都有、本圖原本漏了
    #（多筆基地時看不出哪塊是哪一筆，表格又逐筆列面積，讀者對不起來）。
    if site.fields().indexFromName("地號") >= 0:
        from make_extra_maps import label_field  # noqa: PLC0415
        label_field(site, "地號", size=10)
    project.addMapLayer(site, False)
    keep_alive.append(site)

    # 基地幾何與圖框
    geom = QgsGeometry.unaryUnion([f.geometry() for f in site.getFeatures()])
    bb = geom.boundingBox()
    bbox = (bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum())

    # 敏感圖層
    geom_area = geom.area()
    # ★覆蓋率 ≥99% 的圖層「多層疊加時」才改畫外框不填色（2026-08-20 修正使用者
    # 2026-07-29 定案的延伸）：原意是避免好幾個全覆蓋圖層疊加壓成一片糊，「100%涵蓋」
    # 由表格文字交代即可。但**只有單一命中項**時，外框虛線多半落在圖框外（100%涵蓋、
    # 邊界離基地很遠），畫面近乎全空、完全看不出基地與範圍的關係——這正是外框規則
    # 原本要防的「疊很多層看不清楚」的反面情況。判斷改為：僅當 ≥2 個命中項各自都
    # ≥99% 覆蓋（真的會疊加壓糊）才退為外框；單一全覆蓋命中一律給透明填色。
    # ★2026-09-13 再修（內湖保護區案）：條件由「≥2 個全覆蓋圖層」改為
    #   「本層全覆蓋 **且** 還有別的圖層要看」。全覆蓋層一鋪滿色，底下所有圖層都被
    #   染成同一片糊色——不必等到兩個全覆蓋層才會發生，一個就夠（本案民航禁限建
    #   100% 蓋住山坡地、捷運禁限建、地質敏感區三層）。維持湖口案的修正：**單一命中項**
    #   （len(hits)==1）一律給填色，否則虛線邊界落在圖框外、畫面近乎全空。
    n_full = sum(1 for _, ar in hits if ar >= 0.99 * geom_area)
    # 先把全部命中項的規則解析出來，才能配「保證不撞色」的代表色（見 assign_layer_colors；
    # 不能像舊版那樣邊解析邊用命中順序的索引取色，索引跟語意色互不相關才會撞色）。
    resolved = [(name, *resolve_layer(name, county, case_dir=case_root)) for name, _ in hits]
    layer_colors = assign_layer_colors(resolved, GENERIC_PALETTE)
    env_layers, legend_layers, notes, stats = [], [], [], []
    n_hatch = 0
    for name, area in hits:
        shp, rule = next((s, r) for n, s, r in resolved if n == name)
        if shp is None:
            print(f"  ⚠ 無法解析為本機圖資：{name}（該項僅以表格呈現）")
            notes.append(f"{name}（本機無對應圖資）")
            continue
        subs = rule.get("_subs", {})
        title = strip_env_code(
            rule["title"].format(**subs) if "{" in rule["title"] else rule["title"])
        # ⚠ 全覆蓋判定要用「本層 dissolve 後的實際覆蓋」，**不可用 hits 的 area**：
        #   hits 經上方同名去重後只留「最大那一列」，而 NGIS 是逐面各回一列——圖層被切成
        #   好幾塊時，每塊都 <99%，就永遠判不成全覆蓋（2026-09-24 阿里山案：自來水 2 塊、
        #   水庫集水區 2 塊、國有林 4 塊，實際都 100%，卻被畫成實心填色、疊成一片糊）。
        #   也不能改成把各列相加——同圖層要素互相重疊時會重複計算（內湖案捷運禁限建）。
        cover = real_coverage(shp, rule, geom, bb)
        outline_only = is_full_coverage(cover, geom_area, len(hits))
        n_hatch += int(outline_only)
        lyr = load_env_layer(shp, rule, title, alpha=a.alpha, outline_only=outline_only,
                             fallback_rgb=layer_colors.get(name, (127, 127, 127)),
                             pattern_idx=n_hatch - 1)
        if outline_only:
            print(f"    （{title} 覆蓋率 {100 * cover / geom_area:.1f}% ≥99%，"
                  f"改畫紋理「{FILL_PATTERNS[(n_hatch - 1) % len(FILL_PATTERNS)][1]}」，不用實心填色）")
        if lyr is None:
            print(f"  ⚠ 圖層讀取失敗：{shp}")
            continue
        # 逐類套疊面積（供簡報引用；數字只能來自實算）
        # ★必須依（圖層×分級）彙總，不可逐 feature 各記一列——同一分級的敏感範圍在圖資裡
        # 常是幾十個破碎多邊形（本案山崩與地滑 17 個），逐筆寫出等於給簡報一張沒法用的表
        # （2026-08-04 龍崎案實錯）。分級圖層（如淹水潛勢）才會有多列，且列數＝分級數。
        fld = rule.get("field")
        req = QgsFeatureRequest().setFilterRect(bb)
        # ★同一分級內先 dissolve 再算面積（2026-09-13 內湖保護區案實錯）：原本逐 feature
        #   把交集面積相加，同一圖層的要素若彼此重疊就會**重複計算**。2-31 大眾捷運系統
        #   兩側禁建限建地區有「禁建線」「二側限建範圍」「環狀線」三個互相重疊的面，
        #   相加得 8,449 ㎡（50.3%）、實際聯集只有 6,048 ㎡（36.04%），高估近四成。
        #   多數圖層的要素不重疊、結果不變；會變的就是本來就被算錯的那些。
        agg: dict = {}
        for f in lyr.getFeatures(req):
            inter = geom.intersection(f.geometry())
            if inter.isEmpty():
                continue
            key = f[fld] if (fld and lyr.fields().indexFromName(fld) >= 0) else "（全區）"
            agg.setdefault(key, []).append(inter)
        for key, parts in agg.items():
            u = QgsGeometry.unaryUnion(parts)
            stats.append([title, key, round(u.area(), 2)])
        project.addMapLayer(lyr, False)
        keep_alive.append(lyr)
        env_layers.append(lyr)
        legend_layers.append(lyr)
        # 覆蓋檔在案件層、不在 ENV_ROOT 底下——relative_to 會丟 ValueError（潛在缺陷，
        # 2026-08-04 抽 env_registry 時發現；覆蓋機制當初用平台 identify 幾何測，沒走到這行）
        src = shp.relative_to(ENV_ROOT) if shp.is_relative_to(ENV_ROOT) else shp.name
        print(f"  ✓ {title} ← {src}{'（案件層覆蓋）' if rule.get('_override') else ''}")

    # 正射底圖
    # ★底圖要照「版面引擎算出的最終圖框範圍」抓，不是基地 bbox（2026-08-04 龍崎案實錯：
    # 用 bbox+15% 抓，圖框因 A4 比例與整數比例尺退階而更寬，成品左右各露一條白邊）。
    # ★★2026-08-20 再修：build_sheet_layout 內用 place_fixed（maxmap v2，地圖寬
    # MAP2_W=287mm），這裡原本用 place_overlays（舊 compact，地圖寬 MAP_W=240mm），
    # 兩者 extent 不同→正射底圖鋪不滿較寬的 maxmap 框、四周露白。改用同一支
    # place_fixed＋MAP2 尺寸，底圖範圍才與圖框一致。純幾何、無副作用。
    import overlay_placement as op          # noqa: PLC0415 — 與 build_layout 同一顆幾何引擎

    def _inter(x1, y1, x2, y2):
        r = geom.intersection(QgsGeometry.fromRect(QgsRectangle(x1, y1, x2, y2)))
        return 0.0 if r.isEmpty() else r.area()

    frame_ext = op.place_fixed(_inter, bbox,
                               map_w=op.MAP2_W, map_h=op.MAP2_H).extent   # (L, R, B, T)
    fl, fr, fb, ft = frame_ext
    bm_bbox = (fl, fb, fr, ft)                              # build_basemap 吃 (xmin,ymin,xmax,ymax)
    bm_dir = Path(a.basemap_dir) if a.basemap_dir else (out_dir / "底圖")
    bm_dir.mkdir(parents=True, exist_ok=True)
    photo_rl = None
    try:
        tif = build_basemap(bm_bbox, str(bm_dir / "env_PHOTO2.tif"), layer="PHOTO2",
                           cache_dir=str((out_dir / "底圖") / "_tilecache"),
                           buffer_ratio=0.02)
        if tif:
            photo_rl = QgsRasterLayer(tif, "正射影像(NLSC PHOTO2)")
            if photo_rl.isValid():
                project.addMapLayer(photo_rl, False)
                keep_alive.append(photo_rl)
            else:
                photo_rl = None
                print("  ⚠ 底圖檔無法讀取，成果圖無正射底圖")
        else:
            print("  ⚠ 底圖取得失敗（圖磚全抓不到），成果圖無正射底圖——勿當正射套疊圖交付")
    except Exception as exc:  # noqa: BLE001 — 底圖失敗不阻斷出圖
        print(f"  ⚠ 底圖失敗：{type(exc).__name__}: {exc}")

    layers = [site] + env_layers + ([photo_rl] if photo_rl else [])
    legend = legend_layers + [site]

    # 涵蓋地號（沿用 build_layout 的收合寫法）
    idx_ln = site.fields().indexFromName("地號")
    lns = [str(f[idx_ln]).strip() for f in site.getFeatures()] if idx_ln >= 0 else []
    pnote = sheet.parcel_note(section, lns[0] if lns else "", len(lns))

    # 行政區界（`<案>/行政區界/行政區界.shp` 存在才畫；沒有就完全不影響既有行為）
    _dist = district_line.district_layers(out_dir, project, keep_alive, QgsRectangle(*bbox))
    if _dist:
        layers = layers[:1] + _dist.layers + layers[1:]
        legend = list(legend) + _dist.legend

    img_dir = out_dir / "圖檔_maxmap"
    img_dir.mkdir(parents=True, exist_ok=True)
    fname = ema.env_png_name(a.case, a.site)
    layout, _pl = sheet.build_sheet_layout(
        project, "001_環境敏感", layers, legend, set(), geom, bbox,
        loc, pnote, keep_alive,
        show_loc_parcel=a.loc_parcel)
    exporter = QgsLayoutExporter(layout)
    st = QgsLayoutExporter.ImageExportSettings()
    st.dpi = EXPORT_DPI
    code = exporter.exportToImage(str(img_dir / fname), st)
    ok = code == QgsLayoutExporter.Success
    print(("已輸出 " if ok else "輸出失敗 ") + str(img_dir / fname))

    if stats:
        p = out_dir / ema.env_stats_name(a.site)
        with io.open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["敏感圖層", "分級", "套疊面積_m2"])
            w.writerows(stats)
        tot = geom.area()
        print(f"逐類面積已寫入 {p}（基地 {tot:,.2f} m²）")
        for t, k, ar in stats:
            print(f"    {t} / {k}：{ar:,.2f} m²（{100 * ar / tot:.1f}%）")

    project.clear()
    qgs.exitQgis()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
