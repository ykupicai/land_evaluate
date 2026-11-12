# -*- coding: utf-8 -*-
"""產線內建臨路計算(取代外部 fishpv 依賴,2026-07-26 內建化)。

只做產線 `--no-ai` 實際用到的那條鏈路,自 fishpv 忠實移植、門檻與分類邏輯完全一致:
  國土利用 LUIMAP 圖磚 → 最近色分類向量化 → 逐宗基地臨路三態＋地形組成 →
  輸出 成果.xlsx / sites.shp / features.geojson(格式與 fishpv 相同,make_maps 圖6 不用改)。

與 fishpv 的差異(刻意):**案場定義吃產線權威 sitemap 分組**(build_sites_sitemap 產出),
不再自行聚類——案場幾何與 make_maps 出圖用的完全一致,根除「聚類真相在外部工具」的分歧。
無 QGIS、無 AI;相依 geopandas/shapely/numpy/osgeo/PIL/pyproj(大量圖資轉換工具 既有)。

用法:
  python road_access.py --joined <面積join.shp> --sitemap <案場定義/sitemap.csv> \
      --out <案>\臨路評估 [--colortable <csv>] [--reuse-landuse <既有landuse.tif>]
"""
import argparse
import csv
import math
import io
import os
import shutil
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import geopandas as gpd
import pandas as pd
import shapely
import shapely.wkt
from PIL import Image
from pyproj import Transformer
from osgeo import gdal, ogr, osr

sys.path.insert(0, str(Path(__file__).parent))
from shp_trust_utils import load_sitemap, assign_site  # noqa: E402

gdal.UseExceptions()

# ── 參數(自 fishpv config.py 移植,數值不可動:改了臨路判定就變) ──────────────
WMTS_LANDUSE = "https://wmts.nlsc.gov.tw/wmts/LUIMAP/default/GoogleMapsCompatible/{z}/{row}/{col}"
ORIGIN = 20037508.342789244
_HDR = {"User-Agent": "Mozilla/5.0 (road_access tile fetch)"}


@dataclass
class Cfg:
    buffer_m: float = 30.0        # 拉圖資外擴
    touch_tol: float = 0.5        # 直接臨路距離
    bridge_max: float = 1.5       # 需搭橋臨路上限
    crossing_margin: float = 20.0
    land_flag_ha: float = 2.0
    target_crs: str = "EPSG:3826"
    tile_zoom: int = 18
    sieve: int = 8
    # ── 路寬量測(2026-07-28 新增;純加欄位,不影響臨路三態判定) ──
    width_sample: float = 5.0     # 沿基地邊界取樣間距 m
    width_search: float = 60.0    # 法向射線搜尋長度 m
    width_gap: float = 8.0        # 取樣點到路緣容許間隔 m(超過視為該點未鄰接道路)


# ── 柵格 I/O(自 raster.py 移植) ─────────────────────────────────────────────
def _write_rgb_geotiff(path, arr, epsg, ox, oy, pix):
    _, H, W = arr.shape
    ds = gdal.GetDriverByName("GTiff").Create(path, W, H, 3, gdal.GDT_Byte,
                                              options=["COMPRESS=DEFLATE"])
    ds.SetGeoTransform((ox, pix, 0, oy, 0, -pix))
    srs = osr.SpatialReference(); srs.ImportFromEPSG(epsg)
    ds.SetProjection(srs.ExportToWkt())
    for b in range(3):
        ds.GetRasterBand(b + 1).WriteArray(arr[b])
    ds.FlushCache()
    return path


def _read_rgb(path):
    ds = gdal.Open(path)
    gt = ds.GetGeoTransform(); proj = ds.GetProjection(); arr = ds.ReadAsArray()
    srs = osr.SpatialReference(wkt=proj)
    code = srs.GetAuthorityCode(None)
    epsg = int(code) if code else None
    if arr.ndim == 2:
        arr = np.stack([arr] * 3)
    rgb = np.transpose(arr[:3], (1, 2, 0)).astype(np.uint8)
    return rgb, gt, proj, epsg


