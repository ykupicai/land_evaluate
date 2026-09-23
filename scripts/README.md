# scripts — 依產線順序的索引

新對話接手時：流程順序看 skill `cadastral-map-pipeline`，指令看
[`docs/免AI批次流程操作手冊.md`](../docs/免AI批次流程操作手冊.md)，本檔只回答
**「這支腳本是什麼、該不該碰」**。一次性與已淘汰的在 [`legacy/`](legacy/README.md)。

分層原則（詳 CLAUDE.md「案件成果沉澱機制」）：**本資料夾只放通用引擎**，
判準與清單寫在 `config/`（YAML），案件專屬內容寫在 `outputs/<案>/`（`_` 前綴的一次性腳本）。
換用途時的正常情況是「新增一份 YAML、不改這裡的碼」。

## 維護入口

| 腳本 | 用途 |
|---|---|
| `doctor.py` | 唯讀環境健檢；`--qgis` 驗 GIS 匯入（含 `gdal_array` 與 user-site 污染）、`--nas` 檢查網路圖資。安裝步驟見 [環境建置與驗證](../docs/環境建置與驗證.md) |
| `qgis_python.ps1` | **呼叫 QGIS 內建 Python 的唯一入口**（取代直接執行 `python-qgis-ltr.bat`）：設 `PYTHONNOUSERSITE=1` 隔離使用者 site-packages，避免別的專案裝的 numpy 2.x 讓底圖靜默消失。QGIS 路徑讀 `config/paths(.local).yaml` |
| `api_health.py` | 唯讀**外部服務**健檢（每週排程自動跑）：政府 API／圖台／圖磚今天還能不能用，含「都計圖台欄位有沒有被改名」。登錄表 `config/api_health.yaml`，說明見 [外部服務每週健檢](../docs/外部服務每週健檢.md) |
| `case_inventory.py` | 唯讀盤點案件 STATE 與成果存在性，輸出索引；不自動認定結案 |
| `config_loader.py` | 共用路徑設定；`paths.local.yaml` 可覆蓋 QGIS、外部引擎、NAS／DEM／養殖圖資位置 |
| `make_maps_cli.py` | `make_maps` 參數解析；獨立於 QGIS，可單元測試 |
| `qgis_session.py` | `make_maps` 生命週期：工作函式退場、清除 project 與循環參照後才呼叫 exitQgis；例外不吞掉 |

## 總控

