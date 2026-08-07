# 免 AI 批次流程操作手冊

給定正確的檔案與路徑，以下各段可在終端機直接執行，**全程不需要我或任何 AI 模型參與**。
只有「階段1輸入解析」在換成新版式文件時需要 AI 協助寫一次解析規則；規則寫好後同版式文件也能重複免 AI 執行。

> 最後更新 2026-07-29。**重大變更**：臨路評估已內建（不再相依外部 fishpv 專案），
> 步驟名 `fishpv` → `roadaccess`（`--from fishpv` 仍相容）；新增四支補充出圖腳本。

## 總覽

| 階段 | 腳本 | 需要 AI？ | 需要瀏覽器？ |
|---|---|---|---|
| 0. 禁止事由檢核（★第一步） | `scripts/check_zones.py` | 否（YAML 驅動） | 否 |
| 0b. 逐案場統計 | `scripts/persite_stats.py` | 否 | 否 |
| 0c. 聚落判定（十戶以上聚落條款） | `scripts/settlement_check.py` | 否（門牌開放資料） | 否 |
| 0d. 設施環域查詢（醫療／文教／鄰避） | `scripts/nlsc_facility.py` | 否（測繪中心 API） | 否 |
| 1. 輸入解析（PDF/圖片→中繼檔） | `scripts/parse_stage1.py` | 換版式才需要（見下） | 否 |
| 2. 比對地籍 | `scripts/match_parcels.py` | 否 | 否 |
| 2b. 新舊地號查詢 | `scripts/query_landno.py` | 否（純 HTTP `requests`） | 否 |
| 2c. 案場定義 | `scripts/build_sites_sitemap.py` | 否 | 否 |
| 2d. 臨路評估 | `scripts/road_access.py`（**產線內建**） | 否 | 否（QGIS 內建 Python） |
| 0a. 免登入取地籍＋權屬（基地＋鄰地自動發現） | `scripts/cadastre_fetch.py`、`scripts/cadastre_neighbors.py`、`scripts/public_land.py` | **是**（免帳號免驗證碼） | 否 |
| 0a'. 區位圖道路外擴基準 | `scripts/road_network.py` | 是（國道/省道查OSM） | 否 |
| 3. 出圖（六主題） | `scripts/make_maps.py` | 否 | 否（QGIS 內建 Python） |
| 3b. 區位圖 | `scripts/make_locality_map.py` | 否 | 否 |
| 3c. 環境敏感套疊圖 | `scripts/make_env_map.py` | 否 | 否 |
| 3d. 補充主題圖（權屬／環域／養殖漁業生產區／隔離綠帶／灌排水道） | `scripts/make_extra_maps.py` | 否 | 否 |
| 3f. 隔離綠帶面積計算式圖說 | `scripts/greenbelt_triangles.py` | 否 | 否（不需 QGIS） |
| 3e. 敏感設施距離初篩 | `scripts/osm_sensitive_poi.py` | 否（Overpass API） | 否 |
| 3後. 整理成果 | `scripts/reorg_sheets_persite.py`、`organize_outputs.py`、`rebuild_05.py` | 否 | 否 |

不在此表：政府圖台（NGIS/TCDGIS）操作，仍需人工瀏覽器（含帳密登入、圖形驗證碼），見文末。
**自 2026-08-02 起，平台只為「環境敏感套疊 53 項」而登入**——幾何、使分使類、登記面積、
權屬都改由階段0a 免登入取得；階段1／2（解析＋屬性接回）降為**備選路徑**，
只在 0a 走不通時使用（退回條件見階段0a 末段）。

## 一鍵整合流程 run_case（推薦：shp／清冊 → 六張圖 + qgz）

`scripts/run_case.py` 是總控腳本，把各階段串起來自動跑，最後產 `.qgz`。
**六張圖**＝使用分區／使用地編定／國土功能分區／正射套疊／國土利用調查／臨路判定。
跑在系統 Python，會自動依步驟呼叫 QGIS 內建 Python。**臨路評估自 2026-07-26 起為本專案內建腳本，不再呼叫外部 fishpv 專案。**

有停點的地方腳本會停下來、印出下一步指令，照著加參數再跑同一支即可，全程免 AI。

### 模式零：⓪a 免登入直跑（★主路徑，2026-08-04 接上；只要一份清冊）

清冊只要有 `縣市/鄉鎮(區)/段[/小段]/地號` 四（五）欄，一句指令從清冊跑到首跑停點：

```powershell
python -X utf8 scripts\run_case.py --fetch 待評估資訊\<案>\00_清冊.csv --case <案名>
```

- 先跑 ⓪a `cadastre_fetch`（免登入：界址環＋getLandInfo＋權屬推論）產
  `outputs/<案名>/cadastre/基地.shp`，**自動轉入模式一**（案場定義→臨路→總區位圖→停點）。
- 🚧 內建閘門：shp 筆數＝清冊筆數，任一段抓取失敗即硬停——**備選的平台路徑需本人
  登入，腳本不會自行改走**，停下來等你決定。
- 已有 `cadastre/基地.shp` 時自動跳過重抓（續跑安全；要重抓先刪 `cadastre/`）。
- 跨段清冊會分組逐段抓再合併；縣市「台/臺」自動正規化。
- 之後照常 `--mode single|persite` 出圖。⚠ 權屬欄是「私(推論)」等推論值，
  關鍵筆數仍以謄本或平台覆核（空白≠私有）。

### 模式一：trust-shp（已有基地 shp，直接信任其幾何與屬性，不做地籍比對）

```powershell
python -X utf8 scripts\run_case.py --shp <基地.shp 絕對路徑> [--case 案子名]
```

```powershell
python -X utf8 scripts\run_case.py --shp <基地.shp> --mode single --layout maxmap
```

