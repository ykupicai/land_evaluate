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
ORIGIN = 20037508.342789244          # web mercator 半幅（公尺）
_HEADERS = {"User-Agent": "Mozilla/5.0 (QGIS GDAL tile fetch)"}
MAX_TILES = 64                        # 單張圖磚數上限（自動挑 zoom）


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
                  cache_dir=None, buffer_ratio=0.0):
    """下載涵蓋 extent 的 NLSC 圖磚拼成 GeoTIFF（EPSG:3857）。

    extent_3826: (xmin, ymin, xmax, ymax) in EPSG:3826
    回傳 out_tif 路徑；全部圖磚皆抓不到時回傳 None。
    """
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
    mosaic = np.full((3, H, W), 255, dtype=np.uint8)

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
            if blob is None:
                u = WMTS_TILE.format(layer=layer, z=z, row=r, col=c)
                for attempt in range(3):
                    try:
                        rq = urllib.request.Request(u, headers=_HEADERS)
                        blob = urllib.request.urlopen(rq, timeout=30).read()
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
                mosaic[:, y0:y0 + 256, x0:x0 + 256] = arr[:3]
                ok += 1
            except Exception:
                continue

    if ok == 0:
        return None
    drv = gdal.GetDriverByName("GTiff")
    out = drv.Create(out_tif, W, H, 3, gdal.GDT_Byte,
                     options=["COMPRESS=DEFLATE", "TILED=YES"])
    out.SetGeoTransform((ox, pix, 0, oy, 0, -pix))
    srs = osr.SpatialReference(); srs.ImportFromEPSG(3857)
    out.SetProjection(srs.ExportToWkt())
    for b in range(3):
        out.GetRasterBand(b + 1).WriteArray(mosaic[b])
    out.FlushCache()
    out = None
    return out_tif
