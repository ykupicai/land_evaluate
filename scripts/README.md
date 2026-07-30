# scripts — 依產線順序的索引

新對話接手時：流程順序看 skill `cadastral-map-pipeline`，指令看
[`docs/免AI批次流程操作手冊.md`](../docs/免AI批次流程操作手冊.md)，本檔只回答
**「這支腳本是什麼、該不該碰」**。一次性與已淘汰的在 [`legacy/`](legacy/README.md)。

分層原則（詳 CLAUDE.md「案件成果沉澱機制」）：**本資料夾只放通用引擎**，
判準與清單寫在 `config/`（YAML），案件專屬內容寫在 `outputs/<案>/`（`_` 前綴的一次性腳本）。
換用途時的正常情況是「新增一份 YAML、不改這裡的碼」。

## 總控

| 腳本 | 用途 |
|---|---|
| `run_case.py` | 一鍵串起 清冊/shp → 六主題圖 + .qgz。子進程編排，有停點會印下一步指令 |

## 階段0：先攔障礙（評估案第一步）

| 腳本 | 用途 |
|---|---|
| `check_zones.py` | 禁止事由檢核引擎（YAML 驅動）。項目類型 overlay/distance/area/attribute/settlement/api_distance/inquiry |
| `settlement_check.py` | 十戶以上村莊或聚落判定（門牌坐標開放資料聚類）＋門牌資料下載器 |
| `nlsc_facility.py` | 醫療／文教／鄰避設施環域查詢（國土測繪中心免認證 API） |
| `osm_sensitive_poi.py` | OSM 敏感設施初篩。**已降級為輔助**（社福、宗教場所用），聚落判定不要再用它 |
| `persite_stats.py` | 逐案場統計 → `逐案場統計.json`，簡報數字的唯一來源 |

## 輸入解析與地籍比對

| 腳本 | 用途 |
|---|---|
| `parse_stage1.py` | 清冊（xlsx/PDF）解析 → 01_中繼檔。**換版式才需要 AI 介入** |
| `match_parcels.py` | 01 中繼檔 × 本機地籍 shp 比對 → 02_比對結果 |
| `query_landno.py` | 比不到的地號查內政部新舊地號 → 04_對照清單 |
| `apply_landno.py` | 把 04 的對照結果回填清冊後重比 |
| `shp_to_meta.py` | 直接給基地 shp 時免解析：dbf → 01_中繼檔 |

## 案場定義與臨路

| 腳本 | 用途 |
|---|---|
| `build_sites_sitemap.py` | 相鄰宗地聚類 → **帶「段」欄的權威 sitemap**（多案場必做） |
| `rebuild_sitemap_spatial.py` | 歷史案救援：以既有 sites.shp 幾何重建帶段欄 sitemap |
| `road_access.py` | 臨路評估（LUIMAP 向量化＋臨路三態＋地形）。**須用 QGIS 內建 python**。⚠ 門檻與道路關鍵字非經使用者同意不得改 |

## 出圖

| 腳本 | 用途 |
|---|---|
| `make_maps.py` | 六主題 A4 成果圖 ＋ .qgz（maxmap 版面） |
| `make_locality_map.py` | 區位圖（EMAP2 透明置頂＋都市計畫分區） |
| `make_env_map.py` | 環境敏感套疊圖（無套疊則不出圖） |
| `make_extra_maps.py` | 補充主題：權屬分色／環域／養殖漁業生產區 |

出圖支援模組（被 import，不單獨執行）：`build_layout.py`（版面計算）、
`overlay_placement.py`（角落疊圖避讓）、`nlsc_wmts.py`（WMTS 圖磚拼接）、
`shp_trust_utils.py`（DBF 容錯／地號正規化／sitemap 分派）、`zone_utils.py`（分區名正規化）、
`parse_helpers.py`（解析工具）、`csv_to_xlsx.py`（獨立乾淨子進程寫 xlsx，繞開 openpyxl×GDAL 衝突）。

## 簡報

| 腳本 | 用途 |
|---|---|
| `deck_layout.py` | 版面引擎，30+ 條寫作規則的唯一實作處。內建**防蓋改鎖**（使用者手改過 pptx 後重跑會硬停） |

案件的 `build_deck.py` 只寫內容，放在 `outputs/<案>/`。

## 成果整理

| 腳本 | 用途 |
|---|---|
| `reorg_sheets_persite.py` | 把攤平的圖檔重整為 `<案場ID>_<名稱>/<主題>.png` 並同步改寫 05 清單 |
| `organize_outputs.py` | 案件資料夾結構整理 |
| `rebuild_05.py` | 從 02 ＋ 出圖成果重建涵蓋全部案場的 05 出圖清單 |

## 建置工具（只在圖資／樣式來源更新時重跑）

| 腳本 | 產出 |
|---|---|
| `build_registry.py` | `config/county_registry.yaml`（縣市圖資註冊表） |
| `build_section_index.py` | `config/section_index.csv`（現行段小段索引，14,331 筆） |
| `build_legend_styles.py` | `styles/*.qml`（自 QGIS 樣式庫取使分／使類官方配色） |
