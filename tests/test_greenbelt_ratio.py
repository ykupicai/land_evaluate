"""麻豆兩案場實際交付黃金值；WKT 固定，不依賴本機案件或網路。"""
import json
from pathlib import Path
import sys

import geopandas as gpd
import pytest
from shapely import wkt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import greenbelt_ratio as gb

CASES = json.loads((Path(__file__).parent / 'fixtures/greenbelt_madou.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['site'])
def test_cli_golden_and_geometry_partition(case, tmp_path, monkeypatch):
    base = wkt.loads(case['base'])
    joined, neighbors = tmp_path / 'base.gpkg', tmp_path / 'neighbors.gpkg'
    gpd.GeoDataFrame({'地號': ['1']}, geometry=[base], crs=3826).to_file(joined)
    gpd.GeoDataFrame({'使類': [r['kind'] for r in case['neighbors']]},
                     geometry=[wkt.loads(r['wkt']) for r in case['neighbors']],
                     crs=3826).to_file(neighbors)
    monkeypatch.setattr(sys, 'argv', ['greenbelt_ratio.py', '--joined', str(joined),
        '--neighbours', str(neighbors), '--only-on', '農業用地', '--out', str(tmp_path)])
    gb.main()
    result = json.loads((tmp_path / '綠帶計算.json').read_text(encoding='utf-8'))
    for key in ('基地_m2', '隔離綠帶或設施_m2', '加寬距離_m', '可配置範圍_m2', '占基地_pct'):
        assert result[key] == pytest.approx(case['expected'][key], abs=0.02)
    assert result['寬度檢核']['真不足點數'] == 0
    belt = gpd.read_file(tmp_path / '隔離綠帶/隔離綠帶或設施.shp').geometry.union_all()
    inner = gpd.read_file(tmp_path / '隔離綠帶/可配置範圍.shp').geometry.union_all()
    assert belt.area >= 0.3 * base.area - 0.01
    assert belt.intersection(inner).area < 0.01
    assert belt.union(inner).symmetric_difference(base).area < 0.01
