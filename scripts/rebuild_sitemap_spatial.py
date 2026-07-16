# -*- coding: utf-8 -*-
"""以 fishpv 聚類成果(sites.shp)的幾何為權威,重建帶「段」欄的 sitemap.csv。

背景:多案場清冊若本身無分基地欄,案場是由宗地幾何空間聚類而來;匯出 sitemap 時
若只寫 地號 而漏了 段,跨段重複地號(如 東石案 159/1069/1080/1091)的純地號鍵會被
load_sitemap 判定衝突移除,又建不出 段+地號 複合鍵 → 這些宗地分派失敗、
單筆案場整案消失。本腳本用「宗地代表點落在哪個 site 面內」重建逐筆對應,
輸出含 段 欄的 sitemap,並與舊 sitemap 差異比對供人工確認。

用法:
  python rebuild_sitemap_spatial.py --joined <面積join.shp> --sites <sites.shp> \
      --old <舊sitemap.csv> --out <新sitemap.csv>
"""
import argparse
import csv
import shutil
import sys
from pathlib import Path

import geopandas as gpd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True, help="帶屬性基地 shp(需 地號/地段名稱 欄)")
    ap.add_argument("--sites", required=True, help="fishpv sites.shp(需 site_id 欄)")
    ap.add_argument("--old", help="舊 sitemap.csv(差異比對+備份)")
    ap.add_argument("--out", required=True, help="輸出 sitemap.csv(欄:段,地號,案場ID,案場名稱)")
    ap.add_argument("--name-suffix", default="宗基地", help="案場名稱=site_id+此字尾")
    a = ap.parse_args()

    parcels = gpd.read_file(a.joined, encoding="big5")
    sites = gpd.read_file(a.sites)
    if parcels.crs != sites.crs:
        sites = sites.to_crs(parcels.crs)

    lncol = next(c for c in parcels.columns if "地號" in c)
    segcol = next((c for c in parcels.columns if "段" in c), None)
    if segcol is None:
        sys.exit("joined shp 缺地段欄,無法輸出段+地號複合鍵")

    sidx = sites.sindex
    rows, unassigned = [], []
    for _, p in parcels.iterrows():
        pt = p.geometry.representative_point()
        cand = sites.iloc[list(sidx.intersection(pt.bounds))]
        hit = cand[cand.contains(pt)]
        if hit.empty:  # 聚類面若有微縮邊界,退而取交集面積最大者
            inter = sites.iloc[list(sidx.intersection(p.geometry.bounds))]
            inter = inter[inter.intersects(p.geometry)]
            if inter.empty:
                unassigned.append((p[segcol], p[lncol]))
                continue
            hit = inter.loc[[inter.geometry.intersection(p.geometry).area.idxmax()]]
        sid = hit.iloc[0]["site_id"]
        rows.append({"段": str(p[segcol]), "地號": str(p[lncol]).strip(),
                     "案場ID": sid, "案場名稱": f"{sid}{a.name_suffix}"})

    if unassigned:
        for seg, ln in unassigned:
            print(f"[未分派] {seg} {ln}", file=sys.stderr)
        sys.exit(f"{len(unassigned)} 筆宗地未落在任何 site 面內,中止(不寫出)")

    if a.old and Path(a.old).exists():
        bak = Path(a.old).with_name(Path(a.old).stem + "_無段欄_backup.csv")
        shutil.copy2(a.old, bak)
        old = {}
        with open(a.old, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                old.setdefault(r["地號"].strip(), []).append(r["案場ID"])
        diffs = 0
        for r in rows:
            if r["案場ID"] not in old.get(r["地號"], []):
                print(f"[差異] {r['段']} {r['地號']} -> {r['案場ID']}(舊:{old.get(r['地號'])})")
                diffs += 1
        print(f"舊檔備份:{bak};與舊 sitemap 案場不同:{diffs} 筆")

    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["段", "地號", "案場ID", "案場名稱"])
        w.writeheader()
        w.writerows(rows)
    n_sites = len({r["案場ID"] for r in rows})
    print(f"寫出 {a.out}:{len(rows)} 筆 / {n_sites} 案場")
    return 0


if __name__ == "__main__":
    sys.exit(main())
