# 測試安全網與 make_maps 參數解析 — 設計文件

> 2026-09-16 落地紀錄：本文件保留 9/11 設計背景；最新結果見
> [專案整理與驗證](../../2026-09-16_專案整理與驗證.md)。本次以 `6e7573b` 為整理前基準，
> 尚未 commit。原有 persite_stats 權屬測試已存在，這次補主程式面積口徑接表測試；
> WKT 改放可讀 JSON fixture。陸域＋水域對照「圖面積」，不對照登記面積。
> road_access 保存既有成果與 SHA256，未重新計算。另納入 9/14 新增的 `--urban-image`。

- **日期**：2026-09-11
- **回溯點**：`pre-refactor-20260911`（tag，指向 `e65680c`）
- **範圍**：補三支黃金測試（零產線碼變動）＋ `make_maps` 參數解析抽出改 argparse
- **不在範圍**：`make_maps` 渲染迴圈、抓地籍三支、`deck_layout` 的行為、案件層 `build_deck.py`

---

## 一、為什麼做這件事

### 體檢發現

全專案 57 支腳本／17,863 行，21 支測試／314 測全過。體檢後浮現一個**結構性的對稱**：

| helper 層 | 測試數 | 對應 orchestrator | 測試數 |
|---|---|---|---|
| `parse_helpers` 343 行 | 58 | `parse_stage1` 553 行 | 0 |
| `zone_utils` | 52 | `greenbelt_ratio` 739 行 | 0 |
| `run_case_helpers` | 38（當時） | `run_case.main` | 更正：已有替身子程序整合測試，非零 |
| `shp_trust_utils` | 23 | `make_maps` 2,354 行 | 0 |
| `overlay_placement` | 7 | `make_extra_maps` 660 行 | 0 |

**部分主流程缺少端到端覆蓋，不能從 helper 測試通過推定全產線無誤。**

這不是疏忽，而是硬約束的結果：

```
✔ greenbelt_ratio   系統 python 可 import  → 可測
✔ persite_stats     可 import              → 可測
✔ deck_layout       可 import              → 可測
✖ road_access       需 osgeo（QGIS python）→ pytest 載不進來
✖ make_extra_maps   需 qgis                → 載不進來
✖ make_maps         需 qgis + osgeo        → 載不進來
```

`make_maps.py` 在模組層 `from qgis.core import ...`、`from osgeo import gdal, osr`，
所以 `import make_maps` 在系統 python 下必定失敗。**它零測試的真正原因是這個，不是懶。**
helper 之所以被抽出去，正是因為有人想測它們。

反過來說：**任何想被測到的邏輯，都必須住在不 import qgis 的模組裡。**
這一條決定了本設計的全部做法。

### 風險集中在哪

`make_maps.main()` 一個函式 **870 行**（佔全檔 37%），內含一個 **724 行的 `try:` 區塊**，
同時裝著 QGIS 初始化、圖層抽取、逐案場逐主題渲染、寫 qgz、寫 05 清單；前面還有
**99 行手刻 `if-elif` 參數解析**（24 個旗標）。

CLAUDE.md 記載的靜默失敗幾乎全發生在這支：龍崎案三圖層降級、`--basemap-dir` 路徑被當成
案名寫進岔路資料夾、檔案鎖導致出空圖仍回報成功、主題迴圈樣式殘留。870 行裡走岔一個分支，
沒有東西會叫。

### 體檢中排除的疑點（記錄下來，避免後人重查）

| 疑點 | 結論 |
|---|---|
| 117 個 except 中 76 個靜默吞掉（65%） | **不是問題**。抽樣後多為 QGIS 版本相容墊片（`setTextFormat`／`setHAlign`／`setWrapString` 舊版不存在）與刻意設計——`map_sanity` 明文「檢查本身絕不可成為新的失敗源」、`check_zones._load_layer` 的靜默 `continue` 是編碼重試迴圈、全部失敗才記 ⚠。深思過的，不是隨手寫的 |
| 21 支「孤島」腳本（import graph 上無連結） | **不是問題**。全部有文件記載或被 `run_case` 以 subprocess 呼叫；`deck_layout` 更被 17 個案件層 `build_deck.py` 匯入，是全專案最常被重用的模組。subprocess 呼叫本來就不會出現在 import graph |
| docstring 占 16～32% | **是資產**。承載踩坑脈絡，不動 |
| `place_fixed`、`normalize_landno` | 各自只有一份定義、正確共用，無重複 |
| 清冊模式 1,620 行（`parse_stage1`＋`match_parcels`＋`query_landno`＋`apply_landno`） | **保留為正式產線**（使用者 2026-09-11 確認：大批清冊案還會再來，只是最近沒碰到） |

### 已知但本輪不處理（記錄待辦）

- **「讀基地 shp → 轉 3826 → union」重複 9 次且不一致**：`greenbelt_ratio`／`building_coverage`
  用 `make_valid(union_all([make_valid(x) for x in ...]))`（踩過無效幾何才加的），
  `cadastre_neighbors`／`road_network` 只用 `site.geometry.union_all()`，
  `slope_check` 連 `engine="pyogrio"` 都沒帶。沒加 `make_valid` 的那幾支隨時可能踩同一個坑。
