# -*- coding: utf-8 -*-
"""座標／Google Maps 網址 → 現行地段地號（反查）。

跟產線其他腳本方向相反：別的腳本是「地號→幾何」正查，這支是「點→地號」反查。

用法：
  python -X utf8 scripts/latlon_to_parcel.py "https://maps.app.goo.gl/xxxx"
  python -X utf8 scripts/latlon_to_parcel.py --lat 22.9243646 --lon 120.3469031
  python -X utf8 scripts/latlon_to_parcel.py "<網址>" --out outputs/<案>/cadastre   # 順便寫基地.shp

流程（三個守門機制，皆為踩坑後補上，勿拿掉）：
  1. 展開網址 → 抓地標本身座標（google maps 的 !3d<緯>!4d<經>，不是 @<中心>）
  2. WGS84 → EPSG:3826
  3. 縣市定位：農業部 MapServer identify（縣市界圖層，1 次查詢）決定要讀哪個本機地籍 shp
  4. 本機快照點面相交 → 候選（縣市/鄉鎮/段/**小段**/母/子）
     ★ 小段一定要帶進段名：農業部只認「新化段王公廟小段」，不認裸「新化段」
       （2026-08-21 三子寵物天堂案：漏小段→全查無→誤判成跨母號分割）
  5. 段名先驗：moa.resolve 確認農業部認得這個段名，**排在探測之前**
     ——段名對不上時直接說「段名問題」，不可讓後面的探測誤報成「跨母號分割」
  6. 農業部即時確認候選地號：現行幾何仍含點 → 收工（常態＝1 次 API）
  7. 落差（查無／縮水不含點）→ 收斂式子號探測：探到含點即停、連續 K 筆查無才止，
     不預設固定上限（2026-08-21 關廟案：780-10 已分割，點落在 780-44）
     探到門檻仍未命中 → 判「跨母號分割／重測改號」，明確退線上空間反查、不硬掰

權威分工：本機快照只當「候選來源＋縣市定位失敗時的備援」，現行地號一律以農業部為準。
可信度：點距邊界 <5m 標「近界、建議人工確認相鄰筆」（Google 地標點常有 5~15m 誤差）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(os.path.expanduser("~"), ".claude", "skills", "cadastre-check", "scripts")
CAD_DIR = os.path.join(REPO_ROOT, "地籍圖資")
CRS = 3826
NEAR_EDGE_M = 5.0          # 點距邊界 <此值 → 標「近界、建議人工確認」
PROBE_MISS_STOP = 15       # 收斂探測：連續查無達此數視為到底
PROBE_THROTTLE_S = 0.15    # 每次農業部請求之間的節流（避免像機器人流量）
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _load_cad_modules():
    if not os.path.isdir(SKILL):
        raise SystemExit(f"找不到 skill cadastre-check：{SKILL}（請確認已安裝）")
    if SKILL not in sys.path:
        sys.path.insert(0, SKILL)
    import api  # noqa: PLC0415
    import moa  # noqa: PLC0415
    return api, moa


# --------------------------------------------------------------------------
# 1. 網址 → 座標
# --------------------------------------------------------------------------
def expand_url(url: str) -> str:
    """展開短網址（maps.app.goo.gl）到最終的 google.com/maps 網址。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.geturl()


def extract_lonlat(text: str) -> tuple[float, float, str]:
    """從網址或 'lat,lon' 字串抓經緯度（WGS84）。回 (lon, lat, 來源說明)。

    google maps 網址內有兩組座標，務必取地標本身那組：
      !3d<lat>!4d<lon>   ← 地標實際位置（優先）
      @<lat>,<lon>,<z>   ← 地圖視角中心（退而求其次，會標警告）
    """
    s = text.strip()
    # 直接是 "lat,lon"
    m = re.fullmatch(r"\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*", s)
    if m:
        return float(m.group(2)), float(m.group(1)), "直接輸入座標"
    if s.startswith("http"):
        full = expand_url(s) if "maps.app.goo.gl" in s or "goo.gl" in s else s
        full = urllib.parse.unquote(full)
        m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", full)
        if m:
            return float(m.group(2)), float(m.group(1)), "地標點位(!3d!4d)"
        m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", full)
        if m:
            return (float(m.group(2)), float(m.group(1)),
                    "⚠地圖中心(@)——網址無地標點位，可能偏移")
        raise SystemExit(f"網址內找不到座標：{full[:200]}")
    raise SystemExit(f"無法解析輸入：{text[:120]}")


