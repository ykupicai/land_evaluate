"""只讀環境檢查；不安裝、不下載、不修改圖資。--qgis 可另驗 GIS 執行環境。"""
import argparse
import importlib.metadata as metadata
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from config_loader import load_paths, luimap_colortable_path, qgis_env, qgis_python_bat, resource_path

REPO = Path(__file__).resolve().parent.parent
PACKAGES = {'geopandas': 'geopandas', 'shapely': 'shapely', 'numpy': 'numpy',
            'pandas': 'pandas', 'pyogrio': 'pyogrio', 'pyproj': 'pyproj', 'PyYAML': 'yaml',
            'openpyxl': 'openpyxl', 'xlrd': 'xlrd', 'Pillow': 'PIL', 'python-pptx': 'pptx',
            'requests': 'requests', 'scipy': 'scipy', 'matplotlib': 'matplotlib',
            'dbfread': 'dbfread', 'pdfplumber': 'pdfplumber', 'PyMuPDF': 'fitz',
            'rasterio': 'rasterio'}
SKILLS = {'cadastre-check': ('api.py', 'moa.py'),
          'env-sensitivity-check': ('api.py', 'tgos_layers.py', 'govtls.py'),
          'ngis-sensitive': ('ngis.py', 'overlay.py'),
          'nluz-landzone-query': ('nluz_query.py',)}


def inspect(repo=REPO, check_qgis=False, check_nas=False):
    rows = []
    def add(name, status, detail):
        rows.append({'項目': name, '狀態': status, '說明': str(detail)})
    warnings = []
    paths = load_paths(repo, warnings.append)
    for warning in warnings:
        add('設定', 'FAIL', warning)
    add('Python', 'OK' if sys.version_info >= (3, 10) else 'FAIL', sys.version.split()[0])
    for package, module in PACKAGES.items():
        try:
            version = metadata.version(package)
            ok = importlib.util.find_spec(module) is not None
            add(package, 'OK' if ok else 'FAIL', version)
        except (metadata.PackageNotFoundError, ImportError, ValueError):
            add(package, 'FAIL', '未安裝；見 requirements-core.txt')
    for name, files in SKILLS.items():
        folder = resource_path('skills_root', repo, paths) / name / 'scripts'
        missing = [f for f in files if not (folder / f).is_file()]
        add('外部引擎 ' + name, 'FAIL' if missing else 'OK',
            str(folder) + (f' 缺少 {missing}' if missing else ''))
    for folder in ('地籍圖資', '國土功能分區', '都市計畫', '環境敏感相關',
                   '2021 國道省道以外道路', '2021全台農田水利灌排', '門牌坐標', '社福機構'):
        path = repo / folder
        ready = path.is_dir() and any(path.iterdir())
        add('本機圖資 ' + folder, 'OK' if ready else 'WARN',
            '僅驗存在且非空，版本／縣市覆蓋須按案件檢查' if ready else '缺少或空目錄，相關功能不可用')
    color = luimap_colortable_path(repo, paths)
    add('LUIMAP 色碼', 'OK' if color.is_file() else 'FAIL', color)
    for key in ('dem_path', 'breed_shp'):
        path = resource_path(key, repo, paths)
        network = str(path).startswith('\\\\')
        if network and not check_nas:
            add(key, 'SKIP', f'{path}；加 --nas 檢查網路路徑')
        else:
            add(key, 'OK' if path.is_file() else 'WARN', path)
    bat = qgis_python_bat(paths)
    add('QGIS Python', 'OK' if bat.is_file() else 'FAIL', bat)
    if check_qgis and bat.is_file():
        # ⚠ 一定要驗 gdal_array：nlsc_wmts.build_basemap 讀圖磚靠它。2026-09-16 版只驗
        #   `from osgeo import gdal`，於是 numpy 被 user-site 蓋掉時仍報「全項 OK」——
        #   而那正是會讓所有底圖靜默消失的狀況（見 config_loader.qgis_env）。
        probe = ('import json; from qgis.core import Qgis; from osgeo import gdal, gdal_array; '
                 'import numpy, geopandas, yaml, openpyxl; '
                 'print(json.dumps({"QGIS":Qgis.QGIS_VERSION,"GDAL":gdal.VersionInfo(),'
                 '"numpy":numpy.__version__}))')
        try:
            r = subprocess.run([str(bat), '-c', probe], capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=60,
                               env={**qgis_env(), 'QT_QPA_PLATFORM': 'offscreen'})
            add('QGIS import（產線環境）', 'OK' if r.returncode == 0 else 'FAIL',
                (r.stdout + r.stderr).strip()[-400:])
        except (OSError, subprocess.TimeoutExpired) as exc:
            add('QGIS import（產線環境）', 'FAIL', exc)
        add(*user_site_row(bat))
    return rows


def numpy_from_user_site(numpy_file: str, user_site: str) -> bool:
    """numpy 是否來自使用者 site-packages（純函式，可單測）。

    大小寫與斜線方向一律正規化——Windows 路徑兩者都可能不一致。
    """
    if not numpy_file or not user_site:
        return False
    norm = lambda p: str(p).replace('/', '\\').rstrip('\\').lower()  # noqa: E731
    return norm(numpy_file).startswith(norm(user_site) + '\\')


def user_site_row(bat):
    """以**未隔離**的環境（＝有人手動直接執行 .bat 的情形）查 numpy 從哪裡載入。

    產線（run_case、scripts/qgis_python.ps1）已一律隔離，故此處只報 WARN：
    提醒「手動照舊指令直接跑 .bat 會中招」，並指出根治方式。
    """
    probe = ('import json, site, numpy; print(json.dumps({"numpy": numpy.__file__, '
             '"ver": numpy.__version__, "user_site": site.getusersitepackages()}))')
    name = 'QGIS 使用者 site-packages 污染'
    try:
        r = subprocess.run([str(bat), '-c', probe], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=60,
                           env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
        info = json.loads(r.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError) as exc:
        return name, 'WARN', f'無法判定：{exc}'
    if numpy_from_user_site(info['numpy'], info['user_site']):
        return (name, 'WARN',
                f"numpy {info['ver']} 來自 {info['user_site']}（別的專案 pip --user 裝的），"
                "會蓋掉 QGIS 自帶版本。產線已隔離不受影響；但**手動直接執行 "
                "python-qgis-ltr.bat 會讓底圖靜默消失**，請改用 scripts\\qgis_python.ps1。"
                "根治＝把該專案改用 venv 後清空此資料夾")
    return name, 'OK', f"numpy {info['ver']} 為 QGIS 自帶，未被 user-site 蓋掉"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--qgis', action='store_true')
    ap.add_argument('--nas', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    rows = inspect(check_qgis=a.qgis, check_nas=a.nas)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print(f"[{r['狀態']}] {r['項目']}: {r['說明']}")
    return 1 if any(r['狀態'] == 'FAIL' for r in rows) else 0


if __name__ == '__main__':
    raise SystemExit(main())