- **`_county_file` 原有兩份實作**：2026-09-16 已合併至 `shp_trust_utils.find_county_shp`，保留舊呼叫介面。
- **`parse_stage1` 553 行零測試**（清冊模式的 orchestrator，同型問題）。

---

## 二、Part B：黃金測試（零產線碼變動）

三支新測試檔，**一行產線程式碼都不改**。

### B1 `tests/test_greenbelt_ratio.py`

`greenbelt_ratio.py` 739 行、零測試，而它算出的 30%／1.5 公尺**會直接寫進送審簡報**。
目前這些數字只靠「跑出來看起來對」。

**黃金值**取自已交付的麻豆客子寮段案（2026-09-04 重算、已結案歸檔）：

| | 基地1（S001） | 基地2（S002） |
|---|---|---|
| 基地面積 | 14,801.51 ㎡ | 18,132.74 ㎡ |
| 應留設側共有邊界 | 487.8 m（周長 100%） | 556.0 m（100%） |
| 實算隔離綠帶 | **4,440.48 ㎡ ／ 30.00%** | **5,439.87 ㎡ ／ 30.00%** |
| 補足側內推距離 | 31.56 m | 17.39 m |
| 寬度檢核真不足點數 | 0 | 0 |

**幾何以 WKT 內嵌測試檔**，不用 fixture 檔——基地 WKT 僅 444／298 字元；鄰地只取
「距基地 ≤1.0 m」的緊鄰者（S001 11 筆、S002 7 筆）及其 `使類` 欄。
自我包含、可讀、無二進位檔。

**另加三條性質測試**（不綁特定案子，任何未來案子都該成立）：

1. 綠帶面積 ≥ 法定門檻（`ratio × 基地面積`）
2. 綠帶 ∩ 可配置範圍 = ∅
3. 綠帶 ∪ 可配置範圍 = 基地（浮點容差內）

性質測試比黃金值耐用：換案子、改參數都還在守。

### B2 `tests/test_persite_stats.py`

鎖 `classify_ownership` 的**值域**。CLAUDE.md 記載此處曾只判「公」開頭，
導致 ⓪a 路徑（值為「國有」「直轄市有」「縣市有」）的公有地**全部漏算**，
而 `逐案場統計.json` 是簡報數字的唯一來源。

- 公有側須認得：`公`、`國有`、`直轄市有`、`縣市有`
- 私有側：`私`、`私(推論)`
- 空白／未登記：不可誤判為私有
- 另鎖「陸域 ＋ 水域 = 基地面積」

### B3 `tests/test_deck_layout.py`

鎖**兩道既有防線本身**——防線壞掉最危險，而它現在沒有測試：

1. 缺圖時 `save()` 必須拋 `SystemExit`（不是印紅字就放過）
2. 交付字串含 markdown 標記（`**`、`##`、反引號）必須被 lint 擋下
3. 圖檔存在時 `save()` 正常存檔、不誤擋（反例，確保防線不會過度敏感）

### 為什麼 `road_access` 不在 B 裡

`road_access.py` 需要 `osgeo`，pytest 載不進來，寫不了單元測試。
改以「記錄回歸基準」保護：跑七股案存 `臨路評估/成果.xlsx` 的分類欄與數值欄，
日後若有人動門檻（直接臨路 ≤0.5 m／需搭橋 ≤1.5 m）時比對。
**本輪只記錄基準，不寫程式。**

---

## 三、Part A：`make_maps` 參數解析抽出改 argparse

### A1 新增 `scripts/make_maps_cli.py`

只做一件事：`parse_args(argv: list[str]) -> argparse.Namespace`。
**不 import qgis／osgeo**，所以 pytest 測得到——這是整個 Part A 的關鍵。

- 24 個旗標全搬：`--cases` `--exclude` `--trust-shp` `--sitemap` `--no-qgz` `--mem`
  `--reuse-basemap` `--zoning-local` `--zoning-wmts` `--basemap-dir` `--urban-shp`
  `--no-loc-parcel` `--loc-parcel` `--skip-extract` `--extract-only` `--overview-only`
  `--basic` `--layout` `--road-margin` `--base-line-themes` `--no-neighbor-themes`
  `--no-landno-themes` `--parcel-line-themes` `--themes` ＋ 位置參數 `case_name`
- **`allow_abbrev=False`**：關掉 argparse 預設的縮寫比對（`--road-mar` 不可匹配
  `--road-margin`），維持現行嚴格度
- 未知旗標仍硬停（argparse 預設行為）；錯誤訊息自訂，保留中文提示與 2026-08-04
  `--basemap-dir` 事故的脈絡註解

### A2 順手修一個實際缺陷

現行硬停訊息與 CLAUDE.md 都寫：

> 本腳本的值型旗標**皆**同時支援 `--flag=值` 與 `--flag 值` 兩種寫法

