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