```powershell
python -X utf8 scripts\run_case.py --shp <基地.shp> --mode persite --layout maxmap
```

第一步會做：解析留痕 → 案場定義 → 臨路評估 → 產 `00_總區位圖`，然後停下等你選出圖單位。
看完 `outputs\<案子>\00_總區位圖.png` 後，`--mode single`＝整份 shp 出一組六張（官田模式）；
`--mode persite`＝每宗基地各出一組六張（環保局／漁電模式）。

trust-shp 模式**不經地籍比對**：基地層幾何、地號、使分、使類全部直接讀 shp，**沒有 14 碼段號的宗地也照樣出圖**。省略 `--case` 時案子名取 shp 檔名。
⚠️ `--shp` **一律給絕對路徑**。

### 模式二：土地清冊（xlsx/PDF，走解析＋地籍比對）

```powershell
python -X utf8 scripts\run_case.py --list <清冊.xlsx/pdf> [--case 案子名]
```

```powershell
python -X utf8 scripts\run_case.py --list <清冊> --resume
```

```powershell
python -X utf8 scripts\run_case.py --list <清冊> --resume --mode single --layout maxmap
```

第一步解析出 `01_中繼檔.xlsx` 後停下（請人工核對地號／分區辨識結果）；
`--resume` 續跑到 `00_總區位圖` 停點；再加 `--mode single|persite` 跑完六張圖 + qgz。

`--resume` 續跑會自動做：比對地籍 → 若有比不到的地號，**自動接新舊地號查詢（query_landno）→ 回填（apply_landno）→ 重新比對**，全程留痕於 `04_新舊地號對照清單.xlsx`，不會中斷。查得完整對應→改用新地號；查無（疑逕為分割）→退一步用「母號範圍」示意出圖；母號也無→記入 `03_無法對應清單.xlsx`、該地號不出圖但不影響其他。

> 換新版式清冊仍需 AI 寫一次解析規則（見階段1）；解析、核對後 `--resume` 之後全程免 AI。

### 共通參數

- `--from <步驟>`：從指定階段重跑。步驟為 `meta|match|roadaccess|overview|extract|maps`
  （`match` 為清冊模式限定）。**舊名 `fishpv` 仍相容**，會自動對映到 `roadaccess`。
- `--layout maxmap|compact`：出圖版面。**簡報用一律 `maxmap`**（最大化地圖＋角落避讓）。
- `--road-margin=<公尺>`：臨路判定圖框外擴。**基地 <2 公頃已自動不外擴**（程式逐案場依實際面積判斷並印一行說明，不必再手動帶 `=0`）。你仍要自己判斷的是：**有做區位圖（3b）時一律帶 `=0`**，讓臨路圖與其他主題同比例尺、周邊脈絡交給區位圖；無區位圖且基地 ≥2 公頃才用預設 `700`。帶了 `--road-margin=N` 就以你指定的為準（含刻意要小基地也外擴）。
- 位置／涵蓋地號角落框：**預設就不畫**（簡報本文已交代，圖上重複又遮圖面）。`--no-loc-parcel` 保留為相容旗標、帶了不影響；要回到舊版面畫出角落框才帶 `--loc-parcel`。
- `--reuse-basemap`：底圖 tif 已存在且涵蓋本次圖框就沿用，不重抓圖磚（涵蓋不足或檔案壞掉會照常重建）。
- `--basemap-dir=<路徑>`：底圖 tif 改寫他處。**桌面 QGIS 開著同案 .qgz 時必用**（見下方「常見狀況」）。
- `--mem`：向量層 materialize 成記憶體層算圖，免疫 SQLite 鎖衝突；記憶體層不進 `.qgz`，須搭 `--no-qgz`。
- `--no-qgz`：跳過 `.qgz` 輸出。**產簡報前不要用**（簡報流程需要 qgz 供手動修編）。
- `--basic`：只出前四張（跳過國土利用調查與臨路判定）。
- `--cases=004,005` / `--exclude=021,023`：只出／排除指定案場。

### 停點速記

| 模式 | 首跑停在 | 續跑（--resume/--mode）停在 |
|---|---|---|
| trust-shp | `00_總區位圖`（等 `--mode`） | 選 `--mode` 後跑完六張圖 + qgz |
| 清冊 | `01_中繼檔`（等人工核對＋`--resume`） | `--resume` 停在 `00_總區位圖`；再加 `--mode` 跑完六張圖 + qgz |

### 常見狀況：桌面 QGIS 開著同一個案子

症狀是**警告只有一行**「底圖下載失敗：Deleting … Permission denied」，**圖照出但底圖整片空白**，六張仍全部回報成功——很陰。而且既有的底圖 tif 會被弄壞成無 geotransform（`gdalinfo` 看 Corner Coordinates 變成 `0,0`／`1280,1024` 就是壞了）。

三種處置，任選其一：

```powershell
# (a) 最省事：底圖改寫到別的資料夾，不必關掉你的 QGIS
python -X utf8 scripts\run_case.py --shp <基地.shp> --mode single --layout maxmap --basemap-dir "outputs\<案子>\底圖2"
```

```powershell
# (b) 關掉桌面 QGIS 後重跑（並刪掉壞掉的 tif 讓它重建）
Remove-Item "outputs\<案子>\底圖\*.tif"
```

```powershell
# (c) 只改版面／主題、底圖不用重抓時
python -X utf8 scripts\run_case.py --shp <基地.shp> --mode single --layout maxmap --reuse-basemap
```

## 前置需求（一次性，已在本專案備妥）

