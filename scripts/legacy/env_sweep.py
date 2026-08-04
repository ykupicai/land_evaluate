# -*- coding: utf-8 -*-
"""環境敏感地區**全圖層掃描**（本機 119 個 shp，免登入）

★ 緣由（2026-08-02 龍崎番社段光電案）：平台（TCDGIS）的「環境敏感地區套疊分析」
  一次回 53 項，但**需要本人登入**。本機 `環境敏感相關/` 有 119 個 shp 對應那 53 項
  （多對一，分 第一級／第二級／其他），過去只在 `check_zones` 的 YAML 裡逐項挑著用，
  **沒有「全部掃一遍」的工具** → 容易漏掉沒寫進 YAML 的項目。本腳本補這個缺口。

定位（**與平台的關係要講清楚，不可互相取代**）
  ・本腳本＝**免登入的初篩**，可在動用登入前先看有沒有 ╳ 級障礙，且能逐案場出數字。
  ・平台官方查詢＝**交付的權威來源**（圖資版本由官方維護、且平台圖層可能設有門檻，
    例如淹水潛勢只計 ≥0.3m——見 CLAUDE.md「平台面積與本機圖資不一致」）。
  ・兩者**不一致是常態，不是錯**：本機圖資年份較舊、且無平台的納入門檻。
    交付時以平台為準，並用本機成果補「逐分級面積」與「逐案場面積」。

⚠ 圖資年份：本機多為 105~106 年（2016-2017）更新版，**涉法定禁止事由者用前必查版本**
  （見 CLAUDE.md「圖資一律確認版本年度」；安南案曾因用 2016 舊版而結論反向）。

用法（系統 Python，不需 QGIS）
    python -X utf8 scripts/env_sweep.py --joined <基地.shp> --out <案> \
        [--sites <案場定義/sites.shp>] [--min-area 1.0]
輸出：環境敏感套疊_本機.csv／.json（含逐案場面積、圖資檔案日期）
"""
from __future__ import annotations

import argparse
import io
import json
import os
from datetime import datetime

import geopandas as gpd
from shapely import make_valid
from shapely.ops import unary_union

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENV_DIR = os.path.join(ROOT, "環境敏感相關")
LEVELS = {"第一級": "第一級", "第二級": "第二級", "其他": "其他"}