| 腳本 | 用途 |
|---|---|
| `run_case.py` | 一鍵串起 清冊/shp → 七主題圖（全都市案六張） + .qgz。子進程編排，有停點會印下一步指令。**`--fetch <清冊>` 走 ⓪a 免登入主路徑**（抓地籍→自動轉入 trust-shp 流程；失敗硬停通知使用者，不自行改走平台）。**`--latlon <Google Maps 網址/'lat,lon'> --case <案名>` 走 ⓪a(反查)路徑**（座標→現行地段地號→基地.shp→同 trust-shp 流程；**單點單筆**，--case 必填；反查失敗硬停）→ 引擎 `latlon_to_parcel.py`、詳見 skill `landmark-to-parcel` |

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
| `moa_parcel_query.py` | **農業部地籍空間範圍查詢**（★2026-09-04 新增）：吃一個範圍（通常是 maxmap 出圖框）→ 回範圍內全部宗地的幾何＋屬性。走`CadastralMap/CadastralMap_Tiled_<版次>/MapServer`（layer 0=Penghu、1=Taiwan，maxRecordCount 2000、支援分頁），版次由 `moa.newest_version()` **動態組出、不可寫死**。屬性比 getLandInfo 還多（含**都市計畫區**）。與逐筆 ring API 比對 126 筆 **IoU 全部=1.000000**；密集市區大框 1,684 筆 **0.3 秒**回完（逐筆查要數小時）。⚠ 分頁少抓一律拋錯（少抓＝圖面留白）；權屬仍走 `public_land.py`。回歸測試 `tests/test_moa_parcel_query.py` |
| ↑ 上面兩支是**主路徑**（⓪a 免登入）。走不通時**停下通知使用者改走 TCDGIS**（需本人登入），退回條件見 skill `cadastral-map-pipeline` 的「⓪a 走不通時」一節 |  |
| `cadastre_neighbors.py` | **周邊地籍取得：出圖框直接向農業部框查**（★2026-09-04 全面改寫，麻豆客子寮段案定案）：由基地幾何算出 maxmap 出圖框（合併框＋**逐案場框**，`--sitemap` 給了才含後者）∪ `--buffer` 外擴環 → 整個範圍送 `moa_parcel_query` 空間查詢，一次取回範圍內**全部**宗地的現行幾何＋屬性，**不必事先知道有哪些地號**；排除基地自身（幾何重疊≥50%）、補權屬、寫 `鄰地.shp`，並以**框內覆蓋率**當硬指標（應為 100%，不足即明確警告並寫入 `鄰地_coverage_check.json`）。15 個既有案回測：**逐框全部 100.00%**（舊路徑 53.16%~100%），零資料遺失。⚠ 舊路徑「本機快照認地號→逐筆線上回填」保留為 **fallback**（`--force-local` 可強制），候選清單受限於快照 Ver 111年2月、成果會有留白，輸出會標明來源。**它救不了 coagis 全站斷線**——框查與逐筆回填打的是同一台主機、同一份 token；真正能救的是「Tiled 服務單獨不可用（如版次改名／下架）」。缺口偵測與子號探測補正已於 2026-09-04 移入 `scripts/legacy/cadastre_snapshot_probe.py`（框查不需事先知道地號，猜補的前提已不存在）。回歸測試 `tests/test_cadastre_neighbors.py`、`tests/test_moa_parcel_query.py` |
| `road_network.py` | **區位圖道路外擴基準**（2026-08-06）：算基地外擴到第一條國道／省道（OSM Overpass，本機無此資料）／市道／縣道／鄉道／市區快速道路（`2021 國道省道以外道路/`）為止的距離，供 `make_locality_map.py` 讀取取代固定 `--margin`。`run_case.py --fetch` 已自動接掛，產 `<案>/區位圖_道路基準.json` |
| `nluz_landzone.py` | **⓪c 國土功能分區逐筆查＋本機面圖層比對**（2026-08-06，需 QGIS 內建 python）：橋接 skill `nluz-landzone-query` 引擎，但吃 `<案>/cadastre/基地.shp`＋`鄰地.shp`（不吃清冊），同步用 `make_maps.load_zones_for_county` 算本機面圖層值，不同時**nluz優先**。產 `國土功能分區_逐筆核對.csv`＋`國土功能分區/逐筆覆蓋.gpkg`（`make_maps.py` 的 `apply_nluz_override` 出主題3時自動偵測套用）。**鄰地查不查依案型自動分流**（`--neighbors auto`：非都市土地案查基地+出圖範圍內鄰地／都市計畫案只查基地，判準見 `detect_land_kind()`）。⚠ **跨分區宗地不覆蓋**：nluz 對橫跨多分區者回複合描述字串，不在 QML 21類分類表內、硬覆蓋會上不了色，且本機面圖層對這種宗地更精確（真的切成多塊面）→ 排除於覆蓋外並在 CSV 標註（七股66-14實測）。⚠ 平台限流5.5秒/筆，先 `--limit` 試跑。⚠ 兩個PyQGIS陷阱已修：`QgsRectangle.combineExtentWith()`原地mutate回傳None（不可把回傳值指回變數，同 `make_maps.py` 既有 `X.combineExtentWith(Y) or X` 寫法防的同一坑）；`initQgis`/`exitQgis`同行程呼叫兩次會崩潰（exit code 5），全程須同一個QGIS session |
| `public_land.py` | **權屬查詢**（內政部 MOI_API_039 公有土地開放資料，**免登入**）：以段為單位回全段公有地之所有權人／公私別／管理者。布袋案與 TCDGIS 7/7 一致。⚠ 「未列＝私有」是反面推論；⚠ 管理機關可能是改制前舊名 |
| `urbanplan_fetch.py` | **縣市都市計畫使用分區**（接各縣市都發局 ArcGIS 圖台，**免登入**，2026-08-20；縣市登錄表驅動）：`--county <縣市> --joined/--bbox` 查逐筆分區，回幾何＋分區名／用地／**建蔽率／容積率**／計畫區／計畫案，**現行版**、精度可正確切出道路截角，勝本機 2019 粗版 shp。**✅穩定 bbox 路徑：臺南（up.tainan）、高雄（urbangis.kcg）、彰化（urbangis.chcg）、桃園（uparcgis.tycg /server/rest）、臺北（historygis.udd 免token）、新北（arcgis.planning.ntpc /server/rest，token 靜態JS即時抓＋insecure）、花蓮（map.hl.gov.tw proxy.ashx＋insecure）**（後五者 2026-08-24 新增，多含建蔽容積）；**✅穩定 land-number 反查路徑（`LANDNUM_COUNTY` 表，吃 段/地號，非 bbox）：宜蘭縣（eadgis.e-land，段碼標準4碼、landCode 吃人類地號含子號）、新竹市（urbangis.hccg，handler=hccg，token＋兩段式地號→WKT→分區，段碼全市唯一不需 town）**（2026-08-24 新增，`--sect/--landnos` 或 `--joined`）；**⚠暫不支援：新竹縣（imap.hchg，token 短期輪替＋動態注入，2026-08-24 再確認靜態仍抓不到）**；**✖ 走不通：臺中市（分區層 LandAdminNew3857 受權限鎖死，三種 token 全被擋；RALID 只回地籍無分區）、雲林（luz.nlma 僅全國圖台檢視器、identify 不支援）、臺東縣（PeopleMap 需登入）、nsp NGIS（Cloudflare Turnstile 禁繞過）**。輸出 ASCII 欄名（COUNTY/ZONE/ZONE_USE/BCR/FAR/PLAN_AREA/PLAN_NO）。未登錄／unstable 縣市退回本機粗版 shp。新增縣市＝照登錄表格式補一列。詳 `.claude/rules/出圖上色與分區.md`。⚠ make_maps 尚未自動接掛，先各案產 shp 再指定（整合為後續工作） |
| `urbanplan_image.py` | **縣市都發局「都市計畫使用分區圖」官方彩繪圖 → GeoTIFF**（2026-09-14；使用者定案「計算用向量、出圖用官方圖」）。與 `urbanplan_fetch.py` 分工：那支取**向量＋屬性**供計算與圖例，本檔走同一圖台的**圖磚服務** `/export`（臺北市＝`Urban/UrbanPlan/MapServer`，即該站圖層清單裡的「都市計畫使用分區圖」）取**官方彩繪圖**供出圖，一次解掉「圖台向量 renderer 對住宅區整類回 0,0,0（黑）」。`make_maps.py --urban-image[=<縣市>]` 會自動呼叫；縣市未登錄即自動退回向量著色。⚠ 圖磚快取無視 `dpi` 參數→圖上自帶的分區簡稱是螢幕尺度、印出來太小，故 make_maps 另疊一層全透明向量只借標註。詳 `.claude/rules/出圖上色與分區.md`。 |
| `district_fetch.py` | **行政區界向量**（縣市登錄表驅動；臺北市＝都發局 `UrbanPlan2/UrbanPlan2/MapServer/3`）→ `<案>/行政區界/行政區界.shp`。基地跨行政區時用；未登錄縣市可自備同路徑 shp（欄位需有 `區名`）。 |
| `district_line.py` | 出圖端：把上面那份 shp 畫成**區界虛線＋區名**。`district_layers()` **檔案在就畫、不在回空**，故 make_maps／make_env_map／make_hazard_map／make_extra_maps／make_locality_map 都已無旗標接入，舊案行為不變。⚠ 畫界線不畫面（全覆蓋面填色會蓋掉底下圖層）、區名要另開點層標（標在面層上會跑到圖框外）。 |
| `split_road.py` | **部分變更編定**（涉公路案）：LUIMAP 道路 ∩ 基地分割出去維持農牧、剔除銳角尖端與切分細條，產 申請範圍/保留_道路/鄰地_含保留地 三 shp 餵 greenbelt_ratio。前例＝布袋306-1；判準見衍古 `_判準登記表` B 表 |
| `building_coverage.py` | 現況建蔽率判讀（BUILDX 分棟建物框／航照）＋回推綠帶內應拆除面積。**分母以基地總面積為主要讀法**；30% 綠帶使可配置範圍恰為 70%，與建蔽率上限數值重合是巧合，不可再相乘 |
| `adjacent_landuse.py` | **選用分析**：計算基地周邊不同距離環域內宗地的使用分區／使用地類別組成，供毗鄰使用相容性檢討；目前不由 `run_case.py` 自動執行 |
| `slope_check.py` | **選用分析**：以 DEM 計算基地坡度分級與面積統計，山坡地或法規要求時執行；目前不由 `run_case.py` 自動執行 |
| `greenbelt_triangles.py` | **簡報／送審輔助**：把 `greenbelt_ratio.py` 的非綠帶範圍拆成三角形，再以基地面積扣除來回算綠帶並標示底、高與面積，供計算式及圖面說明；不參與綠帶合規判定 |

