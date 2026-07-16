# -*- coding: utf-8 -*-
"""產線自聚類:從帶屬性基地 shp 直接分出宗基地案場,產權威 sitemap(帶段欄)+ sites.shp。

取代 fishpv 的「案場定義」角色(2026-07-16 縮編定案):清冊無分基地欄時,案場
= 相鄰宗地的連通分量。由產線先聚類、先產 sitemap 過閘門,fishpv 之後只當
純臨路計算器(吃逐案 shp、吐臨路狀態),不再回頭定義案場——避免「聚類真相
在外部工具、產線拿到失真副本」(東石缺段欄事故的結構性根因)。

用法:
  python build_sites_sitemap.py --joined <面積join.shp> --out-dir <目錄>
      [--gap 0] [--match-sites <既有sites.shp>] [--compare <既有sitemap.csv>]

輸出:<out-dir>/sitemap.csv(段,地號,案場ID,案場名稱)、<out-dir>/sites.shp
編號規則:--match-sites 時沿用既有 site_id(幾何最大交集);否則依聚合面
質心 由北到南、同緯度由西到東 編 S001…(確定性,重跑不變)。
"""
import argparse
import csv
import sys
from pathlib import Path

import geopandas as gpd
from shapely.strtree import STRtree


def cluster(geoms, gap: float):
    """相鄰(或間距<gap)宗地連通分量;回傳每筆的群編號。"""
    probe = [g.buffer(gap / 2) for g in geoms] if gap > 0 else list(geoms)
    tree = STRtree(probe)
    parent = list(range(len(probe)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, g in enumerate(probe):
        for j in tree.query(g):
            j = int(j)
            if j > i and probe[j].intersects(g):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    roots = {}
    labels = []
    for i in range(len(probe)):
        r = find(i)
        labels.append(roots.setdefault(r, len(roots)))
    return labels


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--gap", type=float, default=0.0, help="視為同案場的最大間距(m),0=須相鄰")
    ap.add_argument("--match-sites", help="既有 sites.shp:沿用其 site_id(幾何最大交集)")
    ap.add_argument("--compare", help="既有 sitemap.csv:輸出逐筆分派差異")
    ap.add_argument("--name-suffix", default="宗基地")
    a = ap.parse_args()

    parcels = gpd.read_file(a.joined, encoding="big5")
    lncol = next(c for c in parcels.columns if "地號" in c)
    segcol = next((c for c in parcels.columns if "段" in c), None)
    if segcol is None:
        sys.exit("joined shp 缺地段欄——sitemap 必含段欄(跨段重複地號會分派失敗),中止")

    labels = cluster(list(parcels.geometry), a.gap)
    parcels = parcels.assign(_cl=labels)
    dissolved = parcels[["_cl", "geometry"]].dissolve(by="_cl")

    # 編 site_id
    if a.match_sites:
        old = gpd.read_file(a.match_sites)
        if old.crs != parcels.crs:
            old = old.to_crs(parcels.crs)
        ids, used = {}, set()
        for cl, row in dissolved.iterrows():
            inter = old[old.intersects(row.geometry)]
            if inter.empty:
                sys.exit(f"群 {cl} 與既有 sites 無交集,--match-sites 對不上,中止")
            best = inter.loc[inter.geometry.intersection(row.geometry).area.idxmax(), "site_id"]
            if best in used:
                sys.exit(f"site_id {best} 被兩群搶用(聚類與既有 sites 不一致),中止")
            ids[cl] = best
            used.add(best)
        if len(used) != len(old):
            sys.exit(f"既有 sites {len(old)} 個只對上 {len(used)} 個,中止")
    else:
        order = sorted(dissolved.index,
                       key=lambda c: (-dissolved.loc[c].geometry.centroid.y,
                                      dissolved.loc[c].geometry.centroid.x))
        ids = {cl: f"S{i+1:03d}" for i, cl in enumerate(order)}

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    rows = [{"段": str(p[segcol]), "地號": str(p[lncol]).strip(),
             "案場ID": ids[p["_cl"]], "案場名稱": f"{ids[p['_cl']]}{a.name_suffix}"}
            for _, p in parcels.iterrows()]
    with open(out / "sitemap.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["段", "地號", "案場ID", "案場名稱"])
        w.writeheader()
        w.writerows(rows)

    sites = dissolved.reset_index()
    sites["site_id"] = sites["_cl"].map(ids)
    grp = parcels.groupby("_cl")
    sites["n_parcels"] = sites["_cl"].map(grp.size())
    sites["area_m2"] = sites.geometry.area
    sites["landno_lis"] = sites["_cl"].map(
        grp[lncol].apply(lambda s: ",".join(map(str, s))[:250]))
    sites = sites.drop(columns="_cl").sort_values("site_id")
    sites.to_file(out / "sites.shp", encoding="utf-8")

    if a.compare and Path(a.compare).exists():
        oldmap = {}
        with open(a.compare, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                key = (r.get("段", "").strip(), r["地號"].strip())
                oldmap.setdefault(key if r.get("段") else ("*", r["地號"].strip()), []
                                  ).append(r["案場ID"])
        diffs = 0
        for r in rows:
            olds = oldmap.get((r["段"], r["地號"])) or oldmap.get(("*", r["地號"])) or []
            if olds and r["案場ID"] not in olds:
                print(f"[差異] {r['段']} {r['地號']}: {olds} -> {r['案場ID']}")
                diffs += 1
        print(f"與 {a.compare} 分派差異:{diffs} 筆")

    print(f"聚類 {len(parcels)} 宗地 -> {len(sites)} 案場(gap={a.gap}m);"
          f"輸出 {out / 'sitemap.csv'}、{out / 'sites.shp'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
