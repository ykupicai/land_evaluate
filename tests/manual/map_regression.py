"""在 outputs/_maintenance 的案件副本重出七主題；原案件僅讀取。

系統 Python 執行 --phase baseline 或 candidate；需本機 QGIS 與兩份既有案件。
baseline 固定讀取整理前 commit；candidate 讀工作樹。不屬於一般 pytest。
"""
import argparse
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
CASES = ('20260827_麻豆客子寮段256', '20260910_七股新生段14光電')
AUDIT = REPO / 'outputs' / '_maintenance' / '20260916'


def child(phase, case, candidate_source=None):
    source = REPO / 'scripts' / 'make_maps.py'
    code = ((AUDIT / 'make_maps_before.py').read_text(encoding='utf-8')
            if phase == 'baseline' else (candidate_source or source).read_text(encoding='utf-8'))
    ns = {'__name__': 'regression_target', '__file__': str(source)}
    exec(compile(code, str(source), 'exec'), ns)
    # 只略過生成到共用 styles/ 的副產物，渲染和成果清單仍走原函式。
    ns['save_styles'] = lambda *args: None
    target = REPO / 'outputs' / case
    sys.argv = ['make_maps.py', case, '--trust-shp', str(target / 'cadastre' / '基地.shp'),
                '--skip-extract', '--layout', 'maxmap', '--reuse-basemap', '--road-margin=0']
    ns['main']()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--phase', choices=('baseline', 'candidate'), required=True)
    ap.add_argument('--child')
    ap.add_argument('--candidate-source', type=Path,
                    help='僅測候選程式，仍以正式 scripts 目錄解析相依路徑')
    ap.add_argument('--require-clean-exit', action='store_true',
                    help='不接受歷史收尾異常碼；修復驗收必須加此旗標')
    a = ap.parse_args()
    if a.child:
        child(a.phase, a.child, a.candidate_source)
        return
    from config_loader import load_paths, qgis_python_bat
    AUDIT.mkdir(parents=True, exist_ok=True)
    if a.phase == 'baseline':
        code = subprocess.check_output(['git', 'show', '6e7573b:scripts/make_maps.py'], cwd=REPO)
        (AUDIT / 'make_maps_before.py').write_bytes(code)
    report = []
    for index, original in enumerate(CASES):
        case = f'_maintenance_20260916_{index}'
        dest = REPO / 'outputs' / case
        if not dest.exists():
            shutil.copytree(REPO / 'outputs' / original, dest)
        csv_path = dest / '05_出圖清單_maxmap.csv'
        previous = csv_path.stat().st_mtime_ns if csv_path.exists() else None
        log = AUDIT / f'{a.phase}_{index}.log'
        with log.open('w', encoding='utf-8') as stream:
            result = subprocess.run([str(qgis_python_bat(load_paths(REPO))), '-X', 'utf8',
                                     str(Path(__file__).resolve()), '--phase', a.phase,
                                     '--child', case] +
                                    (['--candidate-source', str(a.candidate_source.resolve())]
                                     if a.candidate_source else []), cwd=REPO, stdout=stream,
                                    stderr=subprocess.STDOUT,
                                    # Windows 使用原生 Qt 字型後端；offscreen 會把中文字畫成黑方塊。
                                    env=(dict(os.environ) if os.name == 'nt' else
                                         {**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}), timeout=600)
        if not csv_path.exists() or csv_path.stat().st_mtime_ns == previous:
            raise RuntimeError(f'未產生新清單：{case}，請看 {log}')
        with csv_path.open(encoding='utf-8-sig', newline='') as stream:
            rows = [{k: v for k, v in r.items() if k != '產出時間'} for r in csv.DictReader(stream)]
        if len(rows) != 7 or any(r['狀態'] != '成功' for r in rows):
            raise RuntimeError(f'七主題未全成功：{rows}')
        # 整理前基準可記錄歷史異常；候選程式必須正常退出，不能讓收尾缺陷回歸。
        allowed = (0, 5, 3221225477) if a.phase == 'baseline' and not a.require_clean_exit else (0,)
        if result.returncode not in allowed:
            raise RuntimeError(f'出圖失敗 {result.returncode}，請看 {log}')
        golden = AUDIT / f'baseline_{index}.json'
        if a.phase == 'baseline':
            golden.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        else:
            assert rows == json.loads(golden.read_text(encoding='utf-8')), f'{case} 清單不同'
        report.append({'case': original, 'rows': len(rows), 'returncode': result.returncode})
        print(json.dumps(report[-1], ensure_ascii=False), flush=True)
    (AUDIT / f'{a.phase}_result.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
