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


# ★ 公有的用詞：**兩條取得路徑不一樣，必須都認得**（2026-08-07 做權屬圖時發現）
#   ① TCDGIS 匯出（joined/）  ：「公」／「私」
#   ⓪a cadastre_fetch（cadastre/，**現行主路徑**）：值直接來自 MOI_API_039 的
#      OwnerType，實測有「國有」「直轄市有」「縣市有」，私有側則是「私(推論)」。
#   只判「公」開頭會讓 ⓪a 路徑的公有地**全部歸為無法判定**、公有筆數漏算——
#   逐案場統計.json 是簡報數字的唯一來源，這種漏算會直接寫進交付文件。
#   （既有案子未受害：安南港西段當年走 TCDGIS 路徑故正確；走 ⓪a 的案子基地
#   恰好都無公有地。但只要之後有含國有地的基地走 ⓪a 就會爆，故先修。）
_PUBLIC_TOKENS = ("公", "國有", "中華民國", "省有", "直轄市有",
                  "縣市有", "縣有", "市有", "鄉鎮市有", "鄉有", "鎮有")


def classify_ownership(v) -> str | None:
    """公私別欄值 → '公'／'私'／None（無法判定）。

    ⚠ 不可用 `== "公"` 精確比對（2026-08-04 龍崎案實錯：⓪a cadastre_fetch 寫的值是
    「私(推論)」、TCDGIS 匯出可能是「公/國有」，精確比對讓公有與私有筆數**同時歸 0**，
    而 逐案場統計.json 是簡報數字的唯一來源）。
    判定：字首命中 `_PUBLIC_TOKENS`（含 ⓪a 路徑的「國有」「直轄市有」等）＝公有、
    「私」開頭＝私有，其餘（空白、None、「未登記」……）回 None、不計入任一類——
    空白不等於私有，見 CLAUDE.md「本機地籍快照的所有權欄不可作為權屬依據」。

    ★ 這是全產線唯一的權屬判定，出圖端（make_extra_maps 權屬主題）也 import 這支，
      不要各自再寫一份（兩份必然分岔）。
    """
    s = str(v or "").strip()
    if s.startswith("私"):
        return "私"
    if any(s.startswith(t) for t in _PUBLIC_TOKENS):
        return "公"
    return None


# ★ 面積基準：**登記面積優先**（使用者定案 2026-09-11）
#   交付文件的面積一律以登記面積為準；圖面積（geometry.area）只作參考。
#   ⚠ 不可無條件改用登記面積——**未登記土地（母號 ≥9000）的登記面積是 0**
#   （見 CLAUDE.md「地號母號 ≥9000 ＝ 未登記土地」），逕改會產生 0 面積的案場。
#   故採「逐筆取登記面積、取不到者該筆退回圖面積」，並在輸出標明本案場用的是哪種基準，
#   讓簡報端看得出有沒有混用。長度／周長／距離不受此口徑影響。
_REG_COLS = ("登面積", "登記面積", "標示面積")


def registered_area(row, cols=_REG_COLS):
    """單筆宗地的登記面積（㎡）；取不到或為 0 回 None（＝未登記地／欄位缺）。"""
    for c in cols:
        if c not in row:
            continue
        v = row[c]
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0:
            return f
    return None


def area_of(df, cols=_REG_COLS):
    """一組宗地的採用面積 → (面積, 基準說明, 登記面積合計, 圖面積合計)。

    逐筆優先取登記面積，該筆取不到才用其圖面積；全部取得＝「登記面積」，
    部分取得＝「登記面積（N 筆未登記以圖面積計）」，全部取不到＝「圖面積」。
    """
    reg_sum = geo_sum = 0.0
    n_fallback = 0
    for _, r in df.iterrows():
        ga = float(r.geometry.area)
        geo_sum += ga
        ra = registered_area(r, cols)
        if ra is None:
            n_fallback += 1
            reg_sum += ga
        else:
            reg_sum += ra
    if len(df) == 0:
        return 0.0, "無宗地", 0.0, 0.0
    if n_fallback == 0:
        basis = "登記面積"
    elif n_fallback == len(df):
        basis = "圖面積（無登記面積）"
    else:
        basis = f"登記面積（{n_fallback} 筆未登記以圖面積計）"
    return reg_sum, basis, reg_sum, geo_sum


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True)
    ap.add_argument("--sites", default=None,
                    help="案場面 shp；省略時預設 <案>/臨路評估/sites.shp")
    ap.add_argument("--road", default=None,
                    help="road_access 成果.xlsx；省略時預設 <案>/臨路評估/成果.xlsx")
    ap.add_argument("--overlay", action="append", default=[],
                    help='名稱=路徑[::分級欄]，可多次')
    ap.add_argument("--out", default=None,
                    help="案件資料夾（不是逐案場統計.json的完整路徑；"
                         "輸出檔名固定寫作 <out>/逐案場統計.json）")
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
        inner = site[site.geometry.intersects(g.buffer(-0.5))]
        # 面積基準：登記面積優先（見上方 area_of 說明）。案場面 geometry 的面積另存
        # 圖面積_m2 供對帳——兩者本就會有數十 ㎡ 的差，不是錯誤。
        area, basis, _reg, _geo = area_of(inner)
        d = {"面積_m2": round(area, 2), "公頃": round(area / 10000, 4),
             "面積基準": basis, "圖面積_m2": round(float(g.area), 2)}
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
            d["公有面積_m2"] = round(area_of(pub)[0], 2)   # 同採登記面積優先
            if no_col:
                d["公有地號"] = sorted(pub[no_col].astype(str).tolist())

        # 都市計畫分區組成
        if os.path.exists(URBAN):
            up = gpd.read_file(URBAN, engine="pyogrio",
                               bbox=tuple(gpd.GeoSeries([g], crs=site.crs).total_bounds))
            up = up.to_crs(site.crs)
            up["ov"] = up.geometry.intersection(g).area
            zz = up[up["ov"] > 1].groupby("zone")["ov"].sum().sort_values(ascending=False)
            # 套疊面積本質是幾何交集，維持幾何值；另給占比，簡報端要換算成
            # 登記面積基準時用「占比 × 面積_m2」即可（口徑一致、不必回頭改這裡）
            d["都市計畫分區"] = {k: round(v, 2) for k, v in zz.items()}
            d["都市計畫分區_占基地pct"] = {k: round(v / g.area * 100, 2) for k, v in zz.items()}

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
            d[name + "_占基地pct"] = round(float(hit["ov"].sum()) / g.area * 100, 2)
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

    out["_面積口徑"] = ("面積_m2／公頃／公有面積_m2 採**登記面積優先**（逐筆取登記面積，"
                       "未登記地該筆退回圖面積），基準見各案場「面積基準」欄；"
                       "圖面積_m2 為案場幾何面積，僅供對帳。"
                       "都市計畫分區與 overlay 為幾何交集面積，換算成登記面積基準請用"
                       "「占基地pct × 面積_m2」。長度／周長／距離不受此口徑影響。")

    p = os.path.join(case_dir, "逐案場統計.json")
    io.open(p, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))
    for sid, d in out.items():
        if sid.startswith("_"):
            continue
        print("=" * 50)
        print(sid)
        for k, v in d.items():
            print(f"  {k} = {v}")
    print("\n→", p)


if __name__ == "__main__":
    main()
