# -*- coding: utf-8 -*-
"""專案外部路徑設定的單一讀取入口。"""
from collections.abc import Callable
from pathlib import Path

import yaml


DEFAULT_PATHS = {
    "qgis_prefix": r"C:\Program Files\QGIS 3.40.12\apps\qgis-ltr",
    "luimap_colortable": "",
}


def load_paths(repo_root: Path, warn: Callable[[str], None] | None = None) -> dict:
    """讀取 config/paths.yaml；缺檔、空值或解析失敗時沿用安全預設。"""
    values = dict(DEFAULT_PATHS)
    path = Path(repo_root) / "config" / "paths.yaml"
    if not path.exists():
        return values
    try:
        configured = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(configured, dict):
            raise TypeError("最上層必須是鍵值對")
        values.update({key: value for key, value in configured.items() if value})
    except Exception as exc:  # noqa: BLE001 - 設定損壞時必須安全退回預設
        if warn:
            warn(f"config/paths.yaml 讀取失敗，改用內建預設：{exc}")
    return values


def luimap_colortable_path(repo_root: Path, paths: dict) -> Path:
    """回傳設定的 LUIMAP 色碼表，未設定時使用 repository 內建版本。"""
    configured = paths.get("luimap_colortable")
    return (Path(configured) if configured
            else Path(repo_root) / "config" / "luimap_colortable.csv")


def qgis_python_bat(paths: dict) -> Path:
    """由 .../apps/qgis-ltr 推導 QGIS 隨附的 Python 啟動器。"""
    return Path(paths["qgis_prefix"]).parent.parent / "bin" / "python-qgis-ltr.bat"
