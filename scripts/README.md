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
| `run_case.py` | 一鍵串起 清冊/shp → 六主題圖 + .qgz。子進程編排，有停點會印下一步指令。**`--fetch <清冊>` 走 ⓪a 免登入主路徑**（抓地籍→自動轉入 trust-shp 流程；失敗硬停通知使用者，不自行改走平台）。**`--latlon <Google Maps 網址/'lat,lon'> --case <案名>` 走 ⓪a(反查)路徑**（座標→現行地段地號→基地.shp→同 trust-shp 流程；**單點單筆**，--case 必填；反查失敗硬停）→ 引擎 `latlon_to_parcel.py`、詳見 skill `landmark-to-parcel` |

## 階段0：先攔障礙（評估案第一步）

| 腳本 | 用途 |
|---|---|
| `check_zones.py` | 禁止事由檢核引擎（YAML 驅動）。項目類型 overlay/distance/area/attribute/settlement/api_distance/inquiry |
| `settlement_check.py` | 十戶以上村莊或聚落判定（門牌坐標開放資料聚類）＋門牌資料下載器 |
| `nlsc_facility.py` | 醫療／文教／鄰避設施環域查詢（國土測繪中心免認證 API） |
| `social_welfare_facility.py` | 社福機構200公尺距離查詢（衛福部開放資料 CSV → 社福機構/*.gpkg）。⚠ 主類別篩選（CATEGORY_GUESS）是人工從機構名稱樣本推測，非官方字典，命中或接近門檻務必人工核對 |
| `osm_sensitive_poi.py` | OSM 敏感設施初篩。**已降級為輔助**（社福、宗教場所用），聚落判定不要再用它。`nearest_category()`（2026-08-06 新增）供 `check_zones.py` 的 `osm_distance` 型別呼叫，目前用於「營區200公尺」——判定恆為須函詢，OSM 只附參考距離 |
| `persite_stats.py` | 逐案場統計 → `逐案場統計.json`，簡報數字的唯一來源 |
| `ngis_sensitive.py` | **⓪b 環敏套疊（★正式程序，2026-08-05）**：橋接 skill `ngis-sensitive`，把 ⓪a 基地幾何餵 NGIS 官方 54 圖層（一級25/二級29）做**實際空間套疊**，回命中項＋重疊面積＋佔比。免登入 <1 秒；`--per-site` 逐案場。龍崎三方驗證（山坡地一字不差、自然保留區 8.88㎡ 與平台相同）。⚠ 初步評估非正式套繪；描述性欄位上游有亂碼勿引用；**勿走 skill 的 `check.py --parcel`**（重查地籍慢 20 秒） |
| `env_prescreen.py` | **備選①**（需法定「應查/免查」口徑時用）環敏**免登入**初篩＋與平台結果對帳。初篩用**離線『應免查範圍』PDF**（取代線上 eland，2026-08-05）判應查/免查 → 應查項套疊遠端/本機圖資。橋接 skill `env-sensitivity-check`。自動讀 ⓪a 的 `cadastre/基地.shp`；有 `環境敏感套疊.csv` 時產 `環境敏感對帳.csv` 標★不一致。**初篩非正式判定，法定用途以平台／正式查復為準** |
| `parse_exempt_pdf.py` | 官方『應免查範圍』PDF → `config/環敏應免查範圍.json`（初篩資料源）。⚠ 長項目名會換行、免查列取相反、粒度三種——坑寫在 `tests/test_exempt_prescreen.py` |
| `update_exempt.py` | 每月更新初篩 PDF：`--download` **一鍵免登入下載**（POST eland `ExportPdfAll`）＋重解析；或 `--pdf` 給本地檔重解析。PDF 不進版控、JSON 進 |
| `env_registry.py` | **薄殼**（2026-08-05）：單一登記表已移至 skill `env-sensitivity-check/tgos_layers.py`（遠端＋本機同檔），本檔 re-export 並注入 env_root＝`環境敏感相關/`。改圖層去該檔，不是這裡 |
| `tgos_fetch.py` | TGOS 本機環敏 shp **換版下載**（免登入）：`--list`／`--layer 自然保留區`／`--all`。NR23 教訓——本機 105~106 年快照查不到新增範圍，TGOS 有新版 SHP 直接下載。換版後核對 `test_env_registry` 版本順序 |

## 輸入解析與地籍比對

| 腳本 | 用途 |
|---|---|
| `parse_stage1.py` | 清冊（xlsx/PDF）解析 → 01_中繼檔。**換版式才需要 AI 介入** |
| `match_parcels.py` | 01 中繼檔 × 本機地籍 shp 比對 → 02_比對結果 |
| `query_landno.py` | 比不到的地號查內政部新舊地號 → 04_對照清單 |
| `apply_landno.py` | 把 04 的對照結果回填清冊後重比 |
| `shp_to_meta.py` | 直接給基地 shp 時免解析：dbf → 01_中繼檔 |
| `latlon_to_parcel.py` | **反查**：Google Maps 網址／短網址／經緯度 → 現行地段地號（＋使分/使類/面積）。與正查方向相反。展開網址抓地標點(`!3d!4d`不是`@`中心)→WGS84→3826→農業部identify定縣市→本機快照出候選→**段名先驗**→農業部即時確認→需要時**收斂式子號探測**（分割案例，探到含點即停/連續15筆查無才止）。`--out` 寫 `基地.shp` 可直接餵 `make_maps --trust-shp` 出圖；**已接掛 `run_case.py --latlon <網址> --case <案名>`**（反查→基地→周邊地籍→出圖一鍵，單點單筆）。⚠ 三個守門（座標抓對組/段名含小段/段名先驗排在探測前）看 skill `landmark-to-parcel`；死角＝跨母號分割/重測改號回 `FAIL_CROSS_MOTHER`（需線上空間反查，未接掛）。須 QGIS python。測試 `tests/test_latlon_to_parcel.py`（純函式）＋三真實案例 |

## 案場定義與臨路

| 腳本 | 用途 |
|---|---|
| `build_sites_sitemap.py` | 相鄰宗地聚類 → **帶「段」欄的權威 sitemap**（多案場必做） |
| `rebuild_sitemap_spatial.py` | 歷史案救援：以既有 sites.shp 幾何重建帶段欄 sitemap |
| `road_access.py` | 臨路評估（LUIMAP 向量化＋臨路三態＋地形）。**須用 QGIS 內建 python**。⚠ 門檻與道路關鍵字非經使用者同意不得改 |

## 配置量化（綠帶／建蔽率）

| 腳本 | 用途 |
|---|---|
| `greenbelt_ratio.py` | 隔離綠帶或設施：**最小寬度＋面積比雙門檻**，可指定加寬側。`--only-on 農業用地` 才是變更編定的正解（義務只對相鄰農業用地，定義見農地變更要點§2②，不含交通用地）。`--shape minimal`＝最少頂點構法（送審計算式用）、`--facility-near`＝臨路側分類為隔離設施 |
| `cadastre_fetch.py` | 橋接 skill `cadastre-check`：**免登入**取官方地籍幾何＋使分/使類/登記面積**＋權屬**（自動呼叫 `public_land.py`）→ 產線可吃的 shp（`fetch`），並與平台官方 shp 對帳 IoU（`verify`）。⚠ 環敏53項與離島幾何仍須上 TCDGIS |
| ↑ 上面兩支是**主路徑**（⓪a 免登入）。走不通時**停下通知使用者改走 TCDGIS**（需本人登入），退回條件見 skill `cadastral-map-pipeline` 的「⓪a 走不通時」一節 |  |
| `cadastre_neighbors.py` | **周邊地籍自動發現＋即時屬性回填**（2026-08-06）：評估範圍外擴 N 公尺（預設50）內與地籍有交集者，先用本機縣市快照做**空間識別**（只認地號身分、不採其屬性），再把發現的地號餵回 ⓪a 即時查（getLandInfo），產帶**現行**使分/使類/權屬的「鄰地.shp」——取代舊快照或 WMTS 疊圖作為分區/使用地編定等評估基準。`make_maps.py` 的 `extract_layers_trust` 已改為優先吃這份鄰地.shp（`ZONING_WMTS` 預設改 False，WMTS 降為 `--zoning-wmts` 備選）；`run_case.py --fetch` 已自動接掛（非硬性閘門，失敗只警告）；`--shp` 模式尚未接掛，需要時手動跑。⚠ **本機快照可能漏收「已從舊地號分割出的新地號」**（尤其交通用地/公路用地，七股案 66-42 即從 66-23 分割出、本機快照沒跟上）——內建 `check_coverage_gap()` 覆蓋率自我檢查（比對本機快照候選聯集 vs 即時回填後實際聯集，緊鄰20m內），有落差**自動探測補正**（`probe_siblings()`：找候選宗地母號、批次探測子號1~80、只留落在缺口範圍內者），全程免人工介入；探測用**整塊缺口幾何**找候選母號（不是單一取樣點——七股案67-11教訓：缺口常橫跨多個母號，單點取樣會漏掉另一側），並排除基地自身地號避免誤收。★2026-08-10 潮州中山段20案再強化：探測目標改以 `find_shrunk_mothers()`「**母號縮水**」精準鎖定（快照母號面積 vs 即時回填面積 <0.9 者才探；潮州 16 個緊鄰母號僅 2 個真縮水，避免逐一探 80 子號逾時），且**補正寫入 `_safe_to_file()` 抗檔案鎖**（遇 WinError 32 重試／改備援、不 crash——潮州案原本探到子號卻因寫回鄰地.shp 被鎖而整批丟失）。回歸測試 `tests/test_cadastre_neighbors.py` |
| `road_network.py` | **區位圖道路外擴基準**（2026-08-06）：算基地外擴到第一條國道／省道（OSM Overpass，本機無此資料）／市道／縣道／鄉道／市區快速道路（`2021 國道省道以外道路/`）為止的距離，供 `make_locality_map.py` 讀取取代固定 `--margin`。`run_case.py --fetch` 已自動接掛，產 `<案>/區位圖_道路基準.json` |
| `nluz_landzone.py` | **⓪c 國土功能分區逐筆查＋本機面圖層比對**（2026-08-06，需 QGIS 內建 python）：橋接 skill `nluz-landzone-query` 引擎，但吃 `<案>/cadastre/基地.shp`＋`鄰地.shp`（不吃清冊），同步用 `make_maps.load_zones_for_county` 算本機面圖層值，不同時**nluz優先**。產 `國土功能分區_逐筆核對.csv`＋`國土功能分區/逐筆覆蓋.gpkg`（`make_maps.py` 的 `apply_nluz_override` 出主題3時自動偵測套用）。**鄰地查不查依案型自動分流**（`--neighbors auto`：非都市土地案查基地+出圖範圍內鄰地／都市計畫案只查基地，判準見 `detect_land_kind()`）。⚠ **跨分區宗地不覆蓋**：nluz 對橫跨多分區者回複合描述字串，不在 QML 21類分類表內、硬覆蓋會上不了色，且本機面圖層對這種宗地更精確（真的切成多塊面）→ 排除於覆蓋外並在 CSV 標註（七股66-14實測）。⚠ 平台限流5.5秒/筆，先 `--limit` 試跑。⚠ 兩個PyQGIS陷阱已修：`QgsRectangle.combineExtentWith()`原地mutate回傳None（不可把回傳值指回變數，同 `make_maps.py` 既有 `X.combineExtentWith(Y) or X` 寫法防的同一坑）；`initQgis`/`exitQgis`同行程呼叫兩次會崩潰（exit code 5），全程須同一個QGIS session |
| `public_land.py` | **權屬查詢**（內政部 MOI_API_039 公有土地開放資料，**免登入**）：以段為單位回全段公有地之所有權人／公私別／管理者。布袋案與 TCDGIS 7/7 一致。⚠ 「未列＝私有」是反面推論；⚠ 管理機關可能是改制前舊名 |
| `urbanplan_fetch.py` | **縣市都市計畫使用分區**（接各縣市都發局 ArcGIS 圖台，**免登入**，2026-08-20；縣市登錄表驅動）：`--county <縣市> --joined/--bbox` 查逐筆分區，回幾何＋分區名／用地／**建蔽率／容積率**／計畫區／計畫案，**現行版**、精度可正確切出道路截角，勝本機 2019 粗版 shp。**✅穩定：臺南市（up.tainan）、高雄市（urbangis.kcg）**；**⚠探勘完成暫不支援：新竹縣（imap.hchg，token 短期輪替＋動態注入、靜態自動化不穩）**。輸出 ASCII 欄名（COUNTY/ZONE/ZONE_USE/BCR/FAR/PLAN_AREA/PLAN_NO）。未登錄／unstable 縣市退回本機粗版 shp。新增縣市＝照登錄表格式補一列。詳 `.claude/rules/出圖上色與分區.md`。⚠ make_maps 尚未自動接掛，先各案產 shp 再指定（整合為後續工作） |
| `split_road.py` | **部分變更編定**（涉公路案）：LUIMAP 道路 ∩ 基地分割出去維持農牧、剔除銳角尖端與切分細條，產 申請範圍/保留_道路/鄰地_含保留地 三 shp 餵 greenbelt_ratio。前例＝布袋306-1；判準見衍古 `_判準登記表` B 表 |
| `building_coverage.py` | 現況建蔽率判讀（BUILDX 分棟建物框／航照）＋回推綠帶內應拆除面積。**分母以基地總面積為主要讀法**；30% 綠帶使可配置範圍恰為 70%，與建蔽率上限數值重合是巧合，不可再相乘 |
| `adjacent_landuse.py` | **選用分析**：計算基地周邊不同距離環帶的國土利用組成，供毗鄰使用相容性檢討；目前不由 `run_case.py` 自動執行 |
| `slope_check.py` | **選用分析**：以 DEM 計算基地坡度分級與面積統計，山坡地或法規要求時執行；目前不由 `run_case.py` 自動執行 |
| `greenbelt_triangles.py` | **簡報／送審輔助**：把 `greenbelt_ratio.py` 的綠帶幾何拆成三角形並標示底、高與面積，供計算式及圖面說明；不參與綠帶合規判定 |

⚠ 兩支都是判讀／幾何估算，非測量成果；交付文字須載明以建物測量成果或建築師簽證圖說為準。

## 出圖

| 腳本 | 用途 |
|---|---|
| `make_maps.py` | 六主題 A4 成果圖 ＋ .qgz（maxmap 版面）。位置／涵蓋地號角落框**預設不畫**；基地 <2 公頃**自動不外擴**臨路圖框。**未知旗標硬停**（原靜默忽略，會把旗標值當成案名）；值型旗標皆支援 `--flag=值` 與 `--flag 值` |
| `make_locality_map.py` | 區位圖（EMAP2 透明置頂＋都市計畫分區） |
| `make_env_map.py` | 環境敏感套疊圖（無套疊則不出圖）。多圖層自動**分色**（保護類綠／災害類橙紅）；逐類面積依（圖層×分級）**彙總**；`<案>/環敏/<圖層名>.geojson` 可**覆蓋**過舊的本機圖資 |
| `make_extra_maps.py` | 補充主題圖（`--theme`）：權屬分色／法定距離環域／養殖漁業生產區／**隔離綠帶或設施**（設施與綠帶分色＋圖例，農業用地變更案必備）／農田水利灌排水道（有涉及才出圖、標渠道名與工作站）／**坡度**（審議規範總編§16 三段分色＋疊緩衝綠帶，山坡地案必備）。⚠ **權屬主題**（2026-08-07 使用者定案之做法，之後一律照此）：①預設連 `<案>/cadastre/鄰地.shp` 一起畫（`--neighbours` 可指定）；②**周邊地籍同樣畫宗地外框線並標地號**（框線 0.3／字 9，基地為 0.4／11，靠粗細與紅框分主從）——只有色塊沒地號無法逐筆對照權屬；③**圖框以基地範圍為準**（與其他六主題同尺度，不因鄰地而外擴，否則基地縮成一點）；④分色吃 `persite_stats.classify_ownership` 正規化後的值（**全產線唯一權屬判定**，勿另寫一份），原本拿 shp 原始字串當 key 會讓「私(推論)」「國有」全落到灰色。⚠ 周邊層**不可再疊 `setOpacity()`**：符號已帶 alpha，再乘一次會變 0.30、在正射底圖上幾乎看不見 |

出圖支援模組（被 import，不單獨執行）：`build_layout.py`（版面計算）、
`overlay_placement.py`（角落疊圖避讓）、`nlsc_wmts.py`（WMTS 圖磚拼接）、
`shp_trust_utils.py`（DBF 容錯／地號正規化／sitemap 分派／**欄名別名表 `F_*`**）、
`zone_utils.py`（分區名正規化）、
`parse_helpers.py`（解析工具）、`csv_to_xlsx.py`（獨立乾淨子進程寫 xlsx，繞開 openpyxl×GDAL 衝突）。

**改出圖腳本前先知道的三件事**（皆 2026-08-04 修，都會靜默出錯）：

1. **底圖要照「版面引擎算出的最終圖框」抓，不是基地 bbox**——圖框因 A4 比例與整數
   比例尺退階而更寬，用 bbox 抓成品會左右露白邊。作法：先呼叫一次
   `overlay_placement.place_overlays(...).extent`（純幾何、無副作用），再交給
   `build_sheet_layout` 重算（同輸入必得同結果）。`make_maps` 一直是對的
   （用 `c["extent"]`），`make_env_map`／`make_extra_maps` 已補齊。
2. **多個圖層疊在同一張圖時，每層必須是可區辨的顏色**——否則圖例好幾格同一個紅、
   看不出哪塊屬哪項。`make_env_map` 以 REGISTRY 的 `rgb` 指定語意色，
   未指定者取 `GENERIC_PALETTE` 色環（色相間距 ≥60°）。
3. **右欄圖例只有 45mm 寬**，`build_layout` 以 `setWrapString("／")` 換行 →
   **長標籤裡沒有「／」就會被直接切掉**。另光柵圖例會自動多一行「Band 1 (Gray)」，
   `build_layout` 已逐層挑掉該節點。

**新增基地 shp 來源時只改一處**：`shp_trust_utils` 的 `F_LANDNO/F_COUNTY/F_TOWN/
F_SEG/F_ZONE/F_LTYPE`。② area_join 用 `20251201_*`、⓪a cadastre_fetch 用中文短欄名
（縣市/鄉鎮/段/使分/使類）；漏一個欄名的後果見 CLAUDE.md「踩過的坑」——
周邊地籍、功能分區、使分/使類三者會一起靜默降級而回報「成功」。

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