def to_twd97(lon: float, lat: float) -> tuple[float, float]:
    from pyproj import Transformer  # noqa: PLC0415
    return Transformer.from_crs("EPSG:4326", f"EPSG:{CRS}", always_xy=True).transform(lon, lat)


# --------------------------------------------------------------------------
# 3. 縣市定位（農業部 MapServer identify，縣市界圖層）
# --------------------------------------------------------------------------
_TW = {"臺": "台"}   # 檔名正規化：屬性回「臺南市」、本機檔名是「台南市.shp」


def county_shp_path(county: str) -> str | None:
    fn = county
    for a, b in _TW.items():
        fn = fn.replace(a, b)
    p = os.path.join(CAD_DIR, fn + ".shp")
    return p if os.path.exists(p) else None


def county_of_point(moa, x: float, y: float) -> str | None:
    """農業部 MapServer identify（縣市界）→ 縣市名。失敗回 None（改用備援）。"""
    try:
        tok = moa.config()["cadastralSoeToken"]
        base = moa.config()["cadastralSoeUrl"] + "CadastralMap/SOE/MapServer"
        d = 3.0
        j = moa._get(base + "/identify", {
            "geometry": f"{x},{y}", "geometryType": "esriGeometryPoint", "sr": CRS,
            "tolerance": 2, "mapExtent": f"{x-d},{y-d},{x+d},{y+d}",
            "imageDisplay": "600,600,96", "layers": "all",
            "returnGeometry": "false", "f": "json", "token": tok})
        for r in (j.get("results") or []):
            name = (r.get("attributes") or {}).get("CountyName")
            if name:
                return name
    except Exception:
        return None
    return None


def county_by_bbox_scan(x: float, y: float, pt) -> str | None:
    """備援：農業部 identify 失敗時，掃本機各縣市 shp 找含此點者。"""
    import geopandas as gpd  # noqa: PLC0415
    from glob import glob  # noqa: PLC0415
    for shp in glob(os.path.join(CAD_DIR, "*.shp")):
        try:
            g = gpd.read_file(shp, bbox=(x - 5, y - 5, x + 5, y + 5))
        except Exception:
            continue
        if len(g) and g.contains(pt).any():
            return os.path.splitext(os.path.basename(shp))[0]
    return None


# --------------------------------------------------------------------------
# 4. 本機候選
# --------------------------------------------------------------------------
def full_section_name(seg, subseg) -> str:
    name = str(seg)
    if not name.endswith("段"):
        name += "段"
    if subseg is not None and str(subseg) not in ("", "None", "nan"):
        sub = str(subseg)
        name += sub if sub.endswith("小段") else sub + "小段"
    return name


def local_candidate(shp: str, x: float, y: float, pt) -> dict | None:
    import geopandas as gpd  # noqa: PLC0415
    gdf = gpd.read_file(shp, bbox=(x - 60, y - 60, x + 60, y + 60))
    sel = gdf[gdf.contains(pt)]
    if len(sel) == 0:
        return None
    r = sel.iloc[0]
    mother, child = str(int(r["宗地母"])), str(int(r["宗地子"]))
    landno = f"{mother}-{child}" if child != "0" else mother
    return {
        "city": r["縣市"], "town": r["鄉鎮"], "seg": r["段"], "subseg": r["小段"],
        "secname": full_section_name(r["段"], r["小段"]),
        "mother": mother, "landno": landno,
        "snap_area": float(r.geometry.area), "snap_landtype": r["使類"],
    }


