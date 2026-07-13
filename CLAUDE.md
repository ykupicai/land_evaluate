# 大量圖資轉換工具 — 交接主檔

> 新對話/新帳號接手時，先讀本檔，再讀設計文件：
> `docs/superpowers/specs/2026-07-07-大量圖資轉換工具-design.md`
>
> **只要跑既有案子的比對/出圖（不換輸入版式），不需要開新對話——直接照
> `docs/免AI批次流程操作手冊.md` 的指令在終端機執行即可，全程無需 AI。**

## 這個專案在做什麼

批次處理「帶土地清冊的公文/報告表格」（xlsx/PDF/掃描圖片）：
解析出 縣市/鄉鎮區/段/小段/地號 → 比對全台地籍 shp → 地籍異動自動查新舊地號 →
每個案場出 4 張 A4 橫式圖（使用分區、使用地編定、國土功能分區、正射影像+半透明電子地圖），
另保留 QGIS 專案檔（.qgz）供手動微調。

## 目前進度（2026-07-12 更新：整合流程 run_case 完成）

- [x] **整合總控 `scripts/run_case.py` 已完成並驗收**（spec `docs/superpowers/specs/
      2026-07-11-整合流程run_case-design.md`）：一鍵 shp／清冊 → **六張圖**（使用分區／
      使用地編定／國土功能分區／正射套疊／**國土利用調查**／**臨路判定**）＋ `.qgz`。
      子進程編排（系統 Python 呼叫 QGIS 內建 Python 與漁電 fishpv），停點見 spec §4。
      免 AI 操作指令見 `docs/免AI批次流程操作手冊.md`「一鍵整合流程 run_case」節。
  - trust-shp 模式（`--shp`）：**直取 shp 幾何/屬性、全程不比對地籍**（無 match/02）；
    **無 14 碼段號的宗地照出**（修官田 S002/S003 被丟缺陷）；`--mode single`=整份一組、
    `--mode persite`=依 fishpv sitemap（地號→宗基地）分組每宗一組。已以官田 40MW 驗收
    （S001~S004 六主題齊、使用分區底色正常）。純資料輔助抽到 `scripts/shp_trust_utils.py`
    （DBF 讀取容錯、地號正規化、sitemap 分派；不依賴 QGIS 故可單測）。
  - 清冊模式（`--list`）：解析→01 停點（人工核對）→`--resume` 比對→**fallback 鏈自動化**
    （比不到→query_landno→apply_landno 回填→重比；查無退母號範圍；母號也無記 03，不中斷）
    →00_總區位圖 停點→`--mode` 出圖。接線與 fallback 動態插入已由單測＋main() 端到端
    整合測試證明（tests/test_run_case_helpers.py）。
- [x] 階段1/2/2b（清潔隊案）：見下方歷史；03 已全數解決（04 留痕 5 筆，3 成功 2 查無）。

- [x] 需求釐清完成、設計文件已寫好並經使用者核可（細節全在設計文件，**必讀**）
- [x] `config/county_registry.yaml`（縣市圖資註冊表，`scripts/build_registry.py` 產出）
- [x] `scripts/parse_helpers.py` ＋ 58 個單元測試（`python -X utf8 -m pytest tests/ -q`）
- [x] `config/section_index.csv`（現行段小段索引，`scripts/build_section_index.py` 產出，見下方坑）
- [x] 階段1：`scripts/parse_stage1.py` 解析清潔隊樣本 → `outputs/20260707-清潔隊部資訊/01_中繼檔.xlsx`
      （105 案場 / 327 筆地號，12 筆低信心黃底標示）
- [x] 使用者已核對中繼檔（7 項核示已回填 parse_stage1 OVERRIDES，重產後 0 筆低信心；
      326 筆地號）
- [x] 階段2：`scripts/match_parcels.py` ＋ `scripts/zone_utils.py`（分區名正規化，52 單測）
      → 02_比對結果.xlsx（321/326 成功；60 筆輸入vs圖資不一致標「待確認」）
      → 03_無法對應清單.xlsx（5 筆：021 後壁厝小段、086 東振段〔使用者核示走新舊地號〕、
        023/038/063 查無地號〔疑 Ver111 後異動〕）
