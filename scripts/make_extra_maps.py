# -*- coding: utf-8 -*-
"""兩張補充主題圖（2026-07-29 使用者要求）

  --theme 權屬    依「公私別」分色（公有／私有），並標地號；正射為底
  --theme 環域    以 buffer 畫法定距離環域（預設 200m），環域內外一目瞭然
  --theme 隔離綠帶 綠帶＋現況建築物輪廓＋兩者交集（應拆除範圍），正射為底
  --theme 灌排水道 農田水利灌溉／排水渠道；**有涉及才出圖**，涉及者加粗並標渠道名稱
                  （法源：農地變更要點§5①二 妨礙農業灌排輸水能力者不同意變更）
  --theme 緩衝綠帶 開發審議規範總編§40 的等寬緩衝綠帶＋退縮後可利用範圍，正射為底。
                  ⚠ 與「隔離綠帶」是兩套不同制度：農地變更＝隔離綠帶（要點§9~§11）、
                  開發許可 ≥2 公頃＝緩衝綠帶（總編§40，≥10 公尺）。前置＝buffer-greenbelt。
                  對開發許可案這是**主流程必經步驟**，不是加值選配。
                  ★★ 總編§17 保育區（≥扣除不可開發區後剩餘基地面積 30%）與§40 緩衝綠帶
                  都要滿足，但**§40 緩衝綠帶可納入§17 保育區 30% 的計算，不是外加**
                  （2026-08-06 定案）——緩衝綠帶是保育區的最小基礎配置，量緩衝綠帶占比，
                  達 30% 即滿足；未達 30% 則有缺口，缺口＝30%×(基地−不可開發區)−緩衝綠帶
                  面積，補法待個案函詢（加寬緩衝綠帶或另劃保育區）。太陽光電因第十四編§2
                  豁免§17 是**目前唯一查到的例外**，其他用途未經確認有專章豁免前一律假定
                  §17 仍適用，不可比照光電案「開發許可就不受 30%」。詳見
                  .claude/rules/隔離綠帶與緩衝綠帶判準.md。
                  ⚠ 本主題**一律全部畫成緩衝綠帶，不做隔離設施細分**（評估階段簡化
                  原則，`greenbelt_ratio.py --facility-near` 的臨路側分色不適用本主題）。
  --theme 坡度    坡度依開發審議規範總編§16 三段分色（<30% 可開發／30~40% 僅開放性
                  公共設施／≥40% 不可開發區），吃 scripts/slope_check.py 的
                  坡度_percent.tif；有緩衝綠帶 shp 時一併疊上。
                  ⚠ 該 tif 覆蓋基地 bbox 矩形、**未裁到基地邊界**，本主題會先以
                  cutline 裁切再上色——直接畫未裁的會把基地外的坡度一起著色，
                  且畫面占比與權威占比不符（2026-08-04 龍崎案：未裁 44.8/20.4/34.9，
                  裁切後 38.5/23.4/38.2）。

版面沿用 build_layout.build_sheet_layout（與基本主題圖同框）。
須用 QGIS 內建 python 執行。

  .\\scripts\\qgis_python.ps1 scripts\\make_extra_maps.py `
      --joined <基地.shp> --case <案名> --theme 權屬
  .\\scripts\\qgis_python.ps1 scripts\\make_extra_maps.py `
      --joined <基地.shp> --case <案名> --theme 環域 --buffer 200
"""
from __future__ import annotations