# --------------------------------------------------------------------------
# 6~7. 農業部確認 + 收斂探測
# --------------------------------------------------------------------------
def reverse(text: str, throttle: float = PROBE_THROTTLE_S) -> dict:
    api, moa = _load_cad_modules()
    from shapely.geometry import shape, Point  # noqa: PLC0415

    lon, lat, src = extract_lonlat(text)
    x, y = to_twd97(lon, lat)
    pt = Point(x, y)
    out = {"input": text, "lon": lon, "lat": lat, "coord_source": src,
           "x": round(x, 3), "y": round(y, 3), "warnings": []}
    if src.startswith("⚠"):
        out["warnings"].append(src)

    # 縣市定位
    county = county_of_point(moa, x, y)
    shp = county_shp_path(county) if county else None
    if shp is None:
        county = county_by_bbox_scan(x, y, pt)   # 備援
        shp = county_shp_path(county) if county else None
    if shp is None:
        out["status"] = "FAIL_COUNTY"
        out["message"] = "縣市定位失敗（農業部 identify 與本機掃描皆未命中）"
        return out

    # 本機候選
    cand = local_candidate(shp, x, y, pt)
    if cand is None:
        out["status"] = "FAIL_NO_LOCAL"
        out["message"] = f"{county} 本機快照無候選（點未落在任何宗地）"
        out["county"] = county
        return out
    out.update({"county": cand["city"], "town": cand["town"],
                "section": cand["secname"], "candidate": cand["landno"]})

    # 段名先驗（守門，排在探測之前）
    try:
        moa.resolve(cand["city"], cand["town"], cand["secname"])
    except Exception as e:
        out["status"] = "FAIL_SECTION_NAME"
        out["message"] = (f"段名『{cand['secname']}』農業部查無 → 段名問題（非分割）："
                          f"{e}")
        return out

    def geom_of(no):
        rec = api.query(cand["city"], cand["town"], cand["secname"], [no])["records"][0]
        g = shape(rec["geometry"]) if rec["ok"] and rec.get("geometry") else None
        return g, rec

    api_calls = 0
    g0, rec0 = geom_of(cand["landno"]); api_calls += 1
    if g0 is not None and g0.contains(pt):
        hit, g, rec = cand["landno"], g0, rec0
        out["path"] = "候選即現行（免探測）"
    else:
        # 收斂探測同母號子號
        out["path"] = ("候選查無→探測" if g0 is None
                       else f"候選縮水不含點（{1 - g0.area / cand['snap_area']:.1%}）→探測")
        miss, n, hit, g, rec = 0, 0, None, None, None
        while miss < PROBE_MISS_STOP:
            n += 1
            time.sleep(throttle)
            gg, rr = geom_of(f"{cand['mother']}-{n}"); api_calls += 1
            if gg is None:
                miss += 1
                continue
            miss = 0
            if gg.contains(pt):
                hit, g, rec = f"{cand['mother']}-{n}", gg, rr
                break
        if hit is None:
            out["status"] = "FAIL_CROSS_MOTHER"
            out["message"] = ("同母號子號探測未命中 → 疑跨母號分割／重測改號，"
                              "需線上空間反查（本機候選與農業部正查皆無法解此點）")
            out["api_calls"] = api_calls
            return out

    # 成果
    p = rec.get("parcel") or {}
    reg = rec.get("registry") or {}
    edge = g.boundary.distance(pt)
    out.update({
        "status": "OK", "landno": hit, "code14": p.get("code14"),
        "zone": reg.get("zone"), "land_type": reg.get("land_type"),
        "reg_area": reg.get("area"), "reg_date": reg.get("reg_date"),
        "map_area": round(g.area, 2), "dist_to_edge_m": round(edge, 2),
        "api_calls": api_calls,
    })
    if reg.get("zone") is None and reg.get("land_type") is None:
        out["warnings"].append("使分/使類空白：屬都市土地或未登錄地，身分待查證（不可逕寫都市土地）")
    if edge < NEAR_EDGE_M:
        out["warnings"].append(f"點距邊界僅 {edge:.1f}m（近界）→ 建議人工確認相鄰筆")
    out["_geom"] = g   # 供 --out 寫 shp 用（不進 json）
    out["_landtype_none"] = (reg.get("zone") is None and reg.get("land_type") is None)
    return out