- `config/county_registry.yaml`、`config/section_index.csv`、`config/urban_plan_legend.yaml`、`config/luimap_colortable.csv`
- `地籍圖資/`、`國土功能分區/`、`都市計畫/`、`環境敏感相關/`、`農地重劃區/` 各縣市 shp
- `styles/*.qml`
- QGIS 3.40.12 LTR（路徑固定：`C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat`）
- 系統 Python（跑階段1/2/2b）需有：`pandas`、`openpyxl`、`pyogrio`、`geopandas`、`pdfplumber`、`requests`、`pyyaml`、`dbfread`

## 逐階段指令

### 階段0a：免登入取地籍（★新案先跑這步，再決定何時佔用平台登入）

走 skill `cadastre-check`（農業部地籍圖資＋內政部 getLandInfo），**免帳號、免驗證碼、
無 10.5 秒節流**。基地先跑一次，鄰地改為自動發現（★2026-08-06起，不必手動列地號）：

```powershell
python -X utf8 scripts\cadastre_fetch.py fetch --county 嘉義縣 --town 布袋鎮 --section 考試潭段鹿寮小段 --landnos 306 --out "outputs\<案子>\cadastre" --name 基地
```

```powershell
python -X utf8 scripts\cadastre_neighbors.py --joined "outputs\<案子>\cadastre\基地.shp" --out "outputs\<案子>\cadastre" --buffer 50
```

產 `基地.shp`／`鄰地.shp`（EPSG:3826、UTF-8，欄位含 地號／使分／使類／登面積／圖面積／
版次／**公私別／所有權／管理者**），可直接餵 `greenbelt_ratio --neighbours`、`check_zones --joined`。
`run_case.py --fetch` 已自動接掛這兩步，一般不必手動個別執行。

`cadastre_neighbors.py` 內建**覆蓋率自我檢查＋自動探測補正**：本機縣市快照可能漏收
「已從舊地號分割出的新地號」（尤其交通用地／公路用地，地籍圖重測系統不涵蓋「逕為
分割」）——抓到落差會自動找候選宗地母號、批次探測子號、只留落在缺口範圍內者併入
回填，全程免人工介入；跑完印「缺口已補齊、覆蓋率檢查通過」才算乾淨。

**布袋306 實測**：幾何與 TCDGIS 官方 shp **完全相同**（IoU 100.00%、面積差 0.00 ㎡）；
使分／使類 7/7 一致；登記面積＝平台標示面積；**權屬 7/7 一致**
（278/298/391 國有・農田水利署、323 縣市有・嘉義縣政府、306/306-1/305 私有）。
舊地號會**自動追蹤重測改號**。

權屬單獨查（不取幾何時）：

```powershell
python -X utf8 scripts\public_land.py --city Q --town 03 --sec 1410 --landnos 306 278 323
```

⚠ **只剩兩件要上 TCDGIS**：①環境敏感套疊 53 項 ②離島幾何。
⚠ 權屬的兩個口徑：「未列於公有土地名冊＝私有」是**反面推論**，關鍵筆數請以謄本覆核；
**管理機關可能是改制前舊名**（回「行政院農業委員會農田水利署」，交付請寫「農業部農田水利署」）。

每案順手跑一次對帳，累積「能否升為主來源」的證據：

```powershell
python -X utf8 scripts\cadastre_fetch.py verify --cad "outputs\<案子>\cadastre\基地.shp" --official "outputs\<案子>\joined\<案子>_面積join.shp"
```

**已累積的對帳結果**

| 案 | 筆數 | IoU | 登記面積 | 使分/使類 | 權屬 | 端到端 |
|---|---|---|---|---|---|---|
| 布袋考試潭段306 | 7 | 100.00% | 7/7 | 7/7 | 7/7 | — |
| 安南港西段（都市土地＋國產署地） | 5 | 100.00% | 5/5 | 5/5 | 5/5 | 禁止事由檢核 JSON **209 欄 0 差異**，重現 ╳ |

**⓪a 走不通時停下來、通知使用者改走原路徑（階段1/2 的 TCDGIS 登入）**——因為那條需要
本人輸入帳密與圖形驗證碼。五個觸發條件：①有地號回 MISMATCH/NOT_FOUND/ERROR 且新舊地號
對照也追不到 ②離島案（澎湖／金門／連江無幾何）③`verify` IoU <99.9% 或面積差 >0.1%
④權屬吃重而公有名冊未列（反面推論不夠）⑤免登入服務中斷。
⚠ 環敏套疊 53 項**本來就要上平台**，與 ⓪a 成敗無關。

### 階段0：禁止事由檢核（★評估案第一步，出圖前先跑）

```powershell
python -X utf8 scripts\check_zones.py --joined <面積join.shp> --checklist _共通_農業用地變更 土資場_臺南市 --sites "outputs\<案子>\臨路評估\sites.shp" --out "outputs\<案子>"
```

YAML 驅動：清單在 `config/檢核清單/*.yaml`（`_共通_農業用地變更`＋依用途挑一份，如
`土資場_臺南市`、`運動訓練設施_臺南市`）。輸出 `禁止事由檢核.txt/.json`，
**有 ╳ 級觸及時 exit≠0**——先解決或確認翻轉條件，再投入臨路、面積等細項檢核。
換新用途＝新寫一份 YAML（不寫程式）；引擎會逐圖層印檔案日期並對過舊圖資示警。

項目類型：`overlay`／`distance`／`area`／`attribute`／`settlement`／`api_distance`／`inquiry`。
其中 `settlement` 與 `api_distance` 是 2026-07-30 新增，把「與醫院、學校、文教設施、社福
機構及十戶以上村莊或聚落 200 公尺」這一項從整條函詢改成大部分可實算（詳階段 0c／0d）。

**首次跑某縣市前**要先取該縣市門牌資料（一次性，約 1~2 分鐘）：

```powershell
python -X utf8 scripts\settlement_check.py --download --county 臺南市
```