import argparse
import csv
import gc
import io
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from qgis.core import (  # noqa: E402
    QgsApplication, QgsCategorizedSymbolRenderer, QgsColorRampShader,
    QgsCoordinateReferenceSystem,
    QgsFeature, QgsFeatureRequest, QgsField, QgsFillSymbol, QgsGeometry,
    QgsLayoutExporter,
    QgsLineSymbol, QgsPalLayerSettings,
    QgsProject, QgsRasterLayer, QgsRasterShader, QgsRectangle, QgsRendererCategory,
    QgsSingleBandPseudoColorRenderer, QgsTextBufferSettings,
    QgsTextFormat, QgsVectorLayer, QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import QVariant  # noqa: E402
from qgis.PyQt.QtGui import QColor, QFont  # noqa: E402

import build_layout as sheet  # noqa: E402
import district_line  # noqa: E402
from nlsc_wmts import build_basemap  # noqa: E402

CRS = "EPSG:3826"
EXPORT_DPI = 300
# ⚠️ 一律用 114 年版（2025-02-20、有 .prj、62 個生產區）。
# 同資料夾內的 BREED47.shp 是 **2016-01-20 舊版、僅 47 個、無 .prj**，
# 用它會漏掉後來新增的生產區——本案「安南港西區」就是新增的，
# 舊版算出「未涉及、最近 6,453m」，新版才看出**基地 100% 在生產區內**（2026-07-29 實錯）。
from config_loader import resource_path

BREED_DEFAULT = str(resource_path('breed_shp'))
# 權屬配色與分色實作集中在 ownership_style（make_maps 權屬主題共用同一份，
# 不可各自複製——兩份必然分岔）。此處僅 re-export 供既有引用者沿用。
from ownership_style import OWNER_STYLE, OWNER_UNKNOWN, style_by_owner  # noqa: E402,F401


def label_field(lyr, field, size=11, line=False):
    """標註欄位。line=True 用於**線圖層**——線圖層若沿用預設的 AroundPoint placement
    會完全標不出來（2026-08-01 灌排水道實踩），須改為沿線（Curved）並允許線上／線上方。"""
    if lyr.fields().indexFromName(field) < 0:
        return
    pal = QgsPalLayerSettings()
    pal.fieldName = field
    if line:
        try:
            from qgis.core import Qgis
            pal.placement = Qgis.LabelPlacement.Curved
            ls = pal.lineSettings()
            ls.setPlacementFlags(Qgis.LabelLinePlacementFlag.AboveLine
                                 | Qgis.LabelLinePlacementFlag.OnLine)
            ls.setMergeLines(True)     # 同名分段合併，避免同一條渠道重複標
            pal.setLineSettings(ls)
        except Exception:                      # noqa: BLE001 — 舊版 API 後備
            pal.placement = QgsPalLayerSettings.Curved
        pal.labelPerPart = False
    else:
        # ★ 面圖層強制標註點落在該宗地**內部**（2026-08-07 安南案權屬圖實見）：
        #   凹形宗地（如環抱基地一角的 302-1）質心會落在多邊形之外——落在基地裡，
        #   讀圖的人會誤以為那筆是基地的一部分。centroidInside 改用內部可容點定位。
        pal.centroidInside = True
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
                    choices=["權屬", "環域", "養殖漁業生產區", "隔離綠帶", "灌排水道",
                             "坡度", "緩衝綠帶"])
    ap.add_argument("--neighbours", default=None,
                    help="權屬主題：周邊地籍 shp（預設 <案>/cadastre/鄰地.shp，⓪a′ 產出）。"
                         "與基地同一套公私別配色但淡化、不標地號；找不到就只畫基地。")
    ap.add_argument("--setback", default=None,
                    help="緩衝綠帶主題：退縮後基地 shp（預設 <案>/緩衝綠帶/退縮後基地.shp）")
    ap.add_argument("--slope-tif", default=None,
                    help="坡度主題：坡度百分比 raster（預設 <案>/坡度分級/坡度_percent.tif）")
    ap.add_argument("--buffer-greenbelt", default=None,
                    help="坡度主題：緩衝綠帶 shp（預設 <案>/緩衝綠帶/緩衝綠帶10m.shp，"
                         "有才疊）")
    ap.add_argument("--irr-dir", default=str(REPO_ROOT / "2021全台農田水利灌排" / "農田"),
                    help="農田水利灌排圖資資料夾（內含 灌溉渠道.shp／排水渠道.shp）")
    ap.add_argument("--irr-near", type=float, default=30.0,
                    help="判定「涉及」之距離門檻(m)，預設 30；此範圍內之渠道加粗並標名稱")
    ap.add_argument("--irr-context", type=float, default=200.0,
                    help="周邊脈絡繪製半徑(m)，預設 200；僅細線不標名")
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
    ap.add_argument("--no-loc-parcel", action="store_true",
                    help="（相容旗標，已是預設值）不畫「位置／涵蓋地號」角落框")
    ap.add_argument("--loc-parcel", action="store_true",
                    help="畫出「位置／涵蓋地號」角落框（舊版面；預設不畫）")
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
        # ★ 分色依 persite_stats.classify_ownership 正規化後的值（全產線唯一判定），
        #   不是 shp 原始字串——原始值兩條路徑用詞不同（見 OWNER_STYLE 上方註解）。
        #   為此在圖層上新增暫存欄 `_權屬`，渲染與圖例都吃它。
        def _style_by_owner(lyr, name, label_size=0, outline="60,60,60,255",
                            outline_w="0.4", alpha=a.alpha):
            """薄包裝：實作在 ownership_style.style_by_owner（與 make_maps 共用）。
            此處只補上本腳本的標註設定與「缺欄即硬停」的流程決策。"""
            try:
                return style_by_owner(lyr, name, label_fn=label_field,
                                      label_size=label_size, outline=outline,
                                      outline_w=outline_w, alpha=alpha)
            except KeyError as exc:
                raise SystemExit(str(exc))

        # 周邊地籍（⓪a' 鄰地.shp）：同一套配色但淡化、不標地號，才看得出基地位置
        nb_path = a.neighbours or str(out_dir / "cadastre" / "鄰地.shp")
        nb = None
        if os.path.exists(nb_path):
            nb = QgsVectorLayer(nb_path, "周邊地籍", "ogr")
            if not nb.isValid():
                print(f"  ⚠ 鄰地讀取失敗，僅畫基地：{nb_path}")
                nb = None
        if nb is not None:
            # ★ 周邊地籍同樣要**畫宗地外框線並標地號**（使用者 2026-08-07 定案）——
            #   權屬圖要能逐筆看出哪一號是公有／私有，只有色塊沒有地號無法對照。
            #   框線與字級都比基地小一級（基地 0.4／11，周邊 0.3／9），靠粗細與
            #   紅框區分主從；QGIS 標註引擎會自動避讓重疊，過密處只顯示部分。
            # ⚠ 不要再疊 setOpacity()：符號本身已帶 alpha，再乘圖層透明度會變成
            #   0.55×0.55≈0.30，周邊在正射底圖上幾乎看不見（2026-08-07 基地尺度
            #   出圖時實見）。改為只把符號 alpha 調低一級，維持可辨識度。
            ordn = _style_by_owner(nb, "鄰地", label_size=9,
                                   outline="70,70,70,255", outline_w="0.3",
                                   alpha=max(0.30, a.alpha - 0.10))
            print(f"  周邊地籍 {nb.featureCount()} 筆，權屬類別：{ordn}")
        else:
            print(f"  （無鄰地.shp，僅畫基地）")

        ords = _style_by_owner(site, "基地", label_size=11)
        print("基地權屬類別：", ords)
        # 基地輪廓另加粗紅框，與周邊區隔（分色相同，靠框線與標註分辨）
        red = QgsVectorLayer(f"MultiPolygon?crs={CRS}", "申請範圍", "memory")
        red.dataProvider().addAttributes([QgsField("n", QVariant.String)])
        red.updateFields()
        ft = QgsFeature(red.fields()); ft.setGeometry(geom); ft.setAttributes([""])
        red.dataProvider().addFeatures([ft])
        red.renderer().setSymbol(QgsFillSymbol.createSimple({
            "color": "0,0,0,0", "outline_color": "208,0,0,255",
            "outline_width": "0.9"}))
        # ★ 圖框一律以**基地範圍**為準（使用者 2026-08-07 定案），與其他六主題同尺度。
        #   周邊地籍照畫，但只顯示落在圖框內的部分——若把圖框放大到涵蓋全部鄰地
        #   （安南案 225 筆會撐到 1/4000），基地會縮成一小塊，反而看不出各筆的權屬，
        #   與「基地<2公頃不外擴」的既定原則同理（見 CLAUDE.md 簡報深淺四通則）。
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
    elif a.theme == "灌排水道":
        # 農田水利灌溉／排水渠道（農業部農田水利署）。
        # 法源：農業用地變更審查作業要點§5①二「使用具農業灌溉功能之系統作廢污水排放，
        #   或有妨礙上、下游農業灌排水系統輸水能力之虞者，不同意變更使用」——這是
        #   **不同意變更事由**，不是成本問題，故要指名道姓查出涉及哪幾條、屬哪個工作站。
        # 圖資：`2021全台農田水利灌排\農田\{灌溉,排水}渠道.shp`
        #   LineString、EPSG:3826、**dbf 為 Big5**；`sys_cns`=渠道名稱、`sys_cls`=等級
        #   （灌溉:幹線/分線/主給/小給；排水:中排/小排）、`ia_cns`=管理處、`stn_cns`=工作站。
        #   ⚠ 資料夾名為 2021，但 `ymd` 欄顯示製作日為 2018-04-30 → 引用時以 2018 為準。
        # ★ 使用者定案（2026-08-01）：**有涉及才出圖**；無涉及則不產圖、exit 0。
        site.renderer().setSymbol(QgsFillSymbol.createSimple({
            "color": "255,0,0,60", "outline_color": "200,0,0,255", "outline_width": "0.9"}))
        SPECS = [("灌溉渠道", "0,90,200"), ("排水渠道", "0,150,130")]
        rect = geom.boundingBox()
        rect.grow(a.irr_context)
        hit_all, near_all = [], []
        for fname_, rgb in SPECS:
            src = os.path.join(a.irr_dir, fname_ + ".shp")
            if not os.path.exists(src):
                print(f"  [warn] 找不到 {src} → 略過 {fname_}")
                continue
            sl = QgsVectorLayer(src, fname_, "ogr")
            if not sl.isValid():
                print(f"  [warn] {fname_} 讀取失敗 → 略過")
                continue
            sl.setProviderEncoding("BIG5")
            if not sl.crs().isValid():
                sl.setCrs(QgsCoordinateReferenceSystem(CRS))
            # ⚠ 來源圖層**不可**放進 keep：全台 5.7 萬／5.4 萬條的 OGR provider 若活到
            #   qgs.exitQgis() 才解構，會存取違例硬崩潰（0xC0000005，2026-08-01 實踩；
            #   當時圖與 CSV 其實都已寫出，只是 exit code 非 0 且 stdout 緩衝遺失）。
            #   幾何已深拷貝進記憶體圖層，故本迴圈末尾即釋放來源。
            hit, ctx = [], []          # 涉及／周邊
            for f in sl.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
                # ★必須深拷貝：f.geometry() 的物件會隨 iterator 前進而失效，
                #   存進 list 事後再用會存取違例硬崩潰（0xC0000005，2026-08-01 實踩）。
                g2 = QgsGeometry(f.geometry())
                if g2.isEmpty():
                    continue
                d = geom.distance(g2)
                rec = (d, str(f["sys_cns"] or "（未命名）"), str(f["sys_cls"] or ""),
                       str(f["ia_cns"] or ""), str(f["stn_cns"] or ""), g2)
                if d <= a.irr_near:
                    hit.append(rec)
                elif d <= a.irr_context:
                    ctx.append(rec)
            hit.sort(key=lambda r: r[0]); ctx.sort(key=lambda r: r[0])
            hit_all += [(fname_,) + r for r in hit]
            near_all += [(fname_,) + r for r in hit + ctx]
            # 周邊：細線、不標名（只做脈絡）
            for recs, width, do_label in ((ctx, "0.5", False), (hit, "1.4", True)):
                if not recs:
                    continue
                nm = f"{fname_}（涉及）" if do_label else f"{fname_}（周邊）"
                ml = QgsVectorLayer("LineString?crs=" + CRS + "&field=名稱:string(80)",
                                    nm, "memory")
                dp = ml.dataProvider(); dp.addAttributes([]); ml.updateFields()
                feats = []
                for d, cns, cls, ia, stn, g2 in recs:
                    nf = QgsFeature(ml.fields())
                    nf.setGeometry(g2)
                    nf["名稱"] = f"{cns}（{cls}）" if cls else cns
                    feats.append(nf)
                dp.addFeatures(feats); ml.updateExtents()
                sym = QgsLineSymbol.createSimple({"color": rgb + ",255", "width": width})
                ml.renderer().setSymbol(sym)
                if do_label:
                    label_field(ml, "名稱", 10, line=True)
                project.addMapLayer(ml, False); keep.append(ml)
                layers.append(ml); legend.append(ml)
            sl = None                  # ★見上方警告：先釋放大型來源 provider
            gc.collect()
        if not hit_all:
            print(f"  {a.irr_near:.0f} m 內查無農田水利灌排渠道"
                  f"（{a.irr_context:.0f} m 內 {len(near_all)} 條）→ 未涉及，不產圖")
            return
        print(f"  ★涉及：{a.irr_near:.0f} m 內共 {len(hit_all)} 條渠道")
        stns = sorted({r[5] for r in hit_all if r[5]})
        ias = sorted({r[4] for r in hit_all if r[4]})
        for kind, d, cns, cls, ia, stn, _g in hit_all:
            print(f"    {d:6.1f} m  {cns}（{cls}）　{kind}　{ia}管理處 {stn}")
        print(f"  主管：{'／'.join(ias)}農田水利署　工作站：{'／'.join(stns)}")
        csvp = out_dir / "灌排水道涉及清單.csv"
        with io.open(csvp, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["類別", "渠道名稱", "等級", "距基地_m", "管理處", "工作站"])
            for kind, d, cns, cls, ia, stn, _g in hit_all:
                w.writerow([kind, cns, cls, f"{d:.2f}", ia, stn])
        print(f"  → {csvp}")
        # 圖框以基地為主：渠道是長條線狀物（本案「南考試潭中排２」長 1,889 m），
        # 把整條併進 bbox 會把比例尺拉到 1/10000、基地縮成一點，名稱也擠成一團
        # （2026-08-01 實踩）。只留足以看清涉及渠道與其名稱的邊界。
        maxd = max(r[1] for r in hit_all)
        ext_geom = geom
        bb = ext_geom.boundingBox()
        bb.grow(max(50.0, maxd * 4.0))
    elif a.theme == "緩衝綠帶":
        # 緩衝綠帶配置圖（開發審議規範總編§40，2 公頃以上開發許可案）。
        # ⚠ 與「隔離綠帶」是**兩套不同制度**，不可混用：
        #   農業用地變更＝隔離綠帶（要點§9~§11，面積比＋最小寬度雙門檻，greenbelt_ratio）
        #   開發許可 ≥2ha ＝緩衝綠帶（總編§40，等寬內縮 ≥10m，buffer-greenbelt）
        # ⚠ §17 保育區(30%)可把本主題的§40(10m)緩衝綠帶納入計算、不是外加；
        #   本主題只畫§40 那塊，§17 保育區缺口另見 config/檢核清單 的
        #   conservation_zone 型 inquiry 項（含缺口算法）。
        #   一律全部畫緩衝綠帶、不做隔離設施細分（評估簡化原則，見 .claude/rules/）。
        site.renderer().setSymbol(QgsFillSymbol.createSimple({
            "color": "255,255,255,0", "outline_color": "200,0,0,255",
            "outline_width": "0.9"}))
        gb_p = a.buffer_greenbelt or str(out_dir / "緩衝綠帶" / "緩衝綠帶10m.shp")
        if not os.path.exists(gb_p):
            raise SystemExit(f"找不到緩衝綠帶 shp：{gb_p}"
                             "（先跑 buffer-greenbelt 的 make_greenbelt.py）")
        gl = QgsVectorLayer(gb_p, "緩衝綠帶／總編§40", "ogr")
        if not gl.isValid():
            raise SystemExit(f"緩衝綠帶 shp 讀取失敗：{gb_p}")
        gl.renderer().setSymbol(QgsFillSymbol.createSimple({
            "color": "56,168,0,%d" % A,
            "outline_color": "30,110,0,255", "outline_width": "0.5"}))
        gg = QgsGeometry.unaryUnion([f.geometry() for f in gl.getFeatures()])
        print(f"  緩衝綠帶 {gg.area():,.0f} m²（占基地 {gg.area()/geom.area()*100:.2f}%）")

        stack = [gl]
        sb_p = a.setback or str(out_dir / "緩衝綠帶" / "退縮後基地.shp")
        if os.path.exists(sb_p):
            sl = QgsVectorLayer(sb_p, "退縮後可利用範圍", "ogr")
            if sl.isValid():
                sl.renderer().setSymbol(QgsFillSymbol.createSimple({
                    "color": "255,210,80,%d" % int(A * 0.7),
                    "outline_color": "180,130,0,255", "outline_width": "0.4"}))
                sg = QgsGeometry.unaryUnion([f.geometry() for f in sl.getFeatures()])
                print(f"  退縮後可利用 {sg.area():,.0f} m²"
                      f"（占基地 {sg.area()/geom.area()*100:.2f}%）")
                stack.insert(0, sl)      # 可利用範圍畫在綠帶之下
        else:
            print(f"  （無退縮後基地 shp，略過：{sb_p}）")
        for lyr in stack:
            project.addMapLayer(lyr, False); keep.append(lyr)
            layers.append(lyr); legend.append(lyr)
        ext_geom = geom
        bb = geom.boundingBox()

    elif a.theme == "坡度":
        # 坡度分段圖（開發審議規範總編§16 三段）。這張圖回答的是「可開發的地在哪裡」，
        # 不只是「占多少百分比」——量體配置與不可開發區的空間關係要看得見。
        src = a.slope_tif or str(out_dir / "坡度分級" / "坡度_percent.tif")
        if not os.path.exists(src):
            raise SystemExit(f"找不到坡度 raster：{src}（先跑 scripts/slope_check.py）")

        # ★必須先以基地為遮罩裁切：slope_check 產出的 tif 是基地 bbox 矩形，
        #   直接畫會把基地外的坡度一起著色，畫面占比也對不上權威占比。
        # ⚠ 裁切一律呼叫 `slope_check.clip_slope_raster()`（ALL_TOUCHED 遮罩，與
        #   `坡度分級.json` 的統計數字同一套演算法），**不要用 `gdal.Warp` cutline**——
        #   2026-09-14 內湖保護區案實錯：Warp cutline 是另一套遮罩（像元中心判斷去留），
        #   本案少算了全案唯一一個 30~40% 像元，圖上「整區都<30%」跟 json 對不上、
        #   使用者一眼看出圖表不符。見 `slope_check.clip_slope_raster` docstring。
        from slope_check import clip_slope_raster  # noqa: PLC0415 — 僅本主題需要
        clipped = str(out_dir / "坡度分級" / "坡度_percent_基地裁切.tif")
        clip_slope_raster(src, a.joined, clipped)
        print(f"  坡度 raster 已以基地為遮罩裁切（ALL_TOUCHED，與坡度分級.json 同演算法）"
              f" → {Path(clipped).name}")

        rl = QgsRasterLayer(clipped, "坡度分段（總編§16）")
        if not rl.isValid():
            raise SystemExit(f"坡度 raster 讀取失敗：{clipped}")
        # 離散色階：值 ≤ 門檻者取該色。標籤直接寫法律效果，圖例才看得懂。
        shader = QgsRasterShader()
        ramp = QgsColorRampShader()
        ramp.setColorRampType(QgsColorRampShader.Discrete)
        # 標籤用「／」斷詞：右欄圖例只有 45mm 寬，build_layout 以 setWrapString("／")
        # 換行；不放「／」的長標籤會被直接切掉（2026-08-04 實錯）。
        ramp.setColorRampItemList([
            QgsColorRampShader.ColorRampItem(
                30.0, QColor(64, 160, 64, A), "坡度<30%／可開發"),
            QgsColorRampShader.ColorRampItem(
                40.0, QColor(255, 190, 60, A), "坡度30~40%／僅開放性設施"),
            QgsColorRampShader.ColorRampItem(
                1e6, QColor(214, 39, 40, A), "坡度≥40%／不可開發區"),
        ])
        shader.setRasterShaderFunction(ramp)
        rl.setRenderer(QgsSingleBandPseudoColorRenderer(rl.dataProvider(), 1, shader))
        project.addMapLayer(rl, False); keep.append(rl)
        layers.append(rl); legend.append(rl)

        site.renderer().setSymbol(QgsFillSymbol.createSimple({
            "color": "255,255,255,0", "outline_color": "200,0,0,255",
            "outline_width": "0.9"}))

        # 緩衝綠帶（總編§40）有就疊——它與不可開發區是可開發面積的兩大壓縮因素，
        # 同一張圖看得出兩者是否重疊（重疊部分不重複扣）。
        gb_p = a.buffer_greenbelt or str(out_dir / "緩衝綠帶" / "緩衝綠帶10m.shp")
        if os.path.exists(gb_p):
            gl = QgsVectorLayer(gb_p, "緩衝綠帶10公尺／總編§40", "ogr")
            if gl.isValid():
                gl.renderer().setSymbol(QgsFillSymbol.createSimple({
                    "color": "255,255,255,0", "outline_color": "0,110,40,255",
                    "outline_width": "0.8", "outline_style": "dash"}))
                project.addMapLayer(gl, False); keep.append(gl)
                layers.append(gl); legend.append(gl)
                gg = QgsGeometry.unaryUnion([f.geometry() for f in gl.getFeatures()])
                print(f"  緩衝綠帶 {gg.area():,.0f} m²"
                      f"（占基地 {gg.area() / geom.area() * 100:.1f}%）")
        else:
            print(f"  （無緩衝綠帶 shp，略過：{gb_p}）")
        ext_geom = geom
        bb = geom.boundingBox()

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
        # 臨路側另歸類為「隔離設施」（要點§11 通路、空地等非建築之開放性設施），
        # 疊在綠帶之上分色呈現；兩者均計入面積比，分色只為說明性質
        fc_p = str(out_dir / "隔離綠帶" / "隔離設施.shp")
        if os.path.exists(fc_p):
            fl = QgsVectorLayer(fc_p, "隔離設施(臨路側)", "ogr")
            if fl.isValid():
                fg = QgsGeometry.unaryUnion([f.geometry() for f in fl.getFeatures()])
                fl.setName(f"隔離設施(臨路側) {fg.area():,.2f} m²")
                gl.setName(f"隔離綠帶 {gg.area() - fg.area():,.2f} m²")
                fl.renderer().setSymbol(QgsFillSymbol.createSimple({
                    "color": "120,150,190,%d" % int(a.alpha * 255),
                    "outline_color": "40,90,150,255", "outline_width": "0.5"}))
                stack.insert(0, fl)
                print(f"  其中隔離設施 {fg.area():,.2f} m²、"
                      f"隔離綠帶 {gg.area() - fg.area():,.2f} m²")
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

    if a.theme == "權屬":
        # ⚠ QGIS setLayers：**清單第一個畫在最上層**。要的疊序（上→下）＝
        #   申請範圍紅框 → 基地地籍(標地號) → 周邊地籍(淡化)，否則基地會被鄰地蓋住。
        project.addMapLayer(red, False); keep.append(red)
        layers.append(red); legend.append(red)
        project.addMapLayer(site, False); keep.append(site)
        layers.append(site); legend.append(site)
        if nb is not None:
            project.addMapLayer(nb, False); keep.append(nb)
            layers.append(nb); legend.append(nb)   # 基地全私、鄰地有公時，
            #   公有色塊只在鄰地圖層出現，故鄰地一定要進圖例，否則圖上有橘色卻無說明
    else:
        project.addMapLayer(site, False); keep.append(site)
        layers.append(site); legend.append(site)
    bbox = (bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum())

    # ★底圖照「版面引擎算出的最終圖框」抓，不是 bbox（bbox 較窄，成品左右會露白邊；
    # 2026-08-04 坡度圖實錯，同一個 bug 也修過 make_env_map）。
    # ★★2026-08-20 再修：build_sheet_layout 內是用 place_fixed（maxmap v2，地圖寬
    # MAP2_W=287mm）算最終 extent，但這裡原本用 place_overlays（舊 compact，地圖寬
    # MAP_W=240mm），兩者算出的 extent 不同——較窄的底圖鋪不滿較寬的 maxmap 框，
    # 正射底圖四周露白（關廟深坑子段權屬圖實見）。改用同一支 place_fixed＋MAP2 尺寸，
    # 底圖範圍才會與實際圖框一致、鋪滿。純幾何無副作用。
    import overlay_placement as op          # noqa: PLC0415 — 與 build_layout 同一顆引擎

    def _inter(x1, y1, x2, y2):
        r = ext_geom.intersection(QgsGeometry.fromRect(QgsRectangle(x1, y1, x2, y2)))
        return 0.0 if r.isEmpty() else r.area()

    _fl, _fr, _fb, _ft = op.place_fixed(_inter, bbox,
                                        map_w=op.MAP2_W, map_h=op.MAP2_H).extent
    bm_bbox = (_fl, _fb, _fr, _ft)
    bm = Path(a.basemap_dir) if a.basemap_dir else (out_dir / "底圖")
    bm.mkdir(parents=True, exist_ok=True)
    try:
        base = "EMAP" if a.theme == "養殖漁業生產區" else "PHOTO2"
        tif = build_basemap(bm_bbox, str(bm / f"extra_{a.theme}_{base}.tif"), layer=base,
                            cache_dir=str((out_dir / "底圖") / "_tilecache"),
                            buffer_ratio=0.02)
        if tif:
            rl = QgsRasterLayer(tif, f"{'通用電子地圖' if base == 'EMAP' else '正射影像'}"
                                     f"(NLSC {base})")
            if rl.isValid():
                project.addMapLayer(rl, False); keep.append(rl); layers.append(rl)
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠ 底圖失敗：{type(exc).__name__}: {exc}")

    # 行政區界（`<案>/行政區界/行政區界.shp` 存在才畫；沒有就完全不影響既有行為）
    _dist = district_line.district_layers(out_dir, project, keep, QgsRectangle(*bbox))
    if _dist:
        layers = layers[:1] + _dist.layers + layers[1:]
        legend = list(legend) + _dist.legend

    img_dir = out_dir / "圖檔_maxmap"
    img_dir.mkdir(parents=True, exist_ok=True)
    fname = f"001_{a.case}_{a.theme}.png"
    layout, pl = sheet.build_sheet_layout(
        project, f"001_{a.theme}", layers, legend, set(), ext_geom, bbox, loc, pnote,
        keep, show_loc_parcel=a.loc_parcel)
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
