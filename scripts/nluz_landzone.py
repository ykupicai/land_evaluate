# -*- coding: utf-8 -*-
"""⓪c+ 國土功能分區逐筆查詢＋本機面圖層比對（出圖範圍內鄰地；nluz 優先覆蓋）

橋接 skill `nluz-landzone-query` 的查詢引擎（沿用其 NluzClient/節流/續跑邏輯，
不重造輪子），但輸入不吃清冊檔——直接讀 ⓪a/⓪a' 產出的 <案>/cadastre/基地.shp
＋ 鄰地.shp，逐筆查內政部 nluz 平台報部版草案分區，**同時**用本機
`國土功能分區/` 面圖層算同一筆地號的空間交集值（沿用 make_maps.py 出圖用的
同一套 registry/CRS/正規化邏輯，保證跟主題3出圖來源一致），兩者不同時：

    ★ nluz 優先，本機面圖層退為「周邊示意／nluz 查無時的備援」
    （2026-08-06 使用者定案；理由與本專案一貫原則一致——即時/權威來源蓋過
    本機舊快照，見 CLAUDE.md「⓪b 環敏平台說有涉及但本機圖資算不出來」同型）

★ **要不要查鄰地依案型自動分流**（`--neighbors auto`，預設；2026-08-06 使用者定案）：

    非都市土地案  → 基地 ＋ 出圖範圍內鄰地（鄰地分區攸關綠帶留設側等判斷）
    都市計畫案    → **只查基地**（都市土地的鄰地分區走本機 `都市計畫/` shp，
                     不靠 nluz；查了也只是拉長平台 5.5 秒/筆的作業時間）

  判斷依據＝基地 `使分` 欄有值的比例（`detect_land_kind()`），可用
  `--neighbors on/off` 手動覆寫。

★ 鄰地查詢範圍＝**出圖範圍**，不是 ⓪a' 的原始 50m 發現半徑：用
  `make_maps.compute_scale_extent()` 算出這個案子實際會出圖的框（跟 make_maps.py
  主題3同一套版面計算，再依 `NEIGH_EXPAND` 外擴，與「周邊地籍」圖層在地圖上
  涵蓋的範圍一致），框外的鄰地不查、不比對。
  ⚠ **收斂幅度視案子而定，不保證大**：善南段案 197→185 筆（只少 12 筆），因為
  該案出圖範圍（1/1500 圖框＋8% ≈ 348×331m）本來就與鄰地發現範圍（基地外擴
  50m ≈ 359×343m）差不多大。真正的省時來自上面的案型分流。

用法（★需 QGIS 內建 python，因為要讀本機面圖層與算出圖範圍）：
  & "<QGIS>\\bin\\python-qgis-ltr.bat" scripts\\nluz_landzone.py --case <案名>
    [--limit N]              只處理前 N 筆（試跑/smoke test，建議先用小數字）
    [--neighbors auto|on|off] 是否連鄰地查（預設 auto＝依案型判斷，見上）
    [--skip-neighbors]       （相容舊指令）等同 --neighbors off
    [--overwrite]            忽略既有 checkpoint 全部重查

★ nluz 對都市計畫區**不是必定查無**（2026-08-06 善南段案實測有回值「城鄉發展
  地區第一類」）——舊版 skill 文件寫的「都市計畫區平台查無」指的是使用地土地
  清冊那組欄位，不代表沒有國土功能分區值，所以都市計畫案這步仍值得跑（只是
  不查鄰地）。

★ 時間成本：平台限流 5.5 秒/筆。先 --limit 一個小數字試跑確認流程沒問題再放
  全量跑；筆數多時務必背景跑。

產出（outputs/<案>/）：
  國土功能分區_逐筆核對.csv   地號/段/縣市/是否基地/本機面圖層值/nluz值/
                              查詢狀態/是否不同/採用值(nluz優先)
  國土功能分區/逐筆覆蓋.gpkg  最終採用值（nluz 覆蓋後）的逐宗地面，欄位
                              縣市/zone——供 make_maps.py 主題3出圖優先讀取
                              （比照 make_env_map 的案件層覆蓋機制，見下方
                              make_maps.py 對應改動）
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from argparse import Namespace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
SKILL = Path(os.path.expanduser("~")) / ".claude" / "skills" / "nluz-landzone-query" / "scripts"

from shp_trust_utils import (  # noqa: E402
    F_COUNTY, F_LANDNO, F_SEG, F_TOWN, F_ZONE,
    normalize_landno, read_dbf, shp_get, shp_norm_city,
)
from zone_utils import extract_zones_from_text, normalize_zone  # noqa: E402


def _load_nluz():
    if not SKILL.is_dir():
        sys.exit(f"[nluz] 找不到 skill：{SKILL}\n  本步依賴 skill `nluz-landzone-query`。")
    sys.path.insert(0, str(SKILL))
    import nluz_query  # noqa: PLC0415
    return nluz_query


def read_parcels(shp_path: Path, tag: str) -> list[dict]:
    """讀 ⓪a 來源 shp(dbf) → [{縣市,鄉鎮市區,地段名稱,地號,標記}, ...]。"""
    dbf = str(shp_path.with_suffix(".dbf"))
    if not Path(dbf).exists():
        return []
    out = []
    for r in read_dbf(dbf):
        county = shp_norm_city(shp_get(r, F_COUNTY))
        town = shp_get(r, F_TOWN)
        seg = shp_get(r, F_SEG)
        landno = shp_get(r, F_LANDNO)
        if county and town and seg and landno:
            out.append({"縣市": county, "鄉鎮市區": town, "地段名稱": seg,
                        "地號": landno, "標記": tag})
    return out


def detect_land_kind(base_shp: Path) -> tuple[str, str]:
    """依基地 `使分`（使用分區）欄有值的比例判斷案型，回 (案型, 說明)。

    ★ 用途只有一個：決定**要不要連鄰地一起查**（2026-08-06 使用者定案）——
      非都市土地案要連鄰地查（鄰地分區攸關綠帶留設側等判斷），都市計畫案只查
      基地即可（都市土地的鄰地分區走本機`都市計畫/`shp，不靠 nluz）。

    ⚠ 這裡**不是**在做「都市土地 vs 未登錄地」的身分認定——`使分`空白兩者皆可能，
      getLandInfo 無法區分（見 CLAUDE.md「地籍空白使類≠都市土地」）。但對本用途
      而言兩者處置相同（都不需要查 nluz 鄰地），所以合併為「非非都市土地」一類，
      交付文件仍不可依本函式的回傳值去寫定地號身分。
    """
    dbf = str(base_shp.with_suffix(".dbf"))
    if not Path(dbf).exists():
        return "未知", "讀不到基地 dbf"
    recs = read_dbf(dbf)
    if not recs:
        return "未知", "基地 dbf 無記錄"
    n = len(recs)
    n_zoned = sum(1 for r in recs if shp_get(r, F_ZONE))
    ratio = n_zoned / n
    if ratio >= 0.5:
        return "非都市土地", f"{n} 筆中 {n_zoned} 筆有使用分區（{ratio:.0%}）"
    return "都市土地或未登錄地", f"{n} 筆中僅 {n_zoned} 筆有使用分區（{ratio:.0%}）"


def map_extent_of(base_shp: Path):
    """算這個案子的**出圖範圍**（與 make_maps.py 主題3出圖同一套版面計算）：
    基地 bbox → compute_scale_extent（比例尺退階後的實際圖框）→ 再依
    NEIGH_EXPAND 外擴（與「周邊地籍」圖層在地圖上涵蓋的範圍一致）。
    回傳 QgsRectangle；需 QGIS 環境。"""
    import make_maps  # noqa: PLC0415
    from qgis.core import QgsVectorLayer  # noqa: PLC0415

    lyr = QgsVectorLayer(str(base_shp), "base", "ogr")
    if not lyr.isValid() or not lyr.featureCount():
        return None
    bbox = lyr.extent()
    _scale, extent = make_maps.compute_scale_extent(bbox)
    return extent.buffered(extent.width() * make_maps.NEIGH_EXPAND)


def read_parcels_in_extent(shp_path: Path, tag: str, rect) -> list[dict]:
    """讀鄰地 shp，**只收與出圖範圍相交者**（2026-08-06 使用者定案：鄰地查詢
    收斂到出圖範圍內，範圍外的地號不會畫進圖、查了也用不到，只是拉長平台
    5.5 秒/筆的作業時間）。需 QGIS 環境。"""
    from qgis.core import QgsFeatureRequest, QgsVectorLayer  # noqa: PLC0415

    if not shp_path.exists():
        return []
    lyr = QgsVectorLayer(str(shp_path), tag, "ogr")
    if not lyr.isValid():
        return []
    fld = lyr.fields()

    def idx(keys):
        return next((fld.indexOf(k) for k in keys if fld.indexOf(k) >= 0), -1)

    i_c, i_t, i_s, i_l = idx(F_COUNTY), idx(F_TOWN), idx(F_SEG), idx(F_LANDNO)
    out, seen = [], set()
    for f in lyr.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
        county = shp_norm_city(str(f[i_c]).strip() if i_c >= 0 else "")
        town = str(f[i_t]).strip() if i_t >= 0 else ""
        seg = str(f[i_s]).strip() if i_s >= 0 else ""
        landno = str(f[i_l]).strip() if i_l >= 0 else ""
        if not (county and town and seg and landno):
            continue
        if (seg, landno) in seen:
            continue
        seen.add((seg, landno))
        out.append({"縣市": county, "鄉鎮市區": town, "地段名稱": seg,
                    "地號": landno, "標記": tag})
    return out


def query_nluz(parcels: list[dict], case_dir: Path, overwrite: bool) -> dict:
    """依縣市分組逐一呼叫 skill 既有 cmd_query（原樣沿用節流/續跑/段代碼比對邏輯），
    回傳 {(段,地號): row}。"""
    nluz_query = _load_nluz()
    by_county: dict[str, list[dict]] = {}
    for p in parcels:
        by_county.setdefault(p["縣市"], []).append(p)

    merged: dict[tuple, dict] = {}
    for county, plist in by_county.items():
        tmp_roster = case_dir / f"_nluz_roster_{county}.csv"
        with io.open(tmp_roster, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["鄉鎮市區", "地段名稱", "地號"])
            w.writeheader()
            for p in plist:
                w.writerow({"鄉鎮市區": p["鄉鎮市區"], "地段名稱": p["地段名稱"], "地號": p["地號"]})
        out_csv = case_dir / f"_nluz_out_{county}.csv"
        print(f"[nluz] {county}：{len(plist)} 筆待查（限流 5.5 秒/筆，"
              f"預估 {len(plist) * 5.5 / 60:.1f} 分鐘）", flush=True)
        ns = Namespace(roster=str(tmp_roster), county=county, out=str(out_csv),
                       limit=None, overwrite=overwrite)
        nluz_query.cmd_query(ns)
        with open(out_csv, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                merged[(row["地段名稱"], row["地號"])] = row
        tmp_roster.unlink(missing_ok=True)
    return merged


def local_zone_lookup(parcels: list[dict], base_shp: Path, neigh_shp: Path,
                      county_zones: dict[str, "QgsVectorLayer"]) -> dict:  # noqa: F821
    """對每筆地號算本機 國土功能分區/ 面圖層的空間交集值（最大重疊面積者勝）。
    回傳 {(段,地號): normalized_zone_or_None}。需 QGIS 環境。"""
    from qgis.core import QgsFeatureRequest, QgsGeometry  # noqa: PLC0415

    from shp_trust_utils import F_LANDNO as _FL, F_SEG as _FS  # noqa: PLC0415

    result: dict[tuple, str | None] = {}
    for shp_path, tag in ((base_shp, "基地"), (neigh_shp, "鄰地")):
        if not shp_path.exists():
            continue
        from qgis.core import QgsVectorLayer  # noqa: PLC0415
        lyr = QgsVectorLayer(str(shp_path), tag, "ogr")
        if not lyr.isValid():
            continue
        fld = lyr.fields()
        i_ln = next((fld.indexOf(k) for k in _FL if fld.indexOf(k) >= 0), -1)
        i_seg = next((fld.indexOf(k) for k in _FS if fld.indexOf(k) >= 0), -1)
        for f in lyr.getFeatures():
            landno = str(f[i_ln]).strip() if i_ln >= 0 else ""
            seg = str(f[i_seg]).strip() if i_seg >= 0 else ""
            if not landno:
                continue
            key = (seg, landno)
            if key in result:
                continue
            county = next((p["縣市"] for p in parcels
                          if p["地段名稱"] == seg and p["地號"] == landno), None)
            zones_mem = county_zones.get(county) if county else None
            if zones_mem is None:
                result[key] = None
                continue
            g = f.geometry()
            best_zone, best_area = None, 0.0
            req = QgsFeatureRequest().setFilterRect(g.boundingBox())
            for zf in zones_mem.getFeatures(req):
                inter = g.intersection(zf.geometry())
                if inter.isEmpty():
                    continue
                a = inter.area()
                if a > best_area:
                    best_area, best_zone = a, zf["zone"]
            result[key] = best_zone
    return result


def main():
    ap = argparse.ArgumentParser(description="⓪c+ 國土功能分區逐筆查詢＋本機比對（nluz 優先）")
    ap.add_argument("--case", required=True)
    ap.add_argument("--limit", type=int, default=None, help="只處理前 N 筆（試跑）")
    ap.add_argument("--neighbors", choices=("auto", "on", "off"), default="auto",
                    help="是否連出圖範圍內鄰地一起查。auto（預設）＝依案型自動判斷："
                         "非都市土地案查鄰地、都市計畫案只查基地；on/off 手動覆寫")
    ap.add_argument("--skip-neighbors", action="store_true",
                    help="（相容舊指令）等同 --neighbors off")
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args()

    case_dir = REPO / "outputs" / a.case
    base_shp = case_dir / "cadastre" / "基地.shp"
    neigh_shp = case_dir / "cadastre" / "鄰地.shp"
    if not base_shp.exists():
        sys.exit(f"[nluz] 找不到基地 shp：{base_shp}\n  先跑 ⓪a（cadastre_fetch.py fetch）")

    # ★ 全程單一 QGIS session（⚠ initQgis/exitQgis 同行程呼叫第二次會直接崩潰
    #   exit code 5，2026-08-06 善化案 smoke test 實踩）。出圖範圍計算、鄰地
    #   篩選、本機面圖層比對、覆蓋 gpkg 寫出都在這個 session 內完成；中間的
    #   nluz 平台查詢是純網路 I/O，留在 session 內無妨。
    import yaml  # noqa: PLC0415
    from qgis.core import QgsApplication  # noqa: PLC0415

    qgs = QgsApplication([], False)
    qgs.initQgis()
    try:
        import make_maps  # noqa: PLC0415
        from qgis.core import QgsVectorFileWriter, QgsVectorLayer  # noqa: PLC0415
        with (REPO / "config" / "county_registry.yaml").open(encoding="utf-8") as f:
            registry = yaml.safe_load(f)

        # --- 0. 算出圖範圍、收斂鄰地清單 ---
        parcels = read_parcels(base_shp, "基地")
        n_base = len(parcels)

        # 案型 → 要不要查鄰地（2026-08-06 使用者定案）
        kind, why = detect_land_kind(base_shp)
        if a.skip_neighbors:                      # 舊旗標優先（明示關閉）
            want_neigh, reason = False, "--skip-neighbors 明示關閉"
        elif a.neighbors == "on":
            want_neigh, reason = True, "--neighbors on 明示開啟"
        elif a.neighbors == "off":
            want_neigh, reason = False, "--neighbors off 明示關閉"
        else:                                     # auto
            want_neigh = (kind == "非都市土地")
            reason = (f"auto：判為{kind}（{why}）→ "
                      + ("連鄰地一起查" if want_neigh
                         else "只查基地（都市土地鄰地分區走本機都市計畫shp，不靠 nluz）"))
        print(f"[nluz] 案型判斷 {reason}", flush=True)

        if want_neigh and neigh_shp.exists():
            map_rect = map_extent_of(base_shp)
            if map_rect is None:
                print("[nluz] ⚠ 基地 shp 無有效幾何，無法算出圖範圍 → 本次不含鄰地",
                      flush=True)
            else:
                n_all = len(read_parcels(neigh_shp, "鄰地"))
                neigh = read_parcels_in_extent(neigh_shp, "鄰地", map_rect)
                parcels += neigh
                print(f"[nluz] 基地 {n_base} 筆 ＋ 鄰地 {len(neigh)} 筆"
                      f"（出圖範圍內；鄰地.shp 共 {n_all} 筆，範圍外 "
                      f"{n_all - len(neigh)} 筆不查）= 共 {len(parcels)} 筆待處理",
                      flush=True)
        elif want_neigh:
            print(f"[nluz] 僅基地 {n_base} 筆（找不到 鄰地.shp，⓪a′ 可能未跑）", flush=True)
        else:
            print(f"[nluz] 僅基地 {n_base} 筆", flush=True)
        if a.limit:
            parcels = parcels[: a.limit]
            print(f"[nluz] --limit {a.limit}，只處理前 {len(parcels)} 筆", flush=True)
        if not parcels:
            sys.exit("[nluz] 無可處理地號（縣市/鄉鎮/段/地號四欄缺一即略過，"
                     "檢查 cadastre shp 欄名）")

        # --- 1. nluz 平台逐筆查（純網路 I/O）---
        nluz_rows = query_nluz(parcels, case_dir, a.overwrite)

        counties = sorted({p["縣市"] for p in parcels})
        county_zones = {}
        for county in counties:
            # ⚠ 欄位數要跟 load_zones_for_county 的 add_feat(zones_mem, g, [county, z])
            # 對齊（2欄，位置對應）——只給 1 個 zone 欄會讓 county 值錯位塞進 zone 欄，
            # 真正的分區值反而遺失（2026-08-06 debug 腳本抓到，善南段5筆全部查出
            # zone="臺南市" 才發現）。
            zones_mem = make_maps.mem_layer("功能分區", "field=縣市:string&field=zone:string")
            warnings: list[str] = []
            # 涵蓋全案（基地+鄰地）bbox，外擴 5% 保守起見
            rect = None
            for shp_path in (base_shp, neigh_shp):
                if not shp_path.exists():
                    continue
                lyr = QgsVectorLayer(str(shp_path), "tmp", "ogr")
                if lyr.isValid() and lyr.featureCount():
                    ext = lyr.extent()
                    # ⚠ combineExtentWith 是原地 mutate、回傳 None（同 make_maps.py
                    # 既有 `X.combineExtentWith(Y) or X` 寫法防的同一個坑）——
                    # 直接把回傳值指回 rect 會在第二輪把 rect 蓋成 None，導致
                    # 下面 `if rect is None: continue` 誤判整個縣市無範圍可查，
                    # 本機面圖層比對整批靜默略過（2026-08-06 善南段案 debug 腳本
                    # 對照才抓到：獨立測試只用單一 shp 沒有第二輪 combine，沒踩到）。
                    if rect is None:
                        rect = ext
                    else:
                        rect.combineExtentWith(ext)
            if rect is None:
                continue
            rect = rect.buffered(rect.width() * 0.05 or 50)
            n = make_maps.load_zones_for_county(zones_mem, registry, county, [rect], warnings)
            print(f"[nluz] {county} 本機功能分區要素 {n} 筆" +
                  ("".join(f"\n  ⚠ {w}" for w in warnings) if warnings else ""), flush=True)
            county_zones[county] = zones_mem

        local_rows = local_zone_lookup(parcels, base_shp, neigh_shp, county_zones)

        # 比對（純 python，但留在 QGIS session 內一起做，省一次 init/exit）
        out_rows, override_feats = _compare(parcels, nluz_rows, local_rows)
        out_csv = case_dir / "國土功能分區_逐筆核對.csv"
        _write_compare_csv(out_csv, out_rows)
        n_diff = sum(1 for r in out_rows if r["是否不同"] == "是")
        print(f"\n[nluz] 完成 {len(out_rows)} 筆，其中 {n_diff} 筆本機面圖層與 nluz 不同"
              f"（已採 nluz 值覆蓋）", flush=True)
        print(f"→ {out_csv}", flush=True)

        if override_feats:
            from qgis.core import QgsGeometry  # noqa: PLC0415
            geom_by_key: dict[tuple, QgsGeometry] = {}
            for shp_path in (base_shp, neigh_shp):
                if not shp_path.exists():
                    continue
                lyr = QgsVectorLayer(str(shp_path), "tmp", "ogr")
                if not lyr.isValid():
                    continue
                fld = lyr.fields()
                i_ln = next((fld.indexOf(k) for k in F_LANDNO if fld.indexOf(k) >= 0), -1)
                i_seg = next((fld.indexOf(k) for k in F_SEG if fld.indexOf(k) >= 0), -1)
                for f in lyr.getFeatures():
                    landno = str(f[i_ln]).strip() if i_ln >= 0 else ""
                    seg = str(f[i_seg]).strip() if i_seg >= 0 else ""
                    # ⚠ f.geometry() 要深拷貝才能存進 dict 供本迴圈之外使用，否則
                    # provider 釋放後變成懸空參照，qgs.exitQgis() 時直接崩潰
                    # exit code 5（2026-08-06 smoke test 實踩，同 CLAUDE.md 記錄的
                    # PyQGIS 硬崩潰陷阱同型）。
                    if landno:
                        geom_by_key.setdefault((seg, landno), QgsGeometry(f.geometry()))
                lyr = None
            out = make_maps.mem_layer("逐筆覆蓋",
                                      "field=段:string&field=地號:string&field=zone:string")
            for seg, landno, zone in override_feats:
                g = geom_by_key.get((seg, landno))
                if g is not None:
                    make_maps.add_feat(out, g, [seg, landno, zone])
            override_dir = case_dir / "國土功能分區"
            override_dir.mkdir(parents=True, exist_ok=True)
            gpkg_path = override_dir / "逐筆覆蓋.gpkg"
            opts = QgsVectorFileWriter.SaveVectorOptions()
            opts.driverName = "GPKG"
            opts.layerName = "逐筆覆蓋"
            QgsVectorFileWriter.writeAsVectorFormatV3(out, str(gpkg_path),
                                                       out.transformContext(), opts)
            print(f"→ {gpkg_path}（{out.featureCount()} 筆，供 make_maps.py 主題3優先讀取）",
                  flush=True)
    finally:
        qgs.exitQgis()

    print("\n⚠ 交付口徑：nluz為報部版草案，平台自述僅供參考，仍以直轄市/縣市政府認定為準；"
          "本機面圖層版本各縣市不一，兩者不一致一律以本次覆蓋結果（nluz優先）為準並在"
          "簡報標註差異來源。")


def _compare(parcels, nluz_rows, local_rows):
    """比對本機面圖層值 vs nluz 值，決定採用值與是否寫進覆蓋 gpkg。

    ★ 跨分區宗地的處理（2026-08-06 七股案實測抓到）：nluz 對橫跨多個分區的
      宗地回的是**複合描述字串**（「部分海洋資源地區第一類之一、部分海洋資源
      地區第三類、部分農業發展地區第二類」），不是單一分區值。這種值：
        ① 不在 `styles/國土功能分區.qml` 的 21 類分類表裡 → 拿去覆蓋出圖會
           讓該宗地**上不了色**（比原本更糟）；
        ② 本機面圖層對這種宗地反而更精確——它是真的把宗地切成多塊不同分區的
           面，能如實畫出分界，而 nluz 的字串是資訊降級。
      → 因此複合值**不寫進覆蓋 gpkg**（出圖沿用本機面圖層的真實切分），但
      CSV 照實記錄 nluz 原文與解析出的分區清單，供人工判讀。這不違反「nluz
      優先」原則：該原則要處理的是「兩邊給不同單一值時信誰」，跨分區宗地
      不屬於這個情境。
    """
    out_rows = []
    override_feats = []  # (段, 地號, final_zone) 供覆蓋 gpkg 用
    n_multi = 0
    for p in parcels:
        key = (p["地段名稱"], p["地號"])
        nrow = nluz_rows.get(key, {})
        nluz_status = nrow.get("查詢狀態", "未查")
        nluz_zone_raw = (nrow.get("國土功能分區") or "").strip()
        nluz_zone = normalize_zone(nluz_zone_raw)          # 單一標準名才有值
        local_zone = local_rows.get(key)
        multi = None
        if nluz_status == "有資料" and nluz_zone_raw and nluz_zone is None:
            # 非單一標準名 → 試解析成分區清單（跨分區宗地）
            parts = extract_zones_from_text(nluz_zone_raw)
            if len(parts) > 1:
                multi = parts

        if multi:                    # 跨分區宗地：不覆蓋，出圖沿用本機面圖層
            n_multi += 1
            final_zone = local_zone
            diff = f"跨{len(multi)}分區（不覆蓋，出圖沿用本機面圖層切分）"
            write_override = False
        elif nluz_status == "有資料" and (nluz_zone or nluz_zone_raw):
            final_zone = nluz_zone or nluz_zone_raw
            diff = "是" if (local_zone and local_zone != final_zone) else \
                   ("（本機無資料，nluz補上）" if not local_zone else "否")
            write_override = True
        else:
            final_zone = local_zone
            diff = "—"
            write_override = bool(local_zone)

        out_rows.append({
            "標記": p["標記"], "縣市": p["縣市"], "段": p["地段名稱"], "地號": p["地號"],
            "本機面圖層值": local_zone or "", "nluz查詢狀態": nluz_status,
            "nluz值": nluz_zone_raw, "是否不同": diff,
            "採用值(nluz優先)": final_zone or "",
        })
        if write_override and final_zone:
            override_feats.append((p["地段名稱"], p["地號"], final_zone))
    if n_multi:
        print(f"[nluz] ⚠ {n_multi} 筆為跨分區宗地（nluz 回複合描述值）→ 不寫入覆蓋，"
              f"出圖沿用本機面圖層的真實分區切分；詳見核對 CSV「是否不同」欄", flush=True)
    return out_rows, override_feats


def _write_compare_csv(out_csv: Path, out_rows: list[dict]) -> None:
    with io.open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["標記", "縣市", "段", "地號", "本機面圖層值",
                                          "nluz查詢狀態", "nluz值", "是否不同", "採用值(nluz優先)"])
        w.writeheader()
        w.writerows(out_rows)


if __name__ == "__main__":
    main()