def write_base_shp(out: dict, out_dir: str, name: str = "基地") -> str:
    """把反查到的宗地寫成 基地.shp（欄名比照 cadastre_fetch，可直接餵 make_maps --trust-shp）。"""
    import geopandas as gpd  # noqa: PLC0415
    os.makedirs(out_dir, exist_ok=True)
    rec = {"縣市": out["county"], "鄉鎮": out["town"], "段": out["section"],
           "地號": out["landno"], "使分": out.get("zone"), "使類": out.get("land_type")}
    gdf = gpd.GeoDataFrame([rec], geometry=[out["_geom"]], crs=f"EPSG:{CRS}")
    path = os.path.join(out_dir, f"{name}.shp")
    gdf.to_file(path, encoding="utf-8")
    return path


def _print(out: dict) -> None:
    print(f"輸入：{out['input']}")
    print(f"座標：WGS84 {out['lat']},{out['lon']}（{out['coord_source']}）"
          f" → EPSG:{CRS} {out['x']},{out['y']}")
    if out["status"] != "OK":
        print(f"\n✘ {out['status']}：{out['message']}")
        for w in out["warnings"]:
            print(f"  ⚠ {w}")
        return
    print(f"路徑：{out['path']}（農業部 API {out['api_calls']} 次）")
    print("\n=== 反查結果 ===")
    print(f"  行政區　　 {out['county']} {out['town']}")
    print(f"  地段　　　 {out['section']}")
    print(f"  地號　　　 {out['landno']}   14碼 {out['code14']}")
    print(f"  使用分區　 {out['zone']}")
    print(f"  使用地類別 {out['land_type']}")
    print(f"  登記面積　 {out['reg_area']} ㎡（圖面積 {out['map_area']} ㎡）")
    print(f"  點距邊界　 {out['dist_to_edge_m']} m")
    for w in out["warnings"]:
        print(f"  ⚠ {w}")


def main() -> int:
    ap = argparse.ArgumentParser(description="座標／Google Maps 網址 → 現行地段地號（反查）")
    ap.add_argument("input", nargs="?", help="Google Maps 網址（可短網址）或 'lat,lon'")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--out", help="輸出資料夾，給了就寫 基地.shp（可餵 make_maps --trust-shp）")
    ap.add_argument("--name", default="基地", help="--out 時的 shp 檔名（預設 基地）")
    ap.add_argument("--json", action="store_true", help="以 JSON 輸出")
    ap.add_argument("--throttle", type=float, default=PROBE_THROTTLE_S)
    a = ap.parse_args()

    text = a.input
    if text is None and a.lat is not None and a.lon is not None:
        text = f"{a.lat},{a.lon}"
    if not text:
        ap.error("需給 網址／'lat,lon' 位置參數，或 --lat --lon")

    out = reverse(text, throttle=a.throttle)
    if a.out and out["status"] == "OK":
        out["base_shp"] = write_base_shp(out, a.out, a.name)

    if a.json:
        j = {k: v for k, v in out.items() if not k.startswith("_")}
        print(json.dumps(j, ensure_ascii=False, indent=2))
    else:
        _print(out)
        if out.get("base_shp"):
            print(f"\n基地 shp：{out['base_shp']}（可 make_maps --trust-shp 出圖）")
    return 0 if out["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
