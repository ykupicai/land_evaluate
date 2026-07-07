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

## 資料夾結構

```
待評估資訊/            # 輸入：一個案子一個資料夾（xlsx/pdf/jpg）
地籍圖資/              # 全台 20 縣市地籍 shp + 段藉.shp（全台段小段索引）
國土功能分區/          # 各縣市功能分區 shp（結構異質！）+ 圖例.xml（標準配色）
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
- 地段代碼線上查詢備援：https://lisp.land.moi.gov.tw/MMS/MMSpage.aspx#gobox02

## 使用者的操作習慣

- 用 Claude Code 對話操作：丟新案子資料夾進 `待評估資訊/`，說「跑某某案」。
- 使用者是空間規劃專業（國土計畫/農村再生領域），非程式背景；回報用中文、
  講結論與要人工確認的事項，不要貼大段程式碼。

## 下一步（接手就做這個）

1. `scripts/make_maps.py`（階段3，PyQGIS）：讀 02_比對結果.xlsx（含 圖資FID 欄可
   直接讀幾何），先出 1 個案場的 4 張圖驗證版面，經使用者確認後跑全案。
   執行方式見上方 PyQGIS 一節；功能分區圖層載入/CRS 修正邏輯可參考
   match_parcels.load_county_zones（styles 配色用 國土功能分區/圖例.xml 轉 QML）。
2. `scripts/query_landno.py`（階段2b）：對 03 清單 5 筆自動查內政部新舊地號系統
   （涉及縣市：桃園、臺中、臺南、屏東）→ 04 對照清單 → 使用者核可回填重跑階段2。
3. 全案跑通後依設計文件 §8 驗收（抽 5 案場人工核對）。

## 階段2成果備忘（2026-07-07）

- 02 檔「待確認」大宗：宗地跨功能分區界線（如 94%/6%），原表僅寫主要分區——正常。
- 真差異例：041 上富段474 輸入農2/圖資農1(100%)、017 東隆段部分宗地圖資使分=工業區。
- 使分/使類為 None ＝都市土地（004 土城、009 新莊部分地號），02 標「圖資無值」。
