# -*- coding: utf-8 -*-
"""依 案場定義/sitemap.csv 把 joined shp 拆成逐案場 shp,供 fishpv 逐案跑臨路。

縮編流程(2026-07-16 定案)的第二步:build_sites_sitemap.py 產權威 sitemap 後,
本腳本拆出 case_<案場ID>.shp;fishpv 只吃單一案場、不再自己聚類定義案場。

用法:
  python split_shp_persite.py --joined <面積join.shp> --sitemap <案場定義/sitemap.csv> \
      --out-dir <臨路評估_percase目錄>
"""
import argparse
import sys
from pathlib import Path

import geopandas as gpd

sys.path.insert(0, str(Path(__file__).parent))
from shp_trust_utils import load_sitemap, assign_site  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True)
    ap.add_argument("--sitemap", required=True, help="帶段欄的權威 sitemap.csv")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()

    parcels = gpd.read_file(a.joined, encoding="big5")
    lncol = next(c for c in parcels.columns if "地號" in c)
    segcol = next((c for c in parcels.columns if "段" in c), None)
    sm = load_sitemap(a.sitemap)

    groups: dict[str, list[int]] = {}
    fails = []
    for idx, p in parcels.iterrows():
        seg = str(p[segcol]) if segcol else ""
        got = assign_site(str(p[lncol]), sm, "", seg=seg)
        if not got:
            fails.append((seg, p[lncol]))
            continue
        for sid, _name in (got if isinstance(got, tuple) else ((got, ""),)):
            groups.setdefault(sid, []).append(idx)
    if fails:
        for seg, ln in fails[:10]:
            print(f"[分派失敗] {seg} {ln}", file=sys.stderr)
        sys.exit(f"{len(fails)} 筆宗地無法分派(sitemap 不完整?),中止不拆檔")

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for sid, idxs in sorted(groups.items()):
        parcels.loc[idxs].to_file(out / f"case_{sid}.shp", encoding="big5")
    print(f"拆出 {len(groups)} 案場 shp -> {out}(共 {len(parcels)} 宗地)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