- [x] 階段2b：`scripts/query_landno.py`（新舊地號自動查詢 → 04 對照清單，純 HTTP）
- [x] 階段3：`scripts/make_maps.py`（PyQGIS 出圖；4 主題版面已驗、國土利用/臨路兩新主題
      由 run_case 整合）

## 關鍵決策速記（詳見設計文件 §2）

1. 兩段式流程：先產「01_中繼檔.xlsx」給使用者人工核對，核可後才比對出圖。
2. 出圖引擎用 **PyQGIS 批次腳本**（QGIS 內建 Python），不要用 QGIS MCP 逐張出圖（token 太貴）；MCP 只用於除錯/微調。
3. 國土功能分區**不在**地籍 shp 屬性裡，要用宗地 polygon 與 `國土功能分區/` 各縣市 shp 做空間交集。
4. 比對不到的地號 → 自動到內政部新舊地號系統查（https://www.land.moi.gov.tw/chhtml/landnoqry/51），產「04_新舊地號對照清單」給使用者核可後回填重跑。
5. 每個「案場」（非每筆地號）出一組 4 張圖；輸出到 `outputs/<案子>/`。
6. **都市計畫土地使用分區**（2026-07-07 新增，補「都市土地使分/使類=None」缺口）：
   `都市計畫/全台都市計畫土地使用分區.shp`（201,327 筆、EPSG:3826）。**`Color1` 欄＝
   官方數值化 RGB 色**（依附件三圖例；全台僅26色、99.99%完整），出圖直接用
   `styles/都市計畫.qml`（data-defined 依 Color1 上色，已於 004 驗證通過）。
   官方圖例對照見 `config/urban_plan_legend.yaml`。zone 欄有2,502種計畫個別命名，
   **不要建分類表**，一律走 Color1 data-defined。

## 資料夾結構

```
待評估資訊/            # 輸入：一個案子一個資料夾（xlsx/pdf/jpg）
地籍圖資/              # 全台 20 縣市地籍 shp + 段藉.shp（全台段小段索引）
國土功能分區/          # 各縣市功能分區 shp（結構異質！）+ 圖例.xml（標準配色）
都市計畫/              # 全台都市計畫土地使用分區.shp（Color1=官方色）+ 附件三圖例.pdf
docs/superpowers/specs/ # 設計文件
（實作後新增）config/ scripts/ styles/ outputs/
```

## 踩過的坑 / 重要事實（不要重新探勘）

- **段藉.shp 是舊制段名索引，不能拿來驗證/解析現行段名**：缺重測後新段名
  （例：深坑區賴仲段=段碼0869 在地籍 shp 有、段藉.shp 查無，段藉只有舊名
  「土庫段賴仲坑小段」）。權威段名來源＝各縣市地籍 shp 屬性 →
  已彙整為 `config/section_index.csv`（14,331 筆；圖資更新後重跑
  `scripts/build_section_index.py`）。
- 地籍 shp 的 `鄉鎮` 欄有舊制行政區名（員林**鎮**）與 Big5 轉檔缺字
  （蘆竹區→「蘆區」），比對鄉鎮名要寬鬆處理（見 parse_stage1.town_matches）。
- 地籍 shp：EPSG:3826，各縣市欄位一致；`宗地母`/`宗地子` 是 4 位數字字串
  （331-1 → `0331`/`0001`，無子號 `0000`）；使用分區欄叫 `使分`、使用地類別欄叫 `使類`，
  都市土地此二欄為 None；版本 `Ver`=111年2月（所以有異動時間差）。
- 縣市檔名用「台」（台北市…），輸入文件多用「臺」，要正規化。
- `段藉.shp`：15,385 筆全台段/小段索引，中文段名 → 段碼靠它。
- `國土功能分區/` 各縣市資料夾層級、檔名、欄位都不同；基隆拆陸域/海域多檔；
  部分缺 .prj → 設計上用 `config/county_registry.yaml` 縣市註冊表統一登錄（實作第一步）。
- 樣本 PDF（20260707-清潔隊部資訊v1.pdf，8頁）**有文字層**，直接抽文字即可，不用 OCR/視覺。
- 本機環境：Windows 11；系統 Python 3.14 有 geopandas/pyogrio；QGIS 已安裝且接了 MCP
  （工具名 mcp__qgis__*）。