def _polygonize_classes(cls, gt, proj, sieve=0):
    H, W = cls.shape
    mem = gdal.GetDriverByName("MEM").Create("", W, H, 1, gdal.GDT_Int32)
    mem.SetGeoTransform(gt); mem.SetProjection(proj)
    band = mem.GetRasterBand(1)
    band.WriteArray(cls.astype(np.int32)); band.SetNoDataValue(0)
    if sieve and sieve > 0:
        gdal.SieveFilter(band, None, band, threshold=sieve, connectedness=8)
    vds = ogr.GetDriverByName("Memory").CreateDataSource("out")
    srs = osr.SpatialReference(wkt=proj)
    lyr = vds.CreateLayer("polys", srs=srs, geom_type=ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn("classid", ogr.OFTInteger))
    gdal.Polygonize(band, band, lyr, 0, [], callback=None)
    out = []
    for feat in lyr:
        cid = feat.GetField("classid")
        if cid is None or cid < 1:
            continue
        g = feat.GetGeometryRef()
        if g is None:
            continue
        geom = shapely.wkt.loads(g.ExportToWkt())
        if not geom.is_valid:
            geom = shapely.make_valid(geom)
        out.append((geom, int(cid)))
    return out


# ── WMTS 圖磚(自 fetch_wmts.py 移植) ────────────────────────────────────────
def _bbox_3857(gdf, buffer_m):
    geom = gdf.geometry.union_all()
    minx, miny, maxx, maxy = geom.bounds
    minx -= buffer_m; miny -= buffer_m; maxx += buffer_m; maxy += buffer_m
    tf = Transformer.from_crs(gdf.crs.to_string(), "EPSG:3857", always_xy=True)
    x0, y0 = tf.transform(minx, miny); x1, y1 = tf.transform(maxx, maxy)
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


# 缺磚像元的哨兵色(不在 LUIMAP 色表內);向量化時視為無資料、不參與最近色分類
NODATA_RGB = (1, 2, 3)


def _fetch_landuse_mosaic(bbox3857, zoom, out_tif):
    """相容舊呼叫:只回 tif 路徑。缺磚資訊請用 _fetch_landuse_mosaic_ex。"""
    return _fetch_landuse_mosaic_ex(bbox3857, zoom, out_tif)[0]


