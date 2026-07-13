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

## 一鍵整合流程 run_case（推薦：shp／清冊 → 六張圖 + qgz）

`scripts/run_case.py` 是總控腳本，把下面各階段串起來自動跑，並多產兩張新圖（國土利用調查、臨路判定），最後產 `.qgz`。**六張圖**＝使用分區／使用地編定／國土功能分區／正射套疊／國土利用調查／臨路判定。跑在系統 Python，會自動依步驟呼叫 QGIS 內建 Python 與漁電共生評估專案（fishpv）。

有停點的地方腳本會停下來、印出下一步指令，照著加參數再跑同一支即可，全程免 AI。

### 模式一：trust-shp（已有基地 shp，直接信任其幾何與屬性，不做地籍比對）

```powershell
# 第一步：解析留痕 + 臨路評估 + 產 00_總區位圖，停下等你選出圖單位
python -X utf8 scripts\run_case.py --shp <基地.shp> [--case 案子名]

# 看完 outputs\<案子>\00_總區位圖.png 後，二選一續跑：
python -X utf8 scripts\run_case.py --shp <基地.shp> --mode single    # 整份 shp 出一組六張（官田模式）
python -X utf8 scripts\run_case.py --shp <基地.shp> --mode persite   # 每宗基地各出一組六張（環保局/漁電模式）
```

trust-shp 模式**不經地籍比對**：基地層幾何、地號、使分、使類全部直接讀 shp，**沒有 14 碼段號的宗地也照樣出圖**（不會被丟掉）。省略 `--case` 時案子名取 shp 檔名。

### 模式二：土地清冊（xlsx/PDF，走解析＋地籍比對）

```powershell
# 第一步：解析出 01_中繼檔.xlsx 後停下（請人工核對地號/分區辨識結果）
python -X utf8 scripts\run_case.py --list <清冊.xlsx/pdf> [--case 案子名]

# 人工核對 01_中繼檔.xlsx 後，續跑到 00_總區位圖 停點：
python -X utf8 scripts\run_case.py --list <清冊> --resume

# 看完 00_總區位圖 後選出圖單位（清冊模式：single=整案一組、persite=每案場一組）：
python -X utf8 scripts\run_case.py --list <清冊> --resume --mode single|persite
```

`--resume` 續跑會自動做：比對地籍 → 若有比不到的地號，**自動接新舊地號查詢（query_landno）→ 回填（apply_landno）→ 重新比對**，全程留痕於 `04_新舊地號對照清單.xlsx`，不會中斷。查得完整對應→改用新地號；查無（疑逕為分割）→退一步用「母號範圍」示意出圖；母號也無→記入 `03_無法對應清單.xlsx`、該地號不出圖但不影響其他。

> 換新版式清冊仍需 AI 寫一次解析規則（見階段1）；解析、核對後 `--resume` 之後全程免 AI。

### 共通參數

- `--from <步驟>`：從指定階段重跑（`meta|match|fishpv|overview|extract|maps`；`match` 為清冊模式限定）。
- `--mem`：向量層materialize 成記憶體層算圖，**免疫桌面 QGIS 開著同案 gpkg 造成的 SQLite 鎖衝突**；但記憶體層不會存進 `.qgz`，故要搭 `--no-qgz`。
- `--no-qgz`：跳過 `.qgz` 輸出（大批次記憶體不穩、或搭 `--mem` 時用）。
- 何時用哪個：一般情況直接跑（會產 `.qgz`）；若出圖時**桌面 QGIS 正開著同一案子的 gpkg/.qgz**，主題色可能整片空白卻仍回報成功——**務必先關掉桌面 QGIS**，或改用 `--mem --no-qgz`。

### 停點速記

| 模式 | 首跑停在 | 續跑（--resume/--mode）停在 |
|---|---|---|
| trust-shp | `00_總區位圖`（等 `--mode`） | 選 `--mode` 後跑完六張圖 + qgz |
| 清冊 | `01_中繼檔`（等人工核對＋`--resume`） | `--resume` 停在 `00_總區位圖`；再加 `--mode` 跑完六張圖 + qgz |

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

讀 `02_比對結果.xlsx` + 圖資，**預設輸出每案場 6 張 PNG**（使用分區／使用地編定／國土功能分區／正射套疊／國土利用調查／臨路判定）＋ `05_出圖清單.csv`＋（可選）`.qgz`。

- **只要 1-4 張加 `--basic`**（跳過國土利用調查與臨路判定兩新主題）。
- 圖5（國土利用調查）底圖直接抓 NLSC LUIMAP 圖磚，不需 fishpv；但其圖例與**圖6（臨路判定）需先經 run_case 跑過 fishpv 臨路評估**（產 `臨路評估/sites.shp` 等）。沒跑過 fishpv 就直跑 make_maps 時，圖6 在 05 清單記「**略過**：未跑臨路評估(fishpv)」（不是失敗）；若 fishpv 跑過但失敗（臨路評估資料夾在、產物不齊）才記「失敗：臨路評估失敗」。
- `--cases=` 只出指定案場（逗號分隔），省略＝全部。
- `--exclude=` 排除指定案場。
- `--no-qgz` 跳過 `.qgz` 輸出（大批次時若記憶體不穩可先關，圖檔/清單不受影響）。
- `--skip-extract` 重用既有 `圖層.gpkg`；若其案場分組與本次不一致（如換了 `--mode` 沿用另一模式的舊 gpkg），會自動偵測並落回完整重抽（log 有明說原因）。
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
