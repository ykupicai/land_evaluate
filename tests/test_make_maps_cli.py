import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from make_maps_cli import parse_args


@pytest.mark.parametrize('flag,value', [
    ('cases', 'S001, S002'), ('exclude', 'S003'), ('themes', '使用分區, 權屬'),
    ('trust-shp', 'C:/案件 空間/基地.shp'), ('sitemap', 'a.csv'),
    ('basemap-dir', 'C:/底圖 空間'), ('urban-shp', '都市.shp'),
    ('layout', 'maxmap'), ('road-margin', '0'), ('urban-image', '臺北市'),
    ('base-line-themes', '權屬'), ('no-neighbor-themes', '使用分區'),
    ('no-landno-themes', '國土功能分區'), ('parcel-line-themes', '權屬'),
])
def test_value_flags_accept_both_forms(flag, value):
    assert vars(parse_args(['測試案', f'--{flag}={value}'])) == vars(
        parse_args(['測試案', f'--{flag}', value]))


@pytest.mark.parametrize('args', [
    ['--road-mar', '0'], ['--typo'], ['--basemap-dir'], ['--road-margin', 'bad'],
    ['--layout', 'unknown'], ['案名', '多餘案名'],
])
def test_invalid_flags_fail_before_any_output(args):
    with pytest.raises(SystemExit):
        parse_args(args)


def test_defaults_and_comma_sets():
    a = parse_args(['案', '--cases', ' S001,,S001,S002 ', '--road-margin', '0'])
    assert a.cases == {'S001', 'S002'}
    assert a.road_margin == 0
    assert a.layout == 'compact' and not a.no_qgz
    assert a.themes is None and a.urban_image is None


def test_urban_image_auto_and_last_switch_wins():
    a = parse_args(['案', '--urban-image', '--zoning-wmts', '--zoning-local',
                    '--loc-parcel', '--no-loc-parcel'])
    assert a.urban_image == 'auto'
    assert not a.zoning_wmts and not a.show_loc_parcel
    assert parse_args(['案', '--zoning-local', '--zoning-wmts']).zoning_wmts


@pytest.mark.parametrize('flag', ['no-qgz', 'mem', 'reuse-basemap', 'skip-extract',
                               'extract-only', 'overview-only', 'basic'])
def test_switches(flag):
    assert getattr(parse_args(['案', '--' + flag]), flag.replace('-', '_'))


def test_new_switches_default_off_and_parse():
    a = parse_args(['案'])
    assert not a.force_qgz and not a.area_label_total
    a = parse_args(['案', '--force-qgz', '--area-label-total'])
    assert a.force_qgz and a.area_label_total


ALL = ['a', 'b', 'c', 'd', 'e', 'f', 'g']


def test_is_partial_run():
    from make_maps_cli import is_partial_run
    assert not is_partial_run(ALL, ALL)
    assert not is_partial_run(list(reversed(ALL)), ALL)
    assert is_partial_run(ALL[:4], ALL)
    assert is_partial_run(ALL, ALL, cases={'S001'})
    assert is_partial_run(ALL, ALL, exclude={'S002'})


def test_qgz_target_partial_does_not_overwrite_main(tmp_path):
    from make_maps_cli import qgz_target
    (tmp_path / '案_maxmap.qgz').write_bytes(b'x')      # 主檔已存在才改寫局部檔
    p, note = qgz_target(tmp_path, '案_maxmap', partial=True)
    assert p.name == '案_maxmap_局部.qgz' and note and '--force-qgz' in note
    p, note = qgz_target(tmp_path, '案_maxmap', partial=True, force=True)
    assert p.name == '案_maxmap.qgz' and note
    p, note = qgz_target(tmp_path, '案_maxmap', partial=False)
    assert p.name == '案_maxmap.qgz' and note is None

# ---- qgz 目標決策（局部重出）----
import ast
from make_maps_cli import is_partial_run, qgz_target, selected_themes

ALL = ['使用分區', '使用地編定', '權屬', '國土功能分區', '正射', '國土利用', '臨路判定']