def _read(p):
    """讀 shp；無 .prj 者假定 EPSG:3826 並以 TWD97 bounds 反驗。"""
    g = gpd.read_file(p)
    if g.crs is None:
        g = g.set_crs(3826)
        b = g.total_bounds
        if not (100000 < b[0] < 400000 and 2400000 < b[1] < 2900000):
            return None, "無 .prj 且 bounds 不像 TWD97 TM2，已略過"
    else:
        g = g.to_crs(3826)
    return g, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sites", default=None, help="案場 shp（有 site_id 欄則逐案場拆）")
    ap.add_argument("--min-area", type=float, default=1.0,
                    help="套疊面積小於此值(㎡)視為判讀雜訊，預設 1.0")
    ap.add_argument("--near-m", type=float, default=500.0,
                    help="未套疊時，最近距離小於此值(m)才報距離，預設 500")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    site_g = gpd.read_file(a.joined)
    site_g = site_g.to_crs(3826) if site_g.crs else site_g.set_crs(3826)
    su = make_valid(unary_union(site_g.geometry.tolist()))
    print(f"基地 {len(site_g)} 筆、dissolve 面積 {su.area:,.2f} ㎡ "
          f"= {su.area/10000:.4f} 公頃")

    sites = None
    sid_col = None
    if a.sites and os.path.exists(a.sites):
        sites = gpd.read_file(a.sites)
        sites = sites.to_crs(3826) if sites.crs else sites.set_crs(3826)
        sid_col = next((c for c in sites.columns
                        if c.lower() in ("site_id", "案場id")), None)

    shps = []
    for lv in LEVELS:
        d = os.path.join(ENV_DIR, lv)
        if not os.path.isdir(d):
            continue
        for dp, _dn, fn in os.walk(d):
            for f in fn:
                if f.lower().endswith(".shp"):
                    shps.append((lv, os.path.join(dp, f)))
    print(f"掃描圖層 {len(shps)} 個（第一級／第二級／其他）\n")

    hits, misses, errs = [], [], []
    for lv, p in sorted(shps, key=lambda x: (x[0], x[1])):
        rel = os.path.relpath(p, ROOT)
        name = os.path.splitext(os.path.basename(p))[0]
        mtime = datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d")
        try:
            g, warn = _read(p)
        except Exception as e:  # noqa: BLE001
            errs.append({"級別": lv, "圖層": name, "檔案": rel,
                         "錯誤": f"{type(e).__name__}: {e}"})
            print(f"  ✖ [{lv}] {name}：讀取失敗 {type(e).__name__}")
            continue
        if g is None:
            errs.append({"級別": lv, "圖層": name, "檔案": rel, "錯誤": warn})
            print(f"  ✖ [{lv}] {name}：{warn}")
            continue
        try:
            # ★ 一律先用**空間索引 + bbox 預篩**，不要對全圖層做 intersects／distance。
            #   環境敏感相關/ 有多個全台級圖層（數十萬~數百萬筆），逐筆算距離會跑到天亮
            #   （2026-08-02 實踩：跑 15 分鐘還沒完，只好中斷）。
            #   sindex.query 先把候選縮到十幾筆，再做精確運算。
            cand = g.iloc[list(g.sindex.query(su, predicate="intersects"))]
            area = 0.0
            sub = cand
            if len(cand):
                inter = make_valid(unary_union(
                    [make_valid(x) for x in cand.geometry])).intersection(su)
                area = float(inter.area)
        except Exception as e:  # noqa: BLE001
            errs.append({"級別": lv, "圖層": name, "檔案": rel,
                         "錯誤": f"套疊失敗 {type(e).__name__}: {e}"})
            print(f"  ✖ [{lv}] {name}：套疊失敗 {type(e).__name__}")
            continue

        if area >= a.min_area:
            rec = {"級別": lv, "圖層": name, "檔案": rel, "圖資日期": mtime,
                   "套疊面積m2": round(area, 2),
                   "占基地pct": round(area / su.area * 100, 2)}
            if sites is not None and sid_col:
                per = {}
                for _, r in sites.iterrows():
                    try:
                        pa = make_valid(unary_union(
                            [make_valid(x) for x in sub.geometry])).intersection(
                                make_valid(r.geometry)).area
                    except Exception:  # noqa: BLE001
                        pa = 0.0
                    if pa >= a.min_area:
                        per[str(r[sid_col])] = round(float(pa), 2)
                rec["逐案場m2"] = per
            hits.append(rec)
            print(f"  ✗ [{lv}] {name}（{mtime}）：套疊 {area:,.2f} ㎡"
                  f"（占基地 {area/su.area*100:.2f}%）")
            if rec.get("逐案場m2"):
                print("        " + "、".join(f"{k} {v:,.0f}㎡"
                                             for k, v in rec["逐案場m2"].items()))
        else:
            # 未套疊者才算「最近距離」，且**只在 near_m 範圍內找**：以 bbox 擴張
            # near_m 做 sindex 預篩，落空就回報 None（意思是「>near_m」，不是未知）。
            d = None
            try:
                from shapely.geometry import box
                bb = su.bounds
                probe = box(bb[0] - a.near_m, bb[1] - a.near_m,
                            bb[2] + a.near_m, bb[3] + a.near_m)
                near_idx = list(g.sindex.query(probe, predicate="intersects"))
                if near_idx:
                    d = float(g.iloc[near_idx].distance(su).min())
            except Exception:  # noqa: BLE001
                pass
            misses.append({"級別": lv, "圖層": name, "檔案": rel, "圖資日期": mtime,
                           "最近距離m": round(d, 1) if d is not None else None,
                           "距離說明": ("實測" if d is not None
                                        else f">{a.near_m:.0f} m（未在預篩範圍內）")})
            if d is not None and d <= a.near_m:
                print(f"  ○ [{lv}] {name}（{mtime}）：未套疊，但最近僅 {d:,.1f} m"
                      f"　← 極近，交付宜註記")

    print(f"\n== 總結 ==")
    print(f"  套疊 {len(hits)} 項"
          + (f"：{[h['圖層'] for h in hits]}" if hits else ""))
    lv1 = [h for h in hits if h["級別"] == "第一級"]
    if lv1:
        print(f"  ⚠ **其中第一級環境敏感地 {len(lv1)} 項**："
              f"{[h['圖層'] for h in lv1]} → 管制規則§30-1 不得位於，須逐項確認")
    near = [m for m in misses if m["最近距離m"] is not None
            and m["最近距離m"] <= a.near_m]
    print(f"  未套疊 {len(misses)} 項；其中 {len(near)} 項最近距離 ≤{a.near_m:.0f} m")
    if errs:
        print(f"  ✖ 讀取／套疊失敗 {len(errs)} 項（**不等於未套疊**，須人工確認）：")
        for e in errs:
            print(f"      {e['圖層']}：{e['錯誤']}")

    res = {"基地_dissolve_m2": round(su.area, 2), "掃描圖層數": len(shps),
           "套疊": hits, "未套疊": misses, "失敗": errs,
           "口徑": ["本機圖資初篩，非平台官方查詢；交付以平台 53 項結果為準",
                    "平台圖層可能設有納入門檻（如淹水潛勢只計 ≥0.3m），"
                    "與本機不一致是常態",
                    "本機圖資多為 105~106 年版；涉法定禁止事由者用前必查版本年度",
                    "讀取失敗項目不可視為未套疊"]}
    io.open(os.path.join(a.out, "環境敏感套疊_本機.json"), "w",
            encoding="utf-8").write(json.dumps(res, ensure_ascii=False, indent=2))
    with io.open(os.path.join(a.out, "環境敏感套疊_本機.csv"), "w",
                 encoding="utf-8-sig", newline="") as fh:
        fh.write("級別,圖層,套疊面積m2,占基地pct,最近距離m,圖資日期,檔案\n")
        for h in hits:
            fh.write(f"{h['級別']},{h['圖層']},{h['套疊面積m2']:.2f},"
                     f"{h['占基地pct']:.2f},,{h['圖資日期']},{h['檔案']}\n")
        for m in misses:
            fh.write(f"{m['級別']},{m['圖層']},0,0,"
                     f"{m['最近距離m'] if m['最近距離m'] is not None else ''},"
                     f"{m['圖資日期']},{m['檔案']}\n")
    print(f"\n→ {a.out}\\環境敏感套疊_本機.csv / .json")
    print("⚠ 本機初篩；交付前仍應上 TCDGIS 做官方 53 項查詢（該步需本人登入）。")


if __name__ == "__main__":
    main()
