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
from shapely.geometry import MultiPolygon, Polygon

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


def fill_holes(geom):
    return MultiPolygon([Polygon(p.exterior) for p in parts_of(geom)])


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
            sur = sur[~sur.geometry.intersects(su.buffer(-0.5))]
            kc = "使類" if "使類" in sur.columns else None
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

    side = None
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