存到 `門牌坐標\<縣市>.csv`（不進版控）＋ `.版本.txt`。腳本會自動從資料集的多個年度版本
挑年度最新者——**不要手動去挑「第一個 CSV」**（臺南同一資料集掛著 108~114 年七版，
這是 BREED47 舊版誤判的同型陷阱）。縣市不在 `config/門牌坐標來源.yaml` 就先補 dataset_id。

### 階段0c：聚落判定（十戶以上村莊或聚落條款）

```powershell
python -X utf8 scripts\settlement_check.py --joined <面積join.shp> --sites "outputs\<案子>\案場定義\sites.shp"
```

輸出 `聚落檢核.txt/.json`。走 check_zones 的 `settlement` 項目時會自動呼叫，不必單獨跑；
單獨跑適用於「想換門檻試算」或該用途尚未建 YAML 的場合。

**判定法（不可簡化）**：門牌點聚類 → 群內門牌數達 10 才算「村莊或聚落」→ 量基地到該聚落
最短距離。**不是數 200 公尺內有幾個門牌。** 預設同時輸出 30／50／100 公尺三種群聚門檻，
三者一致才是穩健結論；不一致會回報「門檻敏感須人工判斷」。

口徑：門牌坐標是戶政人工點位、官方聲明僅供參考，且門牌數 ≠ 戶數 → 屬**篩選初判**，
臨界值（8~12）與法定認定仍須戶政事務所／區公所確認。

### 階段0d：設施環域查詢（醫療／文教／鄰避）

```powershell
python -X utf8 scripts\nlsc_facility.py --joined <面積join.shp> --dist 200
```

國土測繪中心免認證 API（`med`／`edu`／`dis`），輸出 `設施環域查詢.txt/.json`。距離是設施點
**到基地面**的實距（非 API 的圓心距離）；門檻內查無時會自動往外探測，回報最近者在幾公尺外。
社福機構未涵蓋、營區屬機密 → 這兩類仍須函詢。API 查無 ≠ 現地沒有。

### 階段0b：環敏套疊（★正式程序＝NGIS，免登入）

```powershell
python -X utf8 scripts\ngis_sensitive.py --case <案子>
```

把 ⓪a 的基地幾何直接餵**內政部國土管理署 NGIS** 一級 25／二級 29 共 **54 個環敏圖層**做
**實際空間套疊**，回命中項＋重疊面積＋佔基地比例。免登入、每次查詢 <1 秒。
產 `環敏NGIS.csv`／`.json`；`--per-site` 依 sitemap 逐案場各算一份。

口徑（交付照寫）：**初步評估、非正式套繪**（NGIS 圖台自身聲明「僅供參考用途，不得作為
任何形式證明或主張」），法定應查免查認定仍以 eland／各主管機關為準。
⚠ 描述性欄位（縣市名等）上游本身有亂碼壞值，不可直接寫進報告（面積／比例／圖層名不受影響）。

### 階段0b-備選①：離線 PDF 應免查初篩（需法定「應查／免查」口徑時才用）

```powershell
python -X utf8 scripts\env_prescreen.py --case <案子>
```

自動讀 ⓪a 的 `outputs\<案子>\cadastre\基地.shp`。**初篩用官方『應免查範圍』離線 PDF**
（`config\環敏應免查範圍.json`）判每項應查/免查 → 只有應查項進第二階段套疊（遠端現行圖資
或本機圖資）。取代線上 eland：離線、權威、有版本、不受 eland 連線坑影響（2026-08-05 定案）。

**每月更新 PDF**（平台會更新）：
```powershell
python -X utf8 scripts\update_exempt.py --download
```
免登入 POST 平台『匯出全部項目』下載最新 PDF＋重解析。PDF 不進版控、JSON 進。

產出：
- `環境敏感初篩.csv`／`.json`：逐項應免查＋套疊結果（遠端「TGOS 現行圖資」／本機「快照」標明）。
- `環境敏感對帳.csv`：**案件已有 `環境敏感套疊.csv`（①b 平台結果）時才產**，逐項對帳標★不一致。

口徑（交付照寫）：**初篩是自我篩檢、不是正式判定**，法定用途仍以平台／正式查復為準；
兩來源不一致一律以平台為準並在報告標註。**平台登入已降為備選**——需權威背書或初篩涵蓋
不到（無公開圖資之逕洽項）才登入。**嚴重地層下陷地區以「地下水管制區第一級」作替代判斷**
（水利署無公開下陷區下載），須載明代理關係。

⚠ 首跑會下載多個全國圖資（含地質雲山崩地滑逐鄉鎮），視網路約數分鐘；之後走快取。
本機環敏 shp 過舊時用 `scripts\tgos_fetch.py`（免登入下載 TGOS 新版，NR23 教訓）。

### 階段0b：逐案場統計（簡報數字來源）

```powershell
python -X utf8 scripts\persite_stats.py --joined <面積join.shp> --out "outputs\<案子>"
```

面積／權屬／都市計畫分區／臨路／地形按 S001/S002 實算 → `逐案場統計.json`。
簡報引用的數字一律出自此檔。`--overlay "名稱=shp路徑[::分級欄]"` 可加算指定圖層套疊。

### 階段1：輸入解析（換版式才需要 AI）

```powershell
python -X utf8 scripts\parse_stage1.py
```

讀 `待評估資訊/<案子>/*`，寫出 `outputs/<案子>/01_中繼檔.xlsx`。
**這支腳本是針對「清潔隊備勤室非都市用地對應國土功能分區彙整表」這一種版面寫的**。
- 同版式的新文件 → 直接重跑，免 AI。
- 不同版式 → 需請 AI 讀一次新樣本、寫一支對應的新版 `parse_stageX.py`；寫好後那個版式後續也免 AI。

