<#
.SYNOPSIS
  一鍵在專案根目錄建立「圖資資料夾 → NAS」的符號連結，免把數十 GB 圖資複製到本機。

.DESCRIPTION
  程式碼把圖資位置寫死成專案根目錄下的固定名稱（地籍圖資/、環境敏感相關/…），
  而 NAS 上的資料夾名稱不同（例：2022地籍），所以用符號連結：本機取程式要的名字、
  指向 NAS 實際資料夾。對照表與 docs/新同事上手指南.md 同一份。

  行為：
    ‧本機已有「實體資料夾」→ 不動（印「略過」；要改用 NAS 請自行先搬走或刪除）
    ‧本機已有連結且指向正確 → 不動；指向別處 → 印警告，加 -Force 才重建
    ‧NAS 來源連不到 → 印錯誤、該項略過，其餘照做
  門牌坐標/ 不在此表：請改跑 settlement_check.py --download（NAS 上是 108 年舊版）。

  權限：建符號連結需「以系統管理員身分執行」PowerShell，或先開啟 Windows「開發人員模式」。
  （junction 不能指向網路路徑，故只能用 SymbolicLink。）

.EXAMPLE
  .\scripts\link_nas_data.ps1 -DryRun     # 只列出會做什麼
  .\scripts\link_nas_data.ps1             # 實際建立
  .\scripts\link_nas_data.ps1 -Force      # 連結指向不對時重建
#>
param(
    [string]$NasRoot = '\\192.168.1.134\Public-TN\3. 圖資\gis圖資',
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [switch]$DryRun,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'

# 單反斜線開頭會被當成目前磁碟機的相對路徑、靜默寫到 C:\192.168...（2026-09-16 實踩）
if ($NasRoot -notmatch '^\\\\') { throw "NasRoot 必須是 \\ 開頭的 UNC 路徑：$NasRoot" }

# 本機名稱（程式寫死） → NAS 相對路徑
$map = [ordered]@{
    '地籍圖資'               = '地段地籍圖資\各縣市\2022地籍'
    '國土功能分區'           = '國土功能分區'
    '都市計畫'               = '都市計畫相關\全台\都市計畫'
    '環境敏感相關'           = '環境敏感相關'
    '2021全台農田水利灌排'   = '產業相關\一級產業\農業\2021全台農田水利灌排'
    '農地重劃區'             = '產業相關\一級產業\農業\農地重劃區'
    '2021 國道省道以外道路'  = '交通相關\2021 國道省道以外道路'
    '社福機構'               = '社福機構'
}

if (-not (Test-Path -LiteralPath $NasRoot)) {
    throw "連不到 NAS：$NasRoot（確認在辦公室內網或已連 VPN、帳號有權限）"
}

$ok = 0; $skip = 0; $fail = 0
foreach ($name in $map.Keys) {
    $link   = Join-Path $Root $name
    $target = Join-Path $NasRoot $map[$name]

    if (-not (Test-Path -LiteralPath $target)) {
        Write-Host "✖ $name：NAS 來源不存在 $target" -ForegroundColor Red; $fail++; continue
    }

    $item = Get-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue
    if ($item) {
        if ($item.LinkType -eq 'SymbolicLink') {
            $cur = @($item.Target)[0]
            if ($cur -eq $target) { Write-Host "＝ $name：已連結 → $target"; $skip++; continue }
            if (-not $Force) {
                Write-Host "⚠ $name：已連到別處 $cur（加 -Force 重建）" -ForegroundColor Yellow; $skip++; continue
            }
            if (-not $DryRun) { $item.Delete() }   # 只刪連結本身，不動 NAS 內容
        } else {
            Write-Host "－ $name：本機已有實體資料夾，略過（不覆蓋本機資料）" -ForegroundColor Yellow; $skip++; continue
        }
    }

    if ($DryRun) { Write-Host "（試跑）$name → $target"; $ok++; continue }
    try {
        New-Item -ItemType SymbolicLink -Path $link -Target $target | Out-Null
        Write-Host "✔ $name → $target" -ForegroundColor Green; $ok++
    } catch {
        Write-Host "✖ $name：建立失敗 $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "   → 請以系統管理員身分執行 PowerShell，或開啟 Windows「開發人員模式」" -ForegroundColor Red
        $fail++
    }
}

Write-Host ""
Write-Host "完成：建立 $ok、略過 $skip、失敗 $fail"
Write-Host "門牌坐標請另跑：python -X utf8 scripts\settlement_check.py --download --county <縣市>"
if ($fail) { exit 1 }
