# -*- coding: utf-8 -*-
"""隔離綠帶或設施：最小寬度＋面積比雙門檻，並可指定「加寬側」

★ 建立緣由（2026-07-30 安定六塊寮段特登工廠案）：
  skill `buffer-greenbelt` 處理的是**等寬**綠帶（dissolve → buffer(−W) → difference）。
  但法規常是**雙門檻**：
    ・特定工廠變更編定審查辦法§6①(一)：未達2公頃者「寬度至少1.5公尺**且**面積不少於
      申請面積30%」
    ・農業用地變更審查作業要點§10：特定農業區變更作其他使用者，隔離綠帶面積不少於
      申請事業面積30%（§9 另定最小寬度1.5公尺）
  等寬帶要達 30% 往往需要遠大於 1.5m 的寬度、且四面均等擴張未必合理——實務上會
  **維持最小寬度、在特定側（如緊鄰灌排渠道、緊鄰農地一側）加寬**，因為要點§9 明定
  「配置區位應與毗鄰農業用地相緊臨」。本引擎即此作法。

演算法（沿用 buffer-greenbelt 的幾何衛生：make_valid → union → 內縮 → 差集）：
  1. 基礎帶 = 基地.difference(基地.buffer(-最小寬度))         ← 全周界最小寬度
  2. 加寬帶 = 基地 ∩ 加寬側鄰地union.buffer(w)                ← 只從指定側往內長
  3. 二分搜尋 w，使 面積(基礎帶 ∪ 加寬帶) ≥ 面積比 × 基地面積
  4. 加寬側單獨不足時，改為全周界等寬加寬（會明確印出）

用法（系統 Python）：
  python scripts/greenbelt_ratio.py --joined <面積join.shp> \
      [--min-width 1.5] [--ratio 0.30] [--widen-on 水利用地] [--county 臺南市] \
      [--out <案件資料夾>]

輸出：<案>/隔離綠帶/隔離綠帶或設施.shp、可配置範圍.shp、綠帶計算.json/.txt
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

import geopandas as gpd
from shapely import make_valid, union_all
from shapely.geometry import Polygon

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from adjacent_landuse import _county_file  # noqa: E402 — 共用地籍檔解析

# ★★ 農業用地（非都市土地）＝農業主管機關同意農業用地變更使用審查作業要點
#    第二點第二款列舉之使用地類別。**交通用地（農路）不在其列**。
#    法源意義：辦法§6①「申請範圍土地與**相鄰農業用地**應規劃隔離綠帶或設施」、
#    要點§9「配置區位應與**毗鄰農業用地**相緊臨」→ 綠帶只在臨農業用地側留設，
#    臨道路、未登錄地等非農業用地側不必留（2026-07-30 使用者指正，安定六塊寮段案）。
AG_KINDS = ("農牧用地", "養殖用地", "水利用地", "林業用地", "生態保護用地", "國土保安用地")


def parts_of(geom):
    if geom.is_empty:
        return []
    return list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]


def min_interior_angle(geom, exclude=None, tol=2.0):
    """回傳多邊形最小內角（度），用來排除實務上不會留設的銳角尖端。

    ⚠ `exclude` 必須傳**非應留設側鄰地**：綠帶＝基地−可配置範圍，在與非應留設側
      銜接的**收尾端**必然收斂成 0 度針尖，那是差集的拓樸產物、不是設計上的銳角
      （2026-08-01 實測：不排除收尾端時，全部候選的最小內角都是 0.0°，
      角度檢查等於把所有解都否決）。只測帶身中間的真銳角。"""
    import math
    from shapely.geometry import Point
    best = 180.0
    for poly in parts_of(geom):
        cs = list(poly.exterior.coords)[:-1]
        n = len(cs)
        for i in range(n):
            a, b, c = cs[(i - 1) % n], cs[i], cs[(i + 1) % n]
            if exclude is not None and Point(b).distance(exclude) < tol:
                continue                          # 收尾端，不計
            v1 = (a[0] - b[0], a[1] - b[1]); v2 = (c[0] - b[0], c[1] - b[1])
            l1 = math.hypot(*v1); l2 = math.hypot(*v2)
            if l1 < 1e-9 or l2 < 1e-9:
                continue
            cosv = (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)
            best = min(best, math.degrees(math.acos(max(-1.0, min(1.0, cosv)))))
    return best


def _interior_angles(poly):
    """各頂點內角（度，0~180 之夾角，不分凹凸），順序同 exterior.coords[:-1]。"""
    import math
    cs = list(poly.exterior.coords)[:-1]
    n = len(cs)
    out = []
    for i in range(n):
        a, b, c = cs[(i - 1) % n], cs[i], cs[(i + 1) % n]
        v1 = (a[0] - b[0], a[1] - b[1]); v2 = (c[0] - b[0], c[1] - b[1])
        l1 = (v1[0] ** 2 + v1[1] ** 2) ** 0.5; l2 = (v2[0] ** 2 + v2[1] ** 2) ** 0.5
        if l1 < 1e-9 or l2 < 1e-9:
            out.append(180.0); continue
        cosv = (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)
        out.append(math.degrees(math.acos(max(-1.0, min(1.0, cosv)))))
    return out


def drop_collinear(poly, tol=0.3):
    """剔除「距前後兩頂點連線 < tol」的近共線頂點。

    simplify(0.3)（TopologyPreservingSimplifier）對這種頂點過於保守、常常留著，
    而它會讓三角化切出「高 0.26、底 32」的細片三角形（2026-08-01 實踩）。
    逐點明確判斷最可靠。"""
    from shapely.geometry import LineString, Point
    cs = list(poly.exterior.coords)[:-1]
    changed = True
    while changed and len(cs) > 3:
        changed = False
        for i in range(len(cs)):
            a, b, c = cs[(i - 1) % len(cs)], cs[i], cs[(i + 1) % len(cs)]
            if LineString([a, c]).distance(Point(b)) < tol:
                cs.pop(i)
                changed = True
                break
    p2 = make_valid(Polygon(cs))
    ps = [x for x in parts_of(p2) if x.area > 0]
    return max(ps, key=lambda x: x.area) if ps else poly


def chop_needles(poly, min_ang=25.0, max_span=6, max_cut_frac=0.03):
    """把**可配置範圍**的銳角針尖以直線弦截平，切下的面積自然歸還綠帶。

    ★ 為什麼作用在可配置範圍而不是綠帶（2026-08-01 布袋案東端尖尾實例）：
      地籍本身有 ~15° 尖角時，退縮後可配置範圍在尖角處殘留同角度的針尖——
      針尖狀的可建地實務上無用，而其鏡像就是綠帶的銳角缺口。從可配置範圍切，
      只會**加大**綠帶與局部寬度，不會像修剪綠帶那樣破壞寬度／面積閘門。
    弦的搜尋同 _split_road.trim_spikes：在既有頂點間連直線、取切除面積最小且
    不產生新銳角者；只切尖端一點會讓相鄰頂點變得更尖，必須跨越整個尖端。"""
    for _ in range(6):
        cs = list(poly.exterior.coords)[:-1]
        n = len(cs)
        angs = _interior_angles(poly)
        bad = [i for i, t in enumerate(angs) if t < min_ang]
        if not bad or n < 4:
            return poly
        k = bad[0]
        key = lambda p: (round(p[0], 3), round(p[1], 3))     # noqa: E731
        old_bad = {key(p) for p, t in zip(cs, angs) if t < min_ang}
        best = None
        # 首選：**尖端截平**——在針尖兩臂距尖端 s 公尺處插點、直線相連。
        # 兩臂的既有頂點往往離尖端很遠（本案 10~20 m），只用既有頂點連弦會
        # 切掉數十平方公尺；就近插點只損失 s²·tan(θ/2) 級（零點幾平方公尺），
        # 且截後兩個新角約 (180°−θ)/2 ≈ 80°，不會產生新銳角。
        a_, b_, c_ = cs[(k - 1) % n], cs[k], cs[(k + 1) % n]
        l1 = ((a_[0] - b_[0]) ** 2 + (a_[1] - b_[1]) ** 2) ** 0.5
        l2 = ((c_[0] - b_[0]) ** 2 + (c_[1] - b_[1]) ** 2) ** 0.5
        for s in (1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 12.0):
            if s > l1 * 0.9 or s > l2 * 0.9:
                break
            pp = (b_[0] + (a_[0] - b_[0]) / l1 * s, b_[1] + (a_[1] - b_[1]) / l1 * s)
            pn = (b_[0] + (c_[0] - b_[0]) / l2 * s, b_[1] + (c_[1] - b_[1]) / l2 * s)
            # 弦太短沒有意義：15° 針尖 s=1 只截出 0.26 m 的邊，三角化照樣切出
            # 「高 0.26」細片（2026-08-01 實踩）。弦至少 2 m 才算把尖端截平。
            if ((pp[0] - pn[0]) ** 2 + (pp[1] - pn[1]) ** 2) ** 0.5 < 2.0:
                continue
            cand = make_valid(Polygon(cs[:k] + [pp, pn] + cs[k + 1:]))
            if cand.geom_type != "Polygon" or cand.is_empty:
                continue
            lost = poly.area - cand.area
            if lost < -1e-6 or lost > poly.area * max_cut_frac:
                continue
            new_bad = {key(p) for p, t in
                       zip(list(cand.exterior.coords)[:-1], _interior_angles(cand))
                       if t < min_ang}
            if not new_bad <= (old_bad - {key(b_)}):
                continue
            best = (lost, cand)
            break
        if best is None:                         # 後備：既有頂點間連弦
            for span in range(2, max_span + 1):
                for off in range(1, span):
                    idx = [((k - off) + t) % n for t in range(span, n + 1)]
                    if len(idx) < 3:
                        continue
                    cand = make_valid(Polygon([cs[t] for t in idx]))
                    if cand.geom_type != "Polygon" or cand.is_empty:
                        continue
                    lost = poly.area - cand.area
                    if lost < -1e-6 or lost > poly.area * max_cut_frac:
                        continue
                    c_cs = list(cand.exterior.coords)[:-1]
                    new_bad = {key(p) for p, t in zip(c_cs, _interior_angles(cand))
                               if t < min_ang}
                    if key(cs[k]) in new_bad or not new_bad <= (old_bad - {key(cs[k])}):
                        continue
                    if best is None or lost < best[0]:
                        best = (lost, cand)
        if best is None:
            return poly                          # 切不掉就交給角度閘門判死
        poly = best[1]
    return poly


# ⚠ 不要事後「修掉」**綠帶**的銳角（2026-08-01 實踩）：
#   ・開運算 buffer(-r).buffer(r) 會把整條帶子往內縮 → 末端寬度不足；
#     用 round 接合還會把頂點從 28 炸到 196（圓弧）。
#   ・純截角看似只動尖端，但針尖往往正是綠帶與基地邊界的夾點，移除後
#     可配置範圍會從該處擴入，寬度檢核由「真不足 0 點」變成「真不足 11 點」。
#   → 銳角必須在**搜尋階段就排除**（見 minimal 分支的 min_interior_angle 檢查），
#     讓選到的幾何本身就沒有銳角，而不是事後補救。


def split_by_side(green, site, inner, near, tol=0.5, ext=1.0):
    """把綠帶依「外緣是否臨 near」切成（隔離設施, 隔離綠帶），分界為**直線**。

    ⚠ 不可用 `green.intersection(near.buffer(r))` 分類（2026-08-01 使用者指正）：
      buffer 的邊界是圓弧，而且在轉角處會切出與綠帶**並排**的細長條，
      看起來像兩條帶子並行——實務上隔離設施與隔離綠帶各自是完整一段。
    作法：沿基地外緣找「臨路／非臨路」的轉換點，由該點向可配置範圍邊界拉**最短直線**
      （端點對接）切開綠帶，再依各片外緣的臨路長度歸類。
    """
    from shapely.geometry import LineString, Point
    from shapely.ops import nearest_points, split as _split

    def is_road(mid):
        return near.distance(Point(mid)) < tol

    cuts = []
    for poly in parts_of(site):
        cs = list(poly.exterior.coords)
        segs = [(cs[i], cs[i + 1]) for i in range(len(cs) - 1)]
        flags = [is_road(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)) for p, q in segs]
        for i in range(len(segs)):
            if flags[i] != flags[i - 1]:                 # 轉換點＝第 i 段的起點
                P = segs[i][0]
                Q = nearest_points(Point(P), inner)[1]
                dx, dy = Q.x - P[0], Q.y - P[1]
                L = (dx * dx + dy * dy) ** 0.5
                if L < 1e-6:
                    continue
                ux, uy = dx / L, dy / L
                cuts.append(LineString([(P[0] - ux * ext, P[1] - uy * ext),
                                        (Q.x + ux * ext, Q.y + uy * ext)]))
    # ⚠ 不可逐條 `shapely.ops.split`：綠帶通常是**環狀**（整圈都要留設），一條由
    #   外緣拉到內緣的線並不能把環切開（要兩條才成兩片），split 會原樣退回，
    #   結果整條帶子都被歸成同一類（2026-08-01 實踩：隔離設施算出 0.00 ㎡）。
    #   改為把邊界與所有切線一起 union 後 polygonize，一次成形。
    from shapely.ops import polygonize
    if cuts:
        merged = union_all([green.boundary] + cuts)
        pieces = [p for p in polygonize(merged)
                  if p.area > 1e-6 and p.representative_point().within(green)]
    else:
        pieces = parts_of(green)
    fac_p, belt_p = [], []
    for p in pieces:
        cs = list(p.exterior.coords)
        rl = ol = 0.0
        for i in range(len(cs) - 1):
            m = ((cs[i][0] + cs[i + 1][0]) / 2, (cs[i][1] + cs[i + 1][1]) / 2)
            if site.exterior.distance(Point(m)) > 0.01:   # 非外緣（內緣或切線）
                continue
            seg = ((cs[i + 1][0] - cs[i][0]) ** 2 + (cs[i + 1][1] - cs[i][1]) ** 2) ** 0.5
            if is_road(m):
                rl += seg
            else:
                ol += seg
        (fac_p if rl > ol else belt_p).append(p)
    from shapely.geometry import Polygon as _Poly
    empty = _Poly()
    return (make_valid(union_all(fac_p)) if fac_p else empty,
            make_valid(union_all(belt_p)) if belt_p else empty)


def solve_widen(su, base_ring, side_geom, target, lo=0.0, hi=None, tol=0.05):
    """二分搜尋加寬距離 w，使 基礎帶∪(基地∩側域buffer(w)) 面積達 target。"""
    if side_geom is None or side_geom.is_empty:
        return None, base_ring
    if hi is None:
        b = su.bounds
        hi = max(b[2] - b[0], b[3] - b[1])          # 基地最大跨距即上限
    def green_at(w):
        if w <= 0:
            return base_ring
        return make_valid(base_ring.union(su.intersection(side_geom.buffer(w))))
    if green_at(hi).area < target - 1e-6:
        return None, green_at(hi)                    # 單側加到底仍不足
    for _ in range(60):
        mid = (lo + hi) / 2
        if green_at(mid).area < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol / 100:
            break
    return hi, green_at(hi)


def solve_uniform(su, target, lo, hi=None, tol=0.001):
    """全周界等寬：搜尋 W 使 基地.difference(基地.buffer(-W)) 面積達 target。"""
    if hi is None:
        b = su.bounds
        hi = max(b[2] - b[0], b[3] - b[1]) / 2
    def green_at(w):
        return make_valid(su.difference(su.buffer(-w)))
    for _ in range(60):
        mid = (lo + hi) / 2
        if green_at(mid).area < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return hi, green_at(hi)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True)
    ap.add_argument("--min-width", type=float, default=1.5, help="法定最小寬度(m)，預設1.5")
    ap.add_argument("--ratio", type=float, default=0.30, help="法定面積比，預設0.30")
    ap.add_argument("--widen-on", nargs="*", default=["水利用地"],
                    help="加寬側之鄰地使用地類別（可多個）；給空值則全周界等寬加寬")
    ap.add_argument("--side-width", action="append", default=[], metavar="使用地類別=寬度",
                    help="指定某側之最小寬度，可多次。例：--side-width 農牧用地=3"
                         "（防火間隔需求：臨農業用地側先給足 3 m，不足額再由 --widen-on 側補）")
    ap.add_argument("--only-on", nargs="*", default=[],
                    help="只在鄰接這些使用地類別的側邊留設綠帶（不給＝全周界）。"
                         "傳入「農業用地」自動展開為要點§2②列舉之六類；"
                         "★辦法§6①僅要求與『相鄰農業用地』留設，臨道路／未登錄地側不必留")
    ap.add_argument("--county", default=None)
    ap.add_argument("--neighbours", default=None, metavar="鄰地.shp",
                    help="權威鄰地層（平台官方 shp 接回屬性後之檔案，須含 使類／使用地類別 欄）。"
                         "★本機 地籍圖資 是固定舊快照（Ver 111年2月）：涉及近年分割或編定變更的"
                         "案子，快照會看不到新宗地，導致(a)應留設側判錯 (b)交界收尾被誤判成"
                         "「真不足」。有官方鄰地資料時一律用本旗標。")
    ap.add_argument("--min-angle", type=float, default=25.0,
                    help="（--shape minimal 用）綠帶允許的最小內角(度)，預設 25。"
                         "實務上不會留下銳角尖端，故在搜尋階段即排除；"
                         "事後修補會破壞寬度合規（詳 clean_spikes 上方註解）。")
    ap.add_argument("--facility-near", default=None, metavar="臨路側.shp",
                    help="臨此範圍（通常為現況道路／保留之公路用地）之綠帶另歸類為"
                         "**隔離設施**，其餘為**隔離綠帶**（使用者 2026-08-01 定案："
                         "臨路側做隔離設施、其他地方做隔離綠帶）。兩者均計入面積比"
                         "（隔離設施定義依**非都市土地使用管制規則§11**——通路、水路、"
                         "空地、廣場、平面停車場、蓄水池及滯洪池等非建築之開放性設施；"
                         "農業用地變更審查作業要點§11 為引用該定義之描述性文字，非法定"
                         "定義本身）。⚠ 本旗標只用於**隔離綠帶**（<2公頃變更編定）情境，"
                         "**開發許可（≥2公頃）走緩衝綠帶時不套用**——評估階段一律全部畫"
                         "緩衝綠帶，見 .claude/rules/隔離綠帶與緩衝綠帶判準.md。")
    ap.add_argument("--ratio-max", type=float, default=None,
                    help="（--shape minimal 用）綠帶面積比上限，預設 --ratio + 0.03。"
                         "★沒有上限時「最少頂點」會把基地一路退縮到形狀最單純為止"
                         "（實測會退到 27 m、綠帶 76.79%%），因為退得越多頂點越少。"
                         "面積比是**約束**不是最大化目標，故必須設上限。")
    ap.add_argument("--shape", choices=["offset", "minimal"], default="offset",
                    help="綠帶構法。offset（預設，既有行為）＝最小寬度基礎帶＋指定側加寬，"
                         "用 round 接合故有圓弧角、頂點多。"
                         "minimal＝**最少頂點構法**（2026-08-01 使用者定案三原則）："
                         "①非綠帶區域三角形數最少 ②綠帶轉折點最少且無圓弧角（mitre 尖角）"
                         "③綠帶面積嚴格大於 --ratio。須搭配 --only-on。")
    ap.add_argument("--tag", default=None, help="輸出檔名後綴（做方案比較用，如 方案二）")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    site = gpd.read_file(a.joined, engine="pyogrio")
    if site.crs and site.crs.to_epsg() != 3826:
        site = site.to_crs(3826)
    su = make_valid(union_all([make_valid(x) for x in site.geometry]))
    A = su.area
    target = a.ratio * A
    log = [f"基地 {A:,.2f} m²（{A/10000:.4f} 公頃）　"
           f"法定：最小寬度 {a.min_width} m、面積比 {a.ratio*100:.0f}%"
           f" → 應留設 ≥ {target:,.2f} m²"]

    # 側別最小寬度（如防火間隔：臨農業用地側至少 3 m）
    sw = {}
    for spec in a.side_width:
        k, _, v = spec.partition("=")
        if k.strip() and v.strip():
            sw[k.strip()] = float(v)
    kinds = [k for k in (a.widen_on or []) if k]

    # --only-on：只在鄰接指定類別（通常＝農業用地）的側邊留設；「農業用地」自動展開
    only = []
    for k in (a.only_on or []):
        only.extend(AG_KINDS if k in ("農業用地", "農業用地全部") else [k])
    only = list(dict.fromkeys(only))

    # 緊鄰宗地（限定留設側、側別加寬、補足加寬側都要用），只讀一次
    adj = kc = None
    if sw or kinds or only:
        sur = None
        if a.neighbours:                      # ★權威鄰地層優先（見 --neighbours 說明）
            sur = gpd.read_file(a.neighbours, engine="pyogrio")
            if sur.crs is not None and site.crs is not None and sur.crs != site.crs:
                sur = sur.to_crs(site.crs)
            log.append(f"鄰地來源＝**權威鄰地層** {a.neighbours}（{len(sur)} 筆），"
                       f"未使用本機地籍快照")
        else:
            county = a.county
            if not county:
                for c in ("縣市名稱", "縣市"):
                    if c in site.columns:
                        county = str(site.iloc[0][c] or "").strip()
                        break
            cad = _county_file(county or "")
            if not cad:
                log.append(f"[warn] 找不到 地籍圖資\\{county}.shp，無法定位側別 → 改全周界等寬")
            else:
                sur = gpd.read_file(cad, engine="pyogrio", bbox=su.buffer(30).bounds)
                log.append(f"鄰地來源＝本機地籍快照 {cad}"
                           f"　⚠ 快照未反映近年分割／編定變更時，側別判定與交界收尾"
                           f"分類會失準，請改以 --neighbours 指定平台官方鄰地層")
        if sur is not None:
            sur = sur[~sur.geometry.intersects(su.buffer(-0.5))]
            for _c in ("使類", "使用地類別"):        # 官方匯出用「使用地類別」，本機地籍用「使類」
                if _c in sur.columns:
                    kc = _c
                    break
            adj = sur[sur.geometry.distance(su) <= 1.0]

    def side_union(names):
        if adj is None or kc is None:
            return None, 0
        pick = adj[adj[kc].isin(names)]
        if len(pick) == 0:
            return None, 0
        return make_valid(union_all([make_valid(x) for x in pick.geometry])), len(pick)

    duty_geom = nonduty_geom = None   # 應留設側／非應留設側之鄰地聯集
    if only:
        if adj is not None and kc is not None:
            rest = adj[~adj[kc].isin(only)]
            if len(rest):
                nonduty_geom = make_valid(union_all([make_valid(x) for x in rest.geometry]))
        duty_geom, n_duty = side_union(only)
        if duty_geom is None:
            raise SystemExit(f"緊鄰宗地中查無 {only}，無法以 --only-on 限定留設側")
        kinds_hit = sorted(set(adj[adj[kc].isin(only)][kc]))
        base_ring = make_valid(su.intersection(duty_geom.buffer(a.min_width)))
        duty_len = duty_geom.buffer(0.3).intersection(su.exterior).length
        log.append(f"應留設側＝鄰接 {'／'.join(kinds_hit)} 之 {n_duty} 筆宗地，"
                   f"共有邊界 {duty_len:,.1f} m（基地周長 {su.exterior.length:,.1f} m，"
                   f"占 {duty_len/su.exterior.length*100:.0f}%）")
        log.append(f"　非應留設側 {su.exterior.length-duty_len:,.1f} m（臨道路／未登錄地等"
                   f"非農業用地）依辦法§6①不須留設")
        log.append(f"基礎帶（應留設側內縮 {a.min_width} m）＝ {base_ring.area:,.2f} m²"
                   f"（{base_ring.area/A*100:.1f}%）")
    else:
        base_ring = make_valid(su.difference(su.buffer(-a.min_width)))
        log.append(f"基礎帶（全周界內縮 {a.min_width} m）＝ {base_ring.area:,.2f} m²"
                   f"（{base_ring.area/A*100:.1f}%）")
    for k, wv in sorted(sw.items()):
        sg, n = side_union([k])
        if sg is None:
            log.append(f"[warn] 緊鄰宗地查無「{k}」→ 該側寬度設定略過")
            continue
        before = base_ring.area
        base_ring = make_valid(base_ring.union(su.intersection(sg.buffer(wv))))
        log.append(f"臨「{k}」側（緊鄰 {n} 筆）加寬至 {wv} m　"
                   f"→ 基礎帶 {before:,.2f} → {base_ring.area:,.2f} m²"
                   f"（{base_ring.area/A*100:.1f}%）")
    log.append("　" + ("→ 已達面積門檻，無需再補" if base_ring.area >= target else
                      f"→ 尚缺 {target-base_ring.area:,.2f} m²，由加寬側補足"))

    side = None          # 兩種構法共用（minimal 不用加寬側，但下游 JSON 會讀）
    if a.shape == "minimal":
        # ── 最少頂點構法（★2026-08-01 使用者定案的三原則）─────────────────
        #   ① 非綠帶區域劃出**最少個三角形** → 簡單多邊形的三角形數＝頂點數−2，
        #      故等價於「非綠帶區域頂點數最少」
        #   ② 綠帶**最少轉折點且不得有圓弧角** → 退縮一律用 mitre（尖角）接合，
        #      不用預設的 round（圓弧）；圓弧會生出幾十個頂點，那是舊構法切出
        #      50 個三角形的主因
        #   ③ 綠帶面積**大於** 30%（嚴格大於，留餘裕；恰好等於 30.00% 時任何
        #      簡化都會掉到門檻以下——布袋306 實測容差 5 公分即掉到 29.98%）
        #   另加法律約束：簡化不得在**臨路側**生出綠帶——§9①要求綠帶須與毗鄰
        #   農業用地相緊臨，非應留設側的綠帶不能計入 30%。
        if duty_geom is None:
            raise SystemExit("--shape minimal 須搭配 --only-on（先界定應留設側）")
        import numpy as _np
        cap = (a.ratio_max if a.ratio_max is not None else a.ratio + 0.03) * A
        log.append(f"綠帶面積比上限 {cap/A*100:.0f}%（{cap:,.2f} m²）"
                   f"——無上限時最少頂點會把基地退縮到形狀最單純為止")
        # 沿基地外緣取樣，標出「非應留設側」界線（近非應留設側鄰地、遠應留設側鄰地）
        ND_STEP = 0.5
        nd_pts = []
        if nonduty_geom is not None:
            from shapely.geometry import Point as _Pt          # noqa: F401
            _ring = su.exterior if su.geom_type == "Polygon" else None
            for _p in parts_of(su):
                r = _p.exterior
                t = ND_STEP / 2
                while t < r.length:
                    q = r.interpolate(t)
                    if (nonduty_geom.distance(q) < ND_STEP and
                            (duty_geom is None or duty_geom.distance(q) >= ND_STEP)):
                        nd_pts.append(q)
                    t += ND_STEP
        nd_len = len(nd_pts) * ND_STEP
        if nonduty_geom is not None:
            log.append(f"非應留設側界線 {nd_len:.1f} m（綠帶覆蓋容許 "
                       f"{max(3.0, nd_len * 0.10):.1f} m）")
        # ★ 退縮基準＝應留設側**共有界線**（線），不是應留設側鄰地（面）。
        #   用鄰地面 buffer(d) 會放射狀外擴、隔著非應留設側宗地「穿透」進基地：
        #   本案 305 距南界（臨 306-1）僅約 4 m，d>4 時 305 的面緩衝穿過 306-1
        #   把不必留設的南界內側也劃成綠帶（2026-08-01 實踩，使用者指正南側多留）。
        #   共有界線 buffer 只會從基地自己的邊界向內長，收尾端由領口規則處理。
        duty_edge = su.exterior.intersection(duty_geom.buffer(0.5))
        for _p in parts_of(su)[1:]:
            duty_edge = duty_edge.union(_p.exterior.intersection(duty_geom.buffer(0.5)))
        best = None
        rej = {}                                 # 淘汰統計：失敗時要能說出為什麼

        def _rej(k):
            rej[k] = rej.get(k, 0) + 1
        for d in _np.arange(a.min_width, 40.0 + 1e-9, 0.25):
            keep = duty_edge.buffer(float(d))
            core_ps = parts_of(make_valid(su.difference(keep)))
            if not core_ps:
                break
            core = max(core_ps, key=lambda p: p.area)
            if A - core.area <= target:          # 連未簡化都不足門檻 → 再退縮
                continue
            if A - core.area > cap:              # 已超過上限，再退只會更多 → 收工
                break
            for tol in (8, 6, 5, 4, 3, 2.5, 2, 1.5, 1.2, 1.0, 0.8, 0.6, 0.4, 0.2, 0.0):
                ps = parts_of(make_valid(core.simplify(tol, preserve_topology=True)))
                if not ps:
                    continue
                u = max(ps, key=lambda p: p.area)
                if u.geom_type != "Polygon" or list(u.interiors):
                    _rej("形狀有孔")
                    continue                     # 須為單一無孔多邊形才好三角化
                # ⚠ 不可用 u.within(su)：簡化後即使溢出面積為 0，浮點邊界也會讓
                #   within() 回 False（2026-08-01 實踩，整個搜尋因此全部落空）。
                #   改以面積容差判斷；溢出量小者裁回基地內（裁切只在溢出處補少數
                #   頂點，仍遠少於不簡化；溢出過大者才捨棄）。
                over = u.difference(su).area
                if over > 2.0:
                    _rej("簡化溢出")
                    continue                     # 溢出過大，簡化過頭
                if over > 1e-6:
                    ps2 = parts_of(make_valid(u.intersection(su)))
                    if not ps2:
                        continue
                    u = max(ps2, key=lambda p: p.area)
                    if u.geom_type != "Polygon" or list(u.interiors):
                        _rej("形狀有孔")
                        continue
                # ① 先剔近共線頂點：裁回基地時會在邊界補出幾乎共線的頂點（實測
                #    偏差 0.26 m），三角化會切出「高 0.26、底 32」的細片三角形。
                # ② 再截尖。順序不可反——先截後剔會把截角的兩個新點當雜訊吃掉、
                #    針尖復活（2026-08-01 實踩：15.4° 回來了）。
                # 所有閘門（寬度／面積／臨非留設側／銳角）都在此之後重新檢核。
                _u3 = drop_collinear(u, 0.3)
                if _u3.geom_type == "Polygon" and not list(_u3.interiors):
                    if _u3.difference(su).area > 1e-6:
                        _ps4 = parts_of(make_valid(_u3.intersection(su)))
                        _u3 = max(_ps4, key=lambda p: p.area) if _ps4 else None
                    if (_u3 is not None and _u3.geom_type == "Polygon"
                            and not list(_u3.interiors)):
                        u = _u3
                u = chop_needles(u, a.min_angle)  # 可配置範圍不留針尖（地籍尖角遺傳）
                if u.distance(duty_geom) < a.min_width - 1e-6:
                    _rej("寬度不足")
                    continue                     # 最小寬度 §9②
                g = make_valid(su.difference(u))
                if not (target < g.area <= cap):
                    _rej("面積出窗")
                    continue                     # 須大於門檻且不超過上限
                # §9①：綠帶須與毗鄰農業用地相緊臨 → 不得沿**非應留設側界線**劃設。
                # ⚠ 三種錯的量法都實踩過（2026-08-01）：
                #   ①「距應留設側 d+0.5 以內」——mitre 尖角在凸角會外伸到 d 的數倍，
                #     正常綠帶被誤判，整個搜尋落空。
                #   ②「距非應留設側鄰地 min_width 以內之面積」——鄰地是**面**，隔著
                #     別的宗地也會被算到（本案曾隔著保留地量到 67.59 ㎡ 而全數落空）。
                #   ③ 量界線但不排除收尾領口——綠帶沿應留設側走到與非應留設側交會處，
                #     必然繞過轉角在非應留設側界線上「收尾」約 d 公尺，那是帶子終止的
                #     幾何必然，不是沿線劃設；不排除的話窄窗口內全部解被否決。
                #   → 正解：量非應留設側界線被綠帶覆蓋的長度，但**距應留設側 d+1 公尺
                #     以內的收尾領口不計**；領口外仍有覆蓋才是真的沿線劃設。
                if nd_pts:
                    # ⚠ g 沿邊界常帶**零寬度數值痕跡**（差集/簡化的浮點殘渣），
                    #   distance<0.01 會把整段界線誤判成被綠帶覆蓋（實測 32 m 全是
                    #   痕跡）。先以 -0.1 侵蝕把 <0.2 m 的痕跡消掉再量。
                    gg = g.buffer(-0.1)
                    cov = sum(1 for p in nd_pts
                              if gg.distance(p) < 0.25 and
                              duty_edge.distance(p) > float(d) + 1.0) * ND_STEP
                    if cov > max(3.0, nd_len * 0.10):
                        _rej(f"臨非留設側劃設(覆蓋{cov:.0f}m)")
                        continue
                if any(p.area < 1.0 for p in parts_of(g)):
                    _rej("夾帶碎片")
                    continue                     # 綠帶不得夾帶 <1 m² 的碎片
                if min_interior_angle(g, nonduty_geom) < a.min_angle:
                    _rej("銳角")
                    continue                     # 實務上不留銳角尖端
                nv = len(u.exterior.coords) - 1
                if best is None or (nv, g.area) < (best[0], best[1]):
                    best = (nv, g.area, float(d), float(tol), u, g)
                break                            # 該退縮距離下已取到最粗容差
        if best is None:
            raise SystemExit("找不到同時滿足「最少頂點／最小寬度／綠帶大於門檻」之幾何"
                             f"　→ 放寬 --ratio 或改用 --shape offset　淘汰統計：{rej}")
        nv, _gA, d_used, tol_used, _u, green = best
        w = d_used
        log.append(f"綠帶最小內角 {min_interior_angle(green, nonduty_geom):.1f}°（不計收尾端）"
                   f"（門檻 {a.min_angle:.0f}°，銳角尖端已於搜尋階段排除）")
        mode = (f"最少頂點構法：應留設側以尖角(mitre)退縮 {d_used:.2f} m、"
                f"簡化容差 {tol_used:.2f} m")
        log.append(f"最少頂點構法：非綠帶區域 {nv} 個頂點 → 可三角化為 "
                   f"{nv - 2} 個三角形；綠帶轉折點 "
                   f"{len(green.exterior.coords) - 1 if green.geom_type == 'Polygon' else '（多塊）'}"
                   f"，全部為直線邊（無圓弧角）")
    else:
        if base_ring.area < target and kinds:
            side, n = side_union(kinds)
            if side is None:
                log.append(f"[warn] 緊鄰宗地中查無 {kinds} → 改全周界等寬加寬")
            else:
                log.append(f"補足加寬側＝緊鄰之 {kinds} 共 {n} 筆")

        sw_note = ("＋".join(f"臨{k}側{v}m" for k, v in sorted(sw.items())) + "＋") if sw else ""
        scope0 = "應留設側" if only else "全周界"
        mode, w, green = f"{scope0}{a.min_width}m{('＋' + sw_note[:-1]) if sw else ''}即達標", None, base_ring
        if base_ring.area < target:
            if side is not None:
                w, green = solve_widen(su, base_ring, side, target)
                if w is not None:
                    mode = (f"{scope0} {a.min_width} m ＋ {sw_note}"
                            f"補足側自邊界內推 {w:.2f} m")
                else:
                    log.append("[warn] 單以加寬側加到底仍不足門檻 → 改全周界等寬加寬")
            if w is None and duty_geom is not None:
                # 限定留設側時不可退回全周界（會在非應留設側劃綠帶）→ 改為整個應留設側加寬
                w, green = solve_widen(su, base_ring, duty_geom, target)
                mode = (f"應留設側整體自邊界內推 {w:.2f} m" if w is not None
                        else f"應留設側加到底仍不足 {a.ratio*100:.0f}%")
                if w is None:
                    log.append("[warn] 僅在應留設側留設無法達面積門檻——須檢討改以"
                               "毗連土地納入（辦法§6②）或調整申請範圍")
            elif w is None:
                W, green = solve_uniform(su, target, a.min_width)
                mode = f"全周界等寬內縮 {W:.2f} m"
                w = W

    inner = make_valid(su.difference(green))
    log += [
        "",
        f"採用方式：{mode}",
        f"隔離綠帶或設施　{green.area:,.2f} m²（占基地 {green.area/A*100:.2f}%）"
        f"　{'✓ 達法定門檻' if green.area >= target - 0.5 else '✗ 未達門檻'}",
        f"可配置範圍　　　{inner.area:,.2f} m²（占基地 {inner.area/A*100:.2f}%）",
    ]
    blocks = parts_of(green)
    if len(blocks) > 1:
        log.append(f"綠帶分為 {len(blocks)} 塊（{'、'.join(f'{p.area:,.1f}' for p in blocks)} m²）")

    # ★ 最小寬度是法定要求，用取樣實測驗證，不靠「建構方式應該對」推定
    import numpy as np
    per = su.exterior if su.geom_type == "Polygon" else max(parts_of(su), key=lambda p: p.area).exterior
    pts = [per.interpolate(i) for i in range(0, max(int(per.length), 2))]
    if duty_geom is not None:      # 只在「應留設側」取樣，非應留設側本來就沒帶
        zone = duty_geom.buffer(0.6)
        pts = [p for p in pts if zone.contains(p)]
    ws = np.array([p.distance(inner) for p in pts]) if pts else np.array([0.0])
    short = [i for i, v in enumerate(ws) if v < a.min_width - 0.01]
    # 綠帶在「應留設側↔非應留設側」交界處必然收尾到 0，那不是缺陷。
    # 距非應留設側鄰地 < 採用寬度者歸類為「交界收尾」，其餘才是真不足。
    taper = real = 0
    for i in short:
        if nonduty_geom is not None and pts[i].distance(nonduty_geom) < a.min_width:
            taper += 1
        else:
            real += 1
    scope = "應留設側" if duty_geom is not None else "全周界"
    core = ws[[i for i in range(len(ws)) if i not in set(short)]] if short else ws
    log += ["",
            f"寬度檢核（沿{scope}每 1 m 取樣 {len(ws)} 點，量至綠帶內緣）："
            f"最小 {ws.min():.2f} m、中位 {np.median(ws):.2f} m、最大 {ws.max():.2f} m",
            f"  未達採用寬度 {a.min_width} m 者 {len(short)} 點："
            f"交界收尾 {taper} 點（與非應留設側銜接處，屬正常）、真不足 {real} 點 → "
            + (f"✓ {scope}除交界收尾外均達 {a.min_width} m" if real == 0
               else "✗ 有真不足，須檢查幾何"),
            f"  扣除交界收尾後最小 {core.min():.2f} m、中位 {np.median(core):.2f} m",
            f"  加寬部位（>{a.min_width*1.5:.1f} m）占取樣 "
            f"{(ws > a.min_width*1.5).mean()*100:.0f}%"]

    out_dir = a.out or os.path.dirname(os.path.dirname(os.path.abspath(a.joined)))
    tag = f"_{a.tag}" if a.tag else ""
    d = os.path.join(out_dir, "隔離綠帶")
    os.makedirs(d, exist_ok=True)
    crs = site.crs
    gpd.GeoDataFrame({"name": ["隔離綠帶或設施"],
                      "面積m2": [round(green.area, 2)],
                      "占基地": [round(green.area / A * 100, 2)]},
                     geometry=[green], crs=crs).to_file(
        os.path.join(d, f"隔離綠帶或設施{tag}.shp"), encoding="utf-8")
    gpd.GeoDataFrame({"name": ["可配置範圍"], "面積m2": [round(inner.area, 2)]},
                     geometry=[inner], crs=crs).to_file(
        os.path.join(d, f"可配置範圍{tag}.shp"), encoding="utf-8")
    # 臨路側歸「隔離設施」、其餘為「隔離綠帶」（使用者 2026-08-01 定案）
    fac_a = belt_a = None
    if a.facility_near and os.path.exists(a.facility_near):
        fn = gpd.read_file(a.facility_near, engine="pyogrio")
        if fn.crs is not None and crs is not None and fn.crs != crs:
            fn = fn.to_crs(crs)
        near = make_valid(union_all([make_valid(x) for x in fn.geometry]))
        fac, belt = split_by_side(green, su, inner, near)
        fac_a, belt_a = fac.area, belt.area
        for nm, gg in (("隔離設施", fac), ("隔離綠帶", belt)):
            if gg.is_empty:
                continue
            gpd.GeoDataFrame({"name": [nm], "面積m2": [round(gg.area, 2)],
                              "占基地": [round(gg.area / A * 100, 2)]},
                             geometry=[gg], crs=crs).to_file(
                os.path.join(d, f"{nm}{tag}.shp"), encoding="utf-8")
        log += ["",
                f"分類（臨路側＝隔離設施、其餘＝隔離綠帶，均計入面積比）：",
                f"　隔離設施　{fac_a:,.2f} m²（占基地 {fac_a/A*100:.2f}%）",
                f"　隔離綠帶　{belt_a:,.2f} m²（占基地 {belt_a/A*100:.2f}%）"]
    res = {"基地_m2": round(A, 2), "法定最小寬度_m": a.min_width, "法定面積比": a.ratio,
           "應留設_m2": round(target, 2), "採用方式": mode,
           "僅留設於": only or None,
           "側別最小寬度": sw or None,
           "補足加寬側": kinds if side is not None else None,
           "加寬距離_m": round(w, 2) if w else None,
           "隔離綠帶或設施_m2": round(green.area, 2),
           "占基地_pct": round(green.area / A * 100, 2),
           "可配置範圍_m2": round(inner.area, 2),
           "寬度檢核": {"取樣點數": int(len(ws)), "最小_m": round(float(ws.min()), 2),
                    "中位_m": round(float(np.median(ws)), 2),
                    "最大_m": round(float(ws.max()), 2), "未達採用寬度點數": len(short),
                    "交界收尾點數": taper, "真不足點數": real}}
    txt = "\n".join(log)
    print(txt)
    io.open(os.path.join(out_dir, f"綠帶計算{tag}.txt"), "w", encoding="utf-8").write(txt)
    io.open(os.path.join(out_dir, f"綠帶計算{tag}.json"), "w", encoding="utf-8").write(
        json.dumps(res, ensure_ascii=False, indent=1))
    print(f"\n→ {d}\\隔離綠帶或設施{tag}.shp、可配置範圍{tag}.shp")
    print(f"→ {out_dir}\\綠帶計算{tag}.txt / .json")


if __name__ == "__main__":
    main()