def _fetch_landuse_mosaic_ex(bbox3857, zoom, out_tif):
    """下載國土利用圖磚並拼接。回傳 (tif 路徑, 缺磚 bbox 清單[EPSG:3857])。

    單張重試 3 次仍失敗(例外/逾時/HTTP 錯誤)或內容無法解碼者列為缺磚:畫布以哨兵色
    (NODATA_RGB)填滿,不留白色(白色會被最近色分到「其他」,道路面消失而誤判不臨路)。
    GIF/0 bytes 空磚是伺服器正常的無資料回應,不算缺磚,畫布維持白色(同修前)。"""
    xmin, ymin, xmax, ymax = bbox3857
    tile_m = 2 * ORIGIN / (2 ** zoom)
    cmin = int((xmin + ORIGIN) / tile_m); cmax = int((xmax + ORIGIN) / tile_m)
    rmin = int((ORIGIN - ymax) / tile_m); rmax = int((ORIGIN - ymin) / tile_m)
    ncol, nrow = cmax - cmin + 1, rmax - rmin + 1
    W, H = ncol * 256, nrow * 256
    ox = cmin * tile_m - ORIGIN; oy = ORIGIN - rmin * tile_m; pix = tile_m / 256.0
    canvas = np.full((3, H, W), 255, np.uint8)
    missing = []
    for r in range(rmin, rmax + 1):
        for c in range(cmin, cmax + 1):
            blob = None
            failed = False          # 真失敗:例外/逾時/非 200 重試 3 次仍失敗
            url = WMTS_LANDUSE.format(z=zoom, row=r, col=c)
            for att in range(3):
                try:
                    blob = urllib.request.urlopen(
                        urllib.request.Request(url, headers=_HDR), timeout=30).read()
                    failed = False
                    break
                except Exception:
                    if att == 2:
                        blob = None
                        failed = True
                    else:
                        time.sleep(1)
            y0 = (r - rmin) * 256; x0 = (c - cmin) * 256
            arr = None
            # 口徑與 nlsc_wmts.build_basemap 一致:GIF 空磚與 0 bytes 空回應是伺服器的
            # 正常「無資料」回應(外海、境外),不算缺磚、維持修前行為(留白色);
            # 只有例外/逾時/HTTP 錯誤、回 HTML 或解碼失敗才算缺磚(哨兵色+旗標)。
            if not failed:
                if not blob or blob[:3] == b"GIF":
                    continue
                try:
                    arr = np.asarray(Image.open(io.BytesIO(blob)).convert("RGB"))
                    if arr.shape[:2] != (256, 256):
                        arr = None
                except Exception:
                    arr = None
            if arr is None:
                for b in range(3):
                    canvas[b, y0:y0 + 256, x0:x0 + 256] = NODATA_RGB[b]
                bx0 = ox + (c - cmin) * tile_m
                by1 = oy - (r - rmin) * tile_m
                missing.append((bx0, by1 - tile_m, bx0 + tile_m, by1))
                continue
            canvas[:, y0:y0 + 256, x0:x0 + 256] = np.transpose(arr, (2, 0, 1))
    if missing:
        print(f"警告:國土利用圖磚缺漏 {len(missing)}/{ncol * nrow} 塊(重試仍失敗或無法解碼),"
              f"缺磚處不參與分類;與基地外擴範圍相交的案場臨路狀態會標「須重跑」",
              file=sys.stderr)
    return _write_rgb_geotiff(out_tif, canvas, 3857, ox, oy, pix), missing


def missing_polys_in_crs(missing, target_crs):
    """缺磚 bbox(EPSG:3857) → 目標座標系的多邊形清單(取四角外包矩形)。"""
    if not missing:
        return []
    tf = Transformer.from_crs("EPSG:3857", target_crs, always_xy=True)
    out = []
    for x0, y0, x1, y1 in missing:
        pts = [tf.transform(x, y) for x, y in ((x0, y0), (x0, y1), (x1, y0), (x1, y1))]
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        out.append(shapely.box(min(xs), min(ys), max(xs), max(ys)))
    return out


# ── 國土利用向量化(自 landuse.py 移植;關鍵字對應不可動) ─────────────────────
# ⚠ 2026-08-06 修正：「國道」「省道」「縣道」「市道」「鄉道」不含「道路」二字，
#   原關鍵字漏接，導致lu_code 030501/030502 等被誤歸「其他」，臨路距離算法
#   完全跳過這些道路（善化善南段案東側緊鄰省道卻判「不臨路」，經使用者提出
#   東側應有鄰路才查出）。使用者 2026-08-06 同意修正，非隨意調整既有判準。
_KEYWORD_MAP = [
    (("太陽", "光電"), "太陽光電"),
    (("養殖", "魚塭"), "魚塭"),
    # ★ 2026-10-20 使用者裁示「鐵路不算道路」:一般鐵路(0302xx)、高速鐵路(0303xx)獨立成
    #   「鐵路」類,不參與臨路判定與路寬量測(阿里山配置範圍案)。須排在「道路」之前。
    #   「交通」也已移出道路關鍵字——03「交通利用土地」是父類(含機場/鐵路/港口),不是道路。
    (("鐵路",), "鐵路"),
    (("道路", "公路", "國道", "省道", "縣道", "市道", "鄉道", "街", "巷"), "道路"),
    (("建築", "房屋", "建物", "住宅", "工業", "廠房", "商業"), "建物"),
    (("河", "渠", "水域", "水利", "埤", "陂", "塘", "池", "海"), "水道"),
    (("農", "田", "果", "蔗", "旱", "作物", "耕"), "農田"),
]


