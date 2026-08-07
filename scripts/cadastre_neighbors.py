# -*- coding: utf-8 -*-
"""⓪a 周邊地籍自動發現＋即時屬性回填（2026-08-06 使用者定案）

★ 建立緣由：既有「周邊地籍」概念（make_maps.py 出圖用）只從**本機縣市快照**
  （Ver 111年2月）撈，僅供製圖時的視覺脈絡，屬性是舊的，不能拿來做分區／使用地
  編定等評估判斷——這正是 2026-08-02 改用 NLSC WMTS 當主題1/2預設底圖的原因。

  現在 ⓪a（cadastre_fetch.py，免登入 getLandInfo）回傳路徑順暢，本模組把「發現
  鄰地」與「取得鄰地即時屬性」分成兩步接起來：
    1. **發現**：本機縣市快照只拿來做**空間識別**（哪些地號在範圍內），不採其屬性——
       地號/段/鄉鎮這些**識別身分**不常變、快照堪用；使分/使類等**屬性**才會過時。
    2. **回填**：把發現到的地號餵回 ⓪a 即時查（getLandInfo），拿到現在的使分／使類／
       登記面積／權屬，跟基地本身用的是同一組免登入權威來源，口徑一致。

  評估範圍外擴 50 公尺內與地籍有交集者全數回傳（不是只挑「明顯相鄰」的幾筆）；
  之後的分區圖、使用地編定圖、周邊使用地分析（adjacent_landuse.py）、隔離綠帶
  應留設側判定（greenbelt_ratio.py --neighbours）都可以直接吃這份「鄰地.shp」，
  取代舊快照或 WMTS 疊圖——WMTS 改列備選（圖磚抓不到、或要快速目視現況套疊時用）。

用法（系統 Python，不需 QGIS）：
  python -X utf8 scripts/cadastre_neighbors.py --joined <cadastre/基地.shp> \
      --out outputs/<案>/cadastre [--buffer 50] [--name 鄰地]

輸出：<out>/<name>.shp（與 cadastre_fetch.py 同欄位：地號/使分/使類/縣市/鄉鎮/段/
  登面積/圖面積/偏差pct/版次/狀態/未登記/公私別/所有權/管理者/原地號）
  ＋ <out>/<name>_neighbors.json（發現階段的分組與筆數，供除錯）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import types
import warnings

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape
from shapely.ops import unary_union

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CAD_DIR = os.path.join(REPO, "地籍圖資")
SKILL = os.path.join(os.path.expanduser("~"), ".claude", "skills",
                     "cadastre-check", "scripts")
GAP_CHECK_M = 20.0     # 覆蓋率自我檢查的緊鄰帶寬度（小於 --buffer，聚焦「緊鄰」而非全部外擴範圍）
GAP_MIN_AREA = 10.0    # 低於此面積視為浮點誤差/畸零縫隙雜訊，不警告
PROBE_MAX_CHILD = 80   # 探測同宗地母號子號時的上限（已見案例最大用到45，留餘裕）
sys.path.insert(0, HERE)
from cadastre_fetch import cmd_fetch  # noqa: E402


def _load_api():
    if not os.path.isdir(SKILL):
        raise SystemExit(f"找不到 skill cadastre-check：{SKILL}")
    sys.path.insert(0, SKILL)
    import api  # noqa: PLC0415
    return api


def _county_file(county: str) -> str | None:
    for c in (county, county.replace("臺", "台"), county.replace("台", "臺")):
        p = os.path.join(CAD_DIR, c + ".shp")
        if os.path.exists(p):
            return p
    return None


def _load_cadlib():
    if not os.path.isdir(SKILL):
        raise SystemExit(f"找不到 skill cadastre-check：{SKILL}")
    sys.path.insert(0, SKILL)
    import cadlib  # noqa: PLC0415
    return cadlib


def discover(joined: str, buffer_m: float, county: str | None) -> tuple[dict, object, object]:
    """回 ({(縣市,鄉鎮,段): [landno,...]}, su, local_check_union)。
    su＝基地聯集幾何；local_check_union＝本機快照在 GAP_CHECK_M 緊鄰帶內的候選
    宗地聯集（含被排除的基地自身候選，供事後覆蓋率比對用，見 check_coverage_gap）。
    """
    cadlib = _load_cadlib()
    site = gpd.read_file(joined, engine="pyogrio")
    if site.crs and site.crs.to_epsg() != 3826:
        site = site.to_crs(3826)
    su = site.geometry.union_all()
    ring = su.buffer(buffer_m)

    if not county:
        for c in ("縣市", "縣市名稱"):
            if c in site.columns:
                county = str(site.iloc[0][c] or "").strip()
                break
    p = _county_file(county or "")
    if not p:
        raise SystemExit(f"找不到 地籍圖資\\{county}.shp（周邊地籍發現需本機地籍快照做"
                         "空間識別；只用它認地號身分，不用它的屬性）")

    sur_all = gpd.read_file(p, engine="pyogrio", bbox=ring.bounds)
    sur_all = sur_all[sur_all.geometry.intersects(ring)]
    # 排除基地本身：與基地重疊面積達自身 50% 以上視為同一宗地（浮點誤差/版本差不可用
    # 地號字串比對，改用幾何重疊比例判斷，較穩健）
    sur_all = sur_all.copy()
    sur_all["_ov"] = sur_all.geometry.intersection(su).area / sur_all.geometry.area.replace(0, 1)
    sur = sur_all[sur_all["_ov"] < 0.5]

    gap_check_ring = su.buffer(GAP_CHECK_M)
    near = sur_all[sur_all.geometry.intersects(gap_check_ring)]
    local_check_union = unary_union(list(near.geometry)) if len(near) else None

    # ★ 本機 shp 的「段」欄不含「段」字、「小段」欄不含「小段」字（如「下山子寮」／
    #   「考試潭」＋「鹿寮」），但即時查詢 API 要的 section 參數要**完整組字**
    #   （「下山子寮段」／「考試潭段鹿寮小段」）——直接拿欄位值去查會回「查無此段」
    #   （2026-08-06 七股案實踩）。段名權威來源見 config/section_index.csv 同構欄位。
    groups: dict[tuple, list[str]] = {}
    for _, r in sur.iterrows():
        no8 = f"{int(r['宗地母']):04d}{int(r['宗地子']):04d}"
        landno = cadlib.format_landno(no8)
        seg = "" if pd.isna(r["段"]) else str(r["段"]).strip()
        sub = "" if pd.isna(r["小段"]) else str(r["小段"]).strip()
        section = f"{seg}段{sub}小段" if sub else f"{seg}段"
        key = (str(r["縣市"]).strip(), str(r["鄉鎮"]).strip(), section)
        groups.setdefault(key, []).append(landno)
    for k in groups:
        groups[k] = sorted(set(groups[k]))
    return groups, su, local_check_union


def fetch_all(groups: dict, out_dir: str, name: str = "鄰地") -> tuple[str, "gpd.GeoDataFrame"]:
    os.makedirs(out_dir, exist_ok=True)
    parts = []
    for (county, town, section), landnos in groups.items():
        tag = f"_{name}_{county}{town}{section}".replace("/", "")
        a = types.SimpleNamespace(
            county=county, town=town, section=section, landnos=landnos,
            version=None, no_owner=False, out=out_dir, name=tag)
        print(f"\n=== 發現 {county}{town}{section}：{len(landnos)} 筆鄰地，即時回填屬性 ===")
        cmd_fetch(a)
        shp = os.path.join(out_dir, f"{tag}.shp")
        if os.path.exists(shp):
            parts.append(gpd.read_file(shp, engine="pyogrio"))

    if not parts:
        raise SystemExit("✖ 所有分組皆無成功筆數，未產出鄰地.shp")
    merged = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
    merged = gpd.GeoDataFrame(merged, crs=parts[0].crs)
    out_shp = os.path.join(out_dir, f"{name}.shp")
    merged.to_file(out_shp, encoding="utf-8")
    print(f"\n→ {out_shp}（合併 {len(groups)} 段、共 {len(merged)} 筆鄰地）")
    return out_shp, merged


def check_coverage_gap(su, local_check_union, live_gdf):
    """比對「本機快照緊鄰帶內候選宗地聯集」vs「即時回填後的實際聯集」，回缺口警告
    或 None。

    ★ 建立緣由（2026-08-06 七股案實踩）：66-42（交通用地、公路總局管理）緊鄰基地
    （距離0m），discover() 卻完全沒發現它——一開始以為是本機快照整筆漏收，
    但逐一比對後發現：本機快照其實**有**涵蓋那塊面積，只是掛在舊識別「66-23」
    名下（本機快照 66-23 面積 13,318㎡，但即時回填後的 66-23 只剩 8,648㎡登記
    面積）——**66-23 已被重測分割，一部分拆成新地號 66-42，本機快照沒跟上**。
    這代表單純比「本機快照 vs 本機快照自己」（覆蓋率100%）抓不到這種問題，
    必須比「本機快照候選聯集 vs 即時回填後實際聯集」，落差就是「本機快照以為
    在地、實際卻不在 discover() 找到的地號範圍內」的部分——不管成因是本機整筆
    漏收（如真的沒有任何舊識別）還是本機舊識別已被分割（如本案），都會在這裡現形。
    """
    if local_check_union is None or local_check_union.is_empty:
        return None, None
    live_union = unary_union(list(live_gdf.geometry) + [su])
    gap = local_check_union.difference(live_union)
    gap_area = gap.area if not gap.is_empty else 0.0
    if gap_area <= GAP_MIN_AREA:
        return None, None
    warning = (
        f"⚠ 基地 {GAP_CHECK_M:.0f}m 內有 {gap_area:,.1f} ㎡本機地籍快照候選宗地的範圍，"
        f"即時回填後對不上任何實際地號（本機快照 Ver 111年2月，落差常見成因：①快照"
        f"整筆漏收新增地號 ②快照的舊地號已被重測分割成新地號，快照沒跟上——如66-42"
        f"即從66-23分割出來）。不可逕自當作真的沒有相鄰宗地，建議：肉眼比對正射影像"
        f"有無明顯地界、或上 TCDGIS 覆核缺口範圍內的實際地號。")
    return warning, gap


def _gap_pieces(gap, min_area: float = GAP_MIN_AREA) -> list:
    if gap is None or gap.is_empty:
        return []
    geoms = list(gap.geoms) if gap.geom_type == "MultiPolygon" else [gap]
    return [g for g in geoms if g.area > min_area]


def find_probe_targets(pieces: list, county_shp: str, existing_landnos: set) -> dict:
    """對每個顯著缺口，找緊鄰的本機候選宗地母號，作為探測起點。

    ★ 建立緣由（2026-08-06 七股案 67-11 教訓，兩階段）：
    第一階段：check_coverage_gap 抓到落差後，上一版只印警告、要求人工「肉眼比對／
    上TCDGIS」——67-11 那次我人工只認真查了落差最大的一塊，其餘幾塊小的當成
    「resurvey 邊界誤差」略過，結果其中一塊正是真正漏收的地號 → 改成自動探測、
    不靠人工肉眼挑。
    第二階段（同日再度實踩，67-11 仍被漏掉）：**單一 `representative_point()`
    不夠**——第一輪修復前的缺口是一整塊連在一起的大範圍（~9,839㎡，同時涵蓋
    66 系與 67-11 所在區域），單一取樣點只落在 66 系那一側，67 母號完全沒被
    當成候選、探測階段連問都沒問過。改成**用整塊缺口幾何找候選**（
    `local.geometry.distance(piece)`，不是「候選到取樣點的距離」），同一塊缺口
    緊鄰的所有本機候選都會被收進來，不會因為缺口形狀複雜、樣本點只代表局部
    而漏掉另一側的候選母號。
    回 {(縣市,鄉鎮,段): {母號4碼, ...}}。
    """
    targets: dict[tuple, set] = {}
    if not pieces:
        return targets
    bounds = unary_union(pieces).buffer(5).bounds
    local = gpd.read_file(county_shp, engine="pyogrio", bbox=bounds)
    for piece in pieces:
        near = local[local.geometry.distance(piece) < 3.0]
        for _, r in near.iterrows():
            seg = "" if pd.isna(r["段"]) else str(r["段"]).strip()
            sub = "" if pd.isna(r["小段"]) else str(r["小段"]).strip()
            section = f"{seg}段{sub}小段" if sub else f"{seg}段"
            key = (str(r["縣市"]).strip(), str(r["鄉鎮"]).strip(), section)
            targets.setdefault(key, set()).add(f"{int(r['宗地母']):04d}")
    return targets


def probe_siblings(targets: dict, existing_landnos: set, gap_union, log: list) -> dict:
    """對 find_probe_targets 找到的每個宗地母號，批次探測子號 1~PROBE_MAX_CHILD
    是否存在（單一宗地母號一次 api 呼叫查完全部候選子號，不是逐筆查，較省時）。
    只留下「即時查詢成功」且「幾何真的落在缺口範圍內」兩者皆符合的，避免抓到
    同母號但离缺口很遠、其實不相關的子號。回可併入 groups 的
    {(縣市,鄉鎮,段): [landno,...]}。
    """
    api = _load_api()
    found: dict[tuple, list] = {}
    for (county, town, section), mothers in targets.items():
        for mother in sorted(mothers):
            candidates = [f"{int(mother)}-{c}" for c in range(1, PROBE_MAX_CHILD + 1)
                         if f"{int(mother)}-{c}" not in existing_landnos]
            if not candidates:
                continue
            log.append(f"    探測 {county}{town}{section} 母號{int(mother)}："
                      f"子號1~{PROBE_MAX_CHILD}（排除已知 {len(existing_landnos)} 筆）")
            try:
                r = api.query(county, town, section, candidates)
            except Exception as e:  # noqa: BLE001
                log.append(f"      ⚠ 探測失敗：{e}")
                continue
            for rec in r["records"]:
                if not rec["ok"] or not rec["geometry"]:
                    continue
                geom = shape(rec["geometry"])
                if gap_union is not None and geom.distance(gap_union) > 1.0:
                    continue   # 同母號但離這次缺口太遠，不相關，不強行納入
                landno = rec["parcel"]["landno"]
                log.append(f"      ✔ 探測到 {landno}（{geom.area:,.1f} ㎡，"
                          f"落在缺口範圍內，本機快照未收錄）")
                found.setdefault((county, town, section), []).append(landno)
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True, help="基地 shp（cadastre/基地.shp 或申請範圍）")
    ap.add_argument("--out", required=True, help="輸出資料夾")
    ap.add_argument("--buffer", type=float, default=50.0, help="外擴公尺數，預設 50")
    ap.add_argument("--county", default=None, help="縣市（未給則從基地 shp 屬性讀）")
    ap.add_argument("--name", default="鄰地")
    ap.add_argument("--no-auto-resolve", action="store_true",
                    help="關閉缺口自動探測（2026-08-06新增，預設開啟；67-11教訓："
                         "光印警告要求人工肉眼查，人工會漏，改成自動探測子號補回）")
    a = ap.parse_args()

    groups, su, local_check_union = discover(a.joined, a.buffer, a.county)
    total = sum(len(v) for v in groups.values())
    print(f"發現階段：{a.buffer:.0f}m 內共 {total} 筆鄰地，分 {len(groups)} 組"
          f"（縣市/鄉鎮/段不同者不能同一次查詢）")
    for k, v in groups.items():
        print(f"  {'/'.join(k)}：{len(v)} 筆　{v[:10]}{'...' if len(v) > 10 else ''}")

    with open(os.path.join(a.out, f"{a.name}_neighbors.json"), "w", encoding="utf-8") as f:
        json.dump({"/".join(k): v for k, v in groups.items()}, f,
                  ensure_ascii=False, indent=1)

    out_shp, merged = fetch_all(groups, a.out, a.name)
    existing_landnos = {ln for v in groups.values() for ln in v}
    # ★ 基地自身地號也要排除，否則探測子號時會把「基地自己」當成新發現的鄰地
    # 探測回來（2026-08-06 實踩：67-6 本身也被探測命中，因為它不在 groups 裡
    # ——discover() 本來就會排除基地自身，這裡要同樣排除，避免探測階段誤收）。
    site_gdf = gpd.read_file(a.joined, engine="pyogrio")
    if "地號" in site_gdf.columns:
        existing_landnos |= {str(x) for x in site_gdf["地號"].dropna()}

    gap_warning, gap = check_coverage_gap(su, local_check_union, merged)
    probe_log: list[str] = []
    if gap_warning and not a.no_auto_resolve:
        print(f"\n{gap_warning}")
        print("→ 自動探測缺口範圍內是否有本機快照未收錄的子號（67-11 教訓："
             "不能只靠人工肉眼挑，逐塊都要探測）...")
        p = _county_file((groups and next(iter(groups))[0]) or a.county or "")
        pieces = _gap_pieces(gap)
        targets = find_probe_targets(pieces, p, existing_landnos) if p else {}
        recovered = probe_siblings(targets, existing_landnos, gap, probe_log)
        for line in probe_log:
            print(line)
        if recovered:
            n_new = sum(len(v) for v in recovered.values())
            print(f"→ 探測到 {n_new} 筆本機快照未收錄、缺口範圍內的地號，併入回填")
            out_shp, merged = fetch_all(recovered, a.out, a.name + "_補正")
            # 併回主檔
            base = gpd.read_file(os.path.join(a.out, f"{a.name}.shp"), engine="pyogrio")
            merged_all = pd.concat([base, merged], ignore_index=True)
            merged_all = gpd.GeoDataFrame(merged_all, crs=base.crs)
            out_shp = os.path.join(a.out, f"{a.name}.shp")
            merged_all.to_file(out_shp, encoding="utf-8")
            merged = merged_all
            for k, v in recovered.items():
                groups.setdefault(k, [])
                groups[k] = sorted(set(groups[k]) | set(v))
            print(f"→ {out_shp}（自動補正後合計 {len(merged)} 筆鄰地）")
            # 補正後重新檢查是否還有殘餘缺口
            gap_warning, gap = check_coverage_gap(su, local_check_union, merged)
            if gap_warning:
                print(f"\n（自動補正後仍有殘餘）{gap_warning}")
            else:
                print("→ 自動補正後缺口已補齊，覆蓋率檢查通過")
        else:
            print("→ 探測未找到符合的子號——落差可能是水域/未登記空地/母號≥9000未登記土地"
                 "等真實現象，非本機快照漏收，建議人工核對正射影像確認。")

    with open(os.path.join(a.out, f"{a.name}_coverage_check.json"), "w",
             encoding="utf-8") as f:
        json.dump({"gap_check_m": GAP_CHECK_M, "警告": gap_warning,
                  "探測紀錄": probe_log}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