**這句話是錯的。** 實測 `--road-margin`、`--themes`、`--cases`、`--exclude`
四個**只支援 `=` 形式**（程式只有 `a.startswith("--road-margin=")`，沒有
`a == "--road-margin"` 分支）。所以打 `--road-margin 0` 會落到未知旗標分支硬停，
而錯誤訊息叫使用者改用剛剛失敗的那種寫法。

改 argparse 後兩種寫法都通——**純加法、向後相容**（原本能跑的指令全部照樣能跑）。
同時修正 CLAUDE.md 那句錯誤描述。

### A3 `make_maps.main()` 的改動範圍

只動開頭 99 行：

```python
a = make_maps_cli.parse_args(sys.argv[1:])
```

其後把區域變數換成 `a.xxx`。**724 行的渲染 `try:` 區塊一行不動。**

### A4 `tests/test_make_maps_cli.py`

- 逐旗標測 `--flag=值` 與 `--flag 值` 兩種寫法
- `--road-margin 0` 可用（**現行版本會炸，此測試即該缺陷的回歸鎖**）
- 未知旗標必須拋錯，不可靜默忽略（2026-08-04 事故的回歸鎖）
- `allow_abbrev=False`：`--road-mar` 必須拋錯
- `--layout` 只接受 `compact`／`maxmap`
- 逗號分隔集合型旗標（`--themes` 等）解析正確、去空白

---

## 四、安全網與驗證

`make_maps` 沒有單元測試可守（載不進 pytest），改用**既有產出當回歸基準**：

兩組基準指令（重構前後完全相同，含 `--reuse-basemap` 以排除圖磚下載造成的差異）：

```powershell
# 基準 A：麻豆（single 模式，無 --sitemap，七主題）
& "<QGIS>\bin\python-qgis-ltr.bat" scripts\make_maps.py 20260827_麻豆客子寮段256 `
    --trust-shp "outputs\20260827_麻豆客子寮段256\cadastre\基地.shp" `
    --layout maxmap --reuse-basemap --road-margin=0

# 基準 B：七股（single 模式，七主題）
& "<QGIS>\bin\python-qgis-ltr.bat" scripts\make_maps.py 20260910_七股新生段14光電 `
    --trust-shp "outputs\20260910_七股新生段14光電\cadastre\基地.shp" `
    --layout maxmap --reuse-basemap --road-margin=0
```

比對方式：讀兩次的 `05_出圖清單_maxmap.csv`，**丟棄「產出時間」欄**後逐格相等。

05 清單的欄位是 `案場ID,案場名稱,主題,檔名,比例尺,涵蓋地號,產出時間,狀態,備註`——
**比例尺、涵蓋地號、狀態、備註正是 `main()` 做的決策**，也正是重構會搬動的東西。

不比對圖片像素：QGIS 渲染有字型 hinting 等微小不確定性，雜訊會蓋掉訊號。

---

## 五、明確不動的東西

| 項目 | 理由 |
|---|---|
| `cadastre_fetch`／`moa_parcel_query`／`cadastre_neighbors` | 2026-09-04 才改寫、已有 19 個測試、而且是薄的 |
| `deck_layout` 的**行為** | 被 17 個案件層檔案匯入，動它等於動所有歷史案子的重建能力。只加測試 |
| `make_maps` 渲染迴圈（724 行） | 零測試、是所有案子的命脈。不在本輪 |
| 所有 docstring 與註解 | 承載踩坑脈絡，是資產 |
| 案件層 `build_deck.py` | 各案內容檔，非引擎 |
| `outputs/` 內既有成果 | 磁碟上的檔案，重構不會回頭改動；只有之後重跑才走新碼 |

---

## 六、執行順序與停損

```
1. B1 B2 B3 三支測試        → pytest 全過        → commit
2. 記錄 road_access 基準     → 存檔               → （併入 1 的 commit）
3. 重構前跑 05 清單基準      → 存 2 份 CSV        → 不 commit（暫存）
4. A1 抽 make_maps_cli       → A4 新測試過        → commit
5. A3 main() 接上            → 05 清單逐格比對    → commit
6. 修 CLAUDE.md 的錯誤描述   → 一併於 5 的 commit
```

**每一步獨立 commit；任何一步比對不過就停下回報，不硬推。**

回溯：`git checkout pre-refactor-20260911`

---

## 七、驗收標準

- [x] `pytest tests/ -q` 全過：整理前 338，本輪 385
- [x] `test_greenbelt_ratio.py` 的兩組黃金值與既有 `綠帶計算.json` 在指定浮點容差內相符
- [x] `test_make_maps_cli.py` 中 `--road-margin 0` 通過
- [x] 麻豆與七股兩案的 05 清單，除「產出時間」欄外逐格相符（案件副本、沿用圖層／底圖）
- [x] `make_maps.main()` 僅搬參數解析；實際以 9/16 版本由 985 降至 892 行，不再套用舊預估值
- [x] 值型旗標兩種寫法已實作並有測試，現行說明與程式一致

上述是重構驗收，不是完整交付 QA；QGIS 結束階段異常及回歸限制見最新驗證紀錄。