⚠ 建蔽率與幾何分析屬判讀／估算，非測量成果；交付文字須載明以建物測量成果或建築師簽證圖說為準。

## 出圖

| 腳本 | 用途 |
|---|---|
| `make_maps.py` | 七主題 A4 成果圖（全都市案六張） ＋ .qgz（maxmap 版面）。位置／涵蓋地號角落框**預設不畫**；基地 <2 公頃**自動不外擴**臨路圖框。**未知旗標硬停**（原靜默忽略，會把旗標值當成案名）；值型旗標皆支援 `--flag=值` 與 `--flag 值` |
| `make_locality_map.py` | 區位圖（EMAP2 透明置頂＋都市計畫分區） |
| `make_env_map.py` | 環境敏感套疊圖（無套疊則不出圖）。多圖層自動**分色**（保護類綠／災害類橙紅）；逐類面積依（圖層×分級）**彙總**；`<案>/環敏/<圖層名>.geojson` 可**覆蓋**過舊的本機圖資 |
| `make_hazard_map.py` | **坡地災害套疊圖**（山崩／崩塌／順向坡／土石流，正射為底）。接**山崩雲 API**（地礦中心：歷史山崩目錄／光達判釋潛在崩塌／山崩與地滑地質敏感區／**順向坡區**）＋**水保署 MVT 向量圖磚**（土石流潛勢溪流／影響範圍／集水區、大規模崩塌潛勢區／影響範圍、全臺崩塌地、事件型崩塌目錄），補上本機 `環境敏感相關/` **完全沒有**的這幾層。用法 `--case <案> [--county 臺北市] [--margin 260]`，須 QGIS python（要 osgeo 解 MVT）。產 `<案>/崩塌土石流/*.geojson` ＋ `坡地災害檢核.csv` ＋ 套疊圖。⚠ **預設外擴 260 m**：土石流常在數百公尺外，以基地為準的框裝不下就整組畫不出來、圖例還會被 legend filter 濾掉而看不出漏了東西。⚠ 兩平臺皆聲明「不可應用於法規作業」→ 只作交叉核對與現況說明。參數坑見 `docs/崩塌土石流圖資的兩條API.md` |
| `make_extra_maps.py` | 補充主題圖（`--theme`）：權屬分色／法定距離環域／養殖漁業生產區／**隔離綠帶或設施**（設施與綠帶分色＋圖例，農業用地變更案必備）／農田水利灌排水道（有涉及才出圖、標渠道名與工作站）／**坡度**（審議規範總編§16 三段分色＋疊緩衝綠帶，山坡地案必備）。⚠ **權屬主題**（2026-08-07 使用者定案之做法，之後一律照此）：①預設連 `<案>/cadastre/鄰地.shp` 一起畫（`--neighbours` 可指定）；②**周邊地籍同樣畫宗地外框線並標地號**（框線 0.3／字 9，基地為 0.4／11，靠粗細與紅框分主從）——只有色塊沒地號無法逐筆對照權屬；③**圖框以基地範圍為準**（與其他六主題同尺度，不因鄰地而外擴，否則基地縮成一點）；④分色吃 `persite_stats.classify_ownership` 正規化後的值（**全產線唯一權屬判定**，勿另寫一份），原本拿 shp 原始字串當 key 會讓「私(推論)」「國有」全落到灰色。⚠ 周邊層**不可再疊 `setOpacity()`**：符號已帶 alpha，再乘一次會變 0.30、在正射底圖上幾乎看不見 |

