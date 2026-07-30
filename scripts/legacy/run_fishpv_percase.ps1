# 逐案場跑 fishpv 臨路評估（序列，避免圖磚限流）
# 用法:.\run_fishpv_percase.ps1 -Base <臨路評估_percase目錄>
#   目錄內須有 split_shp_persite.py 拆出的 case_*.shp;成果各存 out_<案場ID>/
param(
    [Parameter(Mandatory = $true)][string]$Base,
    [string]$FishpvRoot = 'C:\Users\user\Documents\Claude\Projects\漁電共生評估'
)
$ErrorActionPreference = 'Continue'
$ct = Join-Path $FishpvRoot 'qgis_plugin\fishpv_aquavoltaic\resources\luimap_colortable.csv'
$shps = Get-ChildItem "$Base\case_*.shp" | Sort-Object BaseName
$total = $shps.Count
if ($total -eq 0) { Write-Output "[run_fishpv_percase] $Base 無 case_*.shp（先跑 split_shp_persite.py）"; exit 2 }
$i = 0
foreach ($shp in $shps) {
    $i++
    $cid = $shp.BaseName -replace 'case_',''
    $out = Join-Path $Base "out_$cid"
    if (Test-Path (Join-Path $out '成果.xlsx')) { Write-Output "[$i/$total] CASE $cid SKIP(done)"; continue }
    New-Item -ItemType Directory -Force $out | Out-Null
    Write-Output "[$i/$total] CASE $cid START $(Get-Date -Format HH:mm:ss)"
    Push-Location $FishpvRoot
    & cmd /c ".\env.bat python -m fishpv.cli --cadastre `"$($shp.FullName)`" --colortable `"$ct`" --out `"$out`" --no-ai --landno-field 地號" *>> (Join-Path $out 'fishpv.log')
    $code = $LASTEXITCODE
    Pop-Location
    if ((Test-Path (Join-Path $out '成果.xlsx'))) {
        Write-Output "[$i/$total] CASE $cid DONE"
    } else {
        Write-Output "[$i/$total] CASE $cid FAIL(exit=$code)"
    }
}
Write-Output "ALL-CASES-FINISHED"
