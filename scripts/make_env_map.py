# -*- coding: utf-8 -*-
"""環境敏感地套疊圖（簡報第6頁用）

依 `環境敏感套疊.csv` 找出「實際有套疊」的敏感圖層，解析到本機 `環境敏感相關/` 的 shp，
用與六主題相同的 maxmap 版面（build_layout.build_sheet_layout）出一張
「基地×該敏感圖層」的套疊圖，並把逐類套疊面積寫成 csv 供簡報引用。

為什麼不放國土利用調查圖：環境敏感地那一頁要證明的是「基地與敏感範圍的空間關係」，
放國土利用調查圖答非所問（使用者定案 2026-07-29）。

須用 QGIS 內建 python 執行：
  & "<QGIS>\\bin\\python-qgis-ltr.bat" scripts\\make_env_map.py `
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
    QgsApplication, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
    QgsFeatureRequest, QgsFillSymbol, QgsGeometry, QgsLayoutExporter, QgsProject,
    QgsRasterLayer, QgsRectangle, QgsRendererCategory, QgsVectorLayer,
)

import build_layout as sheet  # noqa: E402
from nlsc_wmts import build_basemap  # noqa: E402

CRS = "EPSG:3826"
ENV_ROOT = REPO_ROOT / "環境敏感相關"
EXPORT_DPI = 300

# ── 圖層解析登錄表 ────────────────────────────────────────────────────────
# key = 平台「環境敏感地區套疊分析」結果表的圖層名稱（見 環境敏感套疊.csv）
# shp  = 相對 環境敏感相關/ 的路徑；{county} 會代入縣市名
# field= 分類欄（None＝不分類、單色）；crs＝該 shp 無 .prj 時的假定 CRS
# colors= 分類值 → RGBA
# 只存 RGB；alpha 由 --alpha 統一給（預設 40%），才能讓正射底圖透出來
FLOOD_RGB = {
    "0-0.3": (255, 255, 190), "0.3-0.5": (190, 232, 255), "0.5-1": (115, 178, 255),
    "1-2": (0, 112, 255), "2-3": (0, 38, 194), ">3": (10, 0, 110),
}
FLOOD_LABELS = {
    "0-0.3": "0～0.3 公尺", "0.3-0.5": "0.3～0.5 公尺", "0.5-1": "0.5～1 公尺",
    "1-2": "1～2 公尺", "2-3": "2～3 公尺", ">3": "3 公尺以上",
}

REGISTRY = [
    # 淹水潛勢（各降雨組體，臺南市檔名為 24h500 等；嘉義縣為 q_24hr500）
    {"match": r"淹水潛勢圖?_?定量降雨(\d+)小時(\d+)mm",
     "shp": ["第二級/淹水潛勢/{county}/SHP/{h}h{mm}.shp",
             "第二級/淹水潛勢/{county}/q_{h:0>2}hr{mm}.shp"],
     "field": "type", "colors": FLOOD_RGB, "labels": FLOOD_LABELS,
     "crs": 3826, "title": "淹水潛勢 {h}hr{mm}mm"},
    {"match": r"公路兩側禁建限建", "shp": ["第二級/2-30 公路兩側禁建限建地區/高速公路兩側禁建限建地區.shp"],
     "field": None, "crs": 3826, "title": "公路兩側禁建限建地區"},
    {"match": r"地質敏感區.*活動斷層|活動斷層",
     "shp": ["第二級/2-01 地質敏感區(活動斷層、山崩與地滑、土石流)(1051221update)/全國活動斷層帶.shp"],
     "field": None, "crs": 3826, "title": "地質敏感區（活動斷層）"},
    {"match": r"山崩與地滑|山崩地滑",
     "shp": ["第二級/2-01 地質敏感區(活動斷層、山崩與地滑、土石流)(1051221update)"
             "/地質敏感區山崩與地滑.shp"],
     "field": None, "crs": 3826, "rgb": (255, 127, 14),
     "title": "地質敏感區（山崩與地滑）"},
    # 自然保留區：**一律用 1152（115年第2版，TGOS 下載）**，106 年版只留作 fallback。
    # 2026-08-04 龍崎案實踩：106 年版只有 23 筆、**不含 110.07.30 公告的 NR23 龍崎牛埔
    # 惡地自然保留區**，逐筆距離算出「最近 13,089 公尺、未涉及」，而 TCDGIS 平台正確
    # 回報套疊 8.88 ㎡——**是「有／沒有」的差異，不是面積門檻或邊界誤差**。
    # 換上 1152 後本機重算 8.88 ㎡，與平台一字不差。舊版另漏 NR22 北投石、
    # 且 NR10 台灣一葉蘭為 114 年前的舊範圍。
    {"match": r"自然保留區",
     "shp": ["第一級/1-07 自然保留區 (1152_115年第2版)/自然保留區1152.shp",
             "第一級/1-07 自然保留區 (1060523update)/自然保留區1061.shp"],
     "field": None, "crs": 3826, "rgb": (44, 160, 44),
     "title": "自然保留區"},
    {"match": r"嚴重地層下陷", "shp": ["第二級/2-03 嚴重地層下陷地區(941215update)/941215{county}all_1.shp"],
     "field": None, "crs": 3826, "title": "嚴重地層下陷地區"},
    {"match": r"山坡地", "shp": ["第二級/2-06 山坡地 (1060531update)/BOUNDARY.shp"],
     "field": None, "crs": 3826, "rgb": (148, 103, 189), "title": "山坡地"},
    {"match": r"自來水水質水量保護區",
     "shp": ["第二級/2-20 自來水水質水量保護區(1060629update)/WaterQualityAndQuantityProtectionArea_Twd97_V3.0.shp"],
     "field": None, "crs": 3826, "title": "自來水水質水量保護區"},
    {"match": r"中央管河川區域", "shp": ["第一級/1-03 中央管河川區域 (1060629update)/河川區域線.shp"],
     "field": None, "crs": 3826, "title": "中央管河川區域"},
    {"match": r"中央管區域排水", "shp": ["第一級/1-05 中央管區域排水設施範圍 (1060629update)/排水設施範圍線.shp"],
     "field": None, "crs": 3826, "title": "中央管區域排水設施範圍"},
    {"match": r"海岸管制區", "shp": ["其他/海岸管制區/海岸管制區.shp"],
     "field": None, "crs": 3826, "title": "海岸管制區"},
    # 地下水管制區：平台清單只有「第一級」，第二級須靠本機圖資（2026-07-29 安南案實需）
    # gwconare.shp=第一級(1筆)、gwconare2.shp=gwconare2_all=第二級(11筆，全台)
    {"match": r"地下水管制區第一級|地下水第一級管制區",
     "shp": ["其他/地下水管制區/gwconare.shp"],
     "field": None, "crs": 3826, "title": "地下水管制區第一級", "encoding": "big5"},
    {"match": r"地下水管制區第二級|地下水第二級管制區",
     "shp": ["其他/地下水管制區/gwconare2.shp"],
     "field": None, "crs": 3826, "title": "地下水管制區第二級", "encoding": "big5"},
]
GENERIC_RGB = (255, 80, 80)
# 多個敏感圖層同時套疊時，**每層必須是可區辨的顏色**——否則圖例好幾格同一個紅、
# 看不出哪一塊屬哪一項，等於白畫（2026-08-04 龍崎案實錯：自然保留區與山崩地滑同色）。
# 規則層各自在 REGISTRY 用 "rgb" 指定語意色（保護類偏綠／災害類偏橙紅），
# 未指定者依出現順序取下列色環。色相間距 ≥60°，40% 不透明度疊在正射上仍分得出。
GENERIC_PALETTE = [
    (214, 39, 40),    # 紅
    (255, 127, 14),   # 橙
    (148, 103, 189),  # 紫
    (23, 190, 207),   # 青
    (188, 189, 34),   # 黃綠
    (227, 119, 194),  # 粉
    (44, 160, 44),    # 綠
    (31, 119, 180),   # 藍
]
DEFAULT_ALPHA = 0.40   # 使用者定案 2026-07-29：套疊圖層 40% 不透明度


def _norm_county(name: str) -> list[str]:
    """縣市名的檔名變體（臺/台）。"""
    out = [name]
    if "臺" in name:
        out.append(name.replace("臺", "台"))
    if "台" in name:
        out.append(name.replace("台", "臺"))
    return out


def resolve_case_override(layer_name: str, case_dir: Path):
    """案件層覆蓋：`<案>/環敏/<平台圖層名>.{geojson,shp}` 存在就優先用它。

    用途＝**本機圖資比平台舊**時，把平台的權威幾何拉進來出圖。本機環敏圖資多為
    105~106 年版快照，其後新增／修正的範圍本機一律查不到（2026-08-04 龍崎案：
    NR23 龍崎牛埔惡地自然保留區 110.07.30 新增，本機 106 年版沒有這一筆，
    逐筆距離算出「13 公里外」，而平台正確回報套疊 8.88 ㎡）。

    ⚠ 這是「有／沒有」的差異，不是面積門檻差異——**不可當成邊界誤差忽略**。
    平台幾何取法見 docs 與 skill：MapServer `/identify` 帶 returnGeometry=true。
    """
    d = case_dir / "環敏"
    if not d.is_dir():
        return None
    for p in sorted(d.iterdir()):
        if p.suffix.lower() not in (".geojson", ".json", ".shp"):
            continue
        # 檔名含平台圖層名即視為該項的覆蓋檔（允許加 NR23_ 這類前綴備註）
        if layer_name in p.stem:
            return p
    return None


def resolve_layer(layer_name: str, county: str, case_dir: Path | None = None):
    """把平台圖層名稱解析成 (路徑, 規則)；找不到回 (None, 規則或 None)。

    案件層 `<案>/環敏/` 的覆蓋檔優先於本機 `環境敏感相關/`（見 resolve_case_override）。
    """
    ov = resolve_case_override(layer_name, case_dir) if case_dir else None
    for rule in REGISTRY:
        m = re.search(rule["match"], layer_name)
        if not m:
            continue
        subs = {"county": county}
        if m.lastindex:
            groups = m.groups()
            if len(groups) >= 2:
                subs["h"], subs["mm"] = groups[0], groups[1]
        if ov is not None:
            print(f"    （{layer_name}：採案件層覆蓋檔 {ov.name}，不用本機 環境敏感相關/）")
            return ov, dict(rule, _subs=subs, _override=True)
        for tmpl in rule["shp"]:
            for c in _norm_county(county):
                s = dict(subs, county=c)
                try:
                    rel = tmpl.format(**s)
                except (KeyError, IndexError):
                    continue
                p = ENV_ROOT / rel
                if p.exists():
                    return p, dict(rule, _subs=s)
        return None, rule
    # REGISTRY 沒有這一項，但案件層有覆蓋檔 → 仍然出得了圖（單色、無分類）
    if ov is not None:
        print(f"    （{layer_name}：REGISTRY 未登錄，採案件層覆蓋檔 {ov.name}）")
        return ov, {"match": "", "shp": [], "field": None, "crs": 3826,
                    "title": layer_name, "_subs": {}, "_override": True}
    return None, None


def load_env_layer(shp: Path, rule: dict, name: str, alpha: float = DEFAULT_ALPHA,
                   outline_only: bool = False, color_idx: int = 0):
    lyr = QgsVectorLayer(str(shp), name, "ogr")
    if not lyr.isValid():
        return None
    if not lyr.crs().isValid() and rule.get("crs"):
        lyr.setCrs(QgsCoordinateReferenceSystem(f"EPSG:{rule['crs']}"))
    # 本層代表色：規則層指定的語意色優先，否則依疊圖順序取色環（見 GENERIC_PALETTE）
    base = tuple(rule.get("rgb") or GENERIC_PALETTE[color_idx % len(GENERIC_PALETTE)])
    fld = rule.get("field")
    if fld and lyr.fields().indexFromName(fld) >= 0:
        cats = []
        colors = rule.get("colors") or {}
        labels = rule.get("labels") or {}
        vals = sorted({f[fld] for f in lyr.getFeatures()},
                      key=lambda v: list(colors).index(v) if v in colors else 99)
        for v in vals:
            rgb = colors.get(v, base)
            sym = QgsFillSymbol.createSimple({
                "color": "%d,%d,%d,%d" % (rgb + (0 if outline_only
                                                 else int(alpha * 255),)),
                "outline_color": "%d,%d,%d,255" % rgb,
                "outline_width": "0.8" if outline_only else "0.15",
                "outline_style": "dash" if outline_only else "solid"})
            cats.append(QgsRendererCategory(v, sym, labels.get(v, str(v))))
        lyr.setRenderer(QgsCategorizedSymbolRenderer(fld, cats))
    else:
        dark = tuple(int(c * 0.6) for c in base)   # 外框用同色調暗版，維持可區辨
        sym = QgsFillSymbol.createSimple({
            "color": "%d,%d,%d,%d" % (base + (0 if outline_only
                                              else int(alpha * 255),)),
            "outline_color": "%d,%d,%d,255" % dark,
            "outline_width": "0.8" if outline_only else "0.3",
            "outline_style": "dash" if outline_only else "solid"})
        lyr.setRenderer(lyr.renderer().__class__(sym) if False else lyr.renderer())
        lyr.renderer().setSymbol(sym)
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
    a = ap.parse_args()

    out_dir = Path(a.out) if a.out else (REPO_ROOT / "outputs" / a.case)
    env_csv = Path(a.env_csv) if a.env_csv else (out_dir / "環境敏感套疊.csv")
    if not env_csv.exists():
        raise SystemExit(f"找不到 {env_csv}——請先做①b 環境敏感套疊分析")

    def read_hits(path):
        got = []
        with io.open(path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                v = (r.get("套疊面積_m2") or "").strip()
                try:
                    area = float(v)
                except ValueError:
                    area = 0.0
                if area > 0:
                    got.append((r["圖層名稱"], area))
        return got

    hits = read_hits(env_csv)
    n_total = sum(1 for _ in csv.DictReader(io.open(env_csv, encoding="utf-8-sig")))
    print(f"平台環敏檢核 {n_total} 項，套疊 {len(hits)} 項")
    extra = Path(a.extra_csv) if a.extra_csv else (out_dir / "環境敏感套疊_本機補充.csv")
    if extra.exists():
        ex = read_hits(extra)
        print(f"本機補充（平台清單外）{len(ex)} 項")
        seen = {n for n, _ in hits}
        hits += [(n, ar) for n, ar in ex if n not in seen]
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
    project.addMapLayer(site, False)
    keep_alive.append(site)

    # 基地幾何與圖框
    geom = QgsGeometry.unaryUnion([f.geometry() for f in site.getFeatures()])
    bb = geom.boundingBox()
    bbox = (bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum())

    # 敏感圖層
    geom_area = geom.area()
    env_layers, legend_layers, notes, stats = [], [], [], []
    for color_idx, (name, area) in enumerate(hits):
        shp, rule = resolve_layer(name, county, case_dir=out_dir)
        if shp is None:
            print(f"  ⚠ 無法解析為本機圖資：{name}（該項僅以表格呈現）")
            notes.append(f"{name}（本機無對應圖資）")
            continue
        subs = rule.get("_subs", {})
        title = rule["title"].format(**subs) if "{" in rule["title"] else rule["title"]
        # ★覆蓋率 ≥99% 的圖層改畫「外框不填色」（使用者定案 2026-07-29 的延伸）：
        # 全覆蓋圖層的填色不帶任何資訊，只會把正射底圖與其他套疊層壓成一片。
        # 「100% 涵蓋」這件事由表格與文字交代即可。
        outline_only = area >= 0.99 * geom_area
        lyr = load_env_layer(shp, rule, title, alpha=a.alpha,
                             outline_only=outline_only, color_idx=color_idx)
        if outline_only:
            print(f"    （{title} 覆蓋率 {100 * area / geom_area:.1f}% ≥99%，"
                  f"改畫外框不填色）")
        if lyr is None:
            print(f"  ⚠ 圖層讀取失敗：{shp}")
            continue
        # 逐類套疊面積（供簡報引用；數字只能來自實算）
        # ★必須依（圖層×分級）彙總，不可逐 feature 各記一列——同一分級的敏感範圍在圖資裡
        # 常是幾十個破碎多邊形（本案山崩與地滑 17 個），逐筆寫出等於給簡報一張沒法用的表
        # （2026-08-04 龍崎案實錯）。分級圖層（如淹水潛勢）才會有多列，且列數＝分級數。
        fld = rule.get("field")
        req = QgsFeatureRequest().setFilterRect(bb)
        agg: dict = {}
        for f in lyr.getFeatures(req):
            inter = geom.intersection(f.geometry())
            if inter.isEmpty():
                continue
            key = f[fld] if (fld and lyr.fields().indexFromName(fld) >= 0) else "（全區）"
            agg[key] = agg.get(key, 0.0) + inter.area()
        for key, ar in agg.items():
            stats.append([title, key, round(ar, 2)])
        project.addMapLayer(lyr, False)
        keep_alive.append(lyr)
        env_layers.append(lyr)
        legend_layers.append(lyr)
        print(f"  ✓ {title} ← {shp.relative_to(ENV_ROOT)}")

    # 正射底圖
    # ★底圖要照「版面引擎算出的最終圖框範圍」抓，不是基地 bbox（2026-08-04 龍崎案實錯：
    # 用 bbox+15% 抓，圖框因 A4 比例與整數比例尺退階而更寬，成品左右各露一條白邊）。
    # place_overlays 是純幾何、無副作用，可以先算一次拿 extent，再交給 build_sheet_layout
    # 重算一次（同輸入必得同結果），與 make_maps 用 c["extent"] 的作法一致。
    import overlay_placement as op          # noqa: PLC0415 — 與 build_layout 同一顆幾何引擎

    def _inter(x1, y1, x2, y2):
        r = geom.intersection(QgsGeometry.fromRect(QgsRectangle(x1, y1, x2, y2)))
        return 0.0 if r.isEmpty() else r.area()

    frame_ext = op.place_overlays(_inter, bbox).extent      # (L, R, B, T)
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
    except Exception as exc:  # noqa: BLE001 — 底圖失敗不阻斷出圖
        print(f"  ⚠ 底圖失敗：{type(exc).__name__}: {exc}")

    layers = [site] + env_layers + ([photo_rl] if photo_rl else [])
    legend = legend_layers + [site]

    # 涵蓋地號（沿用 build_layout 的收合寫法）
    idx_ln = site.fields().indexFromName("地號")
    lns = [str(f[idx_ln]).strip() for f in site.getFeatures()] if idx_ln >= 0 else []
    pnote = sheet.parcel_note(section, lns[0] if lns else "", len(lns))

    img_dir = out_dir / "圖檔_maxmap"
    img_dir.mkdir(parents=True, exist_ok=True)
    fname = "001_%s_環境敏感.png" % a.case
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
        p = out_dir / "環境敏感套疊_逐類面積.csv"
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
