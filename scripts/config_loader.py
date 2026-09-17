# -*- coding: utf-8 -*-
"""專案外部路徑設定的單一讀取入口。"""
from collections.abc import Callable
from pathlib import Path

import yaml


DEFAULT_PATHS = {
    "qgis_prefix": r"C:\Program Files\QGIS 3.40.12\apps\qgis-ltr",
    "luimap_colortable": "",
    "skills_root": str(Path.home() / '.claude' / 'skills'),
    "nas_gis_root": r"\\192.168.1.134\Public-TN\3. 圖資\gis圖資",
    "dem_path": "",
    "breed_shp": "",
}


def load_paths(repo_root: Path, warn: Callable[[str], None] | None = None) -> dict:
    """依預設→paths.yaml→paths.local.yaml 疊加；空值沿用、壞檔整層退回。"""
    values = dict(DEFAULT_PATHS)
    for filename in ('paths.yaml', 'paths.local.yaml'):
        path = Path(repo_root) / 'config' / filename
        if not path.exists():
            continue
        try:
            configured = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
            if not isinstance(configured, dict):
                raise TypeError('最上層必須是鍵值對')
            # 防止 YAML 把未加引號的值讀成數字/布林，延遲到 GIS 載入才崩潰。
            for key, value in configured.items():
                if key in DEFAULT_PATHS and value and not isinstance(value, str):
                    raise TypeError(f'{key} 必須是路徑字串')
            values.update({key: value for key, value in configured.items() if value})
        except Exception as exc:  # noqa: BLE001 - 壞設定沿用上一層
            if warn:
                warn(f'config/{filename} 讀取失敗，沿用預設／上一層設定：{exc}')
    return values


def luimap_colortable_path(repo_root: Path, paths: dict) -> Path:
    """回傳設定的 LUIMAP 色碼表，未設定時使用 repository 內建版本。"""
    configured = paths.get("luimap_colortable")
    return (Path(configured) if configured
            else Path(repo_root) / "config" / "luimap_colortable.csv")


def qgis_python_bat(paths: dict) -> Path:
    """由 .../apps/qgis-ltr 推導 QGIS 隨附的 Python 啟動器。"""
    return Path(paths["qgis_prefix"]).parent.parent / "bin" / "python-qgis-ltr.bat"


def resource_path(key: str, repo_root: Path | None = None, paths: dict | None = None) -> Path:
    """外部資源集中定位；相對自訂路徑以 repository 根目錄為準。"""
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parent.parent
    values = load_paths(root) if paths is None else paths
    nas = Path(values['nas_gis_root'])
    derived = {
        'dem_path': nas / 'dem地形資料/20公尺/全國DTM-20/twdtm20m_float.img',
        'breed_shp': nas / '產業相關/一級產業/養殖漁業生產區/養殖漁業生產區_114_SHP/養殖漁業生產區.shp',
    }
    path = Path(values.get(key) or derived[key]) if key in derived else Path(values[key])
    path = path.expanduser()
    return path if path.is_absolute() else root / path


def skill_path(name: str, *parts: str) -> Path:
    return resource_path('skills_root') / name / Path(*parts)
