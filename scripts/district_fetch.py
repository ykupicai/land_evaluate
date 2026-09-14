# -*- coding: utf-8 -*-
r"""行政區界（鄉鎮市區）向量 → `<案>/行政區界/行政區界.shp`。

用途：基地橫跨兩個行政區時，圖上要看得出界線在哪（2026-09-14 內湖／中山案使用者要求
「各張圖加上中山區及內湖區的區界線」）。出圖端用 `district_line.py` 取用，
**檔案存在就自動畫**，不必逐支腳本加旗標。

來源優先序
  ① 縣市都發局圖台的「行政區」圖層（與都市計畫分區同一個服務家族，界線與分區圖吻合）
  ② 未登錄縣市 → 由使用者自備 shp 放同一路徑即可（欄位需有 區名 或 TownName）

臺北市：`UrbanPlan2/UrbanPlan2/MapServer/3`（欄位 CityName／TownName／TownCode）。
⚠ 該服務回的 CityName 是「台北市」（非「臺北市」），本檔一律正規化成「臺」。

用法：
  python scripts/district_fetch.py --county 臺北市 --joined <基地.shp> --margin 1500 \
      --out outputs/<案>/行政區界
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

# 縣市 → 行政區圖層查詢端點（沿用 urbanplan_image 的 Referer 慣例）
DISTRICT_COUNTY = {
    "臺北市": {
        "query": "https://www.historygis.udd.gov.taipei/arcgis/rest/services/"
                 "UrbanPlan2/UrbanPlan2/MapServer/3/query",
        "referer": "https://webgis.udd.gov.taipei/upis_v2",
        "fields": {"區名": "TownName", "縣市": "CityName", "代碼": "TownCode"},
    },
}


def _norm(county: str) -> str:
    c = (county or "").strip()
    return c.replace("台", "臺") if c else c


def county_supported(county: str) -> bool:
    return _norm(county) in DISTRICT_COUNTY


def fetch_districts(county: str, bbox, out_dir, timeout: int = 90):
    """抓與 bbox 相交的行政區多邊形 → `<out_dir>/行政區界.shp`；回 (路徑, 區名清單)。"""
    cfg = DISTRICT_COUNTY.get(_norm(county))
    if cfg is None:
        print(f"  （{county} 未登錄行政區界服務 → 略過；自備 shp 放 <案>/行政區界/ 亦可）")
        return None, []
    import geopandas as gpd
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    x0, y0, x1, y1 = (float(v) for v in bbox)
    q = {"geometry": f"{x0},{y0},{x1},{y1}", "geometryType": "esriGeometryEnvelope",
         "inSR": "3826", "outSR": "3826", "spatialRel": "esriSpatialRelIntersects",
         "outFields": "*", "returnGeometry": "true", "f": "json"}
    req = urllib.request.Request(cfg["query"] + "?" + urllib.parse.urlencode(q),
                                 headers={"User-Agent": "Mozilla/5.0",
                                          "Referer": cfg["referer"]})
    d = json.loads(urllib.request.urlopen(req, timeout=timeout).read()
                   .decode("utf-8-sig", errors="replace"))
    rows, geoms = [], []
    fl = cfg["fields"]
    for f in d.get("features", []):
        a = f.get("attributes", {})
        g = unary_union([Polygon(r).buffer(0) for r in f["geometry"]["rings"]])
        rows.append({"區名": str(a.get(fl["區名"], "")).strip(),
                     "縣市": _norm(str(a.get(fl["縣市"], "")).strip()),
                     "代碼": str(a.get(fl["代碼"], "")).strip()})
        geoms.append(g)
    if not geoms:
        print("  ⚠ 範圍內查無行政區")
        return None, []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "行政區界.shp"
    gpd.GeoDataFrame(rows, geometry=geoms, crs=3826).to_file(out, encoding="utf-8")
    names = [r["區名"] for r in rows]
    print(f"  ✔ 行政區界 {len(geoms)} 區（{'、'.join(names)}）→ {out}")
    return out, names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", required=True, help=f"縣市（已登錄：{list(DISTRICT_COUNTY)}）")
    ap.add_argument("--joined", default=None, help="基地 shp（取外擴 bbox）")
    ap.add_argument("--bbox", nargs=4, type=float, default=None,
                    metavar=("XMIN", "YMIN", "XMAX", "YMAX"))
    ap.add_argument("--margin", type=float, default=1500.0,
                    help="基地 bbox 外擴公尺（預設 1500，區位圖也吃得到）")
    ap.add_argument("--out", required=True, help="輸出資料夾，建議 outputs/<案>/行政區界")
    a = ap.parse_args()
    if a.bbox:
        bbox = tuple(a.bbox)
    elif a.joined:
        import geopandas as gpd
        g = gpd.read_file(a.joined)
        g = g.to_crs(3826) if g.crs else g.set_crs(3826)
        x0, y0, x1, y1 = g.total_bounds
        bbox = (x0 - a.margin, y0 - a.margin, x1 + a.margin, y1 + a.margin)
    else:
        sys.exit("要給 --joined 或 --bbox")
    fetch_districts(a.county, bbox, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
