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
        # /maps/search/<lat>,+<lon>（座標搜尋，短網址常展開成此形；+/空白皆可）——
        # 這是使用者實際查的點，非地圖中心，不標警告。
        m = re.search(r"/(?:search|dir|place)/(-?\d+\.\d+)[,\s+]+(-?\d+\.\d+)", full)
        if m:
            return float(m.group(2)), float(m.group(1)), "座標搜尋(/search)"
        # ?q=／query=／ll=／daddr=lat,lon（查詢參數帶座標）
        m = re.search(r"[?&](?:q|query|ll|daddr|saddr|center)=(-?\d+\.\d+),\+?(-?\d+\.\d+)", full)
        if m:
            return float(m.group(2)), float(m.group(1)), "查詢參數座標(q=)"
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


def _county_name_variants(county: str) -> list[str]:
    """縣市檔名候選：本機命名不一致——六都用「台」（台北/台中/台南市），但臺東縣
    用「臺」；不能一律 臺→台（否則臺東縣 → 台東縣.shp 查無）。原名、臺→台、台→臺
    三式都試。"""
    out = []
    for c in (county, county.replace("臺", "台"), county.replace("台", "臺")):
        if c not in out:
            out.append(c)
    return out


def county_shp_path(county: str) -> str | None:
    for fn in _county_name_variants(county):
        p = os.path.join(CAD_DIR, fn + ".shp")
        if os.path.exists(p):
            return p
    return None


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
        base = os.path.splitext(os.path.basename(shp))[0]
        # 只掃各縣市地籍 shp；排除 段藉.shp（段/小段索引，覆蓋全台、無「宗地母」欄，
        # 會被誤選並在 local_candidate 炸 KeyError）與其他非縣市檔。
        if not base.endswith(("縣", "市")):
            continue
        try:
            g = gpd.read_file(shp, bbox=(x - 5, y - 5, x + 5, y + 5))
        except Exception:  # noqa: BLE001
            continue
        if len(g) and g.contains(pt).any():
            return base
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
    # 段碼／事所碼為主鍵（段名文字比對不可靠：異體字、「段」字後綴、重測改名，
    # 一律以段碼為準，見 memory section-code-as-key）；no8＝母4+子4，供新舊地號 API。
    sec4 = str(r.get("段碼", "")).split(".")[0].strip().zfill(4)
    unit = str(r.get("事所碼", "")).strip()
    no8 = mother.zfill(4) + child.zfill(4)
    return {
        "city": r["縣市"], "town": r["鄉鎮"], "seg": r["段"], "subseg": r["小段"],
        "secname": full_section_name(r["段"], r["小段"]),
        "mother": mother, "landno": landno,
        "sec4": sec4, "unit": unit, "no8": no8,
        "snap_area": float(r.geometry.area), "snap_landtype": r["使類"],
    }


# --------------------------------------------------------------------------
# 5b. 命中判定與 fallback（近界容差 A、新舊地號 API B1）
# --------------------------------------------------------------------------
def hit_or_near(g, pt, tol: float = NEAR_EDGE_M):
    """幾何含點、或點在外部但距邊界 ≤tol → 視為命中。回 (命中?, 邊界距離, 是否近界外側)。

    ★座標反查（Google Maps 圖釘）必然有數公尺誤差，圖釘落在宗地界址外緣幾公尺是常態；
    嚴格 contains 會把它踢掉、再誤判成分割／重測。2026-08-21 芳寶段755 實錯（圖釘離
    界址 1.47m→誤判跨母號）。tol 用 NEAR_EDGE_M(5m)。"""
    if g is None:
        return False, None, False
    if g.contains(pt):
        return True, g.boundary.distance(pt), False
    d = g.distance(pt)          # 點在外部 → 到多邊形的最短距離
    return (d <= tol), d, (d <= tol)


