# 大量圖資轉換工具 — 交接主檔

> 新對話/新帳號接手時，先讀本檔，再讀設計文件：
> `docs/superpowers/specs/2026-07-07-大量圖資轉換工具-design.md`

## 這個專案在做什麼

批次處理「帶土地清冊的公文/報告表格」（xlsx/PDF/掃描圖片）：
解析出 縣市/鄉鎮區/段/小段/地號 → 比對全台地籍 shp → 地籍異動自動查新舊地號 →
每個案場出 4 張 A4 橫式圖（使用分區、使用地編定、國土功能分區、正射影像+半透明電子地圖），
另保留 QGIS 專案檔（.qgz）供手動微調。

## 目前進度（2026-07-07 第2次更新）

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
- [ ] 階段2b：`scripts/query_landno.py`（新舊地號自動查詢 03 的 5 筆 → 04 對照清單）
- [ ] 階段3：`scripts/make_maps.py`（PyQGIS 出圖，先出 1 個案場驗證版面）

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
- 地段代碼線上查詢備援：https://lisp.land.moi.gov.tw/MMS/MMSpage.aspx#gobox02

## 使用者的操作習慣

- 用 Claude Code 對話操作：丟新案子資料夾進 `待評估資訊/`，說「跑某某案」。
- 使用者是空間規劃專業（國土計畫/農村再生領域），非程式背景；回報用中文、
  講結論與要人工確認的事項，不要貼大段程式碼。

## ⏸ 暫停快照（2026-07-07 16:25，使用者指示：額度恢復後一次續跑）

**已完成**：階段1、階段2、階段2b（新舊地號查詢，見下）；階段3進行到一半。

**階段2b結果**（`outputs/20260707-清潔隊部資訊/04_新舊地號對照清單.xlsx`，
腳本 `scripts/query_landno.py` 已提交、可重複執行）：
- 成功3筆：021 坑子口段後壁厝小段1-1→**後壁段12**；038 寶興段176→地籍舊段名
  **聚興段新興小段461**；086 東振段1215→地籍舊段名**東振新段707**（均已驗證存在於地籍）
- 查無2筆：023 草漯段2535-1、063 崑崙段786-4（母號2535/786都在地籍，疑111年2月後
  逕為分割，建議使用者電洽觀音/仁德地政確認）

**階段3出圖 WIP**（額度恢復後已續跑，subagent 執行中）：
- `scripts/make_maps.py`（34KB）＋ `styles/*.qml` 5個樣式已寫好
- 004 土城案場已試出 3 張主題圖 PNG（outputs/…/圖檔/），**第4張WMTS套疊圖尚未驗證**
- 執行方式：PowerShell 呼叫 `& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_maps.py`
  （引號解析有雷，subagent 當時改用暫存腳本檔方式呼叫）
- 出圖範圍：**排除案場 021/023/038/063/086** 的 100 個案場（4×100 張）＋05出圖清單

**使用者追加的3項出圖改善（2026-07-07 額度恢復後，已納入 subagent 任務）**：
1. 每案場先 zoom 到基地（基地 extent 外擴25%）再出圖，比例尺取整級距。
2. 使分/使類顏色依使用者 QGIS 樣式庫內建圖例：
   `C:\Users\user\AppData\Roaming\QGIS\QGIS3\profiles\default\symbology-style.db`（symbol 表）。
   坑：XML 屬性順序 v 在 k 前；國土保安/交通/生態保護用地、山坡地是 LinePatternFill
   斜線紋，要整段 symbol XML 嵌入 QML；甲/乙/丙種建築用地→符號「甲乙丙建築用地」、
   丁種→「丁建用地」、礦業用地→「礦業」，其餘同名。新增 scripts/build_legend_styles.py 產 QML。
3. 周邊宗地也上使分/使類底色：周邊地籍層改為「地圖範圍內所有宗地」且必須帶
   使分/使類/地號欄（舊版 gpkg 周邊層只有案場ID，不能沿用），基地疊粗紅框區別。