### 階段2：比對地籍

```powershell
python -X utf8 scripts\match_parcels.py <案子資料夾名>
```

讀 `01_中繼檔.xlsx`，輸出 `02_比對結果.xlsx` + `03_無法對應清單.xlsx`。純規則比對，無 AI。

### 階段2b：新舊地號查詢（比對不到才需要）

```powershell
python -X utf8 scripts\query_landno.py <案子資料夾名>
```

讀 `03_無法對應清單.xlsx`，用純 HTTP request 逐筆查各縣市地政的「地籍圖重測地段新舊地建號查詢」，輸出 `04_新舊地號對照清單.xlsx`。**不開瀏覽器、不用 AI。**

### 階段2c：案場定義（多案場必做）

```powershell
python -X utf8 scripts\build_sites_sitemap.py --joined <面積join.shp> --out-dir "outputs\<案子>\案場定義"
```

相鄰宗地連通分量聚類（`--gap` 預設 0＝須相鄰），產出**帶「段」欄的權威 `sitemap.csv`** ＋ `sites.shp`；編號北→南確定性（重跑不變），`--match-sites` 可沿用歷史編號。

⚠️ **sitemap 必含「段」欄**。同鄉鎮跨段重複地號（魚塭區常見）在無段欄時會分派失敗，**單筆案場會整案無聲消失**（東石 S012 實錯）。走 run_case 的 `roadaccess` 步驟時會自動先跑本步，一般不必手動執行。

### 階段2d：臨路評估

```powershell
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\road_access.py --joined <面積join.shp> --sitemap "outputs\<案子>\案場定義\sitemap.csv" --out "outputs\<案子>\臨路評估"
```

國土利用 LUIMAP 圖磚 → 最近色分類向量化 → 逐宗基地**臨路三態**（直接臨路≤0.5m／需搭橋≤1.5m／不臨路；一般道路與防汛道路分別評估）＋地形組成＋**沿基地邊界法向量測之鄰接路寬**。
輸出 `成果.xlsx`／`sites.shp`／`features.geojson`／`sites_status.csv`／`sitemap.csv`。

- **須用 QGIS 內建 python**（要 osgeo/GDAL）。
- `--reuse-landuse <既有landuse.tif>` 可跳過圖磚下載（重跑／驗收用）。
- 路寬相關參數：`--width-sample`（取樣間距，預設 5m）、`--width-search`（射線長度 60m）、`--width-gap`（視為鄰接的最大間隔 8m）。
- ⚠️ 臨路三態門檻（≤0.5m／≤1.5m）與道路關鍵字**非經核可不得改動**。
- 效能：逐案 × 全部地物面相交，57 案場約 10 分鐘。極大型（跨縣市／全國）清冊記憶體會爆，須按縣市切分分批跑。

### 階段3：出圖（六主題）

```powershell
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_maps.py <案子資料夾名> --layout maxmap [--cases=004,005] [--road-margin=0]
```

讀 `02_比對結果.xlsx`（或 `--trust-shp=<shp>`）+ 圖資，**預設輸出每案場 6 張 PNG**＋`05_出圖清單_maxmap.csv`＋`.qgz`。

- 旗標同前述「共通參數」，另有 `--themes=使用分區,使用地編定,國土功能分區`、`--skip-extract`（重用既有 `圖層.gpkg`）、`--extract-only`、`--overview-only`。
- 分區圖例只列**圖框內實際出現的分區**（不會再印「農業區／保護區　等」）。
- **使用分區／使用地編定主題自 2026-08-02 起預設以 NLSC WMTS 呈現**
  （`nURBAN1`＝非都市土地使用分區圖、`nURBAN2`＝使用地類別圖，官方**現行**套疊）——
  本機地籍快照是 111年2月 舊制，鄰近宗地近年變更編定會著舊色（布袋306-1 農牧→特目實例）。
  此後這兩張圖**需要連網**；圖磚抓取失敗自動退回本機 QML 著色（05 備註會註記）。
  `--zoning-local` 可整批退回舊制（歷史案重現用）。05 備註會寫入查詢日期，交付照抄。
- 沒跑過臨路評估就直跑 make_maps 時，圖6 在 05 清單記「**略過**：未跑臨路評估」（不是失敗）；跑過但產物不齊才記「失敗」。

### 階段3b：區位圖（簡報第2頁用）

```powershell
python -X utf8 scripts\road_network.py --joined <cadastre\基地.shp> --out "outputs\<案子>"
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_locality_map.py --joined <面積join.shp> --case <案子名> --sites "outputs\<案子>\臨路評估\sites.shp"
```

圖層順序：**透明電子地圖 EMAP2 置最上層** → 宗基地（標 `site_id`）→ 基地地籍 → 都市計畫分區（45%）→ 不透明 EMAP 置最底。
`--site-outline` 基地框線粗度預設 0.5mm；`--no-urban` 可不疊都市計畫分區；`--urban-alpha` 調分區透明度。

**2026-08-06 起外擴基準改為「碰到第一條國道/省道/市道/縣道/鄉道/市區快速道路為止」**
（不再是固定距離）：先跑 `road_network.py` 產 `<案>/區位圖_道路基準.json`，
`make_locality_map.py` 省略 `--margin` 時會自動讀取；`run_case.py --fetch` 已自動接掛
這步，通常不必手動跑。仍可用 `--margin <公尺>` 覆蓋回固定距離。

### 階段3c：環境敏感套疊圖

```powershell
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_env_map.py --joined <面積join.shp> --case <案子名> --alpha 0.40
```