def _map_category(lu_name):
    name = lu_name or ""
    for keys, cat in _KEYWORD_MAP:
        if any(k in name for k in keys):
            return cat
    return "其他"


def _vectorize_landuse(mosaic_tif, colortable_csv, target_crs, sieve):
    codes, names, rgbs = [], [], []
    with open(colortable_csv, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            codes.append(row["lu_code"]); names.append(row["lu_name"])
            rgbs.append((int(row["R"]), int(row["G"]), int(row["B"])))
    pal = np.array(rgbs, dtype=np.int32)
    rgb, gt, proj, epsg = _read_rgb(mosaic_tif)
    H, W = rgb.shape[0], rgb.shape[1]
    flat = rgb.reshape(-1, 3).astype(np.int32)
    uniq, inv = np.unique(flat, axis=0, return_inverse=True)
    dist = ((uniq[:, None, :] - pal[None, :, :]) ** 2).sum(2)
    nearest = dist.argmin(1).astype(np.int32) + 1
    cls = nearest[inv].reshape(H, W)
    # 缺磚哨兵色 → 無資料(0),polygonize 會略過,不參與分類
    cls[(rgb == np.array(NODATA_RGB, dtype=np.uint8)).all(axis=2)] = 0
    feats = []
    for geom, cid in _polygonize_classes(cls, gt, proj, sieve=sieve):
        i = cid - 1
        feats.append({"geometry": geom, "lu_code": codes[i], "lu_name": names[i],
                      "category": _map_category(names[i]),
                      "class_landuse": _map_category(names[i]),
                      "class_image": None, "agree": None, "source": "landuse"})
    gdf = gpd.GeoDataFrame(feats, geometry="geometry", crs=f"EPSG:{epsg}")
    if gdf.crs.to_string() != target_crs:
        gdf = gdf.to_crs(target_crs)
    return gdf


# ── 臨路三態＋地形組成(自 analyze.py 移植;判定邏輯完全一致) ──────────────────
def _site_composition(site_geom, feats):
    total = site_geom.area
    areas = {}
    for _, r in feats.iterrows():
        inter = r.geometry.intersection(site_geom)
        if inter.is_empty:
            continue
        a = inter.area
        if a <= 0:
            continue
        areas[r["category"]] = areas.get(r["category"], 0.0) + a
    pct = {k: (v / total if total > 0 else 0.0) for k, v in areas.items()}
    dominant = max(areas, key=areas.get) if areas else None
    return {"areas": areas, "pct": pct, "dominant": dominant,
            "has_pond": "魚塭" in areas, "has_building": "建物" in areas}


def _access_level(sub, site_geom, cfg):
    if len(sub) == 0:
        return None, None
    d = float(sub.geometry.distance(site_geom).min())
    if d > cfg.bridge_max:
        return None, d
    return ("直接臨路" if d <= cfg.touch_tol else "需搭橋臨路"), d


def _classify_road(site_geom, feats, cfg):
    roads = feats[feats["category"] == "道路"]
    if len(roads) == 0:
        return "不臨路", float("inf"), ""
    if "lu_name" in roads.columns:
        is_levee = roads["lu_name"].astype(str).str.contains("防汛", na=False)
    else:
        is_levee = pd.Series(False, index=roads.index)
    normal_acc, _ = _access_level(roads[~is_levee], site_geom, cfg)
    levee_acc, _ = _access_level(roads[is_levee], site_geom, cfg)
    dist = float(roads.geometry.distance(site_geom).min())
    if normal_acc is None and levee_acc is None:
        return "不臨路", dist, ""
    if normal_acc and levee_acc:
        return (f"{normal_acc}（一般道路）、{levee_acc}（防汛道路）", dist, "同時臨一般道路與防汛道路")
    if levee_acc:
        return levee_acc, dist, "防汛道路"
    return normal_acc, dist, ""


def _road_width(site_geom, feats, cfg):
    """沿基地邊界法向量測鄰接道路寬度(2026-07-28 新增)。

    作法:沿基地外環每 width_sample 公尺取樣,於各取樣點取邊界外法向,
    往外射出 width_search 公尺射線,取射線與道路面相交之**最靠近起點的連續段**
    長度為該處路寬;取樣點到路緣間隔逾 width_gap 者視為該點未鄰接道路而不採計。
    回傳 (中位數, 最小, 最大, 採計點數, 總取樣點數)。純新增資訊,不改臨路三態判定。

    ⚠⚠ **這個值不是「道路計畫寬度」也不是「已開闢車道寬度」,不可直接當路寬交付**
    (2026-08-07 由新化、安南兩案人工修正版同時推翻後補記)。它量的是「LUIMAP 道路類
    圖徵沿基地邊界法向的截線長」,有兩個方向一致的偏大來源:
      ①LUIMAP 是柵格分類向量化,**路肩、側溝、相鄰農路或空地會一併被分到「道路」**
        並與車道合併成同一多邊形 → 安南案現況已開闢約 10m,本欄量出 11.86m(約+19%);
      ②射線方向取的是**基地邊界法向**而非道路法向,基地邊界與道路不平行時(尤其角隅、
        路口)截線會沿道路軸向拉長 → 新化案 8m 鄉道量出中位數 22.48m(近 3 倍)。
    ①是系統性小幅偏大、量測值會很集中;②是極端偏大、特徵是**離散**(同基地並存極小
    與極大值,新化 min 0.36/max 23.11)。`_analyze_site` 已對②加「路寬量測離散」旗標,
    但**①旗標抓不到**——所以凡要寫進交付文件的路寬,一律另以都市計畫圖/地籍圖的道路
    用地劃設寬度加實景判讀覆核,並註明「實際路寬以測量及地籍鑑界結果為準」。
    同理 `_classify_road` 的「直接臨路」是與 LUIMAP 道路面相接,**不等於緊鄰已開闢道路**。
    """
    roads = feats[feats["category"] == "道路"]
    if len(roads) == 0:
        return None, None, None, 0, 0
    road = roads.geometry.union_all()
    if not road.is_valid:
        road = shapely.make_valid(road)

    polys = [site_geom] if site_geom.geom_type == "Polygon" else list(site_geom.geoms)
    widths, n_sample = [], 0
    for poly in polys:
        ring = poly.exterior
        L = ring.length
        if L <= 0:
            continue
        n = max(int(L // cfg.width_sample), 4)
        for i in range(n):
            d = L * i / n
            p = ring.interpolate(d)
            q = ring.interpolate(min(d + 0.5, L))
            dx, dy = q.x - p.x, q.y - p.y
            m = math.hypot(dx, dy)
            if m == 0:
                continue
            # 邊界切線旋轉 90°,取朝外者
            nx, ny = -dy / m, dx / m
            if poly.contains(shapely.Point(p.x + nx * 0.2, p.y + ny * 0.2)):
                nx, ny = -nx, -ny
            n_sample += 1
            ray = shapely.LineString([(p.x, p.y),
                                      (p.x + nx * cfg.width_search, p.y + ny * cfg.width_search)])
            inter = ray.intersection(road)
            if inter.is_empty:
                continue
            segs = [inter] if inter.geom_type == "LineString" else [
                g for g in getattr(inter, "geoms", []) if g.geom_type == "LineString"]
            if not segs:
                continue
            # 取最靠近取樣點的連續段
            segs.sort(key=lambda g: shapely.Point(p.x, p.y).distance(g))
            first = segs[0]
            gap = shapely.Point(p.x, p.y).distance(first)
            if gap > cfg.width_gap:
                continue
            if first.length > 0:
                widths.append(first.length)
    if not widths:
        return None, None, None, 0, n_sample
    widths.sort()
    mid = widths[len(widths) // 2] if len(widths) % 2 else \
        (widths[len(widths) // 2 - 1] + widths[len(widths) // 2]) / 2
    return mid, widths[0], widths[-1], len(widths), n_sample


def _crossing_check(site_geom, feats, category, margin):
    sub = feats[feats["category"] == category]
    if len(sub) == 0:
        return 0.0, 0.0
    sub = sub[sub.intersects(site_geom)]
    if len(sub) == 0:
        return 0.0, 0.0
    geom = sub.geometry.union_all()
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
    ring = site_geom.buffer(margin).difference(site_geom)
    inside = geom.intersection(site_geom).area
    outside = geom.intersection(ring).area
    total = inside + outside
    return outside, (outside / total if total > 0 else 0.0)


def _rail_note(site_geom, feats, road_dist, cfg):
    """最近的「道路類」其實是鐵路時的註記(鐵路不計入臨路)。

    鐵路面距基地比任何道路面都近、且在 crossing_margin 內 → 回「鐵路不計（鐵路距 X m）」。"""
    if "category" not in feats.columns:
        return ""
    rails = feats[feats["category"] == "鐵路"]
    if len(rails) == 0:
        return ""
    rd = float(rails.geometry.distance(site_geom).min())
    if rd <= cfg.crossing_margin and rd < road_dist:
        return f"鐵路不計（鐵路距 {rd:.1f} m）"
    return ""


def _analyze_site(site, feats, cfg, tile_gap=None):
    """tile_gap:None 或 {"n": 缺磚數, "polys": [缺磚多邊形(目標座標系)]}。"""
    g = site["geometry"]
    comp = _site_composition(g, feats)
    road_status, road_dist, road_note = _classify_road(g, feats, cfg)
    rn = _rail_note(g, feats, road_dist, cfg)
    if rn:
        road_note = f"{road_note}；{rn}" if road_note else rn
    gap_hit = False
    if tile_gap and tile_gap.get("n"):
        zone = g.buffer(cfg.buffer_m)
        gap_hit = any(p.intersects(zone) for p in tile_gap.get("polys", []))
        if gap_hit:
            road_status = "須重跑(國土利用圖磚缺漏)"
    w_mid, w_min, w_max, w_n, w_tot = _road_width(g, feats, cfg)
    pond_a, pond_r = _crossing_check(g, feats, "魚塭", cfg.crossing_margin)
    bld_a, bld_r = _crossing_check(g, feats, "建物", cfg.crossing_margin)
    site_area = site["area_m2"]
    water = comp["areas"].get("魚塭", 0.0) + comp["areas"].get("水道", 0.0)
    land = max(site_area - water, 0.0)
    land_over = land > cfg.land_flag_ha * 10000.0
    flags = []
    if land_over:
        flags.append(f"陸域逾{cfg.land_flag_ha:g}公頃")
    if pond_a > 0:
        flags.append("魚塭越界")
    if bld_a > 0:
        flags.append("建物越界")
    if road_status == "不臨路":
        flags.append("未臨路")
    if gap_hit:      # 只標與缺磚相交的案場(不相交者結論不受影響)
        flags.append(f"國土利用圖磚缺漏（{tile_gap['n']}塊）")
    # ⚠ 路寬離散警示(2026-08-07 新增;純加旗標,不改臨路三態判定與路寬數值)
    #   兩案人工修正版同時推翻本欄:新化 8m 鄉道量出中位數 22.48m(min 0.36/max 23.11)、
    #   安南現況已開闢約 10m 量出 11.86m。成因是本欄量的是「LUIMAP 道路類圖徵沿**基地
    #   邊界法向**的截線長」,不是道路計畫寬度、也不是已開闢車道寬:
    #     ①LUIMAP 是柵格分類向量化,路肩/側溝/相鄰農路或空地會一併歸「道路」並與車道
    #       合併成同一多邊形 → 系統性偏大;
    #     ②射線方向取基地邊界法向而非道路法向,基地邊界與道路不平行時(尤其角隅、路口)
    #       截線會沿道路軸向拉長 → 極端偏大。
    #   典型特徵就是**離散**:同一基地出現極小值與極大值並存。以下兩條任一成立即示警,
    #   提醒交付前改以都市計畫圖/地籍圖之道路用地劃設寬度＋實景判讀覆核。
    if w_mid and w_min is not None and w_max is not None:
        if w_max > w_mid * 1.5 or w_min < w_mid * 0.5:
            flags.append("路寬量測離散(須人工覆核)")
        elif w_n and w_tot and w_n < w_tot * 0.25:
            flags.append("路寬採計點偏少(須人工覆核)")
    row = {
        "site_id": site["site_id"], "地號清單": site["landno_list"],
        "基地面積_m2": round(site_area, 2), "陸域面積_m2": round(land, 2),
        "水域面積_m2": round(water, 2), "陸域逾2公頃": "Y" if land_over else "N",
        "主要地形": comp["dominant"], "有無魚塭": "Y" if comp["has_pond"] else "N",
        "有無建物": "Y" if comp["has_building"] else "N",
        "臨路狀態": road_status, "臨路註記": road_note,
        "臨路距離_m": round(road_dist, 2) if road_dist != float("inf") else None,
        "鄰接路寬中位數_m": round(w_mid, 2) if w_mid is not None else None,
        "鄰接路寬最小_m": round(w_min, 2) if w_min is not None else None,
        "鄰接路寬最大_m": round(w_max, 2) if w_max is not None else None,
        "路寬量測點數": f"{w_n}/{w_tot}",
        "魚塭越界面積_m2": round(pond_a, 2), "魚塭越界比例": round(pond_r, 4),
        "建物越界面積_m2": round(bld_a, 2), "建物越界比例": round(bld_r, 4),
        "異常旗標": "、".join(flags) if flags else "",
    }
    for cat in ("魚塭", "農田", "建物", "道路", "太陽光電"):
        row[f"{cat}%"] = round(comp["pct"].get(cat, 0.0) * 100, 1)
    return row


# ── 案場幾何:吃產線權威 sitemap 分組(與 make_maps 一致) ─────────────────────
def _build_sites(joined, sitemap_csv):
    parcels = gpd.read_file(joined, encoding="big5")
    lncol = next(c for c in parcels.columns if "地號" in c)
    segcol = next((c for c in parcels.columns if "段" in c), None)
    sm = load_sitemap(sitemap_csv)
    groups = {}
    fails = []
    for _, p in parcels.iterrows():
        seg = str(p[segcol]) if segcol else ""
        got = assign_site(str(p[lncol]), sm, "", seg=seg)
        if not got:
            fails.append((seg, p[lncol])); continue
        pairs = got if isinstance(got, tuple) else ((got, ""),)
        for sid, _name in pairs:
            groups.setdefault(sid, {"geoms": [], "landnos": []})
            groups[sid]["geoms"].append(p.geometry)
            groups[sid]["landnos"].append(str(p[lncol]))
    if fails:
        for seg, ln in fails[:10]:
            print(f"[分派失敗] {seg} {ln}", file=sys.stderr)
        sys.exit(f"{len(fails)} 筆宗地無法分派(sitemap 不完整?),中止")
    rows = []
    for sid in sorted(groups):
        geom = shapely.make_valid(shapely.union_all(groups[sid]["geoms"]))
        rows.append({"site_id": sid, "geometry": geom, "area_m2": geom.area,
                     "landno_list": ";".join(groups[sid]["landnos"]),
                     "n_parcels": len(groups[sid]["landnos"])})
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=parcels.crs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True)
    ap.add_argument("--sitemap", required=True, help="案場定義/sitemap.csv(帶段欄)")
    ap.add_argument("--out", required=True, help="臨路評估輸出目錄")
    ap.add_argument("--colortable",
                    default=str(Path(__file__).parent.parent / "config" / "luimap_colortable.csv"))
    ap.add_argument("--reuse-landuse", help="既有 landuse.tif(跳過 WMTS 下載;驗收/重跑用)")
    ap.add_argument("--buffer", type=float, default=Cfg.buffer_m)
    ap.add_argument("--tile-zoom", type=int, default=Cfg.tile_zoom)
    ap.add_argument("--width-sample", type=float, default=Cfg.width_sample,
                    help="路寬量測:沿基地邊界取樣間距 m(預設5)")
    ap.add_argument("--width-search", type=float, default=Cfg.width_search,
                    help="路寬量測:法向射線長度 m(預設60)")
    ap.add_argument("--width-gap", type=float, default=Cfg.width_gap,
                    help="路寬量測:取樣點到路緣容許間隔 m(預設8;逾此視為該點未鄰接道路)")
    a = ap.parse_args()
    cfg = Cfg(buffer_m=a.buffer, tile_zoom=a.tile_zoom, width_sample=a.width_sample,
              width_search=a.width_search, width_gap=a.width_gap)

    out = Path(a.out); work = out / "_work"; work.mkdir(parents=True, exist_ok=True)
    sites = _build_sites(a.joined, a.sitemap)
    print(f"案場 {len(sites)} 個(依權威 sitemap 分組)", file=sys.stderr)

    tile_gap = None
    if a.reuse_landuse and Path(a.reuse_landuse).exists():
        lu_tif = a.reuse_landuse
        print(f"重用既有 landuse.tif:{lu_tif}", file=sys.stderr)
    else:
        bbox = _bbox_3857(sites, cfg.buffer_m)
        lu_tif, missing = _fetch_landuse_mosaic_ex(bbox, cfg.tile_zoom, str(work / "landuse.tif"))
        print(f"已下載 landuse.tif" + (f"(缺磚 {len(missing)} 塊)" if missing else ""),
              file=sys.stderr)
        if missing:
            tile_gap = {"n": len(missing),
                        "polys": missing_polys_in_crs(missing, cfg.target_crs)}

    feats = _vectorize_landuse(lu_tif, a.colortable, cfg.target_crs, cfg.sieve)
    print(f"國土利用向量化 {len(feats)} 面;道路 {int((feats['category']=='道路').sum())} 面",
          file=sys.stderr)

    table = pd.DataFrame([_analyze_site(r, feats, cfg, tile_gap) for _, r in sites.iterrows()])

    out.mkdir(parents=True, exist_ok=True)
    table.to_excel(out / "成果.xlsx", index=False)
    sites_out = sites.rename(columns={"landno_list": "landno_lis"})
    sites_out.to_file(out / "sites.shp", encoding="utf-8")
    feats.to_file(out / "features.geojson", driver="GeoJSON")

    # sites_status.csv + sitemap.csv：make_maps 圖6 的圖例來源（2026-07-29 補）。
    # 原本只有 run_case 的 digest_fishpv 會寫，但 skill 的 ②c 是叫人直接跑本腳本
    # → 圖6 圖例會靜默變成「S001 無資料」而 6 張圖仍全部回報成功（安南案實錯）。
    # 格式與 run_case.digest_fishpv 一致，兩條路徑產出相同。
    table[["site_id", "臨路狀態", "臨路距離_m", "臨路註記"]].to_csv(
        out / "sites_status.csv", index=False, encoding="utf-8-sig")
    try:
        shutil.copy2(a.sitemap, out / "sitemap.csv")   # 案場定義/ 為權威（帶段欄）
    except Exception as exc:  # noqa: BLE001 — 複製失敗不影響臨路計算結果
        print(f"警告:sitemap.csv 複製失敗（{type(exc).__name__}），"
              f"make_maps 圖6 需自行指定 --sitemap", file=sys.stderr)

    dist = table["臨路狀態"].value_counts().to_dict()
    print(f"寫出 {out}\\成果.xlsx / sites.shp / features.geojson"
          f" / sites_status.csv / sitemap.csv", file=sys.stderr)
    print(f"臨路狀態分布:{dist}", file=sys.stderr)


if __name__ == "__main__":
    main()
