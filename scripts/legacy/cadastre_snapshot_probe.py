# -*- coding: utf-8 -*-
"""【legacy】本機快照缺口偵測與子號探測補正（2026-09-04 自 `cadastre_neighbors.py` 移出）

★ 當時用途
------------------------------------------------------------------
周邊地籍舊路徑是「本機快照（Ver 111年2月）認出框內有哪些地號 → 逐筆問農業部」。
快照看不到快照之後才分割出來的地號，所以另外做了一整套補救：
  1. `check_coverage_gap()`  比對「快照候選聯集」vs「即時回填後實際聯集」找缺口
  2. `_gap_pieces()`         把缺口切成顯著塊
  3. `find_shrunk_mothers()` 逐母號比面積，<0.9 判為「就地分割」→ 精準探測目標
  4. `find_probe_targets()`  缺口鄰近母號（縮水偵測為空時的後備）
  5. `probe_siblings()`      對母號批次探子號 1~80，只留落在缺口內者

裡面每一條都是實案教訓：七股 66-23→66-42（單一取樣點會漏掉缺口另一側，改用整塊
缺口幾何找候選）、潮州五魁段13（缺口鄰近 16 個母號僅 2 個真縮水，全探會逾時，
故改以母號縮水精準鎖定）。函式本身的 docstring 保留了完整脈絡。

★ 為什麼移出
------------------------------------------------------------------
2026-09-04 起周邊地籍改走**出圖框直接向農業部框查**（`moa_parcel_query`），
一次取回範圍內全部宗地、不必事先知道地號，這套補救的前提（候選清單受限於快照）
就不存在了。麻豆客子寮段案還證明它在「整段重測改號」時會整組失效：縮水偵測靠
同一地號兩邊比面積，全段重編號後兩邊集合完全不重疊；子號探測用舊段名，該段在
現行版查無。

fallback 路徑（`cadastre_neighbors.run_local`）仍在，但已不再呼叫這套機械——
走到 fallback 時本來就接受資料降級，而**框內覆蓋率**會如實把留白報出來，
不需要再自動猜補。

★ 現在該用什麼
------------------------------------------------------------------
`scripts/moa_parcel_query.py` ＋ `cadastre_neighbors.frame_coverage()`。

回歸測試仍在 `tests/test_cadastre_neighbors.py`（改為 import 本模組），
把當年的錯法鎖成斷言，避免日後有人再走回頭路時重蹈覆轍。
"""
from __future__ import annotations

import os
import sys

import geopandas as gpd
from shapely.geometry import shape
from shapely.ops import unary_union

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from cadastre_neighbors import (          # noqa: E402
    GAP_CHECK_M, GAP_MIN_AREA, _load_api, _section_name)

PROBE_MAX_CHILD = 80   # 探測同宗地母號子號時的上限（已見案例最大用到45，留餘裕）
SHRINK_RATIO = 0.9     # 即時回填面積 < 快照面積×此值 → 判為「母號縮水」（就地分割）


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
            section = _section_name(r["段"], r["小段"])
            key = (str(r["縣市"]).strip(), str(r["鄉鎮"]).strip(), section)
            targets.setdefault(key, set()).add(f"{int(r['宗地母']):04d}")
    return targets


def find_shrunk_mothers(sur, live_gdf, ratio: float = SHRINK_RATIO) -> dict:
    """★母號縮水偵測（2026-08-10 潮州中山段20案定案）——比 find_probe_targets 的
    「缺口幾何鄰近」更精準的探測目標來源。

    成因：本機快照的某母號在 Ver 111年2月後**就地分割**成母號＋數個子號，快照仍是
    分割前的大範圍（如五魁段13 快照10,486㎡→即時4,999㎡；七股66-23 13,318→8,648），
    discover() 只認得舊母號、分割出的子號整批不在快照 → 漏。做法：把快照候選（sur）
    與即時回填後（live_gdf）**逐母號比面積**，即時面積 < 快照×ratio 者判為縮水、回其
    母號作探測起點。

    為何優於「缺口鄰近所有母號」：後者會把緊鄰大缺口但**沒縮水**的母號全收進來（潮州
    案 16 個），每個探 80 子號→極慢（實測逾時），且放大寫檔鎖窗口；本法精準鎖定真正
    縮水的（潮州 2 個：9、13），快又不誤觸。回 {(縣市,鄉鎮,段): {母號4碼}}。
    """
    targets: dict[tuple, set] = {}
    if sur is None or len(sur) == 0 or live_gdf is None or len(live_gdf) == 0:
        return targets
    # 快照逐母號面積（母號 = 宗地母，section 完整組字）
    snap = {}
    for _, r in sur.iterrows():
        key = (str(r["縣市"]).strip(), str(r["鄉鎮"]).strip(),
               _section_name(r["段"], r["小段"]), f"{int(r['宗地母']):04d}")
        snap[key] = snap.get(key, 0.0) + r.geometry.area
    # 即時回填逐母號面積（由 live_gdf 的 地號 解析母號；『段』已是完整「五魁段」字串）
    live = {}
    for _, r in live_gdf.iterrows():
        ln = str(r.get("地號", "")).strip()
        if not ln:
            continue
        mother = ln.split("-")[0]
        try:
            m4 = f"{int(mother):04d}"
        except ValueError:
            continue
        key = (str(r.get("縣市", "")).strip(), str(r.get("鄉鎮", "")).strip(),
               str(r.get("段", "")).strip(), m4)
        live[key] = live.get(key, 0.0) + r.geometry.area
    for key, snap_area in snap.items():
        live_area = live.get(key, 0.0)
        if snap_area > GAP_MIN_AREA and live_area < ratio * snap_area \
                and (snap_area - live_area) > GAP_MIN_AREA:
            county, town, section, m4 = key
            targets.setdefault((county, town, section), set()).add(m4)
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


