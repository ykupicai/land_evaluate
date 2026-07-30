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
| 3. 出圖（六主題） | `scripts/make_maps.py` | 否 | 否（QGIS 內建 Python） |
| 3b. 區位圖 | `scripts/make_locality_map.py` | 否 | 否 |
| 3c. 環境敏感套疊圖 | `scripts/make_env_map.py` | 否 | 否 |
| 3d. 補充主題圖（權屬／環域／養殖漁業生產區） | `scripts/make_extra_maps.py` | 否 | 否 |
| 3e. 敏感設施距離初篩 | `scripts/osm_sensitive_poi.py` | 否（Overpass API） | 否 |
| 3後. 整理成果 | `scripts/reorg_sheets_persite.py`、`organize_outputs.py`、`rebuild_05.py` | 否 | 否 |

不在此表：政府圖台（NGIS/TCDGIS）批次選取＋下載官方 shp，仍需人工瀏覽器操作（含帳密登入、圖形驗證碼），見文末。

## 一鍵整合流程 run_case（推薦：shp／清冊 → 六張圖 + qgz）

`scripts/run_case.py` 是總控腳本，把各階段串起來自動跑，最後產 `.qgz`。
**六張圖**＝使用分區／使用地編定／國土功能分區／正射套疊／國土利用調查／臨路判定。
跑在系統 Python，會自動依步驟呼叫 QGIS 內建 Python。**臨路評估自 2026-07-26 起為本專案內建腳本，不再呼叫外部 fishpv 專案。**

有停點的地方腳本會停下來、印出下一步指令，照著加參數再跑同一支即可，全程免 AI。

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
- `--road-margin=<公尺>`：臨路判定圖框外擴。**有做區位圖（3b）時一律 `0`**，讓臨路圖與其他主題同比例尺；周邊脈絡交給區位圖。無區位圖且基地 ≥2 公頃才考慮 `700`。
- `--no-loc-parcel`：圖上不畫「位置／涵蓋地號」角落框（簡報本文已交代，圖上重複又遮圖面）。
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
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_maps.py <案子資料夾名> --layout maxmap [--cases=004,005] [--road-margin=0] [--no-loc-parcel]
```

讀 `02_比對結果.xlsx`（或 `--trust-shp=<shp>`）+ 圖資，**預設輸出每案場 6 張 PNG**＋`05_出圖清單_maxmap.csv`＋`.qgz`。

- 旗標同前述「共通參數」，另有 `--themes=使用分區,使用地編定,國土功能分區`（只出免連網主題）、`--skip-extract`（重用既有 `圖層.gpkg`）、`--extract-only`、`--overview-only`。
- 分區圖例只列**圖框內實際出現的分區**（不會再印「農業區／保護區　等」）。
- 沒跑過臨路評估就直跑 make_maps 時，圖6 在 05 清單記「**略過**：未跑臨路評估」（不是失敗）；跑過但產物不齊才記「失敗」。

### 階段3b：區位圖（簡報第2頁用）

```powershell
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_locality_map.py --joined <面積join.shp> --case <案子名> --sites "outputs\<案子>\臨路評估\sites.shp" --margin 1200 --no-loc-parcel
```

圖層順序：**透明電子地圖 EMAP2 置最上層** → 宗基地（標 `site_id`）→ 基地地籍 → 都市計畫分區（45%）→ 不透明 EMAP 置最底。
`--site-outline` 基地框線粗度預設 0.5mm；`--no-urban` 可不疊都市計畫分區；`--urban-alpha` 調分區透明度。

### 階段3c：環境敏感套疊圖

```powershell
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_env_map.py --joined <面積join.shp> --case <案子名> --alpha 0.40 --no-loc-parcel
```

讀 `環境敏感套疊.csv`（圖台①b 產物）挑出套疊面積>0 的圖層，解析到本機 `環境敏感相關/` 的 shp，出圖並產 `環境敏感套疊_逐類面積.csv`。
另可放 `環境敏感套疊_本機補充.csv` 補圖台清單外的限制（例：地下水管制區**第二級**，圖台清單只有第一級）。

- **全無套疊 → 不出圖、exit 0**（簡報端據此不做該頁）。
- **覆蓋率 ≥99% 的圖層自動改畫「外框虛線不填色」**：全覆蓋圖層的填色不帶資訊，只會把正射壓成一片。

### 階段3d：補充主題圖

```powershell
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_extra_maps.py --joined <面積join.shp> --case <案子名> --theme 權屬 --no-loc-parcel
```

```powershell
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_extra_maps.py --joined <面積join.shp> --case <案子名> --theme 環域 --buffer 200 --no-loc-parcel
```

```powershell
& "C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat" scripts\make_extra_maps.py --joined <面積join.shp> --case <案子名> --theme 養殖漁業生產區 --no-loc-parcel
```

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
