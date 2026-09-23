# -*- coding: utf-8 -*-
r"""坡度分級檢核（開發審議規範總編§16 不可開發區）— **以 DEM 實算**

★ 緣由與版本沿革（2026-08-02 龍崎番社段光電案）
  總編§16 要求：
    ・**平均坡度 ≥40%**：其 80% 土地應維持原始地形地貌，列為**不可開發區**
    ・**30%～40%**：僅得作開放性公共設施或必要性服務設施，**不得作為建築基地**
  第一版曾試「由 NLSC 坡度圖圖磚（MOI_SLOPEP_LV7_2）反推分級」——**失敗，已廢**：
  該圖磚實測 12,108 種顏色、不是 7 個標準色的分類圖，以最近色歸類得到不合理的雙峰
  分布，且用 GT30 當真值校準時沒有一致映射（假設1級「<5%」竟有 88.4% 被判為 >30%）。
  **法定門檻不建立在渲染圖反推上。** 現版改以 20 公尺 DTM 實算，WMTS 的
  `MOI_SLOPEP_GT30_2`（>30% 二值圖）降為**獨立交叉驗證**。

資料來源
  DEM：`\\192.168.1.134\Public-TN\3. 圖資\gis圖資\dem地形資料\20公尺\全國DTM-20\
        twdtm20m_float.img`（Float32、20 m 網格、TWD97 TM2、高程公尺；製作 2010 前）
  驗證：NLSC WMTS `MOI_SLOPEP_GT30_2`（坡度圖-30%坡 2010-2015）

坡度分級（台灣七級，百分比坡度＝rise/run×100）
  1級 <5%｜2級 5~15%｜3級 15~30%｜4級 30~40%｜5級 40~55%｜6級 55~100%｜7級 >100%
  總編§16 三段：<30% 可開發／30~40% 限開放性設施／**≥40% 不可開發區**

★★ 兩個必須誠實交代的口徑（交付文字照寫）
  1. **方法差異**：條文的判定單位是**坵塊圖平均坡度**（坵塊法）。本腳本是
     **DEM 網格坡度**（Horn 演算法，gdaldem），屬業界通用替代法，
     **不等同坵塊法、也不得取代實測地形測量**；正式送審仍以實測地形圖為準。
  2. **DEM 年份與解析度**：20 公尺網格、資料早於 2010，近年整地開挖不會反映；
     20 m 網格會**平滑掉小尺度陡坡**，細部設計須用更高解析度或實測。

用法（**須用 QGIS 內建 python**，要 osgeo）
    .\\scripts\\qgis_python.ps1 scripts\\slope_check.py \\
        --joined <基地.shp> --out <案>\\坡度分級 [--sitemap <sitemap.csv>] \\
        [--dem <自訂DTM>] [--no-verify]
輸出：坡度分級.csv／.json、坡度_DEM.tif、坡度_percent.tif、（驗證時）坡度_GT30.tif
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_loader import resource_path

DEM_DEFAULT = str(resource_path('dem_path'))
GT30 = "MOI_SLOPEP_GT30_2"
PAD = 200.0          # DEM 裁切外擴（公尺），避免邊界像元坡度算不出來

# (級別, 下界%, 上界%, 標籤)
CLASSES = [(1, -1e9, 5, "<5%"), (2, 5, 15, "5~15%"), (3, 15, 30, "15~30%"),
           (4, 30, 40, "30~40%"), (5, 40, 55, "40~55%"),
           (6, 55, 100, "55~100%"), (7, 100, 1e9, ">100%")]
GROUP = {"可開發（<30%）": (1, 2, 3),
         "限開放性設施（30~40%）": (4,),
         "不可開發區（≥40%）": (5, 6, 7)}


def _rasterize(shp, ref_ds, where=None):
    """把 shp 燒成與 ref_ds 同網格的 0/1 遮罩（自動投影轉換）。"""
    from osgeo import gdal, ogr, osr
    src = ogr.Open(shp)
    lyr = src.GetLayer()
    if where:
        lyr.SetAttributeFilter(where)
    dst = osr.SpatialReference(wkt=ref_ds.GetProjection())
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    s = lyr.GetSpatialRef()
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    ct = osr.CoordinateTransformation(s, dst)
    mds = ogr.GetDriverByName("MEM").CreateDataSource("m")
    mlyr = mds.CreateLayer("m", dst, ogr.wkbPolygon)
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
    # ALL_TOUCHED：小宗地不會因為沒蓋到格心而整筆消失
    gdal.RasterizeLayer(mem, [1], mlyr, burn_values=[1],
                        options=["ALL_TOUCHED=TRUE"])
    return mem.ReadAsArray()


def clip_slope_raster(slope_tif: str, site_shp: str, out_tif: str) -> str:
    """把 `坡度_percent.tif` 裁到基地範圍供**出圖**用，遮罩方法與本檔統計數字
    完全同一套（呼叫同一支 `_rasterize`，ALL_TOUCHED=TRUE），**不用 `gdal.Warp`
    cutline**。

    ★2026-09-14 內湖保護區案實錯：`make_extra_maps.py --theme 坡度` 與
    `make_hazard_map.py` 原本各自用 `gdal.Warp(..., cutlineDSName=..., cropToCutline=
    True)` 裁切圖面——這是另一套遮罩方法（cutline 重新計算輸出網格，預設用像元
    中心是否落在多邊形內判斷去留），跟本檔算統計數字用的 ALL_TOUCHED 網格法**不是
    同一份遮罩**。本案 42 個像元（Warp cutline）vs 70 個像元（ALL_TOUCHED）：
    差的 28 個像元裡剛好包含全案**唯一一個 30~40% 像元**（30.9%），圖上因此
    「整區看起來都<30%」，跟 `坡度分級.json` 寫的「30~40% 佔 1.43%」對不上，
    使用者一眼就看出圖表不符。加 `warpOptions=["CUTLINE_ALL_TOUCHED=TRUE"]` 測過
    仍差 7 個像元（Warp 對 cutline 的柵格化與本檔手動 `RasterizeLayer` 演算法不保證
    位元對位元一致）——不要再嘗試調校 Warp 參數去逼近，**直接不重新投影／不重算
    網格，用本檔的 `_rasterize` 把同一份遮罩燒在 `坡度_percent.tif` 原始網格上**，
    保證圖與表用的是同一個像元集合，不會再打架。
    """
    from osgeo import gdal
    import numpy as np
    sd = gdal.Open(slope_tif)
    if sd is None:
        raise SystemExit(f"讀取失敗：{slope_tif}")
    slope = sd.ReadAsArray().astype(np.float32)
    mask = _rasterize(site_shp, sd)
    if mask is None:
        raise SystemExit(f"基地無法燒成遮罩：{site_shp}")
    nod = -9999.0
    out = np.where(mask == 1, slope, nod).astype(np.float32)
    drv = gdal.GetDriverByName("GTiff")
    dst = drv.Create(out_tif, sd.RasterXSize, sd.RasterYSize, 1, gdal.GDT_Float32)
    dst.SetGeoTransform(sd.GetGeoTransform())
    dst.SetProjection(sd.GetProjection())
    b = dst.GetRasterBand(1)
    b.SetNoDataValue(nod)
    b.WriteArray(out)
    b.FlushCache()
    dst = None
    return out_tif


def _stats(slope, sel):
    """回傳 (逐級 list, 分組 dict, 有效像元數, 坡度統計)。"""
    n = int(sel.sum())
    rows, by = [], {}
    for k, lo, hi, lab in CLASSES:
        m = sel & (slope >= lo) & (slope < hi)
        c = int(m.sum())
        by[k] = c
        rows.append({"級別": k, "坡度": lab, "像元": c,
                     "占比pct": round(c / n * 100, 2) if n else 0.0})
    grp = {name: round(sum(by[k] for k in ks) / n * 100, 2) if n else 0.0
           for name, ks in GROUP.items()}
    v = slope[sel]
    st = {"最小": round(float(v.min()), 1), "中位": round(float(np.median(v)), 1),
          "平均": round(float(v.mean()), 1), "最大": round(float(v.max()), 1)}
    return rows, grp, n, st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True, help="基地 shp")
    ap.add_argument("--out", required=True, help="輸出資料夾")
    ap.add_argument("--dem", default=DEM_DEFAULT, help="DTM 檔（預設全國 20m DTM）")
    ap.add_argument("--sites", default=None, help="案場 shp（逐案場統計，需 site_id 欄）")
    ap.add_argument("--no-verify", action="store_true", help="不做 GT30 交叉驗證")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    import geopandas as gpd
    from osgeo import gdal
    from shapely.ops import unary_union
    gdal.UseExceptions()

    g = gpd.read_file(a.joined)
    g = g.to_crs(3826) if g.crs else g.set_crs(3826)
    su = unary_union(g.geometry.tolist())
    x0, y0, x1, y1 = su.bounds
    print(f"基地 {len(g)} 筆、dissolve 面積 {su.area:,.2f} ㎡ = {su.area/10000:.4f} 公頃")

    if not os.path.exists(a.dem):
        raise SystemExit(f"✖ 找不到 DEM：{a.dem}（Z: 或 NAS 未掛載？）")
    dem_clip = os.path.join(a.out, "坡度_DEM.tif")
    gdal.Translate(dem_clip, a.dem,
                   projWin=[x0 - PAD, y1 + PAD, x1 + PAD, y0 - PAD],
                   projWinSRS="EPSG:3826", outputType=gdal.GDT_Float32)
    dd = gdal.Open(dem_clip)
    dem = dd.ReadAsArray().astype(np.float32)
    gtr = dd.GetGeoTransform()
    print(f"DEM 裁切 {dd.RasterXSize}x{dd.RasterYSize}、像元 {gtr[1]:.1f} m"
          f"、高程 {dem.min():.1f}~{dem.max():.1f} m")

    slope_tif = os.path.join(a.out, "坡度_percent.tif")
    gdal.DEMProcessing(slope_tif, dem_clip, "slope",
                       slopeFormat="percent", alg="Horn", computeEdges=True)
    sd = gdal.Open(slope_tif)
    slope = sd.ReadAsArray().astype(np.float32)
    nod = sd.GetRasterBand(1).GetNoDataValue()
    ok = np.isfinite(slope) & (slope >= 0)
    if nod is not None:
        ok &= (slope != nod)

    mask = _rasterize(a.joined, sd)
    if mask is None:
        raise SystemExit("✖ 基地無法燒成遮罩")
    sel = (mask == 1) & ok
    if not sel.any():
        raise SystemExit("✖ 基地範圍內無有效坡度像元")
    px_m2 = abs(gtr[1] * gtr[5])
    rows, grp, npix, st = _stats(slope, sel)
    # ★ 面積**不可**用「像元數 × 像元面積」交付：20 m 網格＋ALL_TOUCHED 會把邊界格
    #   整格算進來，本案多算 17%（606,426 vs 518,752 ㎡）。
    #   **占比才是網格法可信的產物**；面積一律用「占比 × 權威面積」回推。
    #   （交付的權威面積是登記面積；此處先用 dissolve 幾何面積，簡報再換算成登記面積。）
    ref_area = su.area
    print(f"基地內有效像元 {npix:,}（網格粗算 {npix*px_m2:,.0f} ㎡＝dissolve 的 "
          f"{npix*px_m2/ref_area*100:.0f}%，ALL_TOUCHED 邊界溢出）")
    print(f"→ **面積以「占比 × {ref_area:,.0f} ㎡」回推**，不用像元數乘算")
    print(f"坡度：最小 {st['最小']}%／中位 {st['中位']}%／平均 {st['平均']}%／"
          f"最大 {st['最大']}%")

    print(f"\n{'級':<3}{'坡度':<10}{'面積㎡':>12}{'占比':>8}")
    for r in rows:
        r["面積m2"] = round(r["占比pct"] / 100 * ref_area, 2)
        print(f"{r['級別']:<3}{r['坡度']:<10}{r['面積m2']:>12,.0f}{r['占比pct']:>7.2f}%")
    print()
    for name, pct in grp.items():
        print(f"  {name:<24}{pct / 100 * ref_area:>12,.0f} ㎡{pct:>7.2f}%")

    # 逐案場
    persite = {}
    if a.sites and os.path.exists(a.sites):
        sg = gpd.read_file(a.sites)
        idc = next((c for c in sg.columns if c.lower() in ("site_id", "案場id")), None)
        if idc:
            print(f"\n逐案場（{idc}）：")
            for sid in sorted(sg[idc].astype(str).unique()):
                m = _rasterize(a.sites, sd, where=f"\"{idc}\" = '{sid}'")
                if m is None:
                    continue
                s2 = (m == 1) & ok
                if not s2.any():
                    continue
                _r, _g, _n, _s = _stats(slope, s2)
                persite[sid] = {"像元": _n, "面積m2": round(_n * px_m2, 2),
                                "坡度統計": _s, "分段pct": _g}
                print(f"  {sid}　{_n*px_m2:>9,.0f} ㎡　中位 {_s['中位']:>5.1f}%　"
                      + "／".join(f"{k.split('（')[0]} {v:.1f}%" for k, v in _g.items()))

    # 交叉驗證：NLSC GT30（>30% 二值圖）
    ver = None
    if not a.no_verify:
        try:
            from nlsc_wmts import build_basemap
            gt30_tif = os.path.join(a.out, "坡度_GT30.tif")
            if build_basemap((x0, y0, x1, y1), gt30_tif, layer=GT30, keep_alpha=True,
                             cache_dir=os.path.join(a.out, "_tiles"),
                             buffer_ratio=0.03):
                d2 = gdal.Open(gt30_tif)
                aa = d2.ReadAsArray()
                c3 = aa[:3].astype(np.int16)
                op = (aa[3] >= 128) if aa.shape[0] >= 4 else np.ones(c3.shape[1:], bool)
                red = op & (c3[0] > 120) & (c3[1] < 90) & (c3[2] < 90)
                m2 = _rasterize(a.joined, d2)
                s2 = (m2 == 1)
                # ⚠ 分母＝基地全部像元。GT30 只畫「>30%」的範圍，用不透明像元當分母
                #   會恆得 100%（2026-08-02 實踩）。
                gt30 = float(red[s2].sum()) / float(s2.sum()) * 100
                dem30 = 100.0 - grp["可開發（<30%）"]
                ver = {"GT30_gt30_pct": round(gt30, 2),
                       "DEM_gt30_pct": round(dem30, 2),
                       "差_百分點": round(dem30 - gt30, 2),
                       "通過": bool(abs(dem30 - gt30) <= 10)}
                print(f"\n[交叉驗證] NLSC GT30 圖層 >30% 占比 {gt30:.2f}%／"
                      f"本次 DEM 實算 {dem30:.2f}%　差 {dem30 - gt30:+.2f} 個百分點")
                print("  " + ("✓ 兩個獨立來源一致（差 ≤10 個百分點）"
                              if ver["通過"] else
                              "⚠ 差異偏大：DEM 為 20m 網格會平滑陡坡、GT30 為 2010-2015 "
                              "渲染圖，兩者本有差距；交付請以 DEM 實算為主並註明此差異"))
        except Exception as e:  # noqa: BLE001
            print(f"\n[交叉驗證] 略過（{type(e).__name__}: {e}）")

    res = {"基地_dissolve_m2": round(su.area, 2), "有效像元": npix,
           "像元邊長_m": round(math.sqrt(px_m2), 2),
           "DEM": a.dem, "方法": "gdaldem slope percent（Horn）",
           "坡度統計pct": st, "分級": rows, "總編§16分段pct": grp,
           "逐案場": persite, "交叉驗證": ver,
           "口徑": ["條文判定單位為坵塊圖平均坡度（坵塊法）；本成果為 DEM 網格坡度，"
                    "屬業界通用替代法，不等同坵塊法、不得取代實測地形測量",
                    "DEM 為 20 公尺網格且資料早於 2010，近年整地開挖不會反映；"
                    "20m 網格會平滑掉小尺度陡坡"]}
    io.open(os.path.join(a.out, "坡度分級.json"), "w", encoding="utf-8").write(
        json.dumps(res, ensure_ascii=False, indent=2))
    with io.open(os.path.join(a.out, "坡度分級.csv"), "w",
                 encoding="utf-8-sig", newline="") as fh:
        fh.write("級別,坡度,面積m2,占比pct\n")
        for r in rows:
            fh.write(f"{r['級別']},{r['坡度']},{r['面積m2']:.2f},{r['占比pct']:.2f}\n")
        for k, v in grp.items():
            fh.write(f"[總編§16] {k},,{v * npix * px_m2 / 100:.2f},{v:.2f}\n")
    print(f"\n→ {a.out}\\坡度分級.csv / .json、坡度_percent.tif")
    print("⚠ 交付口徑：條文用坵塊法，本成果為 DEM 網格坡度之替代算法，"
          "不得取代實測地形測量；DEM 為 20m 網格、資料早於 2010。")


if __name__ == "__main__":
    main()
