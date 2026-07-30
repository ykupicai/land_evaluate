# -*- coding: utf-8 -*-
"""周邊宗地使用分區／使用地類別分析 — 「零星夾雜地」類條款的實算依據

★ 建立緣由（2026-07-30 安定六塊寮段案）：農業用地變更審查要點第六點第（五）款
  「符合非都市土地使用管制規則規定，為自然地形或**合法建築用地包圍、夾雜**之零星農業用地」
  是特定農業區 ╳ 的少數翻轉條件之一，判斷完全取決於**周邊宗地的使用地類別**。
  同型條款還有：管制規則之零星地變更、非都市土地開發審議規範的毗鄰使用相容性檢討。

判定要點（勿簡化）：
  ・「包圍、夾雜」看的是**緊鄰**宗地，不是某個半徑內的統計——所以預設輸出緊鄰(≤1m)、
    30m、60m、100m 多級，讓「緊鄰全是農地」與「100m 內才有建築用地」能被分開陳述。
  ・要數的是**建築用地**（甲乙丙丁建、特定目的事業用地等），不是「非農牧用地」——
    水利用地（灌排渠道）與交通用地（農路）屬農業生產環境的一部分，不能算成包圍的非農地。
  ・本機地籍是舊快照（Ver 111年2月），周邊使類僅供「趨勢判斷」；基地本身幾何一律用
    平台官方 shp。周邊宗地屬性差異不影響「有無建築用地包圍」這個量級的結論，
    但交付文件仍應註明圖資版本。

副產物：緊鄰的水利用地筆數＝農業灌排系統緊鄰程度，對應審查要點第五點第二款
（妨礙農業灌排者不同意變更），一併輸出。

用法（系統 Python）：
  python scripts/adjacent_landuse.py --joined <面積join.shp> [--county 臺南市]
      [--rings 0 30 60 100] [--out <案件資料夾>]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import warnings

import geopandas as gpd

warnings.filterwarnings("ignore")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAD_DIR = os.path.join(REPO, "地籍圖資")
# 建築用地類別（管制規則各種使用地名稱；「特定目的事業用地」亦屬已編定可建）
BUILDABLE = ("甲種建築", "乙種建築", "丙種建築", "丁種建築", "建築用地",
             "特定目的事業", "遊憩用地", "窯業用地")


def _county_file(county: str) -> str | None:
    for c in (county, county.replace("臺", "台"), county.replace("台", "臺")):
        p = os.path.join(CAD_DIR, c + ".shp")
        if os.path.exists(p):
            return p
    return None


def analyze(joined: str, county: str | None, rings: list[float]) -> dict:
    site = gpd.read_file(joined, engine="pyogrio")
    if site.crs and site.crs.to_epsg() != 3826:
        site = site.to_crs(3826)
    su = site.geometry.union_all()
    if not county:
        for c in ("縣市名稱", "縣市"):
            if c in site.columns:
                county = str(site.iloc[0][c] or "").strip()
                break
    p = _county_file(county or "")
    if not p:
        raise SystemExit(f"找不到 地籍圖資\\{county}.shp（周邊分析需本機地籍快照）")

    sur = gpd.read_file(p, engine="pyogrio", bbox=su.buffer(max(rings) + 50).bounds)
    sur = sur[~sur.geometry.intersects(su.buffer(-0.5))]   # 排除基地本身
    sur["_d"] = sur.geometry.distance(su)
    zc = "使分" if "使分" in sur.columns else None
    kc = "使類" if "使類" in sur.columns else None

    out = {"縣市": county, "地籍圖資": os.path.basename(p),
           "圖資版本": str(sur["Ver"].dropna().unique().tolist()) if "Ver" in sur.columns else "",
           "環域": {}}
    for r in rings:
        n = sur[sur["_d"] <= max(r, 1.0)]
        key = "緊鄰" if r == 0 else f"{int(r)}公尺內"
        comp = {}
        if kc:
            for k, cnt in n[kc].value_counts().items():
                comp[str(k)] = {"筆數": int(cnt),
                                "面積_m2": round(float(n[n[kc] == k].geometry.area.sum()), 1)}
        build = sum(v["筆數"] for k, v in comp.items()
                    if any(b in k for b in BUILDABLE))
        out["環域"][key] = {
            "宗地數": int(len(n)),
            "使用地類別組成": comp,
            "建築用地筆數": int(build),
            "使用分區": ({str(k): int(v) for k, v in n[zc].value_counts().items()}
                     if zc else {}),
        }
    adj = out["環域"].get("緊鄰", {})
    comp = adj.get("使用地類別組成", {})
    out["判定輔助"] = {
        "緊鄰建築用地筆數": adj.get("建築用地筆數", 0),
        "緊鄰水利用地筆數": comp.get("水利用地", {}).get("筆數", 0),
        "零星夾雜地主張": ("有建築用地緊鄰，得進一步主張（仍須符合管制規則零星地定義）"
                   if adj.get("建築用地筆數", 0) > 0
                   else "緊鄰無任何建築用地 → 「合法建築用地包圍、夾雜」主張不成立"),
        "農業灌排相鄰": ("緊鄰農業灌排水利用地 → 廢污水排放須避開，涉審查要點§5②"
                   if comp.get("水利用地", {}).get("筆數", 0) else "緊鄰無水利用地"),
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True)
    ap.add_argument("--county", default=None)
    ap.add_argument("--rings", type=float, nargs="+", default=[0, 30, 60, 100])
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    res = analyze(a.joined, a.county, a.rings)
    lines = [f"周邊宗地使用地類別分析（{res['縣市']}　地籍：{res['地籍圖資']}"
             f"　版本 {res['圖資版本']}）",
             "★ 本機地籍為舊快照，僅供周邊趨勢判斷；基地幾何一律用平台官方 shp", ""]
    for k, v in res["環域"].items():
        lines.append(f"  {k}：{v['宗地數']} 筆　建築用地 {v['建築用地筆數']} 筆")
        for kk, vv in sorted(v["使用地類別組成"].items(),
                             key=lambda x: -x[1]["筆數"]):
            lines.append(f"      {kk}：{vv['筆數']} 筆、{vv['面積_m2']:,.1f} m²")
    lines += ["", "== 判定輔助 =="]
    for k, v in res["判定輔助"].items():
        lines.append(f"  {k}：{v}")
    txt = "\n".join(lines)
    print(txt)
    d = a.out or os.path.dirname(os.path.dirname(os.path.abspath(a.joined)))
    io.open(os.path.join(d, "周邊使用地分析.txt"), "w", encoding="utf-8").write(txt)
    io.open(os.path.join(d, "周邊使用地分析.json"), "w", encoding="utf-8").write(
        json.dumps(res, ensure_ascii=False, indent=1))
    print(f"\n→ {os.path.join(d, '周邊使用地分析.txt')} / .json")


if __name__ == "__main__":
    main()
