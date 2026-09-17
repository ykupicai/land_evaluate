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


def test_local_paths_override_and_derived_resources(tmp_path):
    from config_loader import resource_path
    folder = tmp_path / 'config'
    folder.mkdir()
    (folder / 'paths.yaml').write_text('nas_gis_root: old\n', encoding='utf-8')
    (folder / 'paths.local.yaml').write_text(
        'nas_gis_root: new\nbreed_shp: data/breed.shp\n', encoding='utf-8')
    paths = load_paths(tmp_path)
    assert resource_path('breed_shp', tmp_path, paths) == tmp_path / 'data/breed.shp'
    assert resource_path('dem_path', tmp_path, paths) == (
        tmp_path / 'new/dem地形資料/20公尺/全國DTM-20/twdtm20m_float.img')


def test_invalid_override_preserves_previous_layer(tmp_path):
    folder = tmp_path / 'config'
    folder.mkdir()
    (folder / 'paths.yaml').write_text('qgis_prefix: valid\n', encoding='utf-8')
    (folder / 'paths.local.yaml').write_text('qgis_prefix: 123\n', encoding='utf-8')
    warnings = []
    assert load_paths(tmp_path, warnings.append)['qgis_prefix'] == 'valid'
    assert '字串' in warnings[0]


def test_checklist_resource_key_uses_local_override(tmp_path, monkeypatch):
    import check_zones
    folder = tmp_path / 'config'
    folder.mkdir()
    target = tmp_path / 'breed.shp'
    target.touch()
    (folder / 'paths.local.yaml').write_text('breed_shp: breed.shp\n', encoding='utf-8')
    monkeypatch.setattr(check_zones, 'REPO', str(tmp_path))
    assert check_zones._resolve_paths({'圖層': '@breed_shp'}, '臺南市') == [str(target)]


def test_county_shp_preserves_exact_name_priority(tmp_path, monkeypatch):
    from shp_trust_utils import find_county_shp
    import adjacent_landuse
    import cadastre_neighbors
    alternate = tmp_path / '台南市.shp'
    alternate.touch()
    assert find_county_shp('臺南市', tmp_path) == str(alternate)
    exact = tmp_path / '臺南市.shp'
    exact.touch()
    for module in (adjacent_landuse, cadastre_neighbors):
        monkeypatch.setattr(module, 'CAD_DIR', str(tmp_path))
        assert module._county_file('臺南市') == str(exact)
        assert module._county_file('高雄市') is None
