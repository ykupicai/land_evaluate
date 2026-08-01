# -*- coding: utf-8 -*-
"""部分變更編定：把涉及公路之現況道路範圍分割出去、維持原使用地類別

★ 由 outputs/20260731_布袋考試潭段306/_split_road.py 通用化（2026-08-02 結案回饋）。
  作法依據＝306-1 前例（112 年核准，反算驗證：1,585.396 ÷ 4,897.97 ＝ 32.37%，
  綠帶比例以**分割後之申請範圍**為分母，非原地號全部面積）。
  判準登記：衍古 `_判準登記表` B 表「部分變更編定（涉公路）」列。

流程：
  1. 道路範圍＝臨路評估 features.geojson 中 lu_name 符合者 ∩ 基地（LUIMAP 現況判讀）
  2. 簡化為直線段（LUIMAP 是網格向量化、邊界呈像素階梯；實際分割線以地政複丈為準）
  3. 切分後申請範圍的銳角尖端（實務上不會留）以直線弦切除、併入保留範圍
  4. 產鄰地層：官方鄰地＋保留地（維持原使類＝農業用地，依要點§9① 亦為應留設側），
     並剔除寬度不足之切分細條——細條會把免留設側誤判成應留設側
     （見衍古 [[幾何差集的隱形產物會翻轉法律判定]]）

用法（系統 Python）：
  python -X utf8 scripts/split_road.py --joined <面積join.shp> \
      --features <臨路評估/features.geojson> --neighbours <鄰地_面積join.shp> \
      --out outputs/<案>/申請範圍 [--lu-name 一般道路] [--keep-class 農牧用地] \
      [--keep-zone 一般農業區] [--simplify 1.5] [--min-part 1.0] [--min-angle 45]

輸出：申請範圍.shp、保留_道路.shp、鄰地_含保留地.shp、部分變更編定.json
下游：greenbelt_ratio --joined 申請範圍.shp --neighbours 鄰地_含保留地.shp
      --facility-near 保留_道路.shp
"""
from __future__ import annotations

import argparse
import io
import json
import os

import geopandas as gpd
import pandas as pd
from shapely import make_valid
from shapely.geometry import Polygon
from shapely.ops import unary_union

CRS = 3826


def parts_of(geom):
    if geom.is_empty:
        return []
    return list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]


def interior_angles(poly):
    """各頂點內角（度），順序同 exterior.coords[:-1]。"""
    import math
    cs = list(poly.exterior.coords)[:-1]
    n = len(cs)
    out = []
    for i in range(n):
        a, p, c = cs[(i - 1) % n], cs[i], cs[(i + 1) % n]
        v1 = (a[0] - p[0], a[1] - p[1]); v2 = (c[0] - p[0], c[1] - p[1])
        l1 = math.hypot(*v1); l2 = math.hypot(*v2)
        if l1 < 1e-9 or l2 < 1e-9:
            out.append(180.0); continue
        cosv = (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)
        out.append(math.degrees(math.acos(max(-1.0, min(1.0, cosv)))))
    return out