讀 `環境敏感套疊.csv`（圖台①b 產物）挑出套疊面積>0 的圖層，解析到本機 `環境敏感相關/` 的 shp，出圖並產 `環境敏感套疊_逐類面積.csv`。
另可放 `環境敏感套疊_本機補充.csv` 補圖台清單外的限制（例：地下水管制區**第二級**，圖台清單只有第一級）。

- **全無套疊 → 不出圖、exit 0**（簡報端據此不做該頁）。
- **覆蓋率 ≥99% 的圖層自動改畫「外框虛線不填色」**：全覆蓋圖層的填色不帶資訊，只會把正射壓成一片。
- **多個圖層自動分色**（保護類綠／災害類橙紅／色環），圖例不會好幾格同一個紅。
- **逐類面積依（圖層×分級）彙總**：分級圖層（如淹水潛勢）列數＝分級數；非分級圖層一層一列。

#### ★ 平台說「有套疊」但本機圖資算不出來時（本機圖資過舊）

本機環敏圖資多為 105~106 年快照，其後新增／修正的範圍一律查不到。
**這是「有／沒有」的差異，不是邊界誤差，不可忽略**（2026-08-04 龍崎案：本機自然保留區
只有 23 筆、不含 110.07.30 公告的 NR23 龍崎牛埔惡地自然保留區，算出「13 公里外、未涉及」，
平台正確回報套疊 8.88 ㎡）。兩條處置，**先試 (a)**：

**(a) 補新版圖資**（最好，後案都受益）——TGOS 國家空間資料資產，免登入：
1. 開 `https://www.tgos.tw/tgos/NgdaMap`，關鍵字搜圖層名（例：自然保留區）。
2. 點資料集名進 metadata 頁，看「圖資及服務資訊」表挑最新版的 SHP，按「下載」。
3. 解壓到 `環境敏感相關/<原分級>/<原名稱> (<版次>)/`，並把 `make_env_map.py` REGISTRY
   該項的 `shp` 清單**新版放第一個、舊版留作 fallback**。
4. ⚠ 有些圖層 TGOS **只給 WMS、沒有 SHP**（山崩與地滑就是），此路不通 → 走 (b)。

**(b) 用平台幾何覆蓋**（單案救急）——把平台幾何存成
`outputs/<案>/環敏/<平台圖層名>.geojson`，`make_env_map` 會**優先採用它**而不用本機圖資
（檔名含平台圖層名即生效，可加 `NR23_` 這類前綴備註）。平台幾何取法：圖台結果列按
「載入圖層」後打該 MapServer 的 `/identify?...&returnGeometry=true`。

**換上新版後務必回頭核對平台數字**（龍崎案本機重算 8.88 ㎡，與平台一字不差＝驗證通過）。
⚠ 這類項目**要一併量「緊鄰距離與共同邊界長度」，不可只看重疊面積**。

### 階段3d：補充主題圖

```powershell
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_extra_maps.py --joined <面積join.shp> --case <案子名> --theme 權屬
```

```powershell
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_extra_maps.py --joined <面積join.shp> --case <案子名> --theme 環域 --buffer 200
```

```powershell
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_extra_maps.py --joined <面積join.shp> --case <案子名> --theme 養殖漁業生產區
```

**灌排水道（農田水利灌溉／排水渠道）**——法源：農地變更要點§5①二「有妨礙上、下游農業灌排水系統輸水能力之虞者，不同意變更使用」（**不同意變更事由，非成本問題**）。

```powershell
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_extra_maps.py --joined <面積join.shp> --case <案子名> --theme 灌排水道
```

**坡度分段圖**——山坡地案的必備圖（簡報「開發強度限制」頁）。依非都市土地開發審議
作業規範**總編§16 三段**分色（<30% 可開發／30~40% 僅開放性公共設施／≥40% 不可開發區），
並疊 `緩衝綠帶/緩衝綠帶10m.shp`（有才疊）。前置＝已跑完 `slope_check.py`。
**這張圖回答的是「可開發的地在哪裡」**，不只是「占多少百分比」——量體配置與不可開發區的
空間關係要看得見。

```powershell
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_extra_maps.py --joined <面積join.shp> --case <案子名> --theme 坡度
```

⚠ `slope_check.py` 產的 `坡度_percent.tif` 是**基地 bbox 矩形、未裁到基地邊界**；
本主題內建 cutline 裁切（`坡度_percent_基地裁切.tif`）。自己另外拿那個 tif 算占比時
**務必先裁**，否則對不上權威值（龍崎案未裁 44.8/20.4/34.9、裁切後 38.5/23.4/38.2）。

**隔離綠帶或設施配置圖**——農業用地變更案的必備圖（簡報「隔離綠帶劃設」頁）。正射為底，
依 `隔離設施.shp` 將綠帶分色為**隔離設施（臨路側）**與**隔離綠帶（其餘各側）**並列圖例；
另會疊現況建築物與兩者交集（＝綠帶內應拆除範圍，特定工廠案用）。
前置＝已跑完 3e-2 的 `greenbelt_ratio.py`。**部分變更編定案的 `--joined` 要給
`申請範圍.shp`，不是原地號的面積join.shp。**

```powershell
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_extra_maps.py --joined <申請範圍.shp 或 面積join.shp> --case <案子名> --theme 隔離綠帶
```

### 階段3e-0：部分變更編定（基地涉及公路時，先切再算綠帶）

基地被縣道等公路現況路面穿越／涵蓋時，比照前例（布袋306-1）把道路範圍分割出去
維持農牧用地，**綠帶分母＝分割後之申請範圍**：

```powershell
python -X utf8 scripts\split_road.py --joined <面積join.shp> --features <臨路評估\features.geojson> --neighbours <鄰地_面積join.shp> --out "outputs\<案子>\申請範圍"
```

