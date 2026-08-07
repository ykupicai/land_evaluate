# -*- coding: utf-8 -*-
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from config_loader import (DEFAULT_PATHS, load_paths, luimap_colortable_path,
                           qgis_python_bat)


def test_load_paths_missing_file_uses_defaults(tmp_path):
    assert load_paths(tmp_path) == DEFAULT_PATHS


def test_load_paths_overrides_nonempty_values_and_keeps_empty_default(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "paths.yaml").write_text(
        "qgis_prefix: D:\\QGIS\\apps\\qgis-ltr\nluimap_colortable: ''\n",
        encoding="utf-8",
    )
    paths = load_paths(tmp_path)
    assert paths["qgis_prefix"] == r"D:\QGIS\apps\qgis-ltr"
    assert paths["luimap_colortable"] == ""


def test_load_paths_bad_yaml_warns_and_uses_defaults(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "paths.yaml").write_text("- not\n- a mapping\n", encoding="utf-8")
    warnings = []
    assert load_paths(tmp_path, warnings.append) == DEFAULT_PATHS
    assert "最上層必須是鍵值對" in warnings[0]


def test_path_helpers(tmp_path):
    assert luimap_colortable_path(tmp_path, DEFAULT_PATHS) == (
        tmp_path / "config" / "luimap_colortable.csv"
    )
    custom = {**DEFAULT_PATHS, "luimap_colortable": r"D:\data\colors.csv"}
    assert luimap_colortable_path(tmp_path, custom) == Path(r"D:\data\colors.csv")
    assert qgis_python_bat({"qgis_prefix": r"C:\QGIS\apps\qgis-ltr"}) == Path(
        r"C:\QGIS\bin\python-qgis-ltr.bat"
    )
