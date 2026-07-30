# scripts/legacy — 一次性與已淘汰腳本（**新案不要用**）

2026-07-30 從 `scripts/` 收攏進來，目的是讓 `scripts/` 只留現行產線工具。
這裡的東西**不刪**（保留可追溯的處理過程），但也**不維護**：

- 內含案件專屬硬編碼，換案子會算錯或直接壞掉
- 引用的外部依賴可能已移除
- 產線已有替代品（見下表）

新案的一次性腳本請放**案件資料夾**（`outputs/<案>/_xxx.py`，`_` 前綴），
能通用的則抽進 `scripts/` ＋ `config/` 的 YAML——見 CLAUDE.md「案件成果沉澱機制」。

## 清潔隊部資訊案（2026-07-08）的一次性腳本

清冊比對階段處理該案特殊狀況用，**內含該案專屬硬編碼資料**（排除案場清單、
段名代碼修正對照等）。

| 腳本 | 當時用途 | 現在該用什麼 |
|---|---|---|
| `backfill_excluded.py` | 把 5 個排除案場的新舊地號結果回填 02_比對結果 | `run_case.py` 的 fallback 鏈（query_landno→apply_landno→重比）已自動化 |
| `build_upload_xlsx.py` | 從 02 產平台「批次地籍匯入選取」上傳 xlsx | skill `tcdgis-parcels-to-shp` 的 `batch_driver.js`（直接在圖台批次選取，不再走上傳檔） |
| `build_corrected_upload.py` | 產修正版地籍清冊 | skill `parcel-roster-verify`（平台即時驗證＋MOI 新舊地號 API 回填） |
| `build_supplement.py` | 平台匯入失敗 5 筆的補正清冊 | 同上 |

## 臨路評估內建化前的 legacy 路徑（2026-07-16 之前）

臨路評估自 2026-07-26 起為產線內建（`scripts/road_access.py`），
**本專案不再相依外部「漁電共生評估」專案**。

| 腳本 | 說明 |
|---|---|
| `split_shp_persite.py` | 依 sitemap 把 joined shp 拆成逐案場 shp。**極大型（跨縣市／全國）清冊記憶體會爆時**，分批跑仍可能用到；移入本資料夾後已把 sys.path 指向上一層以便沿用 `shp_trust_utils` |
| `merge_fishpv_percase.py` | 合併逐案場 fishpv 成果 |
| `run_fishpv_percase.ps1` | 逐案序列呼叫外部 fishpv CLI。⚠ **硬編碼 `C:\...\Projects\漁電共生評估`，該依賴已移除，直接跑會失敗**；只能當分批邏輯的參考 |

分批需求的現行做法：直接對 `scripts/road_access.py` 按縣市切分清冊後分批餵，
不要走這三支。
