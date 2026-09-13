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

# 圖層登錄表與解析已抽至 env_registry.py（純模組、可單測；REGISTRY 的自然保留區
# 1152→1061 順序由 tests/test_env_registry.py 鎖住）。本檔只留 QGIS 出圖端。
from env_registry import (  # noqa: E402
    DEFAULT_ALPHA, GENERIC_PALETTE, REGISTRY, resolve_layer,
)


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
                # 兩種來源欄名不同：①b 平台版寫「套疊面積_m2」、⓪b NGIS 版
                # （ngis_sensitive.py）寫「重疊面積_m2」。只認前者會讓 NGIS 來源
                # 靜默回「套疊 0 項 → 不出圖」，明明有命中卻不畫（2026-08-27 麻豆案實踩）。
                v = (r.get("套疊面積_m2") or r.get("重疊面積_m2") or "").strip()
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
    env_layers, legend_layers, notes, stats = [], [], [], []
    for color_idx, (name, area) in enumerate(hits):
        shp, rule = resolve_layer(name, county, case_dir=out_dir)
        if shp is None:
            print(f"  ⚠ 無法解析為本機圖資：{name}（該項僅以表格呈現）")
            notes.append(f"{name}（本機無對應圖資）")
            continue
        subs = rule.get("_subs", {})
        title = rule["title"].format(**subs) if "{" in rule["title"] else rule["title"]
        outline_only = area >= 0.99 * geom_area and len(hits) >= 2
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