產 `申請範圍.shp`／`保留_道路.shp`／`鄰地_含保留地.shp`，之後 3e-2 的 greenbelt_ratio
改吃這三個檔（`--joined 申請範圍.shp --neighbours 鄰地_含保留地.shp --facility-near 保留_道路.shp`）。
銳角尖端與切分細條已自動處理；分割線係 LUIMAP 現況判讀，交付須載明以地政複丈為準。

### 階段3e-2：隔離綠帶構法二選一（`greenbelt_ratio.py --shape`）

| 構法 | 適用 | 特徵 |
|---|---|---|
| `offset`（預設） | 既有案沿用 | 最小寬度基礎帶＋指定側加寬；round 接合故**有圓弧角、頂點多** |
| `minimal` | 需附三角形計算式時 | **最少頂點／無圓弧角（mitre 尖角）／面積嚴格大於門檻** |

```powershell
python -X utf8 scripts\greenbelt_ratio.py --joined <面積join.shp> --neighbours <鄰地_面積join.shp> --only-on 農業用地 --shape minimal --min-width 1.5 --ratio 0.30 --out "outputs\<案子>"
```

`minimal` 的三原則（使用者 2026-08-01 定案）：①非綠帶區域三角形數最少（＝頂點數−2）
②綠帶轉折點最少且不得有圓弧角 ③綠帶面積**嚴格大於** `--ratio`。

⚠ **必須有面積上限**：`--ratio-max`（預設 `--ratio`+0.03）。沒有上限時「頂點最少」會把基地
一路退縮到形狀最單純為止——布袋306 實測會退到 27 公尺、綠帶佔 76.79%，把大半土地讓掉。
面積比是**約束**不是最大化目標。

布袋306 實測效果：`offset` 50 個三角形（含 4 個退化細片，其中一個高 0.00）
→ `minimal` **6 個三角形**、綠帶 735.50 ㎡（34.31%）。

⚠ **實務上不留銳角尖端**（使用者 2026-08-01 定案）：`--min-angle`（預設 25 度）在**搜尋
階段**就把會產生銳角的候選淘汰掉。**不可事後修補**——開運算會把整條帶子縮到寬度不足、
截角會改變局部拓樸（布袋306 實測：真不足由 0 點變成 10～11 點）。
角度只量帶身，**與非應留設側銜接的收尾端一律不計**（那裡必然收斂成 0 度針尖，是差集的
拓樸產物；不排除的話全部候選都是 0.0°，等於把所有解否決）。
找不到解時多半是被 `--ratio-max` 卡住：布袋306 消除銳角需退縮 4.00 公尺、占比 33.5%，
超過預設上限 33%，放寬到 `--ratio-max 0.35` 即得解（代價是多讓出約 25 ㎡）。

`--facility-near <臨路側.shp>`：臨此範圍者另歸類為**隔離設施**、其餘為**隔離綠帶**
（要點§11 隔離設施得為通路、空地等非建築之開放性設施，兩者均計入面積比），
產 `隔離設施.shp`／`隔離綠帶.shp`，計算式圖與 `make_extra_maps --theme 隔離綠帶` 會自動分色。
分界是**直線**（沿外緣找臨路／非臨路轉換點，向可配置範圍拉最短直線切開），
兩者各為完整一段——不是用 buffer 去交集（那會產生圓弧邊界與並排細長條）。

⚠ **銳角要從基地端解決，不是在綠帶端**：綠帶＝基地−可配置範圍，基地邊界有銳角，
綠帶必然跟著有。部分變更編定案請在切分階段就把銳角尖端切除併入保留範圍
（作法見 `outputs/20260731_布袋考試潭段306/_split_road.py` 的 `trim_spikes`，
以既有頂點之間的直線弦跨越整個尖端切除；只切尖端那一個三角形會讓相鄰頂點變得更尖）。

### 階段3f：隔離綠帶面積計算式圖說（送審用）

送審須以**計算式**說明綠帶面積，不能只給 GIS 實算值。公式：
基地面積 － 非隔離綠帶或設施之面積 ＝ 隔離綠帶或設施之面積。

```powershell
python -X utf8 scripts\greenbelt_triangles.py --joined <面積join.shp> --out "outputs\<案子>"
```

跑在系統 Python（**不需 QGIS**）。前置＝已跑完 `greenbelt_ratio.py`。
輸出 `隔離綠帶計算式.csv/.json`、`隔離綠帶\非綠帶三角形.shp`、
`圖檔_maxmap\001_<案>_綠帶計算式.png`（左圖：編號三角形；右上：公式與比例檢核；
右下：編號／底／高／面積計算表含合計）。
底取**最長邊**、高＝2×面積÷底；編號北→南、西→東（重跑不變）。
**內建正確性閘門**：三角形面積合計必須等於非綠帶區域面積，不符即中止。
圖上另沿基地各邊標**留設寬度**（綠色雙箭頭，一邊一個數字；`--no-width` 可關），
並依 `隔離設施.shp` 將綠帶分色為隔離設施／隔離綠帶並列圖例。
`--retained <保留.shp>`：部分變更編定時把分割出去的道路畫成斜線區並在公式區加分割式。

⚠ **綠帶恰好解到 30.00% 時不可簡化邊界**：布袋306 實測，簡化容差 0.05 公尺就讓占比掉到
29.98%、0.5 公尺掉到 29.59%，計算式反而會「證明不合格」。要少一點三角形（表格較好讀）
就得把 `greenbelt_ratio --ratio` 目標提高留餘裕（例 0.305 → 容差 0.5 可得 16 個三角形、
占比仍 30.25%），代價是多讓出可建地。