出圖支援模組（被 import，不單獨執行）：`build_layout.py`（版面計算）、
`overlay_placement.py`（角落疊圖避讓）、`nlsc_wmts.py`（WMTS 圖磚拼接）、
`shp_trust_utils.py`（DBF 容錯／地號正規化／sitemap 分派／**欄名別名表 `F_*`**）、
`zone_utils.py`（分區名正規化）、
`ownership_style.py`（權屬共用配色）、`env_color_assign.py`（環敏配色）、
`deck_layout.py`（簡報版面引擎；**內建兩道產出檢核**：交付字串不得含 markdown
標記、以及**缺圖硬停**——見下方第 6 點）、
`map_sanity.py`（**出圖成果自我檢查**：出圖前擋 0 筆圖層、出圖後驗成品非空白，
見下方第 4 點）、
`urban_mix.py`（**基地都市／非都市組成判定**：全都市→只出使用分區，混合或全非都市→
兩張都出，見下方第 5 點）、
`parse_helpers.py`（解析工具）、`csv_to_xlsx.py`（獨立乾淨子進程寫 xlsx，繞開 openpyxl×GDAL 衝突）。

**改出圖腳本前先知道的六件事**（保留各次事故與補強防線的脈絡）：

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
4. **「exportToImage 成功」不等於「圖裡有東西」**——它只代表檔案寫出來了。上面 1~3
   與縣市欄空白那類問題，全都會出成一張只有紅框的空白圖、05 清單照樣記「成功」
   （龍崎案錯了整整一輪才發現）。`map_sanity.py` 補上兩道：出圖**前**
   `check_theme_layers` 擋 0 筆的關鍵圖層（決定性，不會誤判）；出圖**後**
   `check_blank_image` 驗成品不是整片單色（抓底圖被鎖／tif 壞掉）。兩者都記
   「失敗」＋原因，既有的 `pipeline_check.py sheets` 閘門因此自動生效。
   ⚠ 空白門檻（99.5%）**刻意訂得極保守——寧可漏抓，不可誤殺**：誤判會把整條產線
   擋掉，比漏抓貴。已對 177 張既有交付成果圖實測 0 誤判；改門檻前先重跑該驗證與
   `tests/test_map_sanity.py`。
