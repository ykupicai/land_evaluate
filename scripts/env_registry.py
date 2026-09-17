# -*- coding: utf-8 -*-
"""環境敏感圖層登錄表與解析 —— **薄殼**（2026-08-04 單一登記表合流）。

權威已移至 skill `env-sensitivity-check` 的 `tgos_layers.py`
（單一登記表：遠端源 `LAYERS` ＋ 本機源 `LOCAL_LAYERS` 同檔）。
本檔只做兩件事，維持既有呼叫端（`make_env_map.py`、`tests/test_env_registry.py`）不變：
  1. 把本機 `env_root` 綁定為本專案 `環境敏感相關/`（skill 端相對路徑靠這個落地）。
  2. 用原名 re-export：`REGISTRY / resolve_layer / resolve_case_override /
     DEFAULT_ALPHA / GENERIC_PALETTE / FLOOD_RGB …`。

為什麼搬到 skill：環敏「遠端現行源（免登入）」與「本機圖資快照」原本分兩份登記表
（skill tgos_layers 只有遠端、本檔只有本機），新增一個圖層要先猜放哪、易漏對帳。
合流後單一權威、可攜（本機列用相對路徑，env_root=None 時自動略過）。
搬移設計與可攜性見 tgos_layers.py 檔頭與 CLAUDE.md「環敏」節。

★ NR23 一課（自然保留區必須解析到 1152 新版）之斷言仍在 tests/test_env_registry.py，
  透過本薄殼跑到 skill 的 LOCAL_LAYERS。
"""
from __future__ import annotations

from config_loader import skill_path

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_ROOT = REPO_ROOT / "環境敏感相關"

_SKILL = skill_path("env-sensitivity-check", "scripts")
if str(_SKILL) not in sys.path:
    sys.path.insert(0, str(_SKILL))

try:
    from tgos_layers import (  # noqa: E402
        LOCAL_LAYERS as REGISTRY,
        resolve_by_name,
        resolve_case_override,
        _norm_county,
        FLOOD_RGB,
        FLOOD_LABELS,
        GENERIC_RGB,
        GENERIC_PALETTE,
        DEFAULT_ALPHA,
    )
except ImportError as e:  # pragma: no cover - 環境缺 skill 時給明確訊息
    raise ImportError(
        "env_registry 已是薄殼，權威在 skill env-sensitivity-check 的 tgos_layers（單一登記表）。"
        f"找不到：{_SKILL}\n原始錯誤：{e}") from e

__all__ = ["REGISTRY", "resolve_layer", "resolve_case_override", "_norm_county",
           "FLOOD_RGB", "FLOOD_LABELS", "GENERIC_RGB", "GENERIC_PALETTE",
           "DEFAULT_ALPHA", "ENV_ROOT", "REPO_ROOT"]


def resolve_layer(layer_name, county, case_dir=None, env_root=ENV_ROOT):
    """平台套疊表「圖層名稱」 → (本機路徑, 規則)。

    介面與搬移前完全相同；`env_root` 預設本專案 `環境敏感相關/`。
    優先序：案件層 `<案>/環敏/` 覆蓋檔 > 本機 env_root 下的 shp（見 skill resolve_by_name）。
    """
    return resolve_by_name(layer_name, county, env_root=env_root, case_dir=case_dir)
