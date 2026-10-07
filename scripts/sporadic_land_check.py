# -*- coding: utf-8 -*-
"""零星土地檢核（非都市土地使用管制規則 §35、§35-1）＋所在連續農業用地（農地口袋）聚合

★ 建立緣由（2026-10-03 高雄彌陀安港段587案結案沉澱）
------------------------------------------------------------------
一般農業區／特定農業區的農業用地，附表三不允許以一般興辦事業計畫變更為甲～丁種建築用地，
私人實務上只剩 §35（毗鄰甲／丙建之零星狹小土地）與 §35-1（鄉村區邊緣畸零不整土地）兩條。
彌陀案當時以案件層一次性腳本算出：587 本身 0.118 公頃、在 §35-1 第三款 0.5 公頃以內，
但所在連續農牧用地 49 筆共 4.38 公頃、外圍仍緊鄰農牧用地 → 判不符。本檔把該分析固化。

判準來源（改動前先看衍古 `_判準登記表` B 表「零星土地整體認定的聚合法」列）：
  · 法源「數筆個別申請應就整體認定」屬 A 類（§35Ⅴ；§35-1Ⅳ 準用）。
  · 聚合實作屬 B 類：周邊框查宗地中，**非鄉村區**且使類屬六類農業用地
    （農牧／養殖／水利／林業／生態保護／國土保安；與 greenbelt_ratio.AG_KINDS 同一份）者，
    以 0.3m 容差相接建連通分量，取含基地者＝所在連續農業用地。
  · ★ **水利用地預設當隔絕要素、不併入口袋**（2026-10-07 code review 修正）：§35 第二款、
    §35-1 第一、三款都把「水溝」列為隔絕要素，若把水利用地併進口袋，被水溝隔開的基地會被
    算成大片農地而誤判不符。`--water-as-agri` 可回到舊算法（敏感度分析）。
  · ⚠ 都市計畫**農業區**不是隔絕要素（§35-1 第一款的隔絕要素是都市計畫住宅、商業、工業區）；
    框查屬性只有「都計／非都計」，分區別一律列「待查」。
  · ⚠ §35 的前提是**毗鄰甲種、丙種建築用地**（或國宅等特目用地）——只毗鄰鄉村區**乙種**
    建築用地者前提不成立（彌陀案實例；錯法已寫成 tests/test_sporadic_land_check.py 斷言）。

法條：非都市土地使用管制規則 D0060013（115.05.27）§35、§35-1，官方全文存於
  outputs/20260924_阿里山二萬坪停車場/法規佐證/非都市土地使用管制規則_D0060013.txt。

★ 判定一律為「初判，以縣市政府認定為準」
------------------------------------------------------------------
本檔只能從地籍使用類別與幾何推得的部分下判斷；以下事項**資料上無從得知**，一律標「待查」：
道路水溝平均寬度≥4m、是否 78.04.03 前編定、特目用地是否作建築使用（國宅等）、都市計畫分區別、
未登記土地是否實際作道路水溝、凹入形狀與缺口寬度（以外圍非隔絕要素接觸長度近似）、
「三面連接」（以鄉村區接觸比例近似）、狹長地之對邊、是否禁限建與隔離必要。

用法（系統 Python）：
  python -X utf8 scripts/sporadic_land_check.py --case outputs/<案>
      [--buffer 250] [--frame <既有框查.gpkg>] [--water-as-agri] [--out-dir <資料夾>]
輸出：<out>/零星土地檢核.json／.txt、<out>/cadastre/農地口袋.gpkg、
      <out>/cadastre/周邊<N>m_框查.gpkg（線上框查快取，可用 --frame 重用）
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
from datetime import date

import geopandas as gpd
from shapely import STRtree, union_all
from shapely.geometry import box

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from greenbelt_ratio import AG_KINDS  # noqa: E402 — 六類農業用地唯一定義

TOL = 0.3            # 相接容差（m）：宗地界址常有髮絲縫，0.3m 內視為相接
MIN_EDGE = 1.0       # 共同邊界達此長度才算「毗鄰」；角碰（0.3m 容差約 0.6m）不算
SURROUND = 0.95      # 外圍接觸比例達此值視為「包圍／隔絕」（留 5% 給髮絲縫與量測誤差）
AREA_012 = 1200.0    # 0.12 公頃
AREA_05 = 5000.0     # 0.5 公頃
AREA_0012 = 120.0    # 0.012 公頃
WIDTH_10 = 10.0      # 狹長地平均寬度上限
GAP_20 = 20.0        # §35③ 缺口寬度上限
PLUS10 = 1.10        # 「因地形坵塊完整需要得為百分之十以內之增加」
LAW = "非都市土地使用管制規則（D0060013，115.05.27 修正）"
CAVEAT = "初判，以縣市政府認定為準"

OK, PEND, NG = "符合", "待查", "不符"
_RANK = {NG: 0, PEND: 1, OK: 2}


# ------------------------------------------------------------------ 欄位正規化
def _blank(v) -> bool:
    if v is None:
        return True
    try:
        if v != v:  # NaN
            return True
    except Exception:  # noqa: BLE001
        pass
    return str(v).strip().lower() in ("", "none", "nan", "null")


def _s(v) -> str:
    return "" if _blank(v) else str(v).strip()


def _fmt_landno(v) -> str:
    """'05870000' → '587'；'04830001' → '483-1'；已是人類格式者原樣回傳。"""
    s = _s(v)
    if len(s) == 8 and s.isdigit():
        mo, zi = int(s[:4]), int(s[4:])
        return f"{mo}-{zi}" if zi else f"{mo}"
    return s


def _is_unregistered(landno: str) -> bool:
    mo = landno.split("-", 1)[0]
    return mo.isdigit() and int(mo) >= 9000


def normalize_frame(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """框查成果統一成 地號／段／使分／使類／都計區／登面積／未登記。

    吃兩種來源：①`moa_parcel_query.to_gdf` 的短欄名（本檔線上查詢與快取）；
    ②框查原始欄名（使用分區／使用類別／都市計畫區／登記面積_平方公尺，彌陀案的
    `周邊250m_框查.gpkg` 即此格式）。
    """
    def col(*names):
        for n in names:
            if n in gdf.columns:
                return gdf[n]
        return [None] * len(gdf)

    if "段" in gdf.columns and "小段" in gdf.columns and "使用類別" in gdf.columns:
        import moa_parcel_query as mpq  # noqa: PLC0415
        sec = [mpq.section_name(a, b) for a, b in zip(gdf["段"], gdf["小段"])]
    else:
        sec = [_s(v) for v in col("段")]
    out = gpd.GeoDataFrame({
        "地號": [_fmt_landno(v) for v in col("地號")],
        "段": sec,
        "使分": [_s(v) for v in col("使分", "使用分區")],
        "使類": [_s(v) for v in col("使類", "使用類別")],
        "都計區": [_s(v) for v in col("都計區", "都市計畫區")],
        "登面積": [None if _blank(v) else float(v)
                for v in col("登面積", "登記面積_平方公尺")],
    }, geometry=list(gdf.geometry), crs=gdf.crs or "EPSG:3826")
    if out.crs.to_epsg() != 3826:
        out = out.to_crs(3826)
    out["未登記"] = ["是" if _is_unregistered(n) else "" for n in out["地號"]]
    out = out[out.geometry.notna() & ~out.geometry.is_empty].reset_index(drop=True)
    return out


def reg_area(row, geom) -> float:
    """登記面積優先；取不到（含未登記地＝0）才退回圖面積（CLAUDE.md 面積基準）。"""
    v = row.get("登面積")
    try:
        if v is not None and float(v) > 0:
            return float(v)
    except (TypeError, ValueError):
        pass
    return float(geom.area)


# ------------------------------------------------------------------ 分類
def is_agri(r) -> bool:
    """聚合用：非鄉村區、非都市計畫區，且使類屬六類農業用地。"""
    return (r["使分"] != "鄉村區" and r["都計區"] != "都計"
            and r["使類"] in AG_KINDS)


def categories(r) -> list[str]:
    """外圍／緊鄰接觸的類別（**可重疊**：鄉村區乙建同時屬「建築用地」與「鄉村區」）。"""
    c = []
    lt, lz = r["使類"], r["使分"]
    if r["都計區"] == "都計":
        c.append("都市計畫區")
    if r["未登記"] == "是":
        c.append("未登記土地")
    if lt == "交通用地":
        c.append("交通用地")
    if lt == "水利用地":
        c.append("水利用地")
    if "建築用地" in lt:
        c.append("建築用地")
    if lt == "特定目的事業用地":
        c.append("特定目的事業用地")
    if lz == "鄉村區":
        c.append("鄉村區")
    if lt in AG_KINDS and lz != "鄉村區":
        c.append("農業用地")
    if not c:
        c.append("其他")
    return c


CAT_ORDER = ["交通用地", "建築用地", "鄉村區", "都市計畫區", "水利用地",
             "特定目的事業用地", "未登記土地", "農業用地", "其他"]


# ------------------------------------------------------------------ 幾何工具
def close_union(geoms):
    """多宗地合併並閉合髮絲縫（與 make_greenbelt --close-gap 同理），避免內縫灌大周長。"""
    return union_all([g.buffer(TOL) for g in geoms]).buffer(-TOL)


def rect_dims(geom) -> tuple[float, float]:
    """最小外接矩形 (長邊, 短邊)。"""
    r = geom.minimum_rotated_rectangle
    if r.geom_type != "Polygon":
        return float(r.length), 0.0
    xs = list(r.exterior.coords)
    a, b = math.dist(xs[0], xs[1]), math.dist(xs[1], xs[2])
    return max(a, b), min(a, b)


def contact_len(line, geoms) -> float:
    """line 落在 geoms 各自外擴 TOL 範圍內的長度（先聯集，避免重複計算）。"""
    if not geoms:
        return 0.0
    return float(line.intersection(union_all([g.buffer(TOL) for g in geoms])).length)


def per_neighbor_contacts(outline, nb: gpd.GeoDataFrame):
    """outline 的邊界 ∩ 各鄰地 buffer(TOL) → [(index, 長度, 線幾何)]。"""
    bd = outline.boundary
    hits = []
    tree = STRtree(list(nb.geometry))
    for j in tree.query(outline.buffer(TOL), predicate="intersects"):
        seg = bd.intersection(nb.geometry.iloc[j].buffer(TOL))
        if seg.length > 0:
            hits.append((int(j), float(seg.length), seg))
    return hits


# ------------------------------------------------------------------ 聚合
def build_pocket(base_geoms, frame: gpd.GeoDataFrame, water_barrier: bool = True):
    """回 (口袋成員在 frame 的 index 集合)。base_geoms 為種子（不論其使類）。

    frame 須已排除基地自身。`water_barrier=True`（預設）把水利用地當隔絕要素、不聚合——
    §35 第二款、§35-1 第一、三款明列「水溝」為隔絕要素；False 僅供敏感度分析。
    """
    ok = [is_agri(r) and not (water_barrier and r["使類"] == "水利用地")
          for _, r in frame.iterrows()]
    geoms = list(frame.geometry)
    tree = STRtree(geoms)
    comp: set[int] = set()
    stack = []
    for g in base_geoms:
        for j in tree.query(g.buffer(TOL), predicate="intersects"):
            j = int(j)
            if ok[j] and j not in comp:
                comp.add(j)
                stack.append(j)
    while stack:
        i = stack.pop()
        for j in tree.query(geoms[i].buffer(TOL), predicate="intersects"):
            j = int(j)
            if ok[j] and j not in comp:
                comp.add(j)
                stack.append(j)
    return comp


def split_base(frame: gpd.GeoDataFrame, base_union):
    """框查中屬基地自身者（≥50% 落在基地內）的 index。"""
    idx = []
    for i, g in enumerate(frame.geometry):
        if g.area > 0 and g.intersection(base_union).area >= 0.5 * g.area:
            idx.append(i)
    return idx


# ------------------------------------------------------------------ 判定
def _worst(*vs):
    return min(vs, key=lambda v: _RANK[v])


def area_test(a: float, limit: float, plus10: bool) -> tuple[str, str]:
    if a <= limit:
        return OK, f"{a:,.2f} ㎡ ≤ {limit:,.0f} ㎡"
    if plus10 and a <= limit * PLUS10:
        return PEND, (f"{a:,.2f} ㎡ 超過 {limit:,.0f} ㎡ 但在 10% 增加範圍內"
                      "（須主張地形坵塊完整需要）")
    return NG, f"{a:,.2f} ㎡ > {limit:,.0f} ㎡" + ("（含 10% 增加仍超過）" if plus10 else "")


def share_test(sure: float, maybe: float) -> str:
    if sure >= SURROUND:
        return OK
    if maybe >= SURROUND:
        return PEND
    return NG


def _share(cl: dict, perim: float, cats) -> float:
    """cats 聯集的接觸比例。cl 帶 `_lines` 才能精確聯集；否則以加總上限 1 近似。"""
    lines = cl.get("_lines", {})
    if lines:
        geoms = [lines[c] for c in cats if c in lines and not lines[c].is_empty]
        if not geoms:
            return 0.0
        return min(1.0, union_all(geoms).length / perim) if perim else 0.0
    return min(1.0, sum(cl.get(c, 0.0) for c in cats) / perim) if perim else 0.0


def judge(base: dict, pocket: dict) -> dict:
    """依 §35、§35-1 逐款初判。base／pocket 為 analyze() 算好的量測值。

    單位：款1~3 以「所在連續農業用地」（農地口袋）整體認定（§35Ⅴ、§35-1Ⅳ）；
    款4（狹長平均寬度）、款5（≤0.012 公頃且鄰接無相同使類）以基地本身判斷。
    """
    P = pocket["外圍周長_m"]
    pc = pocket["_contact"]
    pa = pocket["登記面積_m2"]
    ba = base["登記面積_m2"]
    adj = base["緊鄰類別_m"]
    adj_types = base["緊鄰使類_m"]

    def sh(cats):
        return _share(pc, P, cats)

    def adjacent(*types):
        return sum(v for k, v in adj_types.items() if k in types) >= MIN_EDGE

    # ---- 款5（兩條同文）
    same = adj_types.get(base["使類"], 0.0) >= MIN_EDGE if base["使類"] else False
    a5, a5txt = area_test(ba, AREA_0012, True)
    if same:
        k5 = (NG, f"基地 {a5txt}；且鄰接相同使用地類別（{base['使類']} "
                  f"{adj_types.get(base['使類'], 0):.1f} m）")
    else:
        k5 = (a5, f"基地 {a5txt}；鄰接無相同使用地類別")

    # ---- 款4（兩條同文）
    w = base["平均寬度_m"]
    if w <= WIDTH_10:
        k4 = (PEND, f"平均寬度 {w:.2f} m ≤ 10 m；對邊是否為建築用地／道路水溝等、"
                    "變更後是否妨礙鄰近農業生產環境須人工確認")
    else:
        k4 = (NG, f"平均寬度 {w:.2f} m > 10 m")

    # ---- §35
    jia_bing = adjacent("甲種建築用地", "丙種建築用地")
    tm = adjacent("特定目的事業用地")
    if jia_bing:
        pre35 = (OK, "基地毗鄰甲種或丙種建築用地")
    elif tm:
        pre35 = (PEND, "基地未毗鄰甲／丙種建築用地，但毗鄰特定目的事業用地"
                       "（須為國民住宅、勞工住宅、政府專案計畫興建住宅者才成立）")
    else:
        yi = adjacent("乙種建築用地")
        pre35 = (NG, "基地未毗鄰甲種、丙種建築用地"
                     + ("（僅毗鄰乙種建築用地，前提不成立）" if yi else ""))

    s1 = share_test(sh(["建築用地"]), sh(["建築用地", "特定目的事業用地", "都市計畫區"]))
    a1, a1t = area_test(pa, AREA_012, True)
    lb = "（框內下限）" if pocket.get("面積為下限值") else ""
    a1t += lb
    # §35Ⅳ：包圍之「各種建築用地」須為 78.04.03 前編定者 → 資料上無從確認，符合者一律降待查
    k35_1 = (_worst(s1, a1, PEND),
             f"建築用地包圍比例 {sh(['建築用地']):.0%}；口袋 {a1t}；"
             "包圍之建築用地須為 78.04.03 前編定（§35Ⅳ）須查")

    s2 = share_test(sh(["交通用地", "水利用地", "建築用地"]),
                    sh(["交通用地", "水利用地", "建築用地", "特定目的事業用地", "未登記土地"]))
    k35_2 = (_worst(s2, a1, PEND if s2 == OK else OK),
             f"道路水溝／建築用地包圍比例 {sh(['交通用地', '水利用地', '建築用地']):.0%}；"
             f"口袋 {a1t}；道路水溝平均寬度≥4m 與 78.04.03 前編定須查")
    gap_sure = P * (1 - sh(["建築用地"]))
    gap_maybe = P * (1 - sh(["建築用地", "特定目的事業用地"]))
    g3 = OK if gap_sure <= GAP_20 else (PEND if gap_maybe <= GAP_20 else NG)
    k35_3 = (_worst(g3, a1, PEND if g3 == OK else OK),
             f"外圍非建築用地接觸 {gap_sure:.1f} m（近似缺口寬度，上限 20 m）；口袋 {a1t}")

    items35 = {"一": k35_1, "二": k35_2, "三": k35_3, "四": k4, "五": k5}
    v35 = _article(pre35, items35)

    # ---- §35-1
    vil_base = adj.get("鄉村區", 0.0)
    vil_pocket = pc.get("鄉村區", 0.0)
    if vil_base >= MIN_EDGE or vil_pocket >= MIN_EDGE:
        pre351 = (PEND, f"毗鄰鄉村區（基地 {vil_base:.1f} m、所在連續農業用地 {vil_pocket:.1f} m）；"
                        "是否屬鄉村區邊緣畸零不整、未依法禁限建、非作隔離必要，須縣市政府認定")
    else:
        pre351 = (NG, "基地與所在連續農業用地皆未毗鄰鄉村區")

    iso1 = ["鄉村區", "交通用地", "水利用地", "建築用地"]
    s1b = share_test(sh(iso1), sh(iso1 + ["特定目的事業用地", "都市計畫區", "未登記土地"]))
    base_agri = adj.get("農業用地", 0.0)
    # §35-1Ⅲ 準用 §35Ⅲ、Ⅳ：道路水溝平均寬≥4m、建築用地與道路水溝須 78.04.03 前編定 → 符合者降待查
    k351_1 = (_worst(s1b, a1, PEND),
              f"口袋外圍隔絕比例 {sh(iso1):.0%}；口袋 {a1t}；"
              f"基地本身外圍緊鄰農業用地 {base_agri:.1f} m；"
              "道路水溝平均寬≥4m、78.04.03 前編定（§35-1Ⅲ 準用 §35Ⅲ、Ⅳ）須查")
    vs = sh(["鄉村區"])
    s2b = OK if vs >= 0.70 else (PEND if vs >= 0.45 else NG)
    k351_2 = (_worst(s2b, a1, PEND if s2b == OK else OK),
              f"口袋外圍連接鄉村區比例 {vs:.0%}（「三面連接」以比例近似）；口袋 {a1t}")
    iso3 = ["鄉村區", "交通用地", "水利用地"]
    s3b = share_test(sh(iso3), sh(iso3 + ["特定目的事業用地", "未登記土地", "其他"]))
    a3, a3t = area_test(pa, AREA_05, False)
    a3t += lb
    k351_3 = (_worst(s3b, a3, PEND if s3b == OK else OK),
              f"口袋外圍鄉村區＋道路水溝比例 {sh(iso3):.0%}"
              "（機關學校軍事用地、自然界線須另認定）；口袋 " + a3t)
    items351 = {"一": k351_1, "二": k351_2, "三": k351_3, "四": k4, "五": k5}
    v351 = _article(pre351, items351)

    return {"§35": v35, "§35-1": v351}


def _article(pre, items) -> dict:
    if pre[0] == NG:
        verdict = NG
        why = "前提不成立"
    else:
        # 前提「待查」不擋：§35-1 的前提本來就要縣市政府認定，永遠不會是資料上的「符合」
        best = max((v[0] for v in items.values()), key=lambda v: _RANK[v])
        verdict = {OK: "可能符合", PEND: PEND, NG: NG}[best]
        why = {NG: "各款皆不符", PEND: "有款項須進一步查證",
               "可能符合": "至少一款符合" + ("（前提待查）" if pre[0] == PEND else "")}[verdict]
    return {"初判": verdict, "說明": why, "前提": {"判定": pre[0], "說明": pre[1]},
            "各款": {k: {"判定": v[0], "說明": v[1]} for k, v in items.items()},
            "備註": CAVEAT}


# ------------------------------------------------------------------ 主流程
def analyze(base: gpd.GeoDataFrame, frame: gpd.GeoDataFrame, envelope,
            water_barrier: bool = True) -> dict:
    """純計算（不打網路、不寫檔），測試直接呼叫。回結果 dict（含 `_` 開頭的幾何）。"""
    if base.crs is not None and base.crs.to_epsg() != 3826:
        base = base.to_crs(3826)
    bgeoms = list(base.geometry)
    bu = close_union(bgeoms)

    drop = split_base(frame, bu)
    nb = frame.drop(index=frame.index[drop]).reset_index(drop=True)

    # ---- 基地本身
    b_reg = sum(reg_area(r, g) for (_, r), g in zip(base.iterrows(), bgeoms))
    L, S = rect_dims(bu)
    width = bu.area / L if L else 0.0
    b_contacts = per_neighbor_contacts(bu, nb)
    adj_rows = []
    for j, ln, _seg in sorted(b_contacts, key=lambda t: -t[1]):
        r = nb.iloc[j]
        adj_rows.append({"地號": r["地號"], "段": r["段"], "使分": r["使分"],
                         "使類": r["使類"] or ("（都市計畫區）" if r["都計區"] == "都計" else ""),
                         "都計區": r["都計區"], "共同邊界_m": round(ln, 2),
                         "角碰": ln < MIN_EDGE})
    adj_types: dict[str, float] = {}
    for j, ln, _seg in b_contacts:
        k = nb.iloc[j]["使類"] or ("都市計畫區" if nb.iloc[j]["都計區"] == "都計" else "（空白）")
        adj_types[k] = adj_types.get(k, 0.0) + ln
    b_cat = _cat_lengths(bu, nb, [j for j, _, _ in b_contacts])
    base_types = [t for t in base["使類"] if _s(t)] if "使類" in base.columns else []
    base_res = {
        "地號": [f"{_s(r.get('段'))}{_s(r.get('地號'))}" for _, r in base.iterrows()],
        "筆數": len(base),
        "使類": base_types[0] if len(set(base_types)) == 1 else "／".join(sorted(set(base_types))),
        "登記面積_m2": round(b_reg, 2),
        "圖面積_m2": round(bu.area, 2),
        "外接矩形長邊_m": round(L, 2),
        "外接矩形短邊_m": round(S, 2),
        "平均寬度_m": round(width, 2),
        "_平均寬度口徑": "圖面積／最小外接矩形長邊（同一幾何口徑）",
        "周長_m": round(bu.boundary.length, 2),
        "緊鄰宗地": adj_rows,
        "緊鄰使類_m": {k: round(v, 2) for k, v in sorted(adj_types.items(), key=lambda t: -t[1])},
        "緊鄰類別_m": {k: round(v, 2) for k, v in b_cat.items() if not k.startswith("_")},
        "無鄰地接觸_m": round(b_cat["_無宗地"], 2),
    }

    # ---- 農地口袋
    comp = build_pocket(bgeoms, nb, water_barrier)
    members = nb.iloc[sorted(comp)]
    pgeoms = bgeoms + list(members.geometry)
    pu = close_union(pgeoms)
    p_reg = b_reg + sum(reg_area(r, g) for (_, r), g in zip(members.iterrows(), members.geometry))
    outside = nb.drop(index=nb.index[sorted(comp)])
    p_hits = per_neighbor_contacts(pu, outside)
    p_cat = _cat_lengths(pu, outside, [j for j, _, _ in p_hits])
    inner = envelope.buffer(-TOL)
    touches_edge = any(not inner.contains(g) for g in pgeoms)
    pocket_res = {
        "筆數": len(pgeoms),
        "其中基地筆數": len(bgeoms),
        "登記面積_m2": round(p_reg, 2),
        "圖面積_m2": round(pu.area, 2),
        "外圍周長_m": round(pu.boundary.length, 2),
        "外圍接觸_m": {k: round(v, 2) for k, v in p_cat.items() if not k.startswith("_")},
        "外圍無鄰地_m": round(p_cat["_無宗地"], 2),
        "觸及查詢框邊": touches_edge,
        "面積為下限值": touches_edge,
        "水利用地視為隔絕": water_barrier,
        "_contact": p_cat,
    }
    warns = []
    if touches_edge:
        if p_reg > AREA_05:
            warns.append("所在連續農業用地被查詢框切斷，筆數與面積為框內下限值；下限已超過 0.5 公頃，"
                         "面積相關款次「不符」的結論不受影響。交付時請寫「至少 N 公頃」，"
                         "需要實際範圍再加大 --buffer 重跑")
        else:
            warns.append("所在連續農業用地被查詢框切斷，面積為框內下限值且未超過 0.5 公頃，"
                         "結論可能翻轉——請加大 --buffer／--max-buffer 重跑")
    if base_res["無鄰地接觸_m"] >= MIN_EDGE:
        warns.append(f"基地邊界有 {base_res['無鄰地接觸_m']:.1f} m 未接觸任何框查宗地"
                     "（髮絲縫以外的缺口，可能為未登錄地或資料缺漏），請目視確認")

    res = {"基地": base_res, "所在連續農業用地": pocket_res, "警告": warns}
    res["判定"] = judge(base_res, pocket_res)
    # 給寫檔用的幾何
    res["_geom"] = {"base": bu, "pocket": pu, "members": members, "base_hits": b_contacts,
                    "pocket_hits": p_hits, "nb": nb, "outside": outside,
                    "base_gdf": base, "envelope": envelope}
    return res


def _cat_lengths(outline, nb: gpd.GeoDataFrame, idx: list[int]) -> dict:
    """outline 邊界與各類別鄰地的接觸長度（類別可重疊）＋無宗地長度＋各類線幾何。"""
    bd = outline.boundary
    by: dict[str, list] = {c: [] for c in CAT_ORDER}
    for j in idx:
        r = nb.iloc[j]
        for c in categories(r):
            by[c].append(nb.geometry.iloc[j])
    out: dict = {}
    lines = {}
    for c in CAT_ORDER:
        if by[c]:
            seg = bd.intersection(union_all([g.buffer(TOL) for g in by[c]]))
            lines[c] = seg
            out[c] = float(seg.length)
        else:
            out[c] = 0.0
    alln = [nb.geometry.iloc[j] for j in idx]
    out["_無宗地"] = float(bd.length - contact_len(bd, alln)) if alln else float(bd.length)
    out["_lines"] = lines
    return out


# ------------------------------------------------------------------ 輸出
def _strip(o):
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, list):
        return [_strip(v) for v in o]
    return o


def to_text(res: dict, meta: dict) -> str:
    b, p, j = res["基地"], res["所在連續農業用地"], res["判定"]
    w = io.StringIO()
    pr = lambda *a: print(*a, file=w)  # noqa: E731
    pr(f"零星土地檢核（{LAW} §35、§35-1）— {meta['案件']}")
    pr(f"※ 全部判定為「{CAVEAT}」。")
    pr(f"地籍版次 {meta.get('地籍版次') or '—'}；框查外擴 {meta['外擴_m']} m；查詢日期 {meta['查詢日期']}")
    pr("")
    pr("■ 基地")
    pr(f"  地號：{'、'.join(b['地號'])}（{b['筆數']} 筆，使類 {b['使類'] or '—'}）")
    pr(f"  登記面積 {b['登記面積_m2']:,.2f} ㎡（{b['登記面積_m2'] / 1e4:.4f} 公頃）；圖面積 {b['圖面積_m2']:,.2f} ㎡")
    pr(f"  最小外接矩形 {b['外接矩形長邊_m']:.2f} × {b['外接矩形短邊_m']:.2f} m；"
       f"平均寬度 {b['平均寬度_m']:.2f} m（圖面積／長邊）")
    pr("  緊鄰宗地（共同邊界＝基地邊界 ∩ 鄰地外擴 0.3 m）：")
    for r in b["緊鄰宗地"]:
        tag = "（角碰）" if r["角碰"] else ""
        pr(f"    {r['段']}{r['地號']:<8} {r['使分'] or '—'}／{r['使類'] or '—'}　{r['共同邊界_m']:.1f} m{tag}")
    pr("  緊鄰類別長度（可重疊）：" + "、".join(
        f"{k} {v:.1f} m" for k, v in b["緊鄰類別_m"].items() if v > 0))
    pr("")
    pr("■ 所在連續農業用地（非鄉村區之六類農業用地，0.3 m 相接聚合）")
    ge = "≥ " if p["面積為下限值"] else ""
    pr(f"  {ge}{p['筆數']} 筆（含基地 {p['其中基地筆數']} 筆），登記合計 {ge}{p['登記面積_m2']:,.2f} ㎡"
       f"（{ge}{p['登記面積_m2'] / 1e4:.2f} 公頃）；外圍周長 {p['外圍周長_m']:,.1f} m"
       + ("（被查詢框切斷，為框內下限）" if ge else ""))
    pr("  外圍接觸（可重疊）：" + "、".join(
        f"{k} {v:.1f} m" for k, v in p["外圍接觸_m"].items() if v > 0)
       + (f"、無鄰地 {p['外圍無鄰地_m']:.1f} m" if p["外圍無鄰地_m"] >= MIN_EDGE else ""))
    pr(f"  觸及查詢框邊：{'是' if p['觸及查詢框邊'] else '否'}"
       + ("" if p["水利用地視為隔絕"] else "；水利用地併入口袋（敏感度分析，非預設）"))
    for k in ("§35", "§35-1"):
        a = j[k]
        pr("")
        pr(f"■ {k}　初判：{a['初判']}（{a['說明']}）")
        pr(f"  前提［{a['前提']['判定']}］{a['前提']['說明']}")
        for n, v in a["各款"].items():
            pr(f"  第{n}款［{v['判定']}］{v['說明']}")
    if res["警告"]:
        pr("")
        pr("■ 警告")
        for x in res["警告"]:
            pr(f"  ⚠ {x}")
    pr("")
    pr("■ 待查事項（資料上無從判定）")
    pr("  道路水溝平均寬度≥4m 及是否 78.04.03 前編定；特目用地是否作建築使用；都市計畫分區別"
       "（農業區不是隔絕要素）；未登記土地是否實際作道路水溝；是否禁限建、是否作隔離必要。")
    return w.getvalue()


def write_gpkg(res: dict, path: str):
    g = res["_geom"]
    crs = "EPSG:3826"
    if os.path.exists(path):
        os.remove(path)
    nb = g["nb"]
    base = g["base_gdf"]
    rows = []
    for _, r in base.iterrows():
        rows.append({"地號": _s(r.get("地號")), "段": _s(r.get("段")), "使分": _s(r.get("使分")),
                     "使類": _s(r.get("使類")), "登面積": reg_area(r, r.geometry),
                     "圖面積": round(r.geometry.area, 2), "是否基地": "是", "geometry": r.geometry})
    for _, r in g["members"].iterrows():
        rows.append({"地號": r["地號"], "段": r["段"], "使分": r["使分"], "使類": r["使類"],
                     "登面積": reg_area(r, r.geometry), "圖面積": round(r.geometry.area, 2),
                     "是否基地": "", "geometry": r.geometry})
    gpd.GeoDataFrame(rows, crs=crs).to_file(path, layer="農地口袋", driver="GPKG", engine="pyogrio")
    p = res["所在連續農業用地"]
    gpd.GeoDataFrame([{"筆數": p["筆數"], "登記面積": p["登記面積_m2"], "圖面積": p["圖面積_m2"],
                       "geometry": g["pocket"]}], crs=crs).to_file(
        path, layer="農地口袋_範圍", driver="GPKG", engine="pyogrio")

    def lines(hits, src, layer):
        rr = []
        for j, ln, seg in hits:
            r = src.iloc[j]
            rr.append({"地號": r["地號"], "段": r["段"], "使分": r["使分"], "使類": r["使類"],
                       "都計區": r["都計區"], "類別": "、".join(categories(r)),
                       "長度_m": round(ln, 2), "geometry": seg})
        if rr:
            gpd.GeoDataFrame(rr, crs=crs).to_file(path, layer=layer, driver="GPKG", engine="pyogrio")

    lines(g["pocket_hits"], g["outside"], "口袋外圍接觸")
    lines(g["base_hits"], nb, "基地緊鄰")
    gpd.GeoDataFrame([{"說明": "框查範圍", "geometry": g["envelope"]}], crs=crs).to_file(
        path, layer="查詢框", driver="GPKG", engine="pyogrio")


def fetch_frame(env, version=None):
    import moa_parcel_query as mpq  # noqa: PLC0415
    x1, y1, x2, y2 = env.bounds
    recs = mpq.query_envelope(x1, y1, x2, y2, version, progress=print)
    gdf = mpq.to_gdf(recs, version)
    ver = recs[0].get("_version") if recs else (version or "")
    return mpq.drop_internal(gdf), ver


def main(argv=None):
    ap = argparse.ArgumentParser(description="零星土地檢核（管制規則 §35、§35-1）＋農地口袋聚合")
    ap.add_argument("--case", required=True, help="案件資料夾（讀 cadastre/基地.shp）")
    ap.add_argument("--base", help="基地 shp（預設 <case>/cadastre/基地.shp）")
    ap.add_argument("--buffer", type=float, default=250.0, help="框查外擴（m，預設 250）")
    ap.add_argument("--max-buffer", type=float, default=1000.0,
                    help="口袋觸框時自動加倍外擴的上限（m，預設 1000）")
    ap.add_argument("--frame", help="重用既有框查成果（gpkg/shp），不連線、不自動外擴")
    ap.add_argument("--version", help="地籍版次（省略＝最新）")
    ap.add_argument("--water-as-agri", action="store_true",
                    help="敏感度分析：水利用地併入口袋（預設把水利用地當水溝隔絕要素）")
    ap.add_argument("--water-barrier", action="store_true",
                    help="（相容舊旗標，已是預設值）")
    ap.add_argument("--out-dir", help="輸出資料夾（預設＝--case）")
    a = ap.parse_args(argv)

    case = os.path.abspath(a.case)
    out = os.path.abspath(a.out_dir or case)
    base_p = a.base or os.path.join(case, "cadastre", "基地.shp")
    base = gpd.read_file(base_p, engine="pyogrio")
    if base.crs is None:
        base = base.set_crs(3826)
    elif base.crs.to_epsg() != 3826:
        base = base.to_crs(3826)
    x1, y1, x2, y2 = base.total_bounds
    env = box(x1 - a.buffer, y1 - a.buffer, x2 + a.buffer, y2 + a.buffer)

    os.makedirs(os.path.join(out, "cadastre"), exist_ok=True)
    buf = a.buffer
    if a.frame:
        raw = gpd.read_file(a.frame, engine="pyogrio")
        ver = _s(raw["_version"].iloc[0]) if "_version" in raw.columns and len(raw) else \
            (_s(raw["版次"].iloc[0]) if "版次" in raw.columns and len(raw) else "")
        # 觸框判定用的框＝基地外擴 --buffer；重用快取時請給與當初相同的 --buffer
        src = f"重用 {a.frame}"
        frame = normalize_frame(raw)
        print(f"  框查宗地 {len(frame)} 筆（{src}）")
        res = analyze(base, frame, env, not a.water_as_agri)
    else:
        # ★ 口袋觸框且面積下限仍可能低於最大門檻（0.5 公頃）時自動加倍外擴重查——
        #   彌陀案教訓：250m 框內的「49 筆 4.38 公頃」其實被框切斷，外擴 800m 時已達 83 公頃。
        #   下限已超過 0.5 公頃者結論不會翻轉，不必再擴（框越大筆數爆量越快）。
        while True:
            env = box(x1 - buf, y1 - buf, x2 + buf, y2 + buf)
            print(f"框查：基地外擴 {buf:.0f} m …")
            raw, ver = fetch_frame(env, a.version)
            cache = os.path.join(out, "cadastre", f"周邊{buf:.0f}m_框查.gpkg")
            raw.to_file(cache, driver="GPKG", engine="pyogrio")
            src = f"農業部地籍框查（快取 {cache}）"
            frame = normalize_frame(raw)
            print(f"  框查宗地 {len(frame)} 筆")
            res = analyze(base, frame, env, not a.water_as_agri)
            p = res["所在連續農業用地"]
            if not (p["觸及查詢框邊"] and p["登記面積_m2"] <= AREA_05
                    and buf < a.max_buffer):
                break
            print(f"  口袋觸框且下限 {p['登記面積_m2']:,.0f} ㎡ ≤ 0.5 公頃，加大外擴重查")
            buf = min(buf * 2, a.max_buffer)

    meta = {"案件": os.path.basename(case), "法源": LAW, "判定性質": CAVEAT,
            "地籍版次": ver, "外擴_m": buf, "框查來源": src,
            "查詢日期": date.today().isoformat(), "相接容差_m": TOL,
            "毗鄰最小共同邊界_m": MIN_EDGE, "包圍比例門檻": SURROUND,
            "聚合判準": "衍古 _判準登記表 B 表「零星土地整體認定的聚合法」",
            "六類農業用地": list(AG_KINDS)}
    gp = os.path.join(out, "cadastre", "農地口袋.gpkg")
    write_gpkg(res, gp)
    payload = {"meta": meta, **_strip(res)}
    with open(os.path.join(out, "零星土地檢核.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    txt = to_text(res, meta)
    with open(os.path.join(out, "零星土地檢核.txt"), "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt)
    print(f"→ {os.path.join(out, '零星土地檢核.json')}／.txt、{gp}")
    return payload


if __name__ == "__main__":
    main()
