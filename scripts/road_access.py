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
import io
import os
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


def _fetch_landuse_mosaic(bbox3857, zoom, out_tif):
    xmin, ymin, xmax, ymax = bbox3857
    tile_m = 2 * ORIGIN / (2 ** zoom)
    cmin = int((xmin + ORIGIN) / tile_m); cmax = int((xmax + ORIGIN) / tile_m)
    rmin = int((ORIGIN - ymax) / tile_m); rmax = int((ORIGIN - ymin) / tile_m)
    ncol, nrow = cmax - cmin + 1, rmax - rmin + 1
    W, H = ncol * 256, nrow * 256
    ox = cmin * tile_m - ORIGIN; oy = ORIGIN - rmin * tile_m; pix = tile_m / 256.0
    canvas = np.full((3, H, W), 255, np.uint8)
    for r in range(rmin, rmax + 1):
        for c in range(cmin, cmax + 1):
            blob = None
            url = WMTS_LANDUSE.format(z=zoom, row=r, col=c)
            for att in range(3):
                try:
                    blob = urllib.request.urlopen(
                        urllib.request.Request(url, headers=_HDR), timeout=30).read()
                    break
                except Exception:
                    if att == 2:
                        blob = None
                    else:
                        time.sleep(1)
            if not blob or blob[:3] == b"GIF":
                continue
            arr = np.asarray(Image.open(io.BytesIO(blob)).convert("RGB"))
            y0 = (r - rmin) * 256; x0 = (c - cmin) * 256
            canvas[:, y0:y0 + 256, x0:x0 + 256] = np.transpose(arr, (2, 0, 1))
    return _write_rgb_geotiff(out_tif, canvas, 3857, ox, oy, pix)


# ── 國土利用向量化(自 landuse.py 移植;關鍵字對應不可動) ─────────────────────
_KEYWORD_MAP = [
    (("太陽", "光電"), "太陽光電"),
    (("養殖", "魚塭"), "魚塭"),
    (("道路", "公路", "交通", "鐵路", "街", "巷"), "道路"),
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


def _analyze_site(site, feats, cfg):
    g = site["geometry"]
    comp = _site_composition(g, feats)
    road_status, road_dist, road_note = _classify_road(g, feats, cfg)
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
    row = {
        "site_id": site["site_id"], "地號清單": site["landno_list"],
        "基地面積_m2": round(site_area, 2), "陸域面積_m2": round(land, 2),
        "水域面積_m2": round(water, 2), "陸域逾2公頃": "Y" if land_over else "N",
        "主要地形": comp["dominant"], "有無魚塭": "Y" if comp["has_pond"] else "N",
        "有無建物": "Y" if comp["has_building"] else "N",
        "臨路狀態": road_status, "臨路註記": road_note,
        "臨路距離_m": round(road_dist, 2) if road_dist != float("inf") else None,
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
    a = ap.parse_args()
    cfg = Cfg(buffer_m=a.buffer, tile_zoom=a.tile_zoom)

    out = Path(a.out); work = out / "_work"; work.mkdir(parents=True, exist_ok=True)
    sites = _build_sites(a.joined, a.sitemap)
    print(f"案場 {len(sites)} 個(依權威 sitemap 分組)", file=sys.stderr)

    if a.reuse_landuse and Path(a.reuse_landuse).exists():
        lu_tif = a.reuse_landuse
        print(f"重用既有 landuse.tif:{lu_tif}", file=sys.stderr)
    else:
        bbox = _bbox_3857(sites, cfg.buffer_m)
        lu_tif = _fetch_landuse_mosaic(bbox, cfg.tile_zoom, str(work / "landuse.tif"))
        print(f"已下載 landuse.tif", file=sys.stderr)

    feats = _vectorize_landuse(lu_tif, a.colortable, cfg.target_crs, cfg.sieve)
    print(f"國土利用向量化 {len(feats)} 面;道路 {int((feats['category']=='道路').sum())} 面",
          file=sys.stderr)

    table = pd.DataFrame([_analyze_site(r, feats, cfg) for _, r in sites.iterrows()])

    out.mkdir(parents=True, exist_ok=True)
    table.to_excel(out / "成果.xlsx", index=False)
    sites_out = sites.rename(columns={"landno_list": "landno_lis"})
    sites_out.to_file(out / "sites.shp", encoding="utf-8")
    feats.to_file(out / "features.geojson", driver="GeoJSON")

    dist = table["臨路狀態"].value_counts().to_dict()
    print(f"寫出 {out}\\成果.xlsx / sites.shp / features.geojson", file=sys.stderr)
    print(f"臨路狀態分布:{dist}", file=sys.stderr)


if __name__ == "__main__":
    main()