**有涉及才出圖**：預設 `--irr-near 30`（判定涉及之距離）、`--irr-context 200`（周邊脈絡半徑）。
30 公尺內查無渠道 → 不產圖、exit 0（與 `make_env_map` 同一原則）。
涉及者**加粗並沿線標渠道名稱**，另產 `灌排水道涉及清單.csv`
（類別／渠道名稱／等級／距基地_m／管理處／工作站）——**工作站欄直接告訴你該函詢哪個單位**。
⚠ 圖資 `ymd` 欄製作日為 2018-04-30，資料夾名雖為 2021，引用年度寫 **2018**。


- `權屬`：依「公私別」分色（公有暖色／私有冷色）並標地號，正射為底。
- `環域`：以 buffer 畫法定距離環域（`--buffer` 公尺），環域內外一目瞭然。
- `養殖漁業生產區`：套疊 NAS 一級產業圖資，電子地圖為底，圖框自動納入基地與所屬／鄰近生產區。
  ⚠️ **一律用 114 年版**（`養殖漁業生產區_114_SHP\養殖漁業生產區.shp`，2025-02-20、62 區）。
  同資料夾 `BREED47.shp` 是 **2016 年舊版、僅 47 區**，用它會漏掉後來新增的生產區（安南案因此把「100% 涉及」誤判為「未涉及」）。

### 階段3e：敏感設施距離初篩

```powershell
python -X utf8 scripts\osm_sensitive_poi.py --joined <面積join.shp> --dist 200 --out "outputs\<案子>\OSM敏感設施初篩.csv"
```

法規訂有「與醫院／學校／文教設施／社福機構／十戶以上聚落最短距離應大於 N 公尺」時，用 OpenStreetMap（Overpass API）初篩。

**⚠ 2026-07-30 起本步驟降級為輔助**：十戶以上聚落改走階段 0c（門牌開放資料實算）、
醫療與文教改走階段 0d（測繪中心 API），兩者都有官方來源且能給距離數字。OSM 只剩
**社福機構與宗教場所**這類無官方點位者還值得跑，簡報不應再以 OSM 作為聚落判定依據。

- **只能篩選，不能取代函詢。** 查到→幾乎確定有；**查無 ≠ 沒有**。
- **先用 `--dist 1500` 驗證覆蓋率**：若連 1500m 都掛零，是 OSM 沒資料而非現地沒有，該次初篩無效。
- 臺灣鄉間 OSM 建物幾乎沒繪 → **「十戶以上」不可用建物棟數判斷**，以 `place=hamlet/village` 節點為主要信號。
- 公共 Overpass 會回 429/504，腳本已內建退避與鏡像切換；連續查詢請間隔數十秒。

### 階段3後：整理成果

```powershell
python -X utf8 scripts\reorg_sheets_persite.py "outputs\<案子>"
```

```powershell
python -X utf8 scripts\rebuild_05.py
```

`reorg_sheets_persite.py`：多案場清冊出圖後把 `圖檔_maxmap/` 重整為 `<案場ID>_<案場名稱>/<主題>.png` 並同步改寫 05 清單檔名欄（案場ID 支援英數如 S012）。
`rebuild_05.py`：分批出圖後重建完整 05 清單。
另有舊的 `organize_outputs.py`（依案場ID分資料夾），新案建議用 `reorg_sheets_persite.py`。

### 簡報產製（版面引擎）

案件的 `outputs/<案>/build_deck.py` **只寫內容**，版面全部 import `scripts/deck_layout.py`
（寫作規則唯一實作處；改版面只改引擎）。重跑：

```powershell
python -X utf8 "outputs\<案子>uild_deck.py"
```

⚠️ **防蓋改鎖**：若曾在 PowerPoint 手改過簡報，重跑會硬停（指紋不符）。
正確流程＝先把手改內容回寫進 build_deck.py，再以 `init(..., force=True)` 重建一次後改回。

## 案例專用的一次性腳本（不是通用工具，供參考）

**都已於 2026-07-30 收進 `scripts/legacy/`**，該資料夾的 [README](../scripts/legacy/README.md)
逐支列出「當時用途 → 現在該用什麼」。`scripts/` 根目錄自此只放現行產線工具，
索引見 [scripts/README.md](../scripts/README.md)。

- 清潔隊部資訊案的一次性腳本（內含該案專屬硬編碼）：`backfill_excluded.py`、
  `build_upload_xlsx.py`、`build_corrected_upload.py`、`build_supplement.py`
- 臨路評估內建化前的 legacy 路徑：`split_shp_persite.py`、`merge_fishpv_percase.py`、
  `run_fishpv_percase.ps1`（⚠ 最後這支硬編碼外部「漁電共生評估」專案路徑，該依賴已移除，
  直接跑會失敗）

新案的一次性腳本一律放案件資料夾（`outputs\<案>\_xxx.py`，`_` 前綴），不要留在 `scripts\`。

## 目前還離不開人工／AI 的部分

1. **輸入文件換版式**：需要 AI 讀一次新樣本才能寫解析規則（見階段1）。
2. **政府圖台（NGIS/TCDGIS）批次選取＋加入主題＋ShapeFile下載**：該平台無公開 API，「批次地籍匯入選取」可上傳 xlsx 但**沒有「加入主題」功能**；要下載官方 shp 必須把地籍逐段輸入「多重地籍選取」視窗才能加入主題並下載。目前只能人工操作（含帳密登入、圖形驗證碼，這兩項依規範不能由 AI 代勞）。
   自動化 driver 已固化在 skill `tcdgis-parcels-to-shp/scripts/`（`batch_driver.js` 批次選取、`finalize_download.js` 收尾下載），但**登入與驗證碼仍須人工先完成**。
3. **法規檢核與簡報敘述**：法規條號與判定取自法令檢核規則庫，但逐案的○△╳判定、翻轉條件與建議仍須人工或 AI 判斷。

