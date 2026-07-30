# -*- coding: utf-8 -*-
"""聚落判定引擎 — 「與十戶以上之村莊或聚落最短水平距離應大於 N 公尺」的機械化

★ 建立緣由（2026-07-30）：這一項原本在檢核清單裡是 `inquiry`（無官方點位圖資→須函詢），
  簡報只能用 OSM 初篩，且 OSM 在台灣鄉間幾乎沒繪建物。改用**各縣市民政局門牌坐標開放資料**
  即可實算，安南案已驗證。

★★ 判定方法（法律解釋，寫死在本引擎，勿自行簡化）：
  條文是「與十戶以上之**村莊或聚落**最短水平距離應大於 N 公尺」，所以是三步：
    ① 把門牌點單一連結聚類（相鄰 ≤ 群聚門檻 m 視為同一聚落）
    ② 該群門牌數 ≥ 最少戶數（法定十戶）→ 才算「十戶以上之村莊或聚落」
    ③ 量基地到**該聚落**（其成員點）的最短距離，與 N 公尺比較
  兩種常見錯法（安南案實測會得出相反答案，故特別記載）：
    ✗「數 200m 內有幾個門牌，不足十就過」→ 安南 200m 內僅 5~6 筆，答案對但理由錯；
       若周邊是一個大聚落只有邊緣幾戶落在 200m 內，就會誤判為未觸及。
    ✗「最近門牌 2.7m < 200m 所以觸及」→ 那幾筆是分散魚塭寮，構不成十戶聚落，判定反掉。
  ★ 一律輸出多個群聚門檻（預設 30/50/100m）的結果：三者一致才是穩健結論，
    不一致就必須人工判斷並在簡報寫明門檻敏感度。

★ 資料限制（口徑見 config/門牌坐標來源.yaml，交付文件必須照寫）：
  官方聲明坐標為戶政人工點位、非精確、僅供參考，門牌存在與否由戶政事務所審認
  → 本引擎產出是**篩選與初判**；門牌 ≠ 戶（公寓多戶共一門牌／廟宇工廠倉庫有門牌無人住）
  → 輸出一律稱「門牌數」；臨界值（8~12）必須現地或戶政查證。

用法（系統 Python，不需 QGIS）：
  # 首次取得某縣市門牌資料（自動挑年度最新版）
  python scripts/settlement_check.py --download --county 臺南市

  # 判定
  python scripts/settlement_check.py --joined <面積join.shp> \
      [--sites <案場定義/sites.shp>] [--dist 200] [--min-households 10] \
      [--cluster 30 50 100] [--out <案件資料夾>]
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import re
import sys
import urllib.request

import geopandas as gpd
import numpy as np
import pandas as pd
import yaml
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from shapely.geometry import MultiPoint, Point

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO, "門牌坐標")
REGISTRY = os.path.join(REPO, "config", "門牌坐標來源.yaml")
DGT_API = "https://data.gov.tw/api/v2/rest/dataset/{}"
# data.gov.tw 會以 403 擋掉 urllib 預設 User-Agent（curl 可、python-urllib 不可，2026-07-30 實踩）
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _urlopen(url: str, timeout: int = 120):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout)

# 各縣市欄位名不一，自動偵測用的關鍵字（順序即優先序）
X_HINTS = ["橫座標", "橫坐標", "TWD97_X", "TWD97X", "X坐標", "X座標"]
Y_HINTS = ["縱座標", "縱坐標", "TWD97_Y", "TWD97Y", "Y坐標", "Y座標"]
LON_HINTS = ["經度", "LON", "LNG", "WGS84X"]
LAT_HINTS = ["緯度", "LAT", "WGS84Y"]
LABEL_COLS = ["村里", "里", "街路段", "路名", "街道名稱", "巷", "弄", "號"]


def _county_variants(name: str) -> list[str]:
    out = [name]
    for a, b in (("臺", "台"), ("台", "臺")):
        if a in name:
            out.append(name.replace(a, b))
    return out


def load_registry() -> dict:
    if not os.path.exists(REGISTRY):
        return {}
    return yaml.safe_load(io.open(REGISTRY, encoding="utf-8")) or {}


def addr_csv_path(county: str) -> str | None:
    """回傳本機門牌 CSV 路徑（臺/台 變體都試）；找不到回 None。"""
    for c in _county_variants(county):
        p = os.path.join(DATA_DIR, c + ".csv")
        if os.path.exists(p):
            return p
    return None


def download_county(county: str) -> str:
    """自 data.gov.tw 取該縣市門牌 CSV。★ 一律挑 resourceDescription 年度最大者。"""
    reg = load_registry().get("縣市", {})
    entry = next((reg[c] for c in _county_variants(county) if c in reg), None)
    if not entry:
        raise SystemExit(
            f"config/門牌坐標來源.yaml 沒有「{county}」。請先查 dataset_id 補進註冊表"
            f"（查法見該檔『說明.資料集查詢法』）。")
    did = entry["dataset_id"]
    with _urlopen(DGT_API.format(did)) as r:
        meta = json.load(r)["result"]
    cands = []
    for d in meta.get("distribution", []):
        if (d.get("resourceFormat") or "").upper() != "CSV":
            continue
        m = re.search(r"(\d{3})\s*年", d.get("resourceDescription") or "")
        cands.append((int(m.group(1)) if m else -1,
                      d.get("resourceDescription") or "",
                      d.get("resourceDownloadUrl")))
    if not cands:
        raise SystemExit(f"dataset {did} 沒有 CSV 資源，請人工確認：{meta.get('relatedUrl')}")
    cands.sort(key=lambda t: t[0], reverse=True)
    year, desc, url = cands[0]
    if len(cands) > 1:
        print(f"  資料集含 {len(cands)} 個 CSV 版本，取年度最新：{desc}")
        print(f"  （其餘：{[c[1] for c in cands[1:]]}）")
    os.makedirs(DATA_DIR, exist_ok=True)
    dst = os.path.join(DATA_DIR, county + ".csv")
    print(f"  下載 {url}")
    tmp = dst + ".part"
    with _urlopen(url, timeout=900) as r, io.open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    os.replace(tmp, dst)
    io.open(dst + ".版本.txt", "w", encoding="utf-8").write(
        f"{desc}\ndataset_id={did}\nurl={url}\n"
        f"資料集 modifiedDate={meta.get('modifiedDate')}\n"
        f"下載日期={datetime.date.today()}\n"
        f"更新頻率={meta.get('updateFrequency')}\n")
    print(f"→ {dst}（{os.path.getsize(dst)/1e6:.1f} MB）\n→ {dst}.版本.txt")
    return dst


def _pick(cols: list[str], hints: list[str]) -> str | None:
    up = {c: c.upper() for c in cols}
    for h in hints:
        for c in cols:
            if h.upper() in up[c]:
                return c
    return None


def load_addresses(county: str, path: str | None = None,
                   bbox: tuple | None = None, log: list | None = None
                   ) -> gpd.GeoDataFrame:
    """讀門牌 CSV → EPSG:3826 點圖層。bbox=(minx,miny,maxx,maxy) 先裁切以省記憶體。"""
    log = log if log is not None else []
    p = path or addr_csv_path(county)
    if not p:
        raise FileNotFoundError(
            f"找不到 門牌坐標\\{county}.csv。先跑："
            f"python scripts/settlement_check.py --download --county {county}")
    ver = p + ".版本.txt"
    vtxt = io.open(ver, encoding="utf-8").readline().strip() if os.path.exists(ver) else ""
    mtime = datetime.date.fromtimestamp(os.path.getmtime(p))
    log.append(f"  門牌資料：{os.path.basename(p)}"
               f"{('［' + vtxt + '］') if vtxt else ''}（檔案日期 {mtime}）")
    if not vtxt:
        log.append("    ⚠ 無 .版本.txt——無法確認年度版本，建議用 --download 重取")

    df = pd.read_csv(p, dtype=str, encoding="utf-8-sig", low_memory=False)
    cols = list(df.columns)
    xc, yc = _pick(cols, X_HINTS), _pick(cols, Y_HINTS)
    crs = 3826
    if not (xc and yc):
        xc, yc = _pick(cols, LON_HINTS), _pick(cols, LAT_HINTS)
        crs = 4326
    if not (xc and yc):
        raise SystemExit(f"{p} 找不到坐標欄（欄位：{cols}）——請在註冊表補「欄位: x/y」")
    df["_x"] = pd.to_numeric(df[xc], errors="coerce")
    df["_y"] = pd.to_numeric(df[yc], errors="coerce")
    n0 = len(df)
    df = df.dropna(subset=["_x", "_y"])
    if len(df) < n0:
        log.append(f"    ⚠ {n0 - len(df):,} 筆坐標缺漏已剔除（共 {n0:,} 筆）")
    g = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["_x"], df["_y"]), crs=crs)
    if crs != 3826:
        g = g.to_crs(3826)
        log.append(f"    來源為經緯度（{xc}/{yc}）→ 已轉 EPSG:3826")
    log.append(f"    共 {len(g):,} 筆門牌點")
    if bbox:
        g = g.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]]
        log.append(f"    裁切至基地周邊 → {len(g):,} 筆")
    return g


def cluster(pts: np.ndarray, eps: float) -> np.ndarray:
    """單一連結聚類（相鄰 ≤ eps 公尺即同群）→ 回每點的群編號。"""
    if len(pts) == 0:
        return np.array([], dtype=int)
    tree = cKDTree(pts)
    pairs = tree.query_pairs(eps, output_type="ndarray")
    n = len(pts)
    m = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    _, lab = connected_components(m, directed=False)
    return lab


def _label_of(sub: pd.DataFrame) -> str:
    for c in LABEL_COLS:
        if c in sub.columns:
            s = sub[c].dropna()
            if len(s):
                return str(s.mode().iat[0])
    return "（無名）"


def nearest_settlement(geom, addr: gpd.GeoDataFrame, eps: float, min_n: int) -> dict:
    """回傳基地到「最近之 ≥min_n 門牌聚落」的距離與該聚落資訊。"""
    pts = np.c_[addr.geometry.x.values, addr.geometry.y.values]
    lab = cluster(pts, eps)
    best = None
    for c, cnt in zip(*np.unique(lab, return_counts=True)):
        if cnt < min_n:
            continue
        idx = np.where(lab == c)[0]
        d = float(geom.distance(MultiPoint(pts[idx]))) if len(idx) > 1 \
            else float(geom.distance(Point(*pts[idx[0]])))
        if best is None or d < best["最短距離_m"]:
            best = {"最短距離_m": round(d, 1), "門牌數": int(cnt),
                    "名稱": _label_of(addr.iloc[idx])}
    return best or {"最短距離_m": None, "門牌數": 0, "名稱": None}


def assess(geom, addr: gpd.GeoDataFrame, dist: float, min_n: int,
           eps_list: list[float]) -> dict:
    """單一基地（或案場）的完整判定結果。"""
    d_all = addr.geometry.distance(geom)
    res = {
        "範圍內門牌數_僅供參考": int((d_all <= dist).sum()),
        "最近單一門牌_m": round(float(d_all.min()), 1) if len(addr) else None,
        "聚落": {},
    }
    verdicts = set()
    for eps in eps_list:
        r = nearest_settlement(geom, addr, eps, min_n)
        hit = r["最短距離_m"] is not None and r["最短距離_m"] <= dist
        r["判定"] = "觸及" if hit else "未觸及"
        verdicts.add(r["判定"])
        res["聚落"][f"群聚門檻{int(eps)}m"] = r
    res["判定"] = verdicts.pop() if len(verdicts) == 1 else "門檻敏感須人工判斷"
    return res


def check(su, sites, county: str, dist: float, min_n: int, eps_list: list[float],
          log: list, addr_path: str | None = None) -> dict:
    """對整體基地＋逐案場判定；log 追加人可讀敘述，回傳結構化結果。"""
    b = su.buffer(max(eps_list) * 6 + dist + 2000).bounds
    addr = load_addresses(county, addr_path, bbox=b, log=log)
    out = {"距離門檻_m": dist, "最少戶數": min_n,
           "群聚門檻_m": eps_list, "全案": assess(su, addr, dist, min_n, eps_list)}
    if sites is not None and len(sites) > 1:
        out["逐案場"] = {s["site_id"]: assess(s.geometry, addr, dist, min_n, eps_list)
                      for _, s in sites.iterrows()}
    for key, r in [("全案", out["全案"])] + list(out.get("逐案場", {}).items()):
        lines = []
        for k, v in r["聚落"].items():
            lines.append(f"{k}：{v['名稱'] or '無'}"
                         + (f" {v['門牌數']} 門牌、{v['最短距離_m']:,.1f}m"
                            if v["最短距離_m"] is not None else " 查無達標聚落"))
        log.append(f"    {key}：{dist:,.0f}m 內門牌 {r['範圍內門牌數_僅供參考']} 筆"
                   f"（最近單一門牌 {r['最近單一門牌_m']}m，僅供參考，非判定依據）")
        log.append(f"      最近之「{min_n} 戶以上聚落」→ " + "；".join(lines))
        log.append(f"      → {r['判定']}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true", help="下載該縣市門牌資料後結束")
    ap.add_argument("--county", default=None, help="縣市（下載必填；判定時可由基地屬性取得）")
    ap.add_argument("--joined", default=None, help="基地 shp（②的面積join）")
    ap.add_argument("--sites", default=None, help="案場面 shp（有則逐案場判定）")
    ap.add_argument("--addr", default=None, help="門牌 CSV 路徑（預設 門牌坐標\\<縣市>.csv）")
    ap.add_argument("--dist", type=float, default=200, help="法定距離門檻，預設 200 公尺")
    ap.add_argument("--min-households", type=int, default=10, help="法定戶數門檻，預設 10")
    ap.add_argument("--cluster", type=float, nargs="+", default=[30, 50, 100],
                    help="聚落群聚門檻（公尺，可多個做敏感度）")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    if a.download:
        if not a.county:
            raise SystemExit("--download 需搭配 --county")
        download_county(a.county)
        return 0
    if not a.joined:
        raise SystemExit("需要 --joined（或用 --download）")

    site = gpd.read_file(a.joined, engine="pyogrio")
    if site.crs and site.crs.to_epsg() != 3826:
        site = site.to_crs(3826)
    su = site.geometry.union_all()
    sites = gpd.read_file(a.sites, engine="pyogrio") if a.sites else None
    county = a.county
    if not county:
        for c in ("縣市名稱", "縣市"):
            if c in site.columns:
                county = str(site.iloc[0][c] or "").strip()
                break
    if not county:
        raise SystemExit("基地 shp 無縣市欄，請用 --county 指定")

    log = [f"聚落判定（{county}）　法定門檻：{a.min_households} 戶以上聚落 > {a.dist:,.0f} 公尺",
           "判定法：門牌點聚類→群內門牌數達門檻者才算聚落→量基地到該聚落最短距離", ""]
    res = check(su, sites, county, a.dist, a.min_households, a.cluster, log, a.addr)
    log += ["", "★ 口徑：門牌坐標為戶政人工點位、非精確坐標，官方聲明僅供參考；"
                "門牌數≠戶數（公寓多戶共一門牌、廟宇工廠倉庫有門牌無人居住）。",
            "  本結果為篩選與初判，法定認定仍以戶政事務所／區公所為準；"
            "臨界值須現地或戶政查證。"]
    txt = "\n".join(log)
    print(txt)
    out_dir = a.out or os.path.dirname(os.path.dirname(os.path.abspath(a.joined)))
    io.open(os.path.join(out_dir, "聚落檢核.txt"), "w", encoding="utf-8").write(txt)
    io.open(os.path.join(out_dir, "聚落檢核.json"), "w", encoding="utf-8").write(
        json.dumps(res, ensure_ascii=False, indent=1))
    print(f"\n→ {os.path.join(out_dir, '聚落檢核.txt')} / .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
