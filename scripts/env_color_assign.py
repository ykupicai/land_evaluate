# -*- coding: utf-8 -*-
"""環敏套疊圖的圖層配色邏輯 —— 純模組（無 QGIS 依賴，可單測）。

抽出理由與 env_registry.py 同型：`make_env_map.py` 開頭就 `from qgis.core import ...`，
整支在系統 python（無 osgeo）下無法 import，邏輯本身若留在該檔就測不到。這裡只放
純 tuple/dict 運算的配色演算法，QGIS 端（`make_env_map.load_env_layer`）只管拿配好的
顏色去畫圖層，不再自己決定顏色。
"""
from __future__ import annotations


def assign_layer_colors(resolved: list, palette: list) -> dict:
    """幫每個命中圖層配一個**互不相同**的代表色（key＝圖層名稱）。

    `resolved`：`[(name, shp_or_None, rule_or_None), ...]`（`rule` 可含 `"rgb"` 語意色）。
    `palette`：無語意色圖層依疊圖順序取色的色環（如 `env_registry.GENERIC_PALETTE`）。

    ★2026-09-14 內湖保護區案實錯：山坡地規則層指定 `rgb=(148,103,189)`，這個值
    剛好與色環第 3 格完全相同；捷運禁限建沒有指定色、依疊圖順序（第3個命中項＝
    索引2）落到同一格，兩層因此撞成一模一樣的紫色，圖上完全分不出誰是誰——這正是
    `make_env_map.py`〈同一張圖疊多個圖層：每層必須可區辨〉那條規則要防的事，只是
    原本的作法沒防到「語意色本身恰好等於色環某格」這種巧合。
    修法：語意色（`rule["rgb"]`）一律優先保留；**沒有語意色的圖層**依疊圖順序從
    `palette` 依序取色，但**跳過任何已被語意色或前面圖層占用的顏色**——不再單純用
    「命中順序的索引」對色環取模，因為索引與語意色兩者互不相關，才會撞色。
    """
    used = {tuple(rule["rgb"]) for _, _, rule in resolved if rule and rule.get("rgb")}
    out: dict = {}
    pool_i = 0
    for name, _shp, rule in resolved:
        if rule is None:
            continue
        if rule.get("rgb"):
            out[name] = tuple(rule["rgb"])
            continue
        color = None
        for _ in range(len(palette)):
            cand = tuple(palette[pool_i % len(palette)])
            pool_i += 1
            if cand not in used:
                color = cand
                break
        if color is None:  # 色環全被語意色占滿的極端情況：退回原順序，仍可運作
            color = tuple(palette[pool_i % len(palette)])
            pool_i += 1
        used.add(color)
        out[name] = color
    return out
