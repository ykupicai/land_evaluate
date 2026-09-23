# 以 QGIS 內建 Python 執行腳本，並隔離使用者 site-packages。
#
# 用法（取代直接呼叫 python-qgis-ltr.bat）：
#   .\scripts\qgis_python.ps1 scripts\make_maps.py <案名> --layout maxmap
#   .\scripts\qgis_python.ps1 -c "import numpy; print(numpy.__version__)"
#
# 為什麼不能直接呼叫 python-qgis-ltr.bat（2026-09-24 阿里山案實踩）：
#   Python 的使用者 site-packages 依「版本號」分資料夾（%APPDATA%\Python\Python312），
#   不是依安裝位置。本機另有 uv 裝的 CPython 3.12，別的專案用 pip --user 把 numpy 2.x
#   裝進那裡——QGIS 內建的 Python 3.12 也會讀同一個資料夾，蓋掉 QGIS 自帶的 numpy 1.x，
#   osgeo.gdal_array 匯入失敗，所有正射／電子地圖底圖「靜默消失」（出圖照樣回報成功）。
#   PYTHONNOUSERSITE=1 只作用於本次呼叫，不動全機設定、也不動那個專案的套件。
#
# QGIS 路徑讀 config\paths.local.yaml → config\paths.yaml 的 qgis_prefix（與
# config_loader.qgis_python_bat 同一規則）；兩者都沒有才用內建預設值。

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$prefix = 'C:\Program Files\QGIS 3.40.12\apps\qgis-ltr'

foreach ($name in @('paths.yaml', 'paths.local.yaml')) {
    $cfg = Join-Path $repo "config\$name"
    if (-not (Test-Path -LiteralPath $cfg)) { continue }
    foreach ($line in Get-Content -LiteralPath $cfg -Encoding UTF8) {
        if ($line -match '^\s*qgis_prefix:\s*(.+?)\s*$') {
            $value = $Matches[1].Trim().Trim('"').Trim("'")
            if ($value) { $prefix = $value }   # 後讀的 local 覆蓋共用設定
        }
    }
}

$bat = Join-Path (Split-Path -Parent (Split-Path -Parent $prefix)) 'bin\python-qgis-ltr.bat'
if (-not (Test-Path -LiteralPath $bat)) {
    Write-Error "找不到 QGIS Python：$bat（請檢查 config\paths.local.yaml 的 qgis_prefix）"
    exit 2
}

$env:PYTHONNOUSERSITE = '1'
& $bat @args
exit $LASTEXITCODE