**⏸ 續跑快照（2026-07-07 17:15，Fable5額度用完，使用者指示：額度夠再一次重跑）**
- 3項改善的**程式與樣式已改好並 commit（ee56532），但尚未執行驗證**：
  - `make_maps.py`：已加 zoom到基地、周邊宗地帶使分/使類上色（改動已在，未跑過）
  - `scripts/build_legend_styles.py`（新）：從 QGIS 樣式庫 symbology-style.db 產使分/使類 QML
  - `styles/使用分區.qml`、`使用地編定.qml` 已重建：分類值齊、LinePatternFill 斜線紋已保留
  - 註記：`styles/周邊地籍.qml` 仍是 singleSymbol（給國土/WMTS圖當淡灰底用；使分/使類圖的
    周邊上色是靠 make_maps 對周邊層套用 使用分區/使用地編定.qml，續跑時要確認這段有生效）
- 全檔語法/XML 皆驗過完整、無殘留 QGIS 程序、outputs/圖檔 仍只有 004 的舊 3 張 PNG。

**⏸ 續跑快照（2026-07-07 第3次，使用者新增都市計畫圖資後）**
- 都市計畫圖例已確認並產出交付物（commit 8048899）：`config/urban_plan_legend.yaml`、
  `styles/都市計畫.qml`（Color1 data-defined，004 煙霧測試通過，驗證圖存
  `docs/都市計畫Color1上色驗證_004土城.png`）。**尚未整合進 match_parcels/make_maps**。
- 都市計畫整合設計決策（下次執行照做）：
  - **使用分區圖**：非都市宗地→`使分`(內建圖例色)；都市土地宗地→疊 `都市計畫` 層
    (Color1 官方色) 當底，兩套圖例並列。做法：make_maps 在使用分區圖多載入都市計畫 shp
    (裁到地圖範圍)套 都市計畫.qml，置於地籍線之下、非都市使分層可疊其上。
  - **使用地編定圖**：編定是非都市概念，都市土地無；都市案場此圖以都市計畫層淡色為底
    ＋圖面註記「都市土地無使用地編定」。
  - **國土功能分區圖、WMTS套疊圖**：不變。
  - match_parcels 可選加強：都市土地宗地(使分/使類=None)與都市計畫 shp 空間交集，
    把都市計畫分區(zone)寫入 02 檔備查（非出圖必要，出圖靠圖層疊放即可）。

**恢復時的待辦（依序）**：
1. 續跑階段3（含前述3改善＋都市計畫整合）：先重出 004 全部 4 張（004 是都市土地，
   正好驗證都市計畫層）→ Read 目視檢查（zoom框住基地？非都市使分/使類套內建圖例色？
   都市土地套都市計畫官方色？周邊宗地有上色？基地紅框？WMTS底圖位置對？）
   → 批次 100 案場（排除 021/023/038/063/086）→ 05_出圖清單.xlsx → 抽 3 張不同縣市檢查。
2. ✋ 使用者尚未回覆的兩個核示（回覆前不要做）：
   (a) 021/038/086 三筆新舊地號對照是否核可回填（核可→回填中繼檔→重跑 match_parcels→補出圖）
   (b) 023/063 要不要先以母號範圍出圖＋圖面註記，還是等地政確認
3. 全案跑通後依設計文件 §8 驗收（抽 5 案場人工核對）。

## 階段2成果備忘（2026-07-07）

- 02 檔「待確認」大宗：宗地跨功能分區界線（如 94%/6%），原表僅寫主要分區——正常。
- 真差異例：041 上富段474 輸入農2/圖資農1(100%)、017 東隆段部分宗地圖資使分=工業區。
- 使分/使類為 None ＝都市土地（004 土城、009 新莊部分地號），02 標「圖資無值」。
