# -*- coding: utf-8 -*-
"""衛福部社會福利機構代碼開放資料 → EPSG:3826 點 shp（200 公尺距離條款用）

★ 建立緣由（2026-08-06 使用者提供資料源）：「與社福機構最短距離應大於200公尺」這類
  條款（如臺南市土資場自治條例§18①3）過去因無公開點位圖資，整項列為 inquiry
  （函詢社會局）——見 config/檢核清單/土資場_臺南市.yaml 的 facility_200m 項。
  衛福部開放資料平台「社會福利機構代碼」資料集含全國機構經緯度，可補上這塊。

資料來源：衛生福利部開放資料平台（opendata.mohw.gov.tw），dataset cc/165355。
  下載網址見 社福機構/.版本.txt。座標為 WGS84 十進位度（經度,緯度），本模組轉
  EPSG:3826。缺經緯度約 5.5%（502/9124，2026-08-06 快照），這些機構無法納入距離計算。

⚠★★ 主類別（T01~T09）代碼意義未取得官方字典（opendata.mohw.gov.tw 說明頁無法以
  自動化工具讀取，需人工瀏覽器查證）——以下 CATEGORY_GUESS 是**人工從機構名稱樣本
  歸納推測**，不是官方定義，套用前務必核對：
    T01 政府社政機關／服務中心（社會局、家防中心、社福服務中心等，行政/服務窗口）
    T02 大學（社工相關科系？）
    T03 法務部矯正機關（監獄、戒治所）
    T04 醫院
    T05 護理之家（機構住宿式照顧，長者/失能者實際居住）
    T06 長期照顧服務機構（樣本多為「居家式服務類」，到宅服務、非機構住宿）
    T07 托嬰中心／兒少相關機構（幼兒實際在場）
    T08 社福協會／基金會（多為行政/倡議組織，非機構本體）
    T09 大型社福基金會（混合，內含醫院等）
  ⚠ 距離限制條款保護的通常是「有脆弱族群實際在場/居住」的機構，T01/T02/T03/T08 讀起來
  較像行政或非居住性質，**是否該排除、CATEGORY_GUESS 對不對，都需要人工核對真實名冊
  後才能定案**——本模組預設輸出「初判」子集僅供比對用，不可直接當成法定判準。
  許可狀態（C01~C08）代碼意義同樣未經官方確認，本模組不做狀態篩選（全部保留），
  由使用者依 機構名稱／許可狀態 欄自行複核。

用法：
  python scripts/social_welfare_facility.py --build
      → 讀 社福機構/社福機構代碼.csv，輸出：
        社福機構/社福機構_全部.shp（完整，缺經緯度者排除）
        社福機構/社福機構_初判.shp（CATEGORY_GUESS 篩選 T05/T06/T07，僅供初篩比對）

  python scripts/social_welfare_facility.py --joined <面積join.shp> [--dist 200]
      → 直接算距離（用「初判」子集），輸出 社福機構200公尺初篩.txt/.json
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

import geopandas as gpd
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO, "社福機構")
CSV = os.path.join(DATA_DIR, "社福機構代碼.csv")
# ⚠ 存 .gpkg 不存 .shp：中文欄名（如「機構名稱」4字=12bytes）超過 DBF 10-byte 限制，
#   pyogrio 寫入時被攔腰截斷成半個 UTF-8 字元、之後連讀都讀不回來（2026-08-06 實踩）。
#   .gpkg 無此限制，是本專案既有的標準解法（見 CLAUDE.md「中文dbf欄名」教訓）。
SHP_ALL = os.path.join(DATA_DIR, "社福機構_全部.gpkg")
SHP_GUESS = os.path.join(DATA_DIR, "社福機構_初判.gpkg")

# ⚠ 人工推測，非官方字典——見上方 docstring
CATEGORY_GUESS_INCLUDE = {"T05", "T06", "T07"}


def build(log=None) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    log = log if log is not None else []
    if not os.path.exists(CSV):
        raise FileNotFoundError(f"找不到 {CSV}——先把衛福部 CSV 存到 社福機構/ 資料夾")
    df = pd.read_csv(CSV, encoding="utf-8-sig", dtype=str)
    n0 = len(df)
    df = df[df["經度"].notna() & df["緯度"].notna() & (df["經度"] != "") & (df["緯度"] != "")]
    log.append(f"讀入 {n0} 筆，缺經緯度排除後剩 {len(df)} 筆")
    df["經度"] = df["經度"].astype(float)
    df["緯度"] = df["緯度"].astype(float)
    g = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["經度"], df["緯度"]),
                         crs=4326).to_crs(3826)
    g.to_file(SHP_ALL, driver="GPKG")
    log.append(f"→ {SHP_ALL}（{len(g)} 筆，全部類別）")

    guess = g[g["主類別"].isin(CATEGORY_GUESS_INCLUDE)]
    guess.to_file(SHP_GUESS, driver="GPKG")
    log.append(f"→ {SHP_GUESS}（{len(guess)} 筆，CATEGORY_GUESS 初判子集 "
               f"{sorted(CATEGORY_GUESS_INCLUDE)}——人工推測，非官方定義，套用前務必核對）")
    return g, guess


def nearest(geom, dist: float, log: list, shp: str = SHP_GUESS) -> dict:
    """geom 為 EPSG:3826 幾何；回門檻內機構清單與最近距離（到基地面實距）。"""
    if not os.path.exists(shp):
        build(log)
    g = gpd.read_file(shp, engine="pyogrio")
    g["實距_m"] = g.geometry.distance(geom).round(1)
    g = g.sort_values("實距_m")
    within = g[g["實距_m"] <= dist]
    rec = {
        "來源": "衛福部社會福利機構代碼開放資料（初判子集，CATEGORY_GUESS 未經官方核對）",
        "門檻內": [{"名稱": r["機構名稱"], "地址": r["地址"], "主類別": r["主類別"],
                   "許可狀態": r["許可狀態"], "實距_m": float(r["實距_m"])}
                  for _, r in within.iterrows()],
        "最近_m": float(g.iloc[0]["實距_m"]) if len(g) else None,
        "最近名稱": g.iloc[0]["機構名稱"] if len(g) else None,
        "判定": "觸及" if len(within) else "未觸及",
    }
    if len(within):
        log.append(f"    ✗ 社福機構（初判）：{dist:,.0f}m 內 {len(within)} 處"
                   f"（最近 {within.iloc[0]['機構名稱']} {within.iloc[0]['實距_m']:,.1f}m）"
                   f"——CATEGORY_GUESS 未經官方核對，務必人工複核是否確屬條款所稱社福機構")
    else:
        log.append(f"    ✓ 社福機構（初判）：{dist:,.0f}m 內無（最近 {rec['最近名稱']} "
                   f"{rec['最近_m']:,.0f}m）——初判子集非官方定義，查無不代表法定認定為無")
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="重建 shp（CSV 換版後執行）")
    ap.add_argument("--joined", default=None)
    ap.add_argument("--dist", type=float, default=200)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    if a.build or not os.path.exists(SHP_GUESS):
        log = []
        build(log)
        print("\n".join(log))
        if not a.joined:
            return 0

    site = gpd.read_file(a.joined, engine="pyogrio")
    if site.crs and site.crs.to_epsg() != 3826:
        site = site.to_crs(3826)
    su = site.geometry.union_all()
    log = [f"社福機構距離初篩（門檻 {a.dist:,.0f} 公尺，衛福部開放資料 CATEGORY_GUESS 初判子集）"]
    res = nearest(su, a.dist, log)
    log.append("★ 初判子集分類未經官方字典核對，法定認定仍須函詢社會局／核對官方名冊。")
    txt = "\n".join(log)
    print(txt)
    out_dir = a.out or os.path.dirname(os.path.dirname(os.path.abspath(a.joined)))
    io.open(os.path.join(out_dir, "社福機構200公尺初篩.txt"), "w", encoding="utf-8").write(txt)
    io.open(os.path.join(out_dir, "社福機構200公尺初篩.json"), "w", encoding="utf-8").write(
        json.dumps(res, ensure_ascii=False, indent=1))
    print(f"\n→ {os.path.join(out_dir, '社福機構200公尺初篩.txt')} / .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
