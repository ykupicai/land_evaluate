# -*- coding: utf-8 -*-
"""OSM（Overpass API）查基地周邊敏感設施與聚落 — 200 公尺條款的「篩選」工具

用途：土資場等法規訂有「與醫院／學校／文教設施／社福機構／十戶以上村莊或聚落
最短水平距離應大於 N 公尺」時，本機無此類點位圖資，先用 OSM 做**初篩**。

⚠️ 定位務必講清楚（寫進交付文件）：
  OSM 是群眾協作資料，**有漏繪就會漏判**，對「200 公尺內有無學校」這種法定
  禁止條件而言，**只能當篩選與現場查證的線索，不能取代函詢主管機關**。
  - 查到 → 幾乎確定有，該設施位置與距離仍須實測確認
  - 查不到 → **不等於沒有**，仍須函詢（教育局／衛生局／社會局／區公所）
  「十戶以上村莊或聚落」OSM 無對應標籤，本腳本以 building=house/residential/detached
  等住宅類建物**計數**近似（≥10 棟即列警示），並另列 place=hamlet/village 節點。

用法：
  python scripts/osm_sensitive_poi.py --joined <基地.shp> --dist 200 [--out <csv>]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import geopandas as gpd
from shapely.geometry import Point

OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# 單一 Overpass 查詢一次抓齊（用 nwr 取代 node/way/relation 三份，statement 數 ÷3），
# 回來後在本機依 tag 分類——比逐類發 8 個請求快，也不會打爆公共 API。
# ⚠️ 實測教訓（2026-07-29）：**裸鍵選擇器**（如 `["healthcare"]`、`["military"]`、
# `["historic"]`）會讓 Overpass 做全鍵掃描而掛住（120 秒無回應）。
# 一律給值約束（`=` 或 `~"^(...)$"`），查詢才走索引、瞬間回。
SELECTORS = [
    '["amenity"~"^(hospital|clinic|doctors|school|kindergarten|college|university|'
    'library|community_centre|arts_centre|place_of_worship|social_facility)$"]',
    '["healthcare"~"^(hospital|clinic|doctor|centre)$"]',
    '["social_facility"~"^(assisted_living|nursing_home|group_home|day_care|'
    'residential_home|shelter)$"]',
    '["tourism"="museum"]',
    '["landuse"="military"]',
    '["military"~"^(base|barracks|training_area|danger_area|naval_base|airfield)$"]',
    '["historic"~"^(monument|memorial|ruins|archaeological_site|building|'
    'castle|tomb|temple|city_gate|fort)$"]',
    '["heritage"~"^[0-9]$"]',
    '["building"~"^(house|residential|detached|apartments|dormitory|farm)$"]',
    '["place"~"^(hamlet|village|neighbourhood|suburb|town)$"]',
]

# 條文用語 → 分類規則（(tag, 值regex) 任一命中即歸類；順序即優先序）
CLASSIFY = [
    ("醫院", [("amenity", r"^(hospital|clinic)$"), ("healthcare", r".")]),
    ("學校", [("amenity", r"^(school|kindergarten|college|university)$")]),
    ("社福機構", [("amenity", r"^social_facility$"), ("social_facility", r".")]),
    ("文教設施", [("amenity", r"^(library|community_centre|arts_centre)$"),
                  ("tourism", r"^museum$")]),
    # ⚠️ 宗教場所單獨一類，**不自動計入「文教設施」**（2026-07-29 定案）：
    # 條文列舉的是「文教設施」，寺廟／教堂是否屬之須由主管機關認定；
    # 逕行計入會製造假警報，逕行排除又可能漏判（若該廟為古蹟則屬「名勝古蹟」款）。
    ("（待認定）宗教場所", [("amenity", r"^place_of_worship$")]),
    ("營區", [("landuse", r"^military$"), ("military", r".")]),
    ("名勝古蹟", [("historic", r"."), ("heritage", r".")]),
    ("住宅類建物", [("building", r"^(house|residential|detached|apartments|dormitory|farm)$")]),
    ("聚落節點", [("place", r"^(hamlet|village|neighbourhood|suburb|town)$")]),
]
LAW_GROUPS = ["醫院", "學校", "文教設施", "社福機構", "營區", "名勝古蹟",
              "（待認定）宗教場所"]


def overpass(query: str, timeout: int = 120) -> dict:
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
                body = ""
                try:
                    body = exc.read().decode("utf-8", "replace")[:400]
                except Exception:  # noqa: BLE001
                    pass
                last = f"HTTP {exc.code} {body}"
                print(f"  {url} HTTP {exc.code}：{body[:160]}", file=sys.stderr)
                if exc.code in (429, 504) and attempt == 1:
                    time.sleep(20)   # 限流/逾時 → 等一次再試（公共 API 禮貌）
                    continue
                break
            except Exception as exc:  # noqa: BLE001 — 換下一個鏡像
                last = f"{type(exc).__name__}: {exc}"
                print(f"  {url} 失敗（{type(exc).__name__}）", file=sys.stderr)
                break
        time.sleep(2)
    raise SystemExit(f"Overpass 全部鏡像失敗：{last}")


def build_query(bbox, selectors) -> str:
    """nwr = node+way+relation 合併寫法，statement 數只需 1/3。"""
    s, w, n, e = bbox
    parts = [f"nwr{sel}({s},{w},{n},{e});" for sel in selectors]
    return "[out:json][timeout:120];(" + "".join(parts) + ");out center tags;"


KEEP_TAGS = ("amenity", "healthcare", "social_facility", "building", "place",
             "landuse", "military", "historic", "heritage", "tourism")


def classify(tags: dict) -> str:
    import re
    for label, rules in CLASSIFY:
        for k, pat in rules:
            v = tags.get(k)
            if v and re.search(pat, str(v)):
                return label
    return ""


def elems_to_gdf(js) -> gpd.GeoDataFrame:
    rows = []
    for el in js.get("elements", []):
        c = el.get("center") or ({"lat": el.get("lat"), "lon": el.get("lon")}
                                 if el.get("lat") is not None else None)
        if not c or c.get("lat") is None:
            continue
        t = el.get("tags", {}) or {}
        rows.append({
            "osm": f"{el['type']}/{el['id']}",
            "類別": classify(t),
            "name": t.get("name") or t.get("name:zh") or "",
            "tags": ";".join(f"{k}={v}" for k, v in sorted(t.items()) if k in KEEP_TAGS),
            "geometry": Point(c["lon"], c["lat"]),
        })
    if not rows:   # 查無任何圖徵（魚塭區常見）——先建空框再回，不可直接丟進建構式
        return gpd.GeoDataFrame(
            {"osm": [], "類別": [], "name": [], "tags": [], "dist_m": []},
            geometry=gpd.GeoSeries([], crs=4326))
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=4326)


# 單一類別的窄選擇器（給 nearest_category 用，只查該類別、不查全部10種，較省 API 負擔）
CATEGORY_SELECTORS = {
    "營區": ['["landuse"="military"]',
            '["military"~"^(base|barracks|training_area|danger_area|naval_base|airfield)$"]'],
}


def nearest_category(geom, category: str, dist: float, log: list) -> dict:
    """geom 為 EPSG:3826 幾何；查 OSM 單一類別（目前僅「營區」），回最近距離與清單。

    ★ 判定刻意不給「未觸及」——OSM 查無不能當作官方確認無涉及（尤其營區屬機密，
    OSM 標繪完整度更難保證），一律回「須函詢」，只把 OSM 結果當參考距離附註。
    見 osm_sensitive_poi.py 開頭 docstring 的定位說明。
    """
    sels = CATEGORY_SELECTORS.get(category)
    if not sels:
        raise ValueError(f"nearest_category 未支援類別：{category}")
    crs = "EPSG:3826"
    bb = gpd.GeoSeries([geom.buffer(dist + 50)], crs=crs).to_crs(4326).total_bounds
    bbox = (bb[1], bb[0], bb[3], bb[2])
    rec = {"類別": category, "來源": "OpenStreetMap（Overpass API，群眾協作資料）"}
    try:
        js = overpass(build_query(bbox, sels))
    except SystemExit as e:  # noqa: BLE001 — overpass() 用 SystemExit 傳遞失敗
        log.append(f"    ⚠ OSM {category} 查詢失敗：{e} → 純函詢，不附 OSM 參考距離")
        rec["判定"] = "須函詢（OSM查詢失敗）"
        return rec
    g = elems_to_gdf(js)
    if len(g):
        g = g.to_crs(crs)
        g["dist_m"] = g.geometry.distance(geom)
        g = g.sort_values("dist_m")
    within = g[g["dist_m"] <= dist] if len(g) else g
    rec["門檻內筆數"] = int(len(within))
    rec["最近_m"] = float(g.iloc[0]["dist_m"]) if len(g) else None
    rec["最近名稱"] = (g.iloc[0]["name"] or "(未命名)") if len(g) else None
    rec["判定"] = "須函詢"   # 恆為須函詢，OSM 結果只附參考距離，見上方 docstring
    if len(within):
        log.append(f"    ⚠ OSM {category}：{dist:,.0f}m 內 {len(within)} 筆"
                   f"（最近 {rec['最近名稱']} {rec['最近_m']:,.1f}m）——僅供參考，"
                   f"OSM 非官方確認，法定認定仍須函詢")
    else:
        near_s = f"（最近 {rec['最近名稱']} {rec['最近_m']:,.0f}m）" if rec["最近_m"] is not None else "（查詢範圍內無紀錄）"
        log.append(f"    ・OSM {category}：{dist:,.0f}m 內查無{near_s}——"
                   f"OSM 查無不代表官方確認無，機密設施尤然，仍須函詢")
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True)
    ap.add_argument("--dist", type=float, default=200.0, help="法定距離門檻(公尺)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    site = gpd.read_file(a.joined, engine="pyogrio")
    su = site.geometry.union_all()
    # 查詢範圍多抓 50m，避免邊界剛好落在 bbox 外
    bb = gpd.GeoSeries([su.buffer(a.dist + 50)], crs=site.crs).to_crs(4326).total_bounds
    bbox = (bb[1], bb[0], bb[3], bb[2])   # S,W,N,E
    print(f"基地 {site.area.sum():,.0f} m²；查詢半徑 {a.dist:.0f}+50 m")
    print(f"bbox(S,W,N,E)={bbox}\n")

    js = overpass(build_query(bbox, SELECTORS))
    g = elems_to_gdf(js)
    print(f"OSM 回傳 {len(g)} 個圖徵\n")
    if len(g):
        g = g.to_crs(site.crs)
        g["dist_m"] = g.geometry.distance(su)
        g = g.sort_values("dist_m")
    near = g[g["dist_m"] <= a.dist] if len(g) else g

    out_rows = []
    for label in LAW_GROUPS:
        sub = near[near["類別"] == label] if len(near) else near
        if len(sub) == 0:
            print(f"  ✓ {label}：{a.dist:.0f} m 內 OSM 無紀錄（★不等於沒有，仍須函詢）")
            out_rows.append([label, 0, "", "", "OSM無紀錄→仍須函詢"])
        else:
            print(f"  ✗ {label}：{a.dist:.0f} m 內 {len(sub)} 筆")
            for _, r in sub.head(5).iterrows():
                print(f"       {r['dist_m']:6.1f} m  {r['name'] or '(未命名)'}  [{r['tags']}]")
            n0 = sub.iloc[0]
            out_rows.append([label, len(sub), n0["name"] or "(未命名)",
                             round(n0["dist_m"], 1), n0["tags"]])

    # 十戶以上村莊或聚落：住宅類建物計數 + place 節點近似
    n_house = int((near["類別"] == "住宅類建物").sum()) if len(near) else 0
    places = near[near["類別"] == "聚落節點"] if len(near) else near
    near_place = [(r["name"] or "(未命名)", round(r["dist_m"], 1))
                  for _, r in places.iterrows()] if len(places) else []
    # ⚠️ 實測教訓（2026-07-29 安南案）：臺灣鄉間 OSM **建物幾乎沒繪**（該案 1,500 m 內
    # 住宅類建物 0 棟，但同範圍有 place=hamlet「港西」「十一戶子」兩個聚落節點）。
    # 故「十戶以上」不可用建物棟數判斷，**以 place 節點為主要信號**；建物數僅供參考。
    n_all_house = int((g["類別"] == "住宅類建物").sum()) if len(g) else 0
    if near_place:
        verdict = f"★{a.dist:.0f}m內有聚落節點 {near_place} → 疑觸及，須查證戶數"
    elif n_all_house == 0:
        verdict = (f"{a.dist:.0f}m內無聚落節點；惟本區 OSM 建物繪製稀疏"
                   f"（全查詢範圍住宅類建物僅 {n_all_house} 棟），"
                   f"**建物棟數不足以判斷戶數**，須以門牌或現場查證")
    else:
        verdict = (f"{a.dist:.0f}m內無聚落節點、住宅類建物 {n_house} 棟"
                   f"（{'≥10→疑為聚落' if n_house >= 10 else '<10→初判非聚落'}），仍須查證")
    print(f"\n  十戶以上村莊或聚落：{verdict}")
    print(f"       {a.dist:.0f}m內住宅類建物 {n_house} 棟｜place 節點 "
          f"{near_place if near_place else '無'}")
    out_rows.append(["十戶以上村莊或聚落", n_house, str(near_place), "", verdict])

    # 門檻外但最近的幾個，供判斷「差多少」
    if len(g):
        outside = g[(g["dist_m"] > a.dist) & (g["類別"].isin(LAW_GROUPS))].head(5)
        if len(outside):
            print(f"\n  （參考）{a.dist:.0f} m 外最近的法定類別設施：")
            for _, r in outside.iterrows():
                print(f"       {r['dist_m']:7.1f} m  {r['類別']}  "
                      f"{r['name'] or '(未命名)'}  [{r['tags']}]")

    if a.out:
        import csv
        with io.open(a.out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["條文類別", f"{a.dist:.0f}m內筆數", "最近者名稱/place",
                        "最近距離_m", "備註"])
            w.writerows(out_rows)
        print(f"\n→ 已寫 {a.out}")
    print("\n⚠️ OSM 為群眾協作資料，查無不等於沒有；本表僅供初篩，"
          "法定認定仍須函詢教育局／衛生局／社會局／區公所／國防部。")


if __name__ == "__main__":
    main()