- **PyQGIS 執行方式（已驗證）**：`& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" 腳本.py`
  （QGIS 3.40.12 LTR；要用 PowerShell 呼叫，Git Bash 會因路徑空白失敗；
  另有 qgis_process-qgis-ltr.bat。機器上還裝了 3.6~3.24 多版，一律用 3.40.12）。
- NLSC WMTS：https://wmts.nlsc.gov.tw/wmts，正射影像圖層 `PHOTO2`、通用電子地圖 `EMAP`。
  **不要用 QGIS WMS/WMTS provider（會卡住/逾時）**；改用 `scripts/nlsc_wmts.py`
  直接抓 GoogleMapsCompatible 圖磚拼 GeoTIFF（沿用先前 LANDUSE 專案 work/build_mosaic.py，
  URL 模板 `.../wmts/{layer}/default/GoogleMapsCompatible/{z}/{row}/{col}`，快、穩）。
- **知識庫（先前專案經驗，NAS）**：`\\192.168.1.134\Public-TN\8. AI協作\知識庫\衍古\01_AI協作共用知識`
  （含 WMTS 圖磚拼接、QGIS 內建 Python/GDAL 工作流、LANDUSE 專案等，遇 GIS/WMTS 問題先查）。
- **都市計畫圖資**：`都市計畫/全台都市計畫土地使用分區.shp`（201k筆），`Color1` 欄＝官方色，
  用 `styles/都市計畫.qml`（data-defined）上色；官方圖例對照 `config/urban_plan_legend.yaml`。
- **openpyxl 不可在 PyQGIS 出圖進程內用**：openpyxl 依賴 lxml、與 GDAL 共用 libxml2；
  大量圖磚(GDAL)處理後 libxml2 全域狀態損毀，openpyxl 建 Workbook 會存取違例硬崩潰
  （faulthandler 實測）。故 make_maps 迴圈中把 05 清單寫 CSV，xlsx 交由獨立乾淨子進程
  `scripts/csv_to_xlsx.py` 產。`.qgz`（project.write 走 Qt XML，非 libxml2）不受影響。
- **出圖時桌面版 QGIS 不能開著同一案子的 gpkg/.qgz**：SQLite 鎖衝突會讓算圖讀不到
  要素（log 見「unable to open database file」），主題色整片空白但紅框/灰線可能正常，
  且 4 張都回報「成功」——很陰。解法：關桌面 QGIS 再跑，或 `--mem`（向量層 materialize
  成記憶體層算圖，免疫鎖；因記憶體層不入 .qgz，搭配 `--no-qgz`）。
- **直接給基地 shp 出圖**（免 PDF 解析）：`scripts/shp_to_meta.py <dbf路徑> <案子名> <案場名>`
  → 產 01_中繼檔 → match_parcels → make_maps。需 shp 含 14 碼段號（小段碼+宗地母+宗地子）。
  中文 dbf 欄名超過 DBF 10-byte 限制會截斷、pyogrio 讀不了 → 用 dbfread(utf-8, errors=replace)。
  官田 40MW 案（番子渡頭段 133 宗地）已以此流程驗證。
- **臨路判定 → 介接「漁電共生評估」專案（fishpv）**，直接吃案件地籍 shp（含壞欄名容錯）：
  `cd C:\Users\user\Documents\Claude\Projects\漁電共生評估` 後
  `.\env.bat python -m fishpv.cli --cadastre <基地.shp> --colortable qgis_plugin\fishpv_aquavoltaic\resources\luimap_colortable.csv --out <輸出夾> --no-ai --landno-field 地號`
  → 毗鄰聚合宗基地＋LUIMAP 道路向量化＋臨路三態（直接≤0.5m／需搭橋≤1.5m／不臨路；
  一般/防汛道路分開評）。輸出 成果.xlsx／maps/S00N.png 疊圖／sites.shp。
  官田案已驗證：4 宗基地（S001 主基地 57.7ha 直接臨路〔一般+防汛〕、S002 直接臨路、
  S003/S004 不臨路 49.4m/67.7m），結果在 outputs/20260707台南官田40MW/漁電臨路評估/。
