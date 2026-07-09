# 免 AI 批次流程操作手冊

給定正確的檔案與路徑，以下三段可在終端機直接執行，**全程不需要我或任何 AI 模型參與**。
只有「階段1輸入解析」在換成新版式文件時需要 AI 協助寫一次解析規則；規則寫好後同版式文件也能重複免 AI 執行。

## 總覽

| 階段 | 腳本 | 需要 AI？ | 需要瀏覽器？ |
|---|---|---|---|
| 1. 輸入解析（PDF/圖片→中繼檔） | `scripts/parse_stage1.py` | 換版式才需要（見下） | 否 |
| 2. 比對地籍 | `scripts/match_parcels.py` | 否 | 否 |
| 2b. 新舊地號查詢 | `scripts/query_landno.py` | 否（純 HTTP `requests`） | 否 |
| 3. 出圖 | `scripts/make_maps.py`（+ `nlsc_wmts.py`／`build_legend_styles.py`／`csv_to_xlsx.py`） | 否 | 否（QGIS 內建 Python） |
| 3後. 整理成果 | `scripts/organize_outputs.py`、`rebuild_05.py` | 否 | 否 |

不在此表：政府圖台（NGIS/TCDGIS）批次選取＋下載官方 shp，目前仍需人工瀏覽器操作（含帳密登入、圖形驗證碼），見文末說明。

## 前置需求（一次性，已在本專案備妥）

- `config/county_registry.yaml`、`config/section_index.csv`、`config/urban_plan_legend.yaml`
- `地籍圖資/`、`國土功能分區/`、`都市計畫/` 各縣市 shp
- `styles/*.qml`
- QGIS 3.40.12 LTR（路徑固定：`C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat`）
- 系統 Python（跑階段1/2/2b）需有：`pandas`、`openpyxl`、`pyogrio`、`pdfplumber`、`requests`、`pyyaml`

## 逐階段指令

### 階段1：輸入解析（換版式才需要 AI）

```powershell
python -X utf8 scripts\parse_stage1.py
```

讀 `待評估資訊/<案子>/*`，寫出 `outputs/<案子>/01_中繼檔.xlsx`。
**這支腳本是針對「清潔隊備勤室非都市用地對應國土功能分區彙整表」這一種版面寫的**（欄位順序、地號展開規則、括號註記規則都是照這份文件的樣式硬寫）。
- 同版式的新文件（例如同機關下一批案子）→ 直接重跑，免 AI。
- 不同版式（欄位不同、表格結構不同）→ 需要請 AI 讀一次新樣本、寫一支對應的新版 `parse_stageX.py`；寫好後那個版式後續也免 AI。

### 階段2：比對地籍

```powershell
python -X utf8 scripts\match_parcels.py <案子資料夾名>
```

讀 `outputs/<案子>/01_中繼檔.xlsx`，輸出 `02_比對結果.xlsx` + `03_無法對應清單.xlsx`。
純規則比對（段名正規化、地號轉宗地母子號、地籍shp屬性查找），無 AI。

### 階段2b：新舊地號查詢（比對不到才需要）

```powershell
python -X utf8 scripts\query_landno.py <案子資料夾名>
```

讀 `03_無法對應清單.xlsx`，**用純 HTTP request** 逐筆查各縣市地政的「地籍圖重測地段新舊地建號查詢」系統（已逆向出臺中/臺南/屏東/桃園等縣市的查詢協定，見腳本開頭註解），輸出 `04_新舊地號對照清單.xlsx`。查無者會標註，需人工判斷是否採「母號範圍」出圖或洽詢地政。**不開瀏覽器、不用 AI。**

### 階段3：出圖

```powershell
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_maps.py <案子資料夾名> [--cases=004,005] [--exclude=021,023] [--no-qgz]
```

讀 `02_比對結果.xlsx` + 圖資，輸出每案場 4 張 PNG（使用分區／使用地編定／國土功能分區／正射套疊）＋ `05_出圖清單.csv`＋（可選）`.qgz`。

- `--cases=` 只出指定案場（逗號分隔），省略＝全部。
- `--exclude=` 排除指定案場。
- `--no-qgz` 跳過 `.qgz` 輸出（大批次時若記憶體不穩可先關，圖檔/清單不受影響）。
- WMTS 底圖（正射影像+電子地圖）用 `nlsc_wmts.py` 直接抓圖磚拼接，需要網路連線但不需瀏覽器。

### 階段3後：整理成果

```powershell
python -X utf8 scripts\organize_outputs.py <案子資料夾名>   # 圖檔依案場ID分資料夾
python -X utf8 scripts\rebuild_05.py                        # 分批出圖後重建完整05清單
```

## 案例專用的一次性腳本（不是通用工具，供參考）

`backfill_excluded.py`、`build_upload_xlsx.py`、`build_corrected_upload.py`、`build_supplement.py` 是這次「清潔隊部資訊」案處理特殊情況（新舊地號回填、政府圖台上傳清冊）時寫的**一次性腳本**，內含這個案子專屬的硬編碼資料（地段代碼、地號清單）。換新案子時這幾支要重寫或不需要，不算通用管線的一部分。

## 目前還離不開人工／AI 的部分

1. **輸入文件換版式**：需要 AI 讀一次新樣本才能寫解析規則（見階段1說明）。
2. **政府圖台（NGIS/TCDGIS）批次選取＋加入主題＋ShapeFile下載**：該平台無公開API，「批次地籍匯入選取」功能可上傳xlsx（欄位：項次/縣市名稱/鄉鎮市區/地段名稱/地段代碼/地號），但**沒有「加入主題」功能**；要下載官方shp必須把地籍逐段輸入「多重地籍選取」視窗才能加入主題並下載。目前只能人工操作（含帳密登入、圖形驗證碼，這兩項依規範不能由AI代勞）。
   若要進一步免人工，可將已逆向出的DOM操作邏輯（下拉元件id：`selLISP2County`/`Town`/`LandSec`、地號欄`CADANO_SKINDA3`、加入函式`addCADAToTable('SKINDA3')`）寫成Playwright腳本，但登入與驗證碼仍須人工先完成。
