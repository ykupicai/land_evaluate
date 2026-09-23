"""系統 Python 啟動獨立 QGIS 子進程，唯讀檢查真實 OGR 圖層的正常／異常退場。"""
import argparse
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))


def child(mode, source):
    import faulthandler
    faulthandler.enable()
    from qgis.core import QgsApplication, QgsProject, QgsVectorLayer
    from qgis_session import run_qgis_job

    def worker(start):
        start()
        layer = QgsVectorLayer(str(source), 'shutdown check', 'ogr')
        assert layer.isValid(), f'來源無法讀取：{source}'
        # 非 project-owned 的圖層，正是先關 QGIS 再解構時曾重現崩潰的情況。
        cycle = [layer]
        cycle.append(cycle)
        if mode == 'error':
            raise RuntimeError('expected render error')
        return  # 模擬完成與提早 return

    try:
        run_qgis_job(worker, lambda: QgsApplication([], True), lambda: QgsProject.instance().clear())
    except RuntimeError as exc:
        if mode != 'error' or str(exc) != 'expected render error':
            raise
        print('原例外保留；QGIS 收尾完成', flush=True)
    else:
        assert mode == 'normal'
        print('正常 return；QGIS 收尾完成', flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, default=REPO / 'outputs/20260827_麻豆客子寮段256/cadastre/基地.shp')
    ap.add_argument('--child', choices=('normal', 'error'))
    args = ap.parse_args()
    if args.child:
        child(args.child, args.source)
        return
    from config_loader import load_paths, qgis_env, qgis_python_bat
    for mode in ('normal', 'error'):
        result = subprocess.run([str(qgis_python_bat(load_paths(REPO))), '-X', 'utf8',
                                 str(Path(__file__).resolve()), '--child', mode,
                                 '--source', str(args.source.resolve())],
                                env=qgis_env(), timeout=60)
        print(f'{mode}: returncode={result.returncode}', flush=True)
        if result.returncode:
            raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