def landno_to_no8(landno: str) -> str:
    """'755' / '755-1' → 8 碼（母4+子4）。"""
    if "-" in landno:
        mo, ch = landno.split("-", 1)
    else:
        mo, ch = landno, "0"
    return mo.strip().zfill(4) + ch.strip().zfill(4)


def newold_query(unit: str, sec4: str, no8: str, type_: str = "N",
                 timeout: int = 40) -> tuple[str, str] | None:
    """MOI 新舊地號查詢（MOI_API_011）：舊查新(N)／新查舊(O)。

    回 (新段碼末4, 新8碼) 或 None（無異動紀錄／查詢失敗）。重測改號時用它把舊地號
    橋接到現行地號（本機候選現行界址不含點、且非近界時的 fallback）。"""
    if not unit or not sec4:
        return None
    url = "https://openapi.moi.gov.tw/WEBAPI/LandNewOldQuery/1.0/QueryLandNo"
    body = [{"UNIT": unit, "SEC": sec4, "NO": no8, "TYPE": type_}]
    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None
    resp = (j.get("RESPONSE") or [{}])[0]
    ch = resp.get("LANDSNCHANGE")
    if not ch:
        return None
    row = ch[0] if isinstance(ch, list) else ch
    new_sec = str(row.get("SEC") or "").strip()
    new_no = str(row.get("NO") or "").strip()
    if len(new_sec) >= 4 and len(new_no) == 8:
        return new_sec[-4:], new_no
    return None


