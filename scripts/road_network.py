# -*- coding: utf-8 -*-
"""道路階層查詢：找基地外擴後第一條國道／省道／市道／縣道／區道／鄉道／專用公路

★ 建立緣由（2026-08-06 使用者定案）：區位圖（make_locality_map.py）過去用固定
  `--margin`（預設1500m）外擴，跟基地實際離最近的重要道路多遠沒有關係。改成
  「基地外擴到碰到第一條夠格的公路為止」，外擴距離才反映「這個基地跟路網的
  實際疏密關係」，跟臨路判定圖的用途呼應。

資料來源（合併兩種，缺一角就補另一角）：
  ・**國道／省道**：本機無此資料（`2021 國道省道以外道路/` 資料夾名稱本身就講明
    「省道以外」），改用 **OSM Overpass**（`highway=motorway` 近似國道、
    `highway=trunk` 近似省道/快速公路）。實測台南七股周邊可查到中山高速公路、
    台86線東西向快速公路，標繪完整。⚠ OSM 是群眾協作資料，非官方公路路線圖，
    交付文件敘述最近道路名稱/距離時建議加註「以 OSM 資料為準，如需精確里程請洽
    公路局」。
  ・**市道／縣道／區道／鄉道／專用公路**：本機 `2021 國道省道以外道路/道路/`
    的 縣道.shp／鄉道.shp／市區快速道路.shp／市區道路(路街).shp（全國範圍，
    EPSG:3826）。**排除**市區道路(巷弄)（純巷弄，太細）與產業道路（非公路法
    範疇的農路等級，臨路判定圖另有處理）。

用法（系統 Python，不需 QGIS）：
  python scripts/road_network.py --joined <基地或申請範圍.shp> [--out <案件資料夾>]
  → 印出並存 <out>/區位圖_道路基準.json：{"margin_m": ..., "road_name": ...,
    "road_type": ..., "source": "OSM"|"本機"}

也可作為模組匯入：nearest_road(geom, log) -> dict，geom 為 EPSG:3826 shapely 幾何。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import geopandas as gpd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROAD_DIR = os.path.join(REPO, "2021 國道省道以外道路", "道路")
# 本機圖層：檔名 -> 顯示用道路階層名稱（市區快速道路視同快速公路/準省道級）
LOCAL_LAYERS = {
    "縣道.shp": "縣道",
    "鄉道.shp": "鄉道",
    "市區快速道路.shp": "市區快速道路",
    "市區道路(路街).shp": "市道／市區道路",
}

OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OSM_HIGHWAY_LABEL = {"motorway": "國道", "trunk": "省道／快速公路"}
PAD_M = 100.0   # 找到道路後再多留一點，讓道路本身在圖框內看得見，不是剛好切在邊緣


def _overpass(query: str, timeout: int = 60) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for url in OVERPASS:
        for attempt in (1, 2):
            try:
                req = urllib.request.Request(
                    url, data=data,
                    headers={"User-Agent": "cadastral-pipeline/1.0 (land-use assessment)"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last = f"HTTP {exc.code}"
                if exc.code in (429, 504) and attempt == 1:
                    time.sleep(15)
                    continue
                break
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
                break
        time.sleep(2)
    raise RuntimeError(f"Overpass 全部鏡像失敗：{last}")


def _osm_nearest(geom, radius_m: float, log: list):
    """回 (dist_m, name, type) 或 None（半徑內查無）。geom 為 EPSG:3826。"""
    bb = gpd.GeoSeries([geom.buffer(radius_m)], crs=3826).to_crs(4326).total_bounds
    s, w, n, e = bb[1], bb[0], bb[3], bb[2]
    q = ('[out:json][timeout:60];'
         f'way["highway"~"^(motorway|trunk)$"]({s},{w},{n},{e});'
         'out tags geom;')
    js = _overpass(q)
    rows = []
    for el in js.get("elements", []):
        geom_pts = el.get("geometry")
        if not geom_pts:
            continue
        from shapely.geometry import LineString
        line = LineString([(p["lon"], p["lat"]) for p in geom_pts])
        t = el.get("tags", {})
        rows.append({"highway": t.get("highway"), "ref": t.get("ref"),
                     "name": t.get("name") or t.get("ref") or "(未命名)",
                     "geometry": line})
    if not rows:
        return None
    g = gpd.GeoDataFrame(rows, crs=4326).to_crs(3826)
    g["_d"] = g.geometry.distance(geom)
    g = g.sort_values("_d")
    r0 = g.iloc[0]
    label = OSM_HIGHWAY_LABEL.get(r0["highway"], r0["highway"])
    disp = f"{r0['ref']} {r0['name']}" if r0["ref"] and r0["ref"] not in r0["name"] else r0["name"]
    return float(r0["_d"]), disp, label


def _local_nearest(geom, radius_m: float, log: list):
    """回 (dist_m, name, type) 或 None。geom 為 EPSG:3826。"""
    best = None
    ring = geom.buffer(radius_m)
    for fname, label in LOCAL_LAYERS.items():
        p = os.path.join(ROAD_DIR, fname)
        if not os.path.exists(p):
            continue
        try:
            g = gpd.read_file(p, engine="pyogrio", bbox=ring.bounds)
        except Exception as e:  # noqa: BLE001
            log.append(f"    ⚠ 讀取 {fname} 失敗：{e}")
            continue
        if not len(g):
            continue
        g = g[g.geometry.intersects(ring)]
        if not len(g):
            continue
        g["_d"] = g.geometry.distance(geom)
        r0 = g.loc[g["_d"].idxmin()]
        name = r0.get("RDNAME") or r0.get("ROADNAME") or "(未命名)"
        d = float(r0["_d"])
        if best is None or d < best[0]:
            best = (d, str(name), label)
    return best


def nearest_road(geom, log: list) -> dict:
    """geom 為 EPSG:3826 幾何（通常是基地/申請範圍的 union）。

    分別查 OSM（國道/省道）與本機（市道/縣道/鄉道/市區快速道路），取距離較近者。
    半徑不足時逐步放大（1000→3000→10000→20000m），避免偏遠基地永遠查無。
    """
    candidates = []
    radii = (1000, 3000, 10000, 20000)
    osm_hit = local_hit = None
    for r in radii:
        if osm_hit is None:
            try:
                osm_hit = _osm_nearest(geom, r, log)
            except Exception as e:  # noqa: BLE001
                log.append(f"    ⚠ OSM 國道/省道查詢失敗：{e}")
                osm_hit = False   # False＝放棄重試，不是「查無」
        if local_hit is None:
            local_hit = _local_nearest(geom, r, log) or None
        if (osm_hit and osm_hit is not False) or local_hit:
            break
    for hit, src in ((osm_hit, "OSM"), (local_hit, "本機")):
        if hit:
            candidates.append((hit[0], hit[1], hit[2], src))
    if not candidates:
        log.append("    ⚠ 20,000m 內查無任何國道/省道/市道/縣道/鄉道/市區快速道路，"
                   "改用預設外擴 1500m")
        return {"margin_m": 1500.0, "road_name": None, "road_type": None, "source": "預設值"}
    candidates.sort(key=lambda x: x[0])
    d, name, rtype, src = candidates[0]
    margin = round(d + PAD_M, 1)
    log.append(f"    最近道路：{rtype}「{name}」，距基地 {d:,.1f} m（來源：{src}）"
               f"→ 外擴 {margin:,.1f} m（含 {PAD_M:.0f}m 緩衝）")
    return {"margin_m": margin, "road_name": name, "road_type": rtype,
            "distance_m": round(d, 1), "source": src}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    site = gpd.read_file(a.joined, engine="pyogrio")
    if site.crs and site.crs.to_epsg() != 3826:
        site = site.to_crs(3826)
    su = site.geometry.union_all()
    log = ["道路階層查詢（國道/省道=OSM，市道/縣道/鄉道/市區快速道路=本機）"]
    res = nearest_road(su, log)
    print("\n".join(log))
    out_dir = a.out or os.path.dirname(os.path.dirname(os.path.abspath(a.joined)))
    out_p = os.path.join(out_dir, "區位圖_道路基準.json")
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"\n→ {out_p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
