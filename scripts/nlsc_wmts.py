# -*- coding: utf-8 -*-
"""NLSC WMTS 圖磚下載＋拼接成 GeoTIFF（EPSG:3857）。

取代 QGIS 內建 WMS/WMTS provider（會卡住/逾時）的做法，改為直接以 HTTP 抓
GoogleMapsCompatible 圖磚後拼接，含重試與空磚處理。作法沿用先前 LANDUSE 專案
work/build_mosaic.py 的驗證過流程。

用法：
    from nlsc_wmts import build_basemap
    tif = build_basemap((xmin, ymin, xmax, ymax), out_tif, layer="PHOTO2")
    # 輸入 extent 為 EPSG:3826（TWD97/TM2）；輸出 GeoTIFF 為 EPSG:3857。

NLSC 圖層：PHOTO2＝通用版正射影像、EMAP＝臺灣通用電子地圖。
"""
from __future__ import annotations
import os
import time
import urllib.request

import numpy as np
from osgeo import gdal, osr

gdal.UseExceptions()

WMTS_TILE = "https://wmts.nlsc.gov.tw/wmts/{layer}/default/GoogleMapsCompatible/{z}/{row}/{col}"
# ★ 坡度圖圖層（2026-08-02 加入，總編§16 不可開發區判定用）：
#   MOI_SLOPEP_LV7_2  坡度圖-**7級** 2010-2015（png）← 要分 30~40% 與 ≥40% 只能用這張
#   MOI_SLOPEP_GT30_2 坡度圖-30%坡 2010-2015（jpeg）← 只有單一 >30% 門檻
#   舊期：MOI_SLOPEP_LV7／MOI_SLOPEP_GT30（2003-2005）
# ⚠ **GetCapabilities 掛在 maps.nlsc.gov.tw/S_Maps/wmts，但圖磚 template 指回
#   wmts.nlsc.gov.tw** ——與既有 PHOTO2／EMAP 同一台，直接用 WMTS_TILE 即可，
#   不必換主機也不需 Referer（2026-08-02 誤以為要換主機而白繞一次）。
#   要查有哪些圖層才去 maps.nlsc.gov.tw 讀 capabilities。
SMAPS_CAPS = "https://maps.nlsc.gov.tw/S_Maps/wmts?SERVICE=WMTS&REQUEST=GetCapabilities"
SMAPS_REFERER = "https://easymap.moi.gov.tw/"   # 讀 capabilities 時帶著較保險
ORIGIN = 20037508.342789244          # web mercator 半幅（公尺）
_HEADERS = {"User-Agent": "Mozilla/5.0 (QGIS GDAL tile fetch)"}
MAX_TILES = 64                        # 單張圖磚數上限（自動挑 zoom）


def _is_image(blob):
    """魔術位元組驗證：PNG／JPEG／GIF 才算圖磚。

    伺服器打錯路徑時常回「HTTP 200 ＋ HTML 錯誤頁」，不驗就會當成圖磚。"""
    return bool(blob) and (blob[:8] == b"\x89PNG\r\n\x1a\n"
                           or blob[:2] == b"\xff\xd8"
                           or blob[:3] == b"GIF")


def _tf_3826_to_3857(xmin, ymin, xmax, ymax):
    s = osr.SpatialReference(); s.ImportFromEPSG(3826)
    d = osr.SpatialReference(); d.ImportFromEPSG(3857)
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    d.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    ct = osr.CoordinateTransformation(s, d)
    pts = [ct.TransformPoint(x, y)[:2]
           for x, y in ((xmin, ymin), (xmin, ymax), (xmax, ymin), (xmax, ymax))]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _pick_zoom(xmin, ymin, xmax, ymax):
    """在圖磚數 <= MAX_TILES 前提下取最大 zoom（越大越清晰）。"""
    for z in range(20, 12, -1):
        tile_m = 2 * ORIGIN / (2 ** z)
        ncol = int((xmax + ORIGIN) / tile_m) - int((xmin + ORIGIN) / tile_m) + 1
        nrow = int((ORIGIN - ymin) / tile_m) - int((ORIGIN - ymax) / tile_m) + 1
        if ncol * nrow <= MAX_TILES:
            return z
    return 16