- 地段代碼線上查詢備援：https://lisp.land.moi.gov.tw/MMS/MMSpage.aspx#gobox02

## 使用者的操作習慣

- 用 Claude Code 對話操作：丟新案子資料夾進 `待評估資訊/`，說「跑某某案」。
- 使用者是空間規劃專業（國土計畫/農村再生領域），非程式背景；回報用中文、
  講結論與要人工確認的事項，不要貼大段程式碼。

## ⏸ 進度快照（2026-07-13，整合流程 run_case 收官：驗收＋最終複審修復完成）

**整合流程 run_case 已完成並驗收**（Task 1~9 全數完成，見「目前進度」）。兩模式接線、
fallback 鏈、六張圖 + qgz 皆已就緒；`python -X utf8 -m pytest tests/ -q` = 173 passed。
最終跨階段複審 5 項修復（commit 107b373）：①fishpv 失敗**降級不中斷**（圖5/6缺、
1-4照出，主題6區分「略過:未跑fishpv」vs「失敗」）②`--skip-extract` 加 gpkg 基地
分組 vs 本次案場一致性檢查，不一致自動落回完整重抽（防 single↔persite 換模式吃到
舊 gpkg 全案場靜默失敗）③`--from` 截斷先檢查前置產物、缺件中文點名 ④QGIS
returncode 豁免（5/0xC0000005）收斂到只有 maps 步驟，extract/overview 崩潰需驗產物
剛寫出才放行 ⑤手冊補：make_maps 預設 6 張、只要 1-4 張加 `--basic`。

- **官田 40MW（trust-shp）**：已驗收，S001~S004 六主題齊、使用分區底色正常。
- **清潔隊案（清冊）**：01/02 定稿、03 為空（fallback 已歷史性套用，04 留痕 5 筆
  3 成功 2 查無）；1~4 張圖既有成果仍在（004 都市/050 山區/104 離島皆齊）。清冊模式的
  5/6 張（國土利用/臨路）走 run_case：fishpv 吃 02 撈得的官方宗地幾何（=圖層.gpkg 基地層）。
- fallback 鏈可運作證據：`tests/test_run_case_helpers.py` 有 (a) execute_steps mock 排序測試
  與 (b) 真呼叫 `run_case.main()` 的端到端整合測試（僅 stub subprocess 邊界），證明 match
  後偵測非空 03 會動態插入並依序派發 match→query_landno→apply_landno→match2，且在
  STOP_MODE 之前。apply_landno 兩路徑（精確回填/母號範圍）與 match_parcels 母號範圍
  pick_fids（含母號也無回 []）皆有單測。

**曾用過的出圖改善與坑（沿用中，勿重探）**：
- 每案場 zoom 到基地（extent 外擴 25%）、比例尺取整級距。
- 使分/使類色取自 QGIS 樣式庫 `%APPDATA%\QGIS\QGIS3\profiles\default\symbology-style.db`
  （`scripts/build_legend_styles.py` 產 QML）。坑：XML 屬性順序 v 在 k 前；國土保安/交通/
  生態保護用地、山坡地是 LinePatternFill 斜線紋要整段 symbol XML 嵌入；甲/乙/丙→「甲乙丙
  建築用地」、丁→「丁建用地」、礦業→「礦業」。
- 周邊宗地帶使分/使類上色（周邊層須含使分/使類/地號欄），基地疊粗紅框。
- 都市計畫已整合：使用分區圖對都市土地宗地疊 `都市計畫` 層（Color1 官方色）為底；
  使用地編定圖對都市土地以淡色為底＋註記「都市土地無使用地編定」。

**尚待使用者核示（回覆前不要動）**：清潔隊案 021/038/086 新舊地號回填、023/063 母號範圍
出圖或等地政確認——此為該特定案的資料判斷，與 run_case 流程本身無關。

## 階段2成果備忘（2026-07-07）

- 02 檔「待確認」大宗：宗地跨功能分區界線（如 94%/6%），原表僅寫主要分區——正常。
- 真差異例：041 上富段474 輸入農2/圖資農1(100%)、017 東隆段部分宗地圖資使分=工業區。
- 使分/使類為 None ＝都市土地（004 土城、009 新莊部分地號），02 標「圖資無值」。
