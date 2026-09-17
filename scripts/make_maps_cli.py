"""可獨立測試的出圖參數解析；不載入 QGIS 或 GDAL。"""
import argparse


def comma_set(value):
    return {item.strip() for item in value.split(',') if item.strip()}


class MapParser(argparse.ArgumentParser):
    def error(self, message):
        # 未知旗標不可掉進案名分支（2026-08-04 basemap-dir 誤寫目錄事故）。
        super().error('參數錯誤：' + message)


def parse_args(argv=None):
    parser = MapParser(description='基地／比對結果 → 主題圖與 QGIS 專案', allow_abbrev=False)
    parser.add_argument('case_name', nargs='?', default='20260707-清潔隊部資訊')
    for name in ('cases', 'themes'):
        parser.add_argument('--' + name, type=comma_set)
    for name in ('exclude', 'base-line-themes', 'no-neighbor-themes',
                 'no-landno-themes', 'parcel-line-themes'):
        parser.add_argument('--' + name, type=comma_set, default=set())
    for name in ('trust-shp', 'sitemap', 'basemap-dir', 'urban-shp'):
        parser.add_argument('--' + name)
    for name in ('no-qgz', 'mem', 'reuse-basemap', 'skip-extract',
                 'extract-only', 'overview-only', 'basic'):
        parser.add_argument('--' + name, action='store_true')
    parser.add_argument('--layout', choices=('compact', 'maxmap'), default='compact')
    parser.add_argument('--road-margin', type=float)
    parser.add_argument('--urban-image', nargs='?', const='auto')
    # 同時給相反旗標時保持舊版「最後一個生效」，而非互斥報錯。
    parser.add_argument('--zoning-local', dest='zoning_wmts', action='store_false')
    parser.add_argument('--zoning-wmts', dest='zoning_wmts', action='store_true')
    parser.add_argument('--no-loc-parcel', dest='show_loc_parcel', action='store_false')
    parser.add_argument('--loc-parcel', dest='show_loc_parcel', action='store_true')
    parser.set_defaults(zoning_wmts=False, show_loc_parcel=False)
    return parser.parse_args(argv)