def trim_spikes(poly, min_ang=45.0, max_span=6, max_cut_frac=0.08):
    """內角 < min_ang 的尖端以**直線弦**切除，回傳 (整理後多邊形, [切下的片])。

    ⚠ 只切尖端那一個三角形會讓相鄰頂點變得更尖（布袋306 實測：V05 由 97.8° 變 16.8°），
      必須搜尋一條**跨越整個尖端**的弦——在既有頂點間連直線、取切除面積最小者。
    ⚠ 判斷條件是「目標銳角消失且沒生出新銳角」，不是「切完全域無銳角」——
      有兩處銳角時後者永遠不成立，兩處都切不掉。
    ⚠ 退化針尖（0°）面積本來就是 0，切除條件不可要求 lost > 0。"""
    cut, stuck = [], []
    for _ in range(8):
        cs = list(poly.exterior.coords)[:-1]
        n = len(cs)
        angs = interior_angles(poly)
        key = lambda p: (round(p[0], 3), round(p[1], 3))     # noqa: E731
        bad = [i for i, t in enumerate(angs)
               if t < min_ang and key(cs[i]) not in stuck]
        if not bad:
            break
        k = bad[0]
        old_bad = {key(p) for p, t in zip(cs, angs) if t < min_ang}
        best = None
        for span in range(2, max_span + 1):
            for off in range(1, span):
                idx = [((k - off) + t) % n for t in range(span, n + 1)]
                if len(idx) < 3:
                    continue
                cand = make_valid(Polygon([cs[t] for t in idx]))
                if cand.geom_type != "Polygon" or cand.is_empty:
                    continue
                if not cand.within(poly.buffer(1e-6)):
                    continue
                lost = poly.area - cand.area
                if lost < -1e-6 or lost > poly.area * max_cut_frac:
                    continue
                c_cs = list(cand.exterior.coords)[:-1]
                new_bad = {key(p) for p, t in zip(c_cs, interior_angles(cand))
                           if t < min_ang}
                if key(cs[k]) in new_bad or not new_bad <= (old_bad - {key(cs[k])}):
                    continue
                if best is None or lost < best[0]:
                    best = (lost, cand)
        if best is None:
            print(f"[warn] 頂點 {k}（{angs[k]:.1f}°）找不到可切之弦，保留原狀")
            stuck.append(key(cs[k]))
            continue
        piece = make_valid(poly.difference(best[1]))
        cut += [p for p in parts_of(piece) if p.area > 1e-6]
        poly = best[1]
    return poly, cut


