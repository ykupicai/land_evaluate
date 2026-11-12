"""可獨立測試的出圖參數解析；不載入 QGIS 或 GDAL。"""
import argparse


def comma_set(value):
    return {item.strip() for item in value.split(',') if item.strip()}


def is_partial_run(themes, all_themes, cases=None, exclude=None) -> bool:
    """本次是否為局部重出：主題不是完整集合，或有 --cases／--exclude。"""
    return set(themes) != set(all_themes) or bool(cases) or bool(exclude)


def selected_themes(basic, themes_filter, all_themes):
    """--basic 取前 4 個主題，再以 --themes 過濾；make_maps 與測試共用。"""
    themes = list(all_themes[:4] if basic else all_themes)
    if themes_filter:
        themes = [t for t in themes if t in themes_filter]
    return themes


def qgz_target(out_dir, stem, partial, force=False, main_exists=None):
    """決定 qgz 寫到哪。回傳 (路徑, 說明或 None)。

    局部重出時 project 只含本次版面，覆寫既有主 qgz 會讓歸檔檔殘缺（2026-10-19 湖口案實踩）。
    ‧ 非局部 → 主 qgz。
    ‧ 局部且主 qgz 尚不存在 → 照常寫主 qgz（否則歸檔／簡報前「存 QGZ」找不到主檔），印說明。
    ‧ 局部且主 qgz 已存在 → 預設改寫 <stem>_局部.qgz；--force-qgz 才覆寫主檔。
    main_exists 為 None 時查檔案系統（測試可直接傳入）。
    """
    from pathlib import Path
    out_dir = Path(out_dir)
    main = out_dir / f"{stem}.qgz"
    if not partial:
        return main, None
    if main_exists is None:
        main_exists = main.exists()
    if not main_exists:
        return main, (f"局部重出但主 qgz 尚不存在：本次照常寫入 {main.name}"
                      f"（內容僅含本次版面，之後請不帶 --themes／--cases／--exclude 完整重出一次）")
    if force:
        return main, f"局部重出＋--force-qgz：覆寫主 qgz {main.name}（內容僅含本次版面）"
    return (out_dir / f"{stem}_局部.qgz",
            f"局部重出（--themes／--cases／--exclude／--basic）：不覆寫既有主 qgz，"
            f"改寫 {stem}_局部.qgz；要覆寫主檔請加 --force-qgz")


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
                 'no-landno-themes', 'parcel-line-themes', 'area-label-themes'):
        parser.add_argument('--' + name, type=comma_set, default=set())
    for name in ('trust-shp', 'sitemap', 'basemap-dir', 'urban-shp'):
        parser.add_argument('--' + name)
    for name in ('no-qgz', 'mem', 'reuse-basemap', 'skip-extract',
                 'extract-only', 'overview-only', 'basic', 'area-label-total', 'force-qgz'):
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
