"""make_env_map 的純邏輯（讀 csv、案場過濾、檔名）；不依賴 QGIS，可在系統 python 單測。"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

# 預設環敏 csv 的搜尋順序：①b 平台版 → ⓪b NGIS 正式程序版
ENV_CSV_CANDIDATES = ("環境敏感套疊.csv", "環敏NGIS.csv")


def pick_env_csv(dirs, explicit=None):
    """回傳 (路徑, 說明)。explicit 優先；否則依 dirs（先後）× 候選檔名順序找第一份存在者。

    找不到回 (None, 錯誤說明)。
    """
    if explicit:
        p = Path(explicit)
        return (p, f"指定 {p}") if p.exists() else (None, f"找不到 {p}")
    seen = []
    for d in dirs:
        d = Path(d)
        if d in seen:
            continue
        seen.append(d)
        for name in ENV_CSV_CANDIDATES:
            p = d / name
            if p.exists():
                return p, f"採用 {p}"
    tried = "、".join(str(d / n) for d in seen for n in ENV_CSV_CANDIDATES)
    return None, f"找不到環敏 csv（已找：{tried}）——請先跑 ⓪b ngis_sensitive.py 或①b"


def _rows(path):
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def has_site_column(path) -> bool:
    rows = _rows(path)
    return bool(rows) and "案場" in rows[0]


def filter_site_rows(rows, site=None):
    """site 為 None 不過濾；給定時只留「案場」欄等於該 ID 的列（無此欄則不過濾）。"""
    if not site or not rows or "案場" not in rows[0]:
        return list(rows)
    return [r for r in rows if str(r.get("案場") or "").strip() == site]


def _hit_area(r):
    v = (r.get("套疊面積_m2") or r.get("重疊面積_m2") or "").strip()
    try:
        return float(v)
    except ValueError:
        return 0.0


def read_hits_ex(path, site=None):
    """→ (hits[(圖層名, 面積)], n_total, warnings)。

    ‧給 site：只留該案場列；過濾後 0 列但原本有列 → 警告並列出 csv 內實際的案場值。
    ‧不給 site 且「案場」欄有多個不同值（--per-site 產物）：同一圖層會在各案場重複，
      改依圖層名去重取最大面積，並警告建議加 --site。
    """
    all_rows = _rows(path)
    warnings = []
    rows = filter_site_rows(all_rows, site)
    has_col = bool(all_rows) and "案場" in all_rows[0]
    if site and has_col and all_rows and not rows:
        vals = sorted({str(r.get("案場") or "").strip() for r in all_rows})
        warnings.append(f"--site {site} 在 {Path(path).name} 過濾後 0 項；csv 內實際的案場值："
                        f"{'、'.join(vals) or '(空白)'}（可能是整份基地版 csv，請確認案場ID）")
    multi = False
    if not site and has_col:
        multi = len({str(r.get("案場") or "").strip() for r in all_rows}) > 1
        if multi:
            warnings.append("多案場 csv（「案場」欄有多個值）且未給 --site：同圖層依名稱去重取最大面積，"
                            "建議加 --site 逐案場出圖")
    hits = []
    for r in rows:
        area = _hit_area(r)
        if area > 0:
            hits.append((r["圖層名稱"], area))
    if multi:
        best = {}
        for n, ar in hits:
            best[n] = max(ar, best.get(n, 0.0))
        hits = list(best.items())
        n_total = len({r.get("圖層名稱") for r in rows})
    else:
        n_total = len(rows)
    return hits, n_total, warnings


def read_hits(path, site=None):
    """→ (hits[(圖層名, 面積)], n_total)。面積>0 才算命中；n_total 為過濾後的列數。

    兩種來源欄名不同：①b 寫「套疊面積_m2」、⓪b NGIS 寫「重疊面積_m2」。
    （警告與多案場去重見 read_hits_ex；本函式維持舊行為供既有呼叫端使用。）
    """
    rows = filter_site_rows(_rows(path), site)
    hits = [(r["圖層名稱"], _hit_area(r)) for r in rows if _hit_area(r) > 0]
    return hits, len(rows)


def strip_section_code(seg) -> str:
    """「[0283]港墘厝段」→「港墘厝段」。"""
    return re.sub(r"^\[\d+\]", "", str(seg or "")).strip()


def load_site_keys(sitemap_path, site):
    """sitemap.csv → 該案場的 {(段, 地號)} 集合；段已去代碼前綴，無段欄時段為 ""。"""
    keys = set()
    for r in _rows(sitemap_path):
        sid = str(r.get("案場ID") or r.get("site_id") or "").strip()
        if sid != site:
            continue
        ln = str(r.get("地號") or r.get("landno") or "").strip()
        if ln:
            keys.add((strip_section_code(r.get("段")), ln))
    return keys


def in_site(keys, seg, landno) -> bool:
    """宗地是否屬該案場。sitemap 有段時（段, 地號）都要符合；sitemap 無段（""）時只比地號。"""
    ln = str(landno or "").strip()
    sg = strip_section_code(seg)
    return (sg, ln) in keys or ("", ln) in keys


def env_png_name(case, site=None) -> str:
    """不給 site 維持舊檔名 001_<案>_環境敏感.png；給了就帶案場 ID。"""
    return f"001_{case}_{site}_環境敏感.png" if site else f"001_{case}_環境敏感.png"


def env_stats_name(site=None) -> str:
    return f"環境敏感套疊_逐類面積_{site}.csv" if site else "環境敏感套疊_逐類面積.csv"