def neighbor_sweep(shp, x, y, pt, city, town, moa, api, throttle,
                   radius: float = 45.0, topn: int = 14):
    """點附近快照宗地逐筆查農業部現行界址 → 回第一個含點／近界者，或 None。

    最後手段：候選、子號探測、新舊地號都不中時，點多半落在相鄰宗地（快照界址比現行大／
    位移）。用段碼取現行段名再查（不用段名文字）。"""
    import geopandas as gpd  # noqa: PLC0415
    from shapely.geometry import shape as _shape  # noqa: PLC0415
    try:
        g = gpd.read_file(shp, bbox=(x - radius, y - radius, x + radius, y + radius))
    except Exception:  # noqa: BLE001
        return None
    if not len(g):
        return None
    g = g.assign(_d=g.geometry.distance(pt)).sort_values("_d")
    calls = 0
    for _, r in g.head(topn).iterrows():
        sec4 = str(r.get("段碼", "")).split(".")[0].strip().zfill(4)
        try:
            name = moa.section_name_of(city, town, sec4)
        except Exception:  # noqa: BLE001
            name = None
        if not name:
            continue
        mo, ch = str(int(r["宗地母"])), str(int(r["宗地子"]))
        landno = f"{mo}-{ch}" if ch != "0" else mo
        time.sleep(throttle)
        rec = api.query(city, town, name, [landno])["records"][0]
        calls += 1
        gg = _shape(rec["geometry"]) if rec.get("ok") and rec.get("geometry") else None
        ok, e, near = hit_or_near(gg, pt)
        if ok:
            return {"landno": landno, "secname": name, "g": gg, "rec": rec,
                    "near": near, "api_calls": calls}
    return None


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

    # C：段以段碼為主鍵——用段碼反查農業部現行段名，避開段名文字比對（異體字／「段」字
    # 後綴／重測改名）。段碼在現行版查無＝整段重測改號，直接走新舊地號 fallback。
    cur_secname = None
    try:
        if cand["sec4"]:
            cur_secname = moa.section_name_of(cand["city"], cand["town"], cand["sec4"])
    except Exception:  # noqa: BLE001
        cur_secname = None
    secname = cur_secname or cand["secname"]
    out["section"] = secname
    if cur_secname and cur_secname != cand["secname"]:
        out["warnings"].append(
            f"段名以段碼{cand['sec4']}校正：快照『{cand['secname']}』→現行『{cur_secname}』")
    section_renamed = bool(cand["sec4"]) and cur_secname is None

    def geom_of(sec, no):
        rec = api.query(cand["city"], cand["town"], sec, [no])["records"][0]
        g = shape(rec["geometry"]) if rec["ok"] and rec.get("geometry") else None
        return g, rec

    api_calls = 0
    hit = g = rec = None
    near_flag = False

    # 1) 候選本身（A：含點或近界 ≤NEAR_EDGE_M 即命中）
    if not section_renamed:
        g0, rec0 = geom_of(secname, cand["landno"]); api_calls += 1
        ok0, e0, n0 = hit_or_near(g0, pt)
        if ok0:
            hit, g, rec, near_flag = cand["landno"], g0, rec0, n0
            out["path"] = "候選即現行" + (f"（近界外緣 {e0:.1f}m）" if n0 else "（免探測）")
        else:
            # 2) 收斂探測同母號子號（同樣採近界容差）
            out["path"] = ("候選查無→探測子號" if g0 is None else "候選現行不含點→探測子號")
            miss, n = 0, 0
            while miss < PROBE_MISS_STOP:
                n += 1
                time.sleep(throttle)
                gg, rr = geom_of(secname, f"{cand['mother']}-{n}"); api_calls += 1
                if gg is None:
                    miss += 1
                    continue
                miss = 0
                okk, e2, nn = hit_or_near(gg, pt)
                if okk:
                    hit, g, rec, near_flag = f"{cand['mother']}-{n}", gg, rr, nn
                    break

    # 3) B1：新舊地號 API 橋接（重測改號／候選整段消失時）
    if hit is None:
        res = newold_query(cand["unit"], cand["sec4"], cand["no8"], "N")
        if res:
            new_sec4, new_no8 = res
            new_name = None
            try:
                new_name = moa.section_name_of(cand["city"], cand["town"], new_sec4)
            except Exception:  # noqa: BLE001
                new_name = None
            if new_name:
                mo, ch = str(int(new_no8[:4])), str(int(new_no8[4:]))
                new_landno = mo if ch == "0" else f"{mo}-{ch}"
                gN, rN = geom_of(new_name, new_landno); api_calls += 1
                okN, eN, nnN = hit_or_near(gN, pt)
                if okN:
                    hit, g, rec, near_flag, secname = new_landno, gN, rN, nnN, new_name
                    out["section"] = new_name
                    out["path"] = (f"新舊地號橋接（舊 {cand['secname']}{cand['landno']} → "
                                   f"現行 {new_name}{new_landno}）")
                    out["warnings"].append(
                        f"經 MOI 新舊地號查詢橋接（重測改號），現行地號 {new_name}{new_landno}")

    # 4) B2：鄰地掃描（點附近快照宗地逐筆驗農業部現行界址）
    if hit is None:
        sw = neighbor_sweep(shp, x, y, pt, cand["city"], cand["town"], moa, api, throttle)
        if sw:
            hit, g, rec, secname, near_flag = (sw["landno"], sw["g"], sw["rec"],
                                               sw["secname"], sw["near"])
            out["section"] = secname
            out["path"] = "鄰地掃描命中（候選相鄰宗地）"
            api_calls += sw["api_calls"]

    # 全數未命中 → 需人工鑑界（E：正名，不再一律喊「跨母號分割」）
    if hit is None:
        out["status"] = "FAIL_NEEDS_SURVEY"
        out["message"] = ("候選／子號探測／新舊地號查詢／鄰地掃描皆未命中現行含此點之宗地 → "
                          "此點需人工地籍鑑界確認（可能重測改號無新舊對照、或圖釘偏移過大）")
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
    if near_flag:
        out["warnings"].append(
            f"圖釘落在本宗地界址外緣 {edge:.1f}m（座標反查誤差內，已認定為本宗地）"
            "→ 界址／相鄰筆以地籍鑑界為準")
    elif edge < NEAR_EDGE_M:
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
