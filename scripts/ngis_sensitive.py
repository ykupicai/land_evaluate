# -*- coding: utf-8 -*-
"""⓪b 環敏套疊（NGIS 官方 54 圖層，免登入）—— **正式初篩程序**（2026-08-05 使用者定案）。

橋接 skill `ngis-sensitive`：直接把 ⓪a 產的基地幾何餵進 NGIS 一級 25／二級 29 個
環境敏感圖層做**實際空間套疊**，回命中項目＋重疊面積＋佔基地比例＋主管機關屬性。

用法：
  python -X utf8 scripts/ngis_sensitive.py --case <案名> [--shp <基地.shp>] [--per-site]

  預設讀 outputs/<案>/cadastre/基地.shp（⓪a 產出）；--per-site 時依
  案場定義/sitemap.csv 逐案場各算一份（未給則整份基地一起算）。

產出（outputs/<案>/）：
  環敏NGIS.csv    逐命中項：級別／圖層／重疊面積／佔基地比例
  環敏NGIS.json   完整結果（含屬性、基地面積、聯集重疊）
  環敏/<平台圖層名>.geojson   ★命中圖層的**平台幾何**（EPSG:3826），供出圖用

★ 環敏出圖幾何一律以 **NGIS 為優先**（2026-09-13 使用者定案）：本步驟會把命中圖層的
  幾何一併抓回，寫成 `<案>/環敏/<平台圖層名>.geojson`——`make_env_map` 的
  `resolve_case_override` 本來就把案件層 `環敏/` 排在本機 `環境敏感相關/` 之前，
  所以檔案一落地就自動優先採用，本機圖資退為「NGIS 無幾何時」的備援。
  與既有原則一致（CLAUDE.md：本機 vs NGIS 不一致時以 NGIS 為主），且一次解決兩件事：
  ① 本機圖資過舊（龍崎 NR23）② **本機根本沒有該圖層**（大眾捷運兩側禁建限建）。
  ⚠ 實測 NGIS 回的是**完整要素、不是裁到查詢範圍**：2-27 民航禁限建回 35,123,240 ㎡，
    與本機松山機場 shp 同一面 35,123,241 ㎡ 只差 1 ㎡。故可直接拿來出圖，不會缺周邊。
  ⚠ 檔名必須是**完整平台圖層名**（含 `2-27`／`2-31` 前綴）——覆蓋檔比對是
    `layer_name in p.stem`，少了前綴會比不中而**靜默忽略**。
  `--no-geometry` 可關閉（只要面積不要幾何時用）。

★ 為什麼是主路徑（取代原 PDF 應免查初篩，2026-08-05 使用者定案）：
  單一官方來源一次涵蓋 54 項，且做的是**實際幾何套疊**（不是行政區級的應查／免查），
  免登入、每次查詢不到 1 秒。原 `env_prescreen.py`（離線 PDF 應免查）與平台 eland／
  TCDGIS 降為**備選**：需要法定「應查／免查」口徑、或要權威背書時才用。

⚠ 交付口徑（不可省，NGIS 圖台自己也這樣聲明）：
  **本成果為初步評估、非正式套繪**（圖台聲明「僅供參考用途，不得作為任何形式證明或
  主張」）。法定應查免查認定仍須走環境敏感地區單一窗口查詢平台（eland）或各主管機關。
⚠ 描述性欄位（COUNTYNAME／city 等）上游本身有亂碼壞值，**不可直接寫進報告**；
  判定本身（重疊面積、比例、圖層名）不受影響。詳 skill SKILL.md。
⚠ 用**實際地號的幾何**，不可用行政區中心點代替（山坡地逐地塊劃定，同一區內有平地）；
  本橋接一律傳整個基地環（面查詢），不用形心點查（只查點會漏判邊角重疊）。

⚠ 不要走 skill 的 `check.py --parcel`：那條會再呼叫 cadastre-check 重查地籍
  （2026-08-05 實測單筆 21.9 秒、selftest 因此全 timeout），而我們 ⓪a 已經有幾何了。
  NGIS 查詢本身每次 <1 秒——逾時幾乎都是地籍那段，不是 NGIS。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = Path(os.path.expanduser("~")) / ".claude" / "skills" / "ngis-sensitive" / "scripts"

# ★ NGIS 已知涵蓋落差清單（2026-08-06 建，七股土資場案實錯）——這幾類 NGIS 曾經
#   回 0 命中、但本機圖資／eland／同案前例證實確有涉及。不是查詢錯誤，是 NGIS
#   單一圖層本身涵蓋不到；每案務必額外交叉核對，不能只憑 NGIS 0 命中就下「無涉及」。
#   新增案例時把「哪個圖層漏了」補進這裡，讓後案不必再重踩一次。
KNOWN_GAPS = [
    ("氣象法禁限建範圍", "七股土資場案（2026-08-06）：NGIS 回 0，本機圖層＋同案前例"
     "證實涉及氣象雷達站限建範圍 5,176 ㎡（7.6%）。交叉核對：本機 環境敏感相關/ 對應"
     "圖層，或該地號同案歷史前例。"),
]


def _load_skill():
    if not SKILL.is_dir():
        sys.exit(f"[ngis] 找不到 skill：{SKILL}\n  本步依賴 skill `ngis-sensitive`。")
    sys.path.insert(0, str(SKILL))
    import ngis, overlay  # noqa: PLC0415
    return ngis, overlay


def rings_of(geom) -> list:
    """shapely polygon/multipolygon → TM2 環清單（外環＋內環，overlay.analyze 吃這個）。"""
    out = []
    parts = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
    for p in parts:
        out.append([(x, y) for x, y in p.exterior.coords])
        for h in p.interiors:
            out.append([(x, y) for x, y in h.coords])
    return out


def load_sitemap(path: Path) -> dict:
    """sitemap.csv → {地號: 案場ID}。"""
    m = {}
    if not path.exists():
        return m
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            ln = str(row.get("地號") or row.get("landno") or "").strip()
            sid = str(row.get("案場ID") or row.get("site_id") or "").strip()
            if ln and sid:
                m[ln] = sid
    return m


def save_ngis_geometry(ngis, token, groups, case_dir: Path) -> None:
    """把命中圖層的平台幾何寫成 <案>/環敏/<平台圖層名>.geojson（EPSG:3826）。

    ★ 讓環敏出圖以 NGIS 幾何為優先（見模組 docstring）。逐組查完後依圖層名合併：
    同一圖層 NGIS 會**逐面各回一筆**，而且不同組（逐案場時）會重複回同一面，
    故以 WKT 去重後再寫檔。
    """
    import geopandas as gpd  # noqa: PLC0415 — 與 main() 一致，延後載入
    from shapely.geometry import MultiPolygon, Polygon  # noqa: PLC0415

    def rings_merc(geom):
        """★ `query_polygon` 吃的是 **Mercator(3857)** 環（參數名就叫 rings_merc），
        與 `overlay.analyze` 吃的 TM2(3826) 環**不同**——直接把 rings_of() 餵進去會
        靜默回空（2026-09-13 實踩：印「平台未回幾何」但其實是座標系送錯）。"""
        m = gpd.GeoSeries([geom], crs=3826).to_crs(3857).iloc[0]
        parts = [m] if m.geom_type == "Polygon" else list(m.geoms)
        out = []
        for pp in parts:
            out.append([list(c) for c in pp.exterior.coords])
            for h in pp.interiors:
                out.append([list(c) for c in h.coords])
        return out

    out_dir = case_dir / "環敏"
    out_dir.mkdir(parents=True, exist_ok=True)
    bucket: dict[str, list] = {}
    for geom in groups.values():
        try:
            res = ngis.query_polygon(token, rings_merc(geom),
                                     levels=("一級", "二級"), return_geometry=True)
        except Exception as exc:  # noqa: BLE001 — 取幾何失敗不阻斷面積成果
            print(f"  ⚠ 取平台幾何失敗（{type(exc).__name__}: {exc}）→ 出圖將退回本機圖資")
            return
        for items in (res or {}).values():
            for it in items:
                gj = it.get("geometry") or {}
                rings = [Polygon(r) for r in gj.get("rings", []) if len(r) >= 4]
                if not rings:
                    continue
                gg = (MultiPolygon(rings) if len(rings) > 1 else rings[0]).buffer(0)
                bucket.setdefault(it["layerName"], []).append(
                    (it.get("attributes", {}), gg))
    if not bucket:
        print("  （平台未回幾何；出圖沿用本機 環境敏感相關/）")
        return
    n = 0
    for name, recs in bucket.items():
        seen, rows, geoms = set(), [], []
        for attrs, gg in recs:
            k = gg.wkt
            if k in seen:
                continue
            seen.add(k)
            rows.append({"Layer": str(attrs.get("Layer", "") or "")})
            geoms.append(gg)
        gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs=3857).to_crs(3826)
        # 檔名＝完整平台圖層名，resolve_case_override 靠 `layer_name in p.stem` 比對
        gdf.to_file(out_dir / f"{name}.geojson", driver="GeoJSON")
        n += 1
    print(f"  ✔ 平台幾何 {n} 層已寫入 {out_dir}（出圖將優先採用，本機圖資退為備援）")


def main():
    ap = argparse.ArgumentParser(description="⓪b NGIS 環敏套疊（正式初篩）")
    ap.add_argument("--case", required=True, help="案名（outputs/<案名>）")
    ap.add_argument("--no-geometry", action="store_true",
                    help="不抓命中圖層的平台幾何（預設會抓並寫入 <案>/環敏/，供出圖優先採用）")
    ap.add_argument("--shp", default=None, help="基地 shp（預設 <案>/cadastre/基地.shp）")
    ap.add_argument("--per-site", action="store_true",
                    help="依 案場定義/sitemap.csv 逐案場各算一份")
    ap.add_argument("--min-area", type=float, default=0.01,
                    help="小於此重疊面積(㎡)視為邊界雜訊不計，預設 0.01")
    a = ap.parse_args()

    import geopandas as gpd  # noqa: PLC0415
    from shapely.ops import unary_union  # noqa: PLC0415

    case_dir = REPO / "outputs" / a.case
    case_dir.mkdir(parents=True, exist_ok=True)
    shp = Path(a.shp) if a.shp else (case_dir / "cadastre" / "基地.shp")
    if not shp.exists():
        sys.exit(f"[ngis] 找不到基地 shp：{shp}\n  先跑 ⓪a（cadastre_fetch.py fetch）")

    g = gpd.read_file(shp)
    if g.crs is None:
        g = g.set_crs(3826)
    g = g.to_crs(3826)
    lncol = next((c for c in ("地號", "landno") if c in g.columns), None)

    groups: dict[str, object] = {}
    if a.per_site:
        smap = load_sitemap(case_dir / "案場定義" / "sitemap.csv")
        if not smap or not lncol:
            print("[ngis] 無 sitemap 或無地號欄 → 改以整份基地計算")
        else:
            for sid in sorted(set(smap.values())):
                sel = g[g[lncol].astype(str).map(lambda x: smap.get(str(x).strip())) == sid]
                if len(sel):
                    groups[sid] = unary_union(sel.geometry.values)
    if not groups:
        groups["全案"] = unary_union(g.geometry.values)

    ngis, overlay = _load_skill()
    token = ngis.fetch_token()
    print(f"[ngis] token 取得成功；分析 {len(groups)} 組（{'逐案場' if a.per_site else '整份基地'}）")

    all_rows, result = [], {}
    for sid, geom in groups.items():
        res = overlay.analyze(rings_of(geom), token=token, min_area=a.min_area)
        result[sid] = res
        hits = res["hits"]
        print(f"  {sid}：基地 {res['site_area']:,.2f} ㎡，命中 {len(hits)} 項"
              f"（聯集重疊 {res['total_overlap_ratio'] * 100:.1f}%）")
        for h in hits:
            print(f"      [{h['level']}] {h['layerName']}　{h['overlap_area']:,.2f} ㎡"
                  f"（{h['overlap_ratio'] * 100:.2f}%）")
            all_rows.append({
                "案場": sid, "級別": h["level"], "圖層名稱": h["layerName"],
                "重疊面積_m2": round(h["overlap_area"], 2),
                "佔基地_pct": round(h["overlap_ratio"] * 100, 2),
                "基地面積_m2": round(res["site_area"], 2),
            })
        if not hits:
            all_rows.append({"案場": sid, "級別": "", "圖層名稱": "（54 項均無套疊）",
                             "重疊面積_m2": 0, "佔基地_pct": 0,
                             "基地面積_m2": round(res["site_area"], 2)})

    if not a.no_geometry:
        save_ngis_geometry(ngis, token, groups, case_dir)

    with io.open(case_dir / "環敏NGIS.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["案場", "級別", "圖層名稱", "重疊面積_m2",
                                          "佔基地_pct", "基地面積_m2"])
        w.writeheader()
        w.writerows(all_rows)
    (case_dir / "環敏NGIS.json").write_text(json.dumps(
        {"來源": "內政部國土管理署 NGIS 環境敏感地區（一級25／二級29）",
         "口徑": "初步評估、非正式套繪；法定認定仍以 eland／主管機關為準",
         "基地來源": str(shp), "結果": result},
        ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"→ {case_dir / '環敏NGIS.csv'}")
    print(f"→ {case_dir / '環敏NGIS.json'}")
    print("⚠ 交付口徑：初步評估、非正式套繪；描述性欄位（縣市名等）上游有亂碼壞值，勿直接引用。")
    print("⚠ NGIS 已知涵蓋落差（0 命中 ≠ 真的無涉及，務必交叉核對，不是查詢錯誤）：")
    for name, note in KNOWN_GAPS:
        print(f"    ・{name}：{note}")


if __name__ == "__main__":
    main()
