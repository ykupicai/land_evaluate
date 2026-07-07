# -*- coding: utf-8 -*-
"""
掃描 20 縣市地籍 shp 的屬性表，彙整「現行段/小段索引」→ config/section_index.csv。

背景：段藉.shp（15,385 筆）實測為**舊制段名**索引，缺重測後新段名
（例：新北市深坑區「賴仲段」在地籍 shp 有段碼 0869，段藉.shp 卻查無，
段藉裡只有舊名「土庫段賴仲坑小段」）。地籍比對（match_parcels）本來就
是對地籍 shp 篩宗地，因此段名→段碼的權威來源改為地籍 shp 本身；
段藉.shp 僅供舊段名交叉參考。

執行：python -X utf8 scripts/build_section_index.py（圖資更新後重跑）
輸出欄位：縣市, 鄉鎮, 段, 小段, 段碼, 小段碼
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pandas as pd
import pyogrio
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_YAML = REPO_ROOT / "config" / "county_registry.yaml"
OUTPUT_CSV = REPO_ROOT / "config" / "section_index.csv"

COLUMNS = ["鄉鎮", "段", "小段", "段碼", "小段碼"]


def main() -> None:
    with REGISTRY_YAML.open(encoding="utf-8") as f:
        registry = yaml.safe_load(f)

    frames: list[pd.DataFrame] = []
    for county, data in registry["counties"].items():
        shp_rel = data.get("cadastral")
        if not shp_rel:
            continue
        shp_path = REPO_ROOT / shp_rel
        with warnings.catch_warnings():
            # 部分罕用字 Big5→UTF-8 轉換警告，不影響段碼比對
            warnings.filterwarnings("ignore", category=RuntimeWarning)
            df = pyogrio.read_dataframe(
                shp_path, read_geometry=False, columns=COLUMNS
            )
        uniq = df.drop_duplicates().reset_index(drop=True)
        uniq.insert(0, "縣市", county)
        frames.append(uniq)
        print(f"{county}: 宗地 {len(df):>9,} 筆 → 段/小段 {len(uniq):>6,} 筆", flush=True)

    result = pd.concat(frames, ignore_index=True)
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n合計 {len(result):,} 筆，已寫入 {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