def build_basemap(extent_3826, out_tif, layer="PHOTO2", z=None,
                  cache_dir=None, buffer_ratio=0.0, keep_alpha=False,
                  tile_tpl=None, referer=None):
    """下載涵蓋 extent 的 NLSC 圖磚拼成 GeoTIFF（EPSG:3857）。

    extent_3826: (xmin, ymin, xmax, ymax) in EPSG:3826
    keep_alpha=True：輸出 4 波段 RGBA（供背景透明的疊圖層，如 EMAP2 通用電子地圖透明版；
        未取得的區域 alpha=0 透明）。keep_alpha=False：輸出 3 波段、白底。
    tile_tpl／referer：改抓另一台主機用。**maps.nlsc.gov.tw/S_Maps 必須帶 Referer**，
        不帶會回 0 bytes（坡度圖走這台）：
            build_basemap(ext, tif, layer="MOI_SLOPEP_LV7_2",
                          tile_tpl=SMAPS_TILE, referer=SMAPS_REFERER)
    回傳 out_tif 路徑；全部圖磚皆抓不到時回傳 None。
    """
    tpl = tile_tpl or WMTS_TILE
    hdrs = dict(_HEADERS)
    if referer:
        hdrs["Referer"] = referer
    xmin, ymin, xmax, ymax = extent_3826
    if buffer_ratio:
        dx = (xmax - xmin) * buffer_ratio; dy = (ymax - ymin) * buffer_ratio
        xmin -= dx; xmax += dx; ymin -= dy; ymax += dy
    mxmin, mymin, mxmax, mymax = _tf_3826_to_3857(xmin, ymin, xmax, ymax)
    if z is None:
        z = _pick_zoom(mxmin, mymin, mxmax, mymax)

    tile_m = 2 * ORIGIN / (2 ** z)
    col_min = int((mxmin + ORIGIN) / tile_m); col_max = int((mxmax + ORIGIN) / tile_m)
    row_min = int((ORIGIN - mymax) / tile_m); row_max = int((ORIGIN - mymin) / tile_m)
    ncol = col_max - col_min + 1; nrow = row_max - row_min + 1
    W, H = ncol * 256, nrow * 256
    pix = tile_m / 256.0
    ox = col_min * tile_m - ORIGIN
    oy = ORIGIN - row_min * tile_m
    nb = 4 if keep_alpha else 3
    if keep_alpha:
        mosaic = np.zeros((4, H, W), dtype=np.uint8)   # 全透明底
    else:
        mosaic = np.full((3, H, W), 255, dtype=np.uint8)  # 白底

    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    ok = 0
    for r in range(row_min, row_max + 1):
        for c in range(col_min, col_max + 1):
            blob = None
            fn = os.path.join(cache_dir, f"{layer}_{z}_{r}_{c}.png") if cache_dir else None
            if fn and os.path.exists(fn) and os.path.getsize(fn) > 100:
                with open(fn, "rb") as f:
                    blob = f.read()
                # ⚠ 只看檔案大小會被「HTTP 200 的 HTML 錯誤頁」污染快取，而且是**永久性**的：
                #   之後每次重跑都讀到那份 HTML、gdal 開不起來、整張圖靜默變成 None
                #   （2026-08-02 實踩：圖磚主機打錯時伺服器回 200＋HTML，
                #    之後改對主機仍一直「抓不到圖磚」，查了三輪才發現是快取）。
                #   → 一律驗魔術位元組，不合就丟掉重抓。
                if not _is_image(blob):
                    blob = None
                    try:
                        os.remove(fn)
                    except OSError:
                        pass
            if blob is None:
                u = tpl.format(layer=layer, z=z, row=r, col=c)
                for attempt in range(3):
                    try:
                        rq = urllib.request.Request(u, headers=hdrs)
                        blob = urllib.request.urlopen(rq, timeout=30).read()
                        if not _is_image(blob):      # 200＋HTML 錯誤頁，不可入快取
                            blob = None
                            break
                        if fn:
                            with open(fn, "wb") as f:
                                f.write(blob)
                        break
                    except Exception:
                        blob = None
                        if attempt < 2:
                            time.sleep(1)
            if not blob or blob[:3] == b"GIF":
                continue
            try:
                mem = "/vsimem/nlsc_t.png"
                gdal.FileFromMemBuffer(mem, blob)
                t = gdal.Open(mem)
                arr = t.ReadAsArray()
                gdal.Unlink(mem)
                if arr is None:
                    continue
                if arr.ndim == 2:
                    arr = np.stack([arr] * 3)
                y0 = (r - row_min) * 256; x0 = (c - col_min) * 256
                if keep_alpha:
                    nbnd = arr.shape[0]
                    rgb = arr[:3]
                    if nbnd >= 4:
                        a = arr[3]
                    else:
                        a = np.full((256, 256), 255, dtype=np.uint8)
                    mosaic[:3, y0:y0 + 256, x0:x0 + 256] = rgb
                    mosaic[3, y0:y0 + 256, x0:x0 + 256] = a
                else:
                    mosaic[:, y0:y0 + 256, x0:x0 + 256] = arr[:3]
                ok += 1
            except Exception:
                continue

    if ok == 0:
        return None
    drv = gdal.GetDriverByName("GTiff")
    out = drv.Create(out_tif, W, H, nb, gdal.GDT_Byte,
                     options=["COMPRESS=DEFLATE", "TILED=YES"])
    out.SetGeoTransform((ox, pix, 0, oy, 0, -pix))
    srs = osr.SpatialReference(); srs.ImportFromEPSG(3857)
    out.SetProjection(srs.ExportToWkt())
    for b in range(nb):
        out.GetRasterBand(b + 1).WriteArray(mosaic[b])
    if keep_alpha:
        out.GetRasterBand(4).SetColorInterpretation(gdal.GCI_AlphaBand)
    out.FlushCache()
    out = None
    return out_tif