def test_qgz_target_four_combinations(tmp_path):
    main = tmp_path / "案_maxmap.qgz"
    part = tmp_path / "案_maxmap_局部.qgz"
    # 非局部：一律主檔、無說明
    assert qgz_target(tmp_path, "案_maxmap", False, False, main_exists=True) == (main, None)
    assert qgz_target(tmp_path, "案_maxmap", False, False, main_exists=False) == (main, None)
    # 局部＋主檔不存在：照寫主檔（舊流程 --exclude 首跑要有主 qgz），並印說明
    p, note = qgz_target(tmp_path, "案_maxmap", True, False, main_exists=False)
    assert p == main and note
    # 局部＋主檔存在：改寫局部檔
    p, note = qgz_target(tmp_path, "案_maxmap", True, False, main_exists=True)
    assert p == part and "--force-qgz" in note
    # 局部＋主檔存在＋force：覆寫主檔
    p, note = qgz_target(tmp_path, "案_maxmap", True, True, main_exists=True)
    assert p == main and note


def test_qgz_target_checks_filesystem_when_unspecified(tmp_path):
    assert qgz_target(tmp_path, "案", True)[0] == tmp_path / "案.qgz"
    (tmp_path / "案.qgz").write_bytes(b"x")
    assert qgz_target(tmp_path, "案", True)[0] == tmp_path / "案_局部.qgz"


def test_partial_wiring_basic_themes_cases_exclude():
    assert selected_themes(False, None, ALL) == ALL
    assert not is_partial_run(selected_themes(False, None, ALL), ALL)
    # --basic 取前 4 個 → 局部
    assert selected_themes(True, None, ALL) == ALL[:4]
    assert is_partial_run(selected_themes(True, None, ALL), ALL)
    assert is_partial_run(ALL[:4], ALL)
    # --themes 子集；--themes 給全集不算局部
    assert is_partial_run(selected_themes(False, {'權屬'}, ALL), ALL)
    assert not is_partial_run(selected_themes(False, set(ALL), ALL), ALL)
    # --cases／--exclude
    assert is_partial_run(ALL, ALL, cases={'S001'})
    assert is_partial_run(ALL, ALL, exclude={'S002'})
    assert not is_partial_run(ALL, ALL, cases=None, exclude=set())


def test_make_maps_wires_partial_logic():
    """接線（grep 式）：make_maps 必須用 selected_themes 與 is_partial_run(themes, THEMES, only, exclude) 決定 qgz。"""
    src = (Path(__file__).resolve().parents[1] / 'scripts' / 'make_maps.py').read_text(encoding='utf-8')
    assert 'selected_themes(a.basic, a.themes, THEMES)' in src
    assert 'is_partial_run(themes, THEMES, only, exclude)' in src
    assert 'qgz_target(' in src and 'a.force_qgz' in src


def _load_road_status_color():
    src = (Path(__file__).resolve().parents[1] / 'scripts' / 'make_maps.py').read_text(encoding='utf-8')
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == 'road_status_color':
            ns = {}
            exec(compile(ast.Module([node], []), 'x', 'exec'), ns)
            return ns['road_status_color']
    raise AssertionError('road_status_color 不存在')


def test_road_status_color_rerun_is_alert_not_grey():
    f = _load_road_status_color()
    grey = (120, 120, 120)
    assert f("須重跑(國土利用圖磚缺漏)") not in (grey,) and f("須重跑") == f("須重跑(國土利用圖磚缺漏)")
    assert f("直接臨路") == (0, 150, 0) and f("不臨路") == (200, 0, 0)
    assert f("需搭橋") == (255, 140, 0) and f("") == grey


def test_run_case_passthru_accepts_force_qgz_and_area_label_flags(monkeypatch):
    import run_case
    for argv in (['--force-qgz'], ['--area-label-total'],
                 ['--area-label-themes', '權屬'], ['--area-label-themes=權屬'],
                 ['--themes', '權屬'], ['--exclude', 'S001']):
        monkeypatch.setattr(sys, 'argv', ['run_case.py'] + argv)
        with pytest.raises(SystemExit) as e:
            run_case.main()
        assert '未知參數' not in str(e.value.code), argv   # 落到「缺輸入」而非未知參數
