# -*- coding: utf-8 -*-
"""國土測繪中心設施環域查詢（免認證 API）— 200 公尺條款的醫療／文教／鄰避項目

★ 建立緣由（2026-07-30）：「與醫院、學校、文教設施…最短水平距離應大於 200 公尺」
  原本整項列為 inquiry（須函詢教育局／衛生局／社會局／區公所），簡報只能用 OSM 初篩。
  內政部國土測繪中心提供「坐標＋半徑」的免認證環域查詢 API，回傳設施名稱、地址與距離，
  可直接實算，函詢對象因此從五個機關縮到兩個（社福機構、營區）。

端點（資料來源：data.gov.tw 內政部國土測繪中心「查詢醫療／文教／鄰避設施」）：
  med  醫療設施   dataset 139250
  edu  文教設施   dataset 139251（含各級學校、幼兒園、圖書館、社教館）
  dis  鄰避設施   dataset 152928（公墓、焚化廠、掩埋場…；非本條款項目，列為參考資訊）
  ⚠ 工商設施（dataset 139249）文件寫的 /com 端點實測回空字串（非空陣列），
    端點碼可能有誤或已停用——不要當成「查無設施」，需要時先人工驗證。

★ 距離算法：API 是「以點為圓心」，但條文要的是**基地邊界**到設施的最短水平距離。
  故本模組以基地重心送查、半徑放大到（門檻 + 重心到基地最遠點 + 緩衝），
  再把回傳設施點投影回 EPSG:3826 **重算到基地面的精確距離**，不直接採用 API 的 distance。

★ 覆蓋率限制：API 查無 ≠ 現地沒有（同 OSM 的教訓）。社福機構本 API 未涵蓋，仍須函詢。

用法：
  python scripts/nlsc_facility.py --joined <面積join.shp> [--dist 200] \
      [--endpoints med edu dis] [--out <案件資料夾>]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

import geopandas as gpd
from shapely.geometry import Point

API = "https://api.nlsc.gov.tw/other/MarkBufferAnlys/{ep}/{lon}/{lat}/{r}"
NAMES = {"med": "醫療設施", "edu": "文教設施", "dis": "鄰避設施", "com": "工商設施"}
MAX_R = 20_000


def query(ep: str, lon: float, lat: float, radius: int, tries: int = 3) -> list[dict]:
    """呼叫環域查詢 API。回 [] 表示該半徑內查無；連線失敗則丟例外（不可當成查無）。"""
    url = API.format(ep=ep, lon=f"{lon:.6f}", lat=f"{lat:.6f}", r=int(min(radius, MAX_R)))
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                raw = r.read().decode("utf-8", "replace").strip()
            if raw == "":
                raise ValueError(f"{ep} 端點回空字串（非空陣列）——端點碼可能無效，不得視為查無")
            return json.loads(raw)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"{url} 連線失敗：{last}")


def _to_items(rows: list[dict], geom) -> list[dict]:
    if not rows:
        return []
    g = gpd.GeoDataFrame(
        rows, geometry=gpd.points_from_xy([r["lon"] for r in rows],
                                          [r["lat"] for r in rows]),
        crs=4326).to_crs(3826)
    g["實距_m"] = g.geometry.distance(geom).round(1)
    return [{"名稱": r.get("name") or r.get("sname"), "地址": r.get("addr"),
             "實距_m": float(r["實距_m"])}
            for _, r in g.sort_values("實距_m").iterrows()]


def nearest(geom, endpoints: list[str], dist: float, log: list) -> dict:
    """geom 為 EPSG:3826 幾何；回各端點在門檻內的設施與最近距離（皆為到基地面的實距）。"""
    cen = gpd.GeoSeries([geom.centroid], crs=3826).to_crs(4326).iloc[0]
    x0, y0, x1, y1 = geom.bounds
    R = max(geom.centroid.distance(Point(x, y))
            for x in (x0, x1) for y in (y0, y1))
    radius = int(dist + R + 100)
    out = {}
    for ep in endpoints:
        rec = {"設施類別": NAMES.get(ep, ep), "查詢半徑_m": radius}
        try:
            items = _to_items(query(ep, cen.x, cen.y, radius), geom)
            # 門檻內查無時往外探測，讓交付文件能寫出「最近者在 N 公尺外」而非只說「無」
            for probe in (3_000, 10_000):
                if items or probe <= radius:
                    break
                items = _to_items(query(ep, cen.x, cen.y, int(probe + R)), geom)
                rec["探測半徑_m"] = int(probe + R)
        except Exception as e:  # noqa: BLE001
            log.append(f"    ⚠ {NAMES.get(ep, ep)}：{e} → 須人工確認（不得記為查無）")
            rec["判定"] = "API失敗須確認"
            out[ep] = rec
            continue
        within = [i for i in items if i["實距_m"] <= dist]
        rec["門檻內"] = within
        rec["最近_m"] = items[0]["實距_m"] if items else None
        rec["最近名稱"] = items[0]["名稱"] if items else None
        rec["判定"] = "觸及" if within else "未觸及"
        if within:
            log.append(f"    ✗ {NAMES.get(ep, ep)}：{dist:,.0f}m 內 {len(within)} 處"
                       f"（最近 {within[0]['名稱']} {within[0]['實距_m']:,.1f}m）")
        else:
            rad = rec.get("探測半徑_m", radius)
            near = (f"（最近：{rec['最近名稱']} {rec['最近_m']:,.0f}m）"
                    if rec["最近_m"] is not None else f"（半徑 {rad:,}m 內查無）")
            log.append(f"    ✓ {NAMES.get(ep, ep)}：{dist:,.0f}m 內無{near}")
        out[ep] = rec
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True)
    ap.add_argument("--dist", type=float, default=200)
    ap.add_argument("--endpoints", nargs="+", default=["med", "edu", "dis"])
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    site = gpd.read_file(a.joined, engine="pyogrio")
    if site.crs and site.crs.to_epsg() != 3826:
        site = site.to_crs(3826)
    su = site.geometry.union_all()
    log = [f"國土測繪中心設施環域查詢（門檻 {a.dist:,.0f} 公尺，距離為設施到基地面實距）"]
    res = nearest(su, a.endpoints, a.dist, log)
    log.append("★ API 查無≠現地沒有；社福機構本 API 未涵蓋，仍須函詢社會局。")
    txt = "\n".join(log)
    print(txt)
    out_dir = a.out or os.path.dirname(os.path.dirname(os.path.abspath(a.joined)))
    # --out 給子資料夾而它還不存在時，原本查完才在寫檔處 FileNotFoundError（2026-10-06 基隆案實踩）
    os.makedirs(out_dir, exist_ok=True)
    io.open(os.path.join(out_dir, "設施環域查詢.txt"), "w", encoding="utf-8", newline="\n").write(txt)
    io.open(os.path.join(out_dir, "設施環域查詢.json"), "w", encoding="utf-8", newline="\n").write(
        json.dumps(res, ensure_ascii=False, indent=1))
    print(f"\n→ {os.path.join(out_dir, '設施環域查詢.txt')} / .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