5. **使用分區／使用地編定要不要都出，依基地組成自動決定**（2026-08-26 使用者定案）：
   ①全都市計畫區→**只出使用分區**（都市土地無使類，兩張圖會一模一樣；員林案實見兩檔
   同為 2,875 KB、內容相同）②都市＋非都市→兩張都出 ③全非都市→兩張都出。
   引擎＝`urban_mix.py`，`make_maps` 於案場迴圈自動判定、略過者記入 05 清單「略過」欄
   並**刪除前次殘留的同名舊圖**（避免「清單說略過、資料夾卻有圖」）。
   ⚠ **判定不可只看使分／使類空白**——空白同時可能是都市土地或未登錄地（CLAUDE.md
   已載、2026-08-06 使用者親自更正）。故須「空白**且**落在都市計畫圖層內」才算都市；
   未登錄地（母號 ≥9000）不參與投票；**判定不足一律兩張都出**（寧可多一張，不可漏一張）。
   回歸測試 `tests/test_urban_mix.py`。

**出圖的圖面微調旗標**（皆**主題限定**、可自由組合，不給則完全維持原行為）：
`--no-landno-themes=<主題…>` 不標地號／`--base-line-themes=<主題…>` 基地框改黑／
`--parcel-line-themes=<主題…>` 周邊宗地界線改黑／`--no-neighbor-themes=<主題…>` 不畫
周邊地籍界線。⚠ 圖層在案場內是**共用的同一份物件**，故於主題迴圈內逐張重設，否則前
一張的樣式會殘留到下一張。

6. **簡報缺圖一律硬停，不得只在版面上放紅字放行**（2026-08-27 麻豆案實踩）：
   `deck_layout.pic()` 原本找不到圖檔時只放一行「（圖檔缺 xxx）」紅字就繼續，結果兩份
   變更編定簡報**各缺 5 張圖仍回報「saved / slides 8」**，要到逐頁目視才發現。
   簡報每一頁的圖就是該頁主題的成果，缺圖等同該頁沒有依據——這種錯必須在產出當下擋，
   不能靠人眼補救。現行行為：缺圖登記到 `_missing_pics`，`save()` 直接 `SystemExit`
   並印出頁碼與檔名，**不產出 pptx**。
   ⚠ 最常見的缺圖原因是**檔名對不上**：`make_maps` 的成果圖命名是
   `<案場ID>_<案場名稱>_<主題>.png`（例 `S001_S001宗基地_使用分區.png`），
   案件層另外產的主題圖（如本案的隔離綠帶）才可能是別的命名，內容檔要分開處理。

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
| `rebuild_05.py` | **舊案專用**：固定 `20260707-清潔隊部資訊`、四主題、數字案場 ID。不可當新案七主題／trust-shp 通用重建器；現行清單由 `make_maps.py` 產生 |

## 建置工具（只在圖資／樣式來源更新時重跑）

| 腳本 | 產出 |
|---|---|
| `build_registry.py` | `config/county_registry.yaml`（縣市圖資註冊表） |
| `build_section_index.py` | `config/section_index.csv`（現行段小段索引，14,331 筆） |
| `build_legend_styles.py` | `styles/*.qml`（自 QGIS 樣式庫取使分／使類官方配色） |
