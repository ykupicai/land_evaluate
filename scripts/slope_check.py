# -*- coding: utf-8 -*-
"""坡度分級檢核（開發審議規範總編§16 不可開發區）— 由 NLSC 坡度圖圖磚反推

★ 建立緣由（2026-08-02 龍崎番社段光電案）：總編§16 要求
    ・**平均坡度 ≥40%**：其 80% 土地應維持原始地形地貌，列為**不可開發區**
    ・**30%～40%**：僅得作開放性公共設施或必要性服務設施，**不得作為建築基地**
  而本專案圖資庫**沒有 DEM／DTM**，無法自行算坡度。使用者提供 NLSC 坡度圖 WMTS
  （`MOI_SLOPEP_LV7_2`）後，改以「圖磚反推分級」取得可交付的初判數字。

資料來源與圖層（GetCapabilities 在 maps.nlsc.gov.tw/S_Maps/wmts，圖磚在 wmts.nlsc.gov.tw）
    MOI_SLOPEP_LV7_2   坡度圖-**7級** 2010-2015（png）← 本腳本主要來源
    MOI_SLOPEP_GT30_2  坡度圖-30%坡 2010-2015（png，紅=>30%、黑=≤30%）← 獨立交叉驗證
    舊期 MOI_SLOPEP_LV7／MOI_SLOPEP_GT30 為 2003-2005。

七級坡度與色階（實測色票為 ColorBrewer **RdYlGn 7-class**，2026-08-02 由圖磚取樣確認）
    1級 <5%      #1a9850 (26,153,80)
    2級 5~15%    #91cf60 (145,207,96)
    3級 15~30%   #d9ef8b (217,239,139)
    4級 30~40%   #ffffbf (255,255,191)   ← 總編§16「限開放性公共設施」
    5級 40~55%   #fee08b (254,224,139)   ┐
    6級 55~100%  #fc8d59 (252,141,89)    ├ 總編§16「不可開發區」（≥40%）
    7級 >100%    #d73027 (215,48,38)     ┘

★★★ 現況：**此法在龍崎案未通過驗證，預設會硬停、不輸出數字**（2026-08-02）
  用 GT30（>30% 二值圖）當真值反推 LV7 的色階對應，結果**沒有一致映射**：
      假設1級(深綠 <5%) → GT30 說 88.4% 是 >30%      ← 完全矛盾
      假設2級            → 3.4%          假設4級 → 100%
      假設5級            → 0%            假設7級 → 54.3%（混合）
  結論：**LV7 圖磚不是「7 個標準色的分類圖」**（實測 12,108 種顏色），
  以最近色歸類會產生不合理的雙峰分布（1級 39%＋7級 38%）。
  同一 GT30 圖層在 keep_alpha 開／關兩種抓法下，>30% 占比還會從 81.66% 變成 61.65%
  → 本管線對這類圖層**尚不穩定**。

  **法定門檻（總編§16 不可開發區）不可建立在這種反推上。**可靠來源是：
    ① 內政部 20 公尺網格 DTM 開放資料（自行算坡度，可重複用於所有山坡地案）
    ② 正式地形測量成果（送審最終仍以此為準）
  本腳本保留作為「圖層探索與校準」的工具，並以交叉驗證為**硬閘門**：
  不通過就 exit 2、不寫出任何數字，避免不可靠的占比流入交付文件。
  `--force` 可強制輸出（僅供除錯，輸出檔會標記 unverified）。

★★ 若日後校準成功，仍有三個必須誠實交代的限制（交付文字要照寫）
  1. 這是「已上色的主題圖反推」，不是 DEM 實算，屬**概估**。
  2. 總編§16 的判定單位是**坵塊圖平均坡度**（正式須實測地形圖），
     本成果**不得取代實測地形測量**。
  3. 圖資年份 **2010-2015**；基地近年整地或開挖不會反映。

用法（**須用 QGIS 內建 python**，要 osgeo）
    & "<QGIS>\bin\python-qgis-ltr.bat" scripts\slope_check.py --joined <基地.shp> \
        --out <案>\坡度分級 [--sitemap <sitemap.csv>] [--zoom 17] [--no-verify]
輸出：坡度分級.csv／.json、坡度分級_LV7.tif、（--verify 時）坡度_GT30.tif
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nlsc_wmts import build_basemap  # noqa: E402

LV7 = "MOI_SLOPEP_LV7_2"
GT30 = "MOI_SLOPEP_GT30_2"

# 級別 → (標準色 RGB, 坡度範圍文字)
CLASSES = [
    (1, (26, 153, 80), "<5%"),
    (2, (145, 207, 96), "5~15%"),
    (3, (217, 239, 139), "15~30%"),
    (4, (255, 255, 191), "30~40%"),
    (5, (254, 224, 139), "40~55%"),
    (6, (252, 141, 89), "55~100%"),
    (7, (215, 48, 38), ">100%"),
]
# 總編§16 三段
GROUP = {"可開發（<30%）": (1, 2, 3),
         "限開放性設施（30~40%）": (4,),
         "不可開發區（≥40%）": (5, 6, 7)}


def _mask_from_shp(shp, ref_ds, where=None):
    """把 shp 燒成與 ref_ds 同網格的 0/1 遮罩（ref_ds 為 EPSG:3857 的圖磚 tif）。"""
    from osgeo import gdal, ogr, osr
    src = ogr.Open(shp)
    lyr = src.GetLayer()
    if where:
        lyr.SetAttributeFilter(where)
    # 圖磚是 3857，shp 多為 3826 → 建記憶體圖層做投影轉換
    dst_srs = osr.SpatialReference()
    dst_srs.ImportFromWkt(ref_ds.GetProjection())
    dst_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    s_srs = lyr.GetSpatialRef()
    s_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    ct = osr.CoordinateTransformation(s_srs, dst_srs)
    drv = ogr.GetDriverByName("Memory")
    mds = drv.CreateDataSource("m")
    mlyr = mds.CreateLayer("m", dst_srs, ogr.wkbPolygon)
    n = 0
    for f in lyr:
        g = f.GetGeometryRef()
        if g is None:
            continue
        g2 = g.Clone()
        g2.Transform(ct)
        nf = ogr.Feature(mlyr.GetLayerDefn())
        nf.SetGeometry(g2)
        mlyr.CreateFeature(nf)
        n += 1
    if not n:
        return None
    mem = gdal.GetDriverByName("MEM").Create(
        "", ref_ds.RasterXSize, ref_ds.RasterYSize, 1, gdal.GDT_Byte)
    mem.SetGeoTransform(ref_ds.GetGeoTransform())
    mem.SetProjection(ref_ds.GetProjection())
    gdal.RasterizeLayer(mem, [1], mlyr, burn_values=[1])
    return mem.ReadAsArray()


def classify_lv7(tif):
    """回傳 (ds, 級別陣列 1~7, 該像元是否有效)。以最近色歸類。

    ★★ **主題圖層一定要用 keep_alpha=True 抓**（2026-08-02 實踩）：
      坡度圖是帶透明背景的疊圖層，`keep_alpha=False` 會把**透明區寫成 RGB(0,0,0) 黑色**，
      而黑色離「1級深綠 (26,153,80)」最近 → 大片無資料區被誤歸成「坡度<5%」。
      症狀是分布呈現不合理的雙峰（1級 39%＋7級 38%），而且與獨立的 GT30 圖層
      對不上（81.66% vs 40.98%，差 40 個百分點）。**alpha 才是「有沒有資料」的唯一依據。**
    """
    from osgeo import gdal
    ds = gdal.Open(tif)
    all_ = ds.ReadAsArray()
    if all_.shape[0] < 4:
        raise SystemExit("✖ 坡度圖磚缺 alpha 波段——請以 keep_alpha=True 重抓")
    arr = all_[:3].astype(np.int16)                      # (3,H,W)
    alpha = all_[3]
    pal = np.array([c for _, c, _ in CLASSES], dtype=np.int16)   # (7,3)
    d = np.stack([((arr - pal[i].reshape(3, 1, 1)) ** 2).sum(0) for i in range(7)])
    lvl = d.argmin(0) + 1
    # 有效＝不透明，且顏色離某個標準色夠近（排除圖廓、文字、註記）
    valid = (alpha >= 128) & (d.min(0) <= 3 * (70 ** 2))
    return ds, lvl, valid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True, help="基地 shp")
    ap.add_argument("--out", required=True, help="輸出資料夾")
    ap.add_argument("--sitemap", default=None, help="（保留）逐案場統計用")
    ap.add_argument("--zoom", type=int, default=None, help="圖磚 zoom，預設自動")
    ap.add_argument("--no-verify", action="store_true",
                    help="不抓 GT30 做交叉驗證（預設會抓；跳過驗證等於放棄唯一的把關）")
    ap.add_argument("--force", action="store_true",
                    help="交叉驗證不通過仍輸出（僅供除錯；輸出會標記 unverified）")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    import geopandas as gpd
    from shapely.ops import unary_union
    g = gpd.read_file(a.joined)
    g = g.to_crs(3826) if g.crs else g.set_crs(3826)
    su = unary_union(g.geometry.tolist())
    ext = su.bounds
    print(f"基地 {len(g)} 筆、dissolve 面積 {su.area:,.2f} ㎡ = {su.area/10000:.4f} 公頃")

    lv7_tif = os.path.join(a.out, "坡度分級_LV7.tif")
    if not build_basemap(ext, lv7_tif, layer=LV7, z=a.zoom, keep_alpha=True,
                         cache_dir=os.path.join(a.out, "_tiles"),
                         buffer_ratio=0.03):
        raise SystemExit(f"✖ {LV7} 圖磚抓不到")
    ds, lvl, valid = classify_lv7(lv7_tif)
    gt = ds.GetGeoTransform()
    px_m2 = abs(gt[1] * gt[5])          # 3857 像元面積（公尺²，含緯度變形）
    # 3857 在本緯度的面積放大率 ≈ 1/cos(lat)²，回推真實面積
    import math
    lat = math.degrees(2 * math.atan(math.exp(
        (gt[3] + gt[5] * ds.RasterYSize / 2) / 6378137.0)) - math.pi / 2)
    px_m2 *= math.cos(math.radians(lat)) ** 2
    print(f"圖磚 {ds.RasterXSize}x{ds.RasterYSize}、像元 {math.sqrt(px_m2):.2f} m")

    mask = _mask_from_shp(a.joined, ds)
    if mask is None:
        raise SystemExit("✖ 基地無法燒成遮罩")
    sel = (mask == 1) & valid
    npix = int(sel.sum())
    if not npix:
        raise SystemExit("✖ 基地範圍內無有效坡度像元")
    site_px_area = npix * px_m2
    print(f"基地內有效像元 {npix:,}（≈{site_px_area:,.0f} ㎡，"
          f"對 dissolve 面積 {site_px_area/su.area*100:.1f}%）")

    rows, by_lvl = [], {}
    for k, rgb, rng in CLASSES:
        c = int(((lvl == k) & sel).sum())
        by_lvl[k] = c
        rows.append({"級別": k, "坡度": rng, "像元": c,
                     "面積m2": round(c * px_m2, 2),
                     "占比pct": round(c / npix * 100, 2)})
    print(f"\n{'級':<3}{'坡度':<10}{'面積㎡':>12}{'占比':>8}")
    for r in rows:
        print(f"{r['級別']:<3}{r['坡度']:<10}{r['面積m2']:>12,.0f}{r['占比pct']:>7.2f}%")

    grp = {}
    print()
    for name, ks in GROUP.items():
        c = sum(by_lvl[k] for k in ks)
        grp[name] = {"面積m2": round(c * px_m2, 2), "占比pct": round(c / npix * 100, 2)}
        print(f"  {name:<24}{c * px_m2:>12,.0f} ㎡{c / npix * 100:>7.2f}%")

    ver = None
    if not a.no_verify:
        gt30_tif = os.path.join(a.out, "坡度_GT30.tif")
        if build_basemap(ext, gt30_tif, layer=GT30, z=a.zoom, keep_alpha=True,
                         cache_dir=os.path.join(a.out, "_tiles"),
                         buffer_ratio=0.03):
            from osgeo import gdal
            d2 = gdal.Open(gt30_tif)
            aa = d2.ReadAsArray()
            a2 = aa[:3].astype(np.int16)
            # 同理：alpha 才是「有沒有資料」——GT30 的黑色其實是透明區（未上色＝≤30%），
            # 但透明也可能是「無資料」，兩者不可混淆，故分母只取不透明像元。
            op = (aa[3] >= 128) if aa.shape[0] >= 4 else np.ones(a2.shape[1:], bool)
            red = op & (a2[0] > 120) & (a2[1] < 90) & (a2[2] < 90)
            m2 = _mask_from_shp(a.joined, d2)
            s2 = (m2 == 1)
            # ⚠ 分母必須是**基地全部像元**，不是「不透明像元」——GT30 只畫 >30% 的範圍，
            #   以不透明為分母會得到恆為 100% 的假結果（2026-08-02 實踩）。
            if not s2.any():
                raise SystemExit("✖ GT30 圖層在基地範圍內無像元，無法交叉驗證")
            gt30_pct = float(red[s2].sum()) / float(s2.sum()) * 100
            lv7_gt30 = sum(by_lvl[k] for k in (4, 5, 6, 7)) / npix * 100
            ver = {"GT30圖層_pct": round(gt30_pct, 2),
                   "LV7推得_4至7級_pct": round(lv7_gt30, 2),
                   "差_pct": round(lv7_gt30 - gt30_pct, 2)}
            print(f"\n[交叉驗證] 獨立圖層 GT30 的 >30% 占比 {gt30_pct:.2f}%"
                  f"／本腳本由 LV7 推得 {lv7_gt30:.2f}%"
                  f"　差 {lv7_gt30 - gt30_pct:+.2f} 個百分點")
            passed = abs(lv7_gt30 - gt30_pct) <= 5
            ver["通過"] = bool(passed)
            print("  " + ("✓ 兩個獨立圖層一致（差 ≤5 個百分點），分級可採信"
                          if passed else
                          "✖ 兩圖層差異過大 → **色階反推不可靠，不輸出數字**"))
            if not passed and not a.force:
                print("\n法定門檻（總編§16 不可開發區）不可建立在不可靠的反推上。")
                print("可靠來源：① 內政部 20 公尺網格 DTM 開放資料自行算坡度"
                      "　② 正式地形測量成果。")
                print("（要看未通過的數字請加 --force，輸出會標記 unverified）")
                raise SystemExit(2)

    res = {"基地_dissolve_m2": round(su.area, 2), "有效像元": npix,
           "像元邊長_m": round(math.sqrt(px_m2), 3),
           "圖層": LV7, "圖資年份": "2010-2015",
           "分級": rows, "總編§16分段": grp, "交叉驗證": ver,
           "限制": ["由已上色主題圖反推、非 DEM 實算，屬概估",
                    "總編§16 判定單位為坵塊圖平均坡度，本成果不得取代實測地形測量",
                    "圖資年份 2010-2015，近年整地開挖不會反映"]}
    io.open(os.path.join(a.out, "坡度分級.json"), "w", encoding="utf-8").write(
        json.dumps(res, ensure_ascii=False, indent=2))
    with io.open(os.path.join(a.out, "坡度分級.csv"), "w",
                 encoding="utf-8-sig", newline="") as fh:
        fh.write("級別,坡度,面積m2,占比pct\n")
        for r in rows:
            fh.write(f"{r['級別']},{r['坡度']},{r['面積m2']:.2f},{r['占比pct']:.2f}\n")
        for k, v in grp.items():
            fh.write(f"[總編§16] {k},,{v['面積m2']:.2f},{v['占比pct']:.2f}\n")
    print(f"\n→ {a.out}\\坡度分級.csv / .json / 坡度分級_LV7.tif")
    print("⚠ 交付須標明：本成果由 NLSC 坡度圖（2010-2015）反推，屬概估，"
          "不得取代實測地形測量。")


if __name__ == "__main__":
    main()

