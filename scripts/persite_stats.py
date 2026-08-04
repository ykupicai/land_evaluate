# -*- coding: utf-8 -*-
"""逐案場（S001／S002…）實算統計 — 簡報「所有評估內容分案場說明」的數字來源

★ 案件成果沉澱機制：由安南案一次性腳本 _persite_stats.py 通用化（2026-07-29）。
簡報引用的每個數字都要能指到 `逐案場統計.json`，不得自行推估。

用法（系統 Python）：
  python scripts/persite_stats.py --joined <面積join.shp> \
      [--sites <臨路評估/sites.shp>] [--road <臨路評估/成果.xlsx>] \
      [--overlay "地下水管制區第二級=<shp路徑>"] \
      [--overlay "淹水潛勢24hr500mm=<shp路徑>::type"] \
      [--out <案件資料夾>]

每案場輸出：面積／公頃、逐筆地號、公私別統計與公有地號、都市計畫分區組成、
臨路狀態與鄰接路寬（讀 road_access 成果）、指定 overlay 的套疊面積（::欄名＝分級統計）。
"""
from __future__ import annotations

import argparse
import io
import json
import os

import geopandas as gpd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URBAN = os.path.join(REPO, "都市計畫", "全台都市計畫土地使用分區.shp")


def classify_ownership(v) -> str | None:
    """公私別欄值 → '公'／'私'／None（無法判定）。

    ⚠ 不可用 `== "公"` 精確比對（2026-08-04 龍崎案實錯：⓪a cadastre_fetch 寫的值是
    「私(推論)」、TCDGIS 匯出可能是「公/國有」，精確比對讓公有與私有筆數**同時歸 0**，
    而 逐案場統計.json 是簡報數字的唯一來源）。判字首：「公」開頭＝公有、「私」開頭＝私有，
    其餘（空白、None、「未登記」……）回 None、不計入任一類——空白不等於私有，
    見 CLAUDE.md「本機地籍快照的所有權欄不可作為權屬依據」。
    """
    s = str(v or "").strip()
    if s.startswith("公"):
        return "公"
    if s.startswith("私"):
        return "私"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True)
    ap.add_argument("--sites", default=None,
                    help="案場面 shp；省略時預設 <案>/臨路評估/sites.shp")
    ap.add_argument("--road", default=None,
                    help="road_access 成果.xlsx；省略時預設 <案>/臨路評估/成果.xlsx")
    ap.add_argument("--overlay", action="append", default=[],
                    help='名稱=路徑[::分級欄]，可多次')
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    case_dir = a.out or os.path.dirname(os.path.dirname(os.path.abspath(a.joined)))
    sites_p = a.sites or os.path.join(case_dir, "臨路評估", "sites.shp")
    road_p = a.road or os.path.join(case_dir, "臨路評估", "成果.xlsx")

    site = gpd.read_file(a.joined, engine="pyogrio")
    sites = gpd.read_file(sites_p, engine="pyogrio")
    out = {}

    pub_col = next((c for c in ("公私別",) if c in site.columns), None)
    no_col = next((c for c in ("地號",) if c in site.columns), None)

    for _, s in sites.iterrows():
        sid, g = s["site_id"], s.geometry
        d = {"面積_m2": round(g.area, 2), "公頃": round(g.area / 10000, 4)}
        inner = site[site.geometry.intersects(g.buffer(-0.5))]
        if no_col:
            d["地號"] = sorted(inner[no_col].astype(str).tolist())
        if pub_col:
            cls = inner[pub_col].map(classify_ownership)
            d["公有筆數"] = int((cls == "公").sum())
            d["私有筆數"] = int((cls == "私").sum())
            n_unk = int(cls.isna().sum())
            if n_unk:
                d["公私別無法判定筆數"] = n_unk   # 空白≠私有，明著標出來
            pub = inner[cls == "公"]
            d["公有面積_m2"] = round(float(pub.area.sum()), 2)
            if no_col:
                d["公有地號"] = sorted(pub[no_col].astype(str).tolist())

        # 都市計畫分區組成
        if os.path.exists(URBAN):
            up = gpd.read_file(URBAN, engine="pyogrio",
                               bbox=tuple(gpd.GeoSeries([g], crs=site.crs).total_bounds))
            up = up.to_crs(site.crs)
            up["ov"] = up.geometry.intersection(g).area
            zz = up[up["ov"] > 1].groupby("zone")["ov"].sum().sort_values(ascending=False)
            d["都市計畫分區"] = {k: round(v, 2) for k, v in zz.items()}

        # 使用者指定的 overlay
        for spec in a.overlay:
            name, _, rest = spec.partition("=")
            path, _, fld = rest.partition("::")
            if not os.path.exists(path):
                d[name] = "（圖層不存在）"
                continue
            gg = None
            for enc in ("utf-8", "big5", None):
                try:
                    gg = gpd.read_file(path, engine="pyogrio",
                                       **({"encoding": enc} if enc else {}))
                    break
                except Exception:  # noqa: BLE001
                    continue
            if gg is None:
                d[name] = "（讀取失敗）"
                continue
            if gg.crs is None:
                gg = gg.set_crs(3826)
            elif gg.crs != site.crs:
                gg = gg.to_crs(site.crs)
            gg["ov"] = gg.geometry.intersection(g).area
            hit = gg[gg["ov"] > 1]
            if fld and fld in hit.columns:
                d[name] = {str(k): round(v, 2)
                           for k, v in hit.groupby(fld)["ov"].sum().items()}
            else:
                d[name] = round(float(hit["ov"].sum()), 2)
        out[sid] = d

    # 臨路與地形（road_access 成果）
    if os.path.exists(road_p):
        import pandas as pd
        t = pd.read_excel(road_p)
        for _, r in t.iterrows():
            sid = r["site_id"]
            out.setdefault(sid, {})
            out[sid].update({
                "臨路狀態": r.get("臨路狀態"),
                "鄰接路寬中位數_m": round(float(r["鄰接路寬中位數_m"]), 2)
                if "鄰接路寬中位數_m" in t.columns else None,
                "陸域_m2": round(float(r["陸域面積_m2"]), 0),
                "水域_m2": round(float(r["水域面積_m2"]), 0),
                "主要地形": r.get("主要地形"),
            })

    p = os.path.join(case_dir, "逐案場統計.json")
    io.open(p, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))
    for sid, d in out.items():
        print("=" * 50)
        print(sid)
        for k, v in d.items():
            print(f"  {k} = {v}")
    print("\n→", p)


if __name__ == "__main__":
    main()
