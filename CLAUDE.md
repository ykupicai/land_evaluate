# 大量圖資轉換工具 — 交接主檔

> 新對話/新帳號接手時，先讀本檔，再讀設計文件：
> `docs/superpowers/specs/2026-07-07-大量圖資轉換工具-design.md`

## 這個專案在做什麼

批次處理「帶土地清冊的公文/報告表格」（xlsx/PDF/掃描圖片）：
解析出 縣市/鄉鎮區/段/小段/地號 → 比對全台地籍 shp → 地籍異動自動查新舊地號 →
每個案場出 4 張 A4 橫式圖（使用分區、使用地編定、國土功能分區、正射影像+半透明電子地圖），
另保留 QGIS 專案檔（.qgz）供手動微調。

## 目前進度（2026-07-07）

- [x] 需求釐清完成、設計文件已寫好並經使用者核可（細節全在設計文件，**必讀**）
- [ ] 使用者審閱 spec → 用 `writing-plans` skill 寫實作計畫
- [ ] 實作（尚未開始，repo 還沒有任何程式碼）

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

1. 請使用者審閱設計文件，有修改就改。
2. 核可後 invoke `writing-plans` skill 產實作計畫。
3. 實作順序建議：county_registry 掃描建檔 → parse_helpers＋單測 → 階段1解析本次樣本
   → match_parcels → make_maps（先出 1 個案場驗證版面）→ query_landno → 全案跑通驗收。