def rd(p):
    g = gpd.read_file(p)
    return g.to_crs(CRS) if g.crs else g.set_crs(CRS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True, help="基地 面積join.shp")
    ap.add_argument("--features", required=True, help="臨路評估 features.geojson")
    ap.add_argument("--neighbours", required=True,
                    help="官方鄰地 面積join.shp（須含 使用地類別/使類 欄）")
    ap.add_argument("--out", required=True, help="輸出資料夾（建議 outputs/<案>/申請範圍）")
    ap.add_argument("--lu-name", default="一般道路", help="LUIMAP 判為道路之類別名，預設 一般道路")
    ap.add_argument("--keep-class", default="農牧用地", help="保留地之使用地類別，預設 農牧用地")
    ap.add_argument("--keep-zone", default="一般農業區", help="保留地之使用分區，預設 一般農業區")
    ap.add_argument("--simplify", type=float, default=1.5,
                    help="分割線簡化容差(m)，預設 1.5（LUIMAP 網格階梯→直線段）")
    ap.add_argument("--min-part", type=float, default=1.0,
                    help="小於此面積(㎡)之道路判讀視為雜訊，預設 1.0")
    ap.add_argument("--min-angle", type=float, default=45.0,
                    help="申請範圍允許之最小內角(度)，預設 45；更小之尖端切除併入保留")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    site_g = rd(a.joined)
    site = unary_union(site_g.geometry.tolist())
    feats = rd(a.features)
    road_raw = unary_union(feats[feats["lu_name"] == a.lu_name].geometry.tolist())
    road_raw = road_raw.intersection(site)
    if road_raw.is_empty:
        raise SystemExit(f"基地內查無「{a.lu_name}」判讀範圍——本案不需部分變更編定")
    parts = parts_of(road_raw)
    road = unary_union([p for p in parts if p.area >= a.min_part])
    dropped = sum(p.area for p in parts if p.area < a.min_part)

    nv0 = len(road.exterior.coords) if road.geom_type == "Polygon" else -1
    a0 = road.area
    road = road.simplify(a.simplify, preserve_topology=True).intersection(site)
    road = max(parts_of(road), key=lambda p: p.area)
    print(f"分割線簡化：容差 {a.simplify} m，頂點 {nv0} → "
          f"{len(road.exterior.coords)}，面積 {a0:,.2f} → {road.area:,.2f} ㎡")

    apply_ = site.difference(road)
    ap_ = sorted(parts_of(apply_), key=lambda p: -p.area)
    if len(ap_) > 1:
        print(f"[warn] 扣除後分成 {len(ap_)} 塊，取最大塊；其餘 "
              f"{[round(p.area, 2) for p in ap_[1:]]} ㎡ 併回保留地")
        road = unary_union([road] + ap_[1:])
    apply_geom = ap_[0]

    # 銳角尖端從源頭剔除（綠帶＝基地−可配置範圍，基地有銳角綠帶必然跟著有）
    apply_geom, spikes = trim_spikes(apply_geom, a.min_angle)
    if spikes:
        road = unary_union([road] + spikes)
        print(f"銳角剔除：{len(spikes)} 處、共 {sum(p.area for p in spikes):,.2f} ㎡ 併入保留範圍")

    A0, Ar, Aa = site.area, road.area, apply_geom.area
    print(f"原地號        {A0:10,.2f} ㎡")
    print(f"－保留(道路)  {Ar:10,.2f} ㎡（{Ar/A0*100:.2f}%，判讀雜訊已剔除 {dropped:.2f} ㎡）")
    print(f"＝申請範圍    {Aa:10,.2f} ㎡（{Aa/A0*100:.2f}%）　對帳差 {abs(Ar + Aa - A0):.4f} ㎡")
    print(f"綠帶法定下限（申請範圍 30%）＝ {Aa * 0.30:,.2f} ㎡"
          f"（誤用原地號會算成 {A0 * 0.30:,.2f}，多 {A0*0.30 - Aa*0.30:,.2f} ㎡）")

    # 欄名一律 ≤10 bytes（DBF 截斷會讓整份 shp 讀不出來）
    r0 = site_g.iloc[0]

    def pick(*names):
        for n in names:
            if n in site_g.columns and str(r0[n]).strip() not in ("", "None", "nan"):
                return str(r0[n])
        return ""
    gpd.GeoDataFrame({"地號": [pick("地號")], "使分": [pick("使用分區", "使分")],
                      "使類": [pick("使用地類別", "使類")],
                      "縣市": [pick("縣市名稱", "縣市")],
                      "鄉鎮": [pick("鄉鎮市區", "鄉鎮")], "範圍": ["申請範圍"]},
                     geometry=[apply_geom], crs=f"EPSG:{CRS}").to_file(
        os.path.join(a.out, "申請範圍.shp"), encoding="utf-8")
    gpd.GeoDataFrame({"範圍": [f"保留({a.lu_name})"], "使類": [a.keep_class]},
                     geometry=[road], crs=f"EPSG:{CRS}").to_file(
        os.path.join(a.out, "保留_道路.shp"), encoding="utf-8")

    # 鄰地層＝官方鄰地＋保留地。★保留地幾何以侵蝕測試剔除寬度<1.5m 的切分細條——
    # 1 公分數值細縫曾把整條免留設側（臨特目用地）誤判成應留設側（布袋306 實踩）
    nb_road = road.buffer(-0.75).buffer(0.9, join_style=2).intersection(road)
    print(f"鄰地層之保留地幾何：{road.area:,.2f} → {nb_road.area:,.2f} ㎡（剔除細條）")
    nb = rd(a.neighbours).rename(columns={"使用地類別": "使類", "使用分區": "使分"})
    if "使類" not in nb.columns:
        raise SystemExit("鄰地層缺 使用地類別/使類 欄——請用平台官方屬性接回後的 shp")
    keep_row = gpd.GeoDataFrame(
        {"地號": ["保留"], "使類": [a.keep_class], "使分": [a.keep_zone]},
        geometry=[nb_road], crs=f"EPSG:{CRS}")
    cols = [c for c in ("地號", "使類", "使分") if c in nb.columns] + ["geometry"]
    both = gpd.GeoDataFrame(pd.concat([nb[cols], keep_row[cols]], ignore_index=True),
                            crs=f"EPSG:{CRS}")
    both.to_file(os.path.join(a.out, "鄰地_含保留地.shp"), encoding="utf-8")

    io.open(os.path.join(a.out, "部分變更編定.json"), "w", encoding="utf-8").write(
        json.dumps({
            "原地號面積_m2": round(A0, 2),
            "保留_道路_m2": round(Ar, 2),
            "申請範圍_m2": round(Aa, 2),
            "綠帶法定下限_m2": round(Aa * 0.30, 2),
            "銳角剔除_處": len(spikes),
            "道路範圍依據": f"國土利用調查(LUIMAP){a.lu_name} ∩ 基地；現況判讀，"
                            "實際分割線以地政複丈成果為準",
        }, ensure_ascii=False, indent=2))
    print(f"→ {a.out}\\申請範圍.shp、保留_道路.shp、鄰地_含保留地.shp、部分變更編定.json")


if __name__ == "__main__":
    main()
