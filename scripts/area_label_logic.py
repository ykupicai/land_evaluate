"""基地面積標註的純邏輯（不依賴 QGIS／geopandas，可在系統 python 單測）。

make_maps.registered_area_index／site_area_labels 只做 QGIS 端的薄呼叫，
地號去重、面積加總、文字組裝都在這裡。
"""
from __future__ import annotations

# 登記面積欄名清單。與 persite_stats._REG_COLS 同步（該模組頂層 import geopandas，
# 在 QGIS 內建 python 內不一定載得到，故複製一份；tests/test_area_label_logic.py 有對齊斷言）。
REG_COLS = ("登面積", "登記面積", "標示面積")
LANDNO_COLS = ("地號", "20251201_4")


def pick_field(names, candidates):
    """依候選順序回傳第一個存在於 names 的欄名，沒有則 None。"""
    return next((n for n in candidates if n in names), None)


def build_registered_index(rows) -> dict:
    """rows＝[(地號, 原始登記面積值), …] → {地號: 登記面積}。

    僅留正值；**同一地號出現兩次以上（跨段同號）一律剔除**，避免誤配。
    ⚠ 「出現過」要在解析面積之前就記錄：先出現的那筆面積空白或 0 時，
    若不留紀錄，後出現的正值會被誤當唯一而保留（與順序有關，2026-11 審查）。
    """
    out, seen, dup = {}, set(), set()
    for landno, raw in rows:
        k = str(landno).strip()
        if k in seen:
            dup.add(k)
        seen.add(k)
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out[k] = v
    for k in dup:
        out.pop(k, None)
    return out


def sum_registered(entries, reg_idx: dict):
    """entries＝[(地號, 圖面積), …] → (合計, 以圖面積計的筆數, 總筆數)。

    逐筆取登記面積、取不到才退回圖面積（登記面積優先）。
    """
    total, n_geo, n = 0.0, 0, 0
    for landno, geo_area in entries:
        n += 1
        k = str(landno).strip()
        if k in reg_idx:
            total += reg_idx[k]
        else:
            total += geo_area
            n_geo += 1
    return total, n_geo, n


def area_label_text(total: float, n_geo: int, n: int, *, total_mode: bool = False,
                    force_total: bool = False, n_total_parts: int = 1) -> str:
    txt = f"基地面積\n{total:,.2f} ㎡\n（{total / 10000:.4f} 公頃）"
    if n_geo:
        txt += "\n（含圖面積計算）" if n_geo < n else "\n（圖面積）"
    if total_mode and not force_total:
        txt = txt.replace("基地面積", f"基地面積合計（{n_total_parts}處）", 1)
    return txt


def is_total_mode(n_parts: int, force_total: bool, max_parts: int) -> bool:
    return bool(force_total) or n_parts > max_parts
