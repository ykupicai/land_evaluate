# 整合流程 run_case — 設計文件

日期:2026-07-11
狀態:設計已經使用者核可,待撰寫實作計畫(writing-plans)

## 1. 目的

把本專案(大量圖資轉換工具)與「漁電共生評估(fishpv)」整合為單一總控流程 `scripts/run_case.py`:
使用者提供 **shp 檔** 或 **土地清冊檔** → 撈取基地 → 產 **六張基本圖**(使用分區、
使用地編定、國土功能分區、空照套疊、國土利用調查、臨路判定)→ 產 **.qgz**。

既有已驗證的階段腳本全數重用,新程式只做編排與兩張新圖。

## 2. 已確認的需求決策

| 議題 | 決策 |
|---|---|
| 程式落點 | 併入本專案,新增總控 `scripts/run_case.py`;臨路/國土利用 import 漁電共生專案 fishpv |
| 投入模式 | **兩種**:`--list`(土地清冊模式,走解析＋比對)/`--shp`(**trust-shp 模式**,幾何屬性全信 shp、不比對,功能分區改用 shp 幾何直接空間交集) |
| shp 需官方檢核時 | 不在 run_case 內;沿用既有兩步 `shp_to_meta.py` → `match_parcels.py` 手動跑 |
| 比對不到的地號(清冊模式) | **自動 fallback 鏈**,見 §5.2,不設人工阻斷點,全程留痕 |
| shp 模式出圖單位 | **兩段式**:先 fishpv 毗鄰聚合 → 出 `00_總區位圖.png`(宗基地上色編號) ←✋使用者選 `--mode single`(官田模式,整份一組六張)或 `--mode persite`(環保局/漁電模式,每宗基地一組六張) |
| 清冊模式人工核對 | 保留 01_中繼檔 核對點(解析有辨識風險);核對後 `--resume` 續跑 |
| 國土利用調查圖 | LUIMAP 官方色(色碼表 150 類);圖例只列圖面實際出現類別 |
| 臨路判定 | 介接 fishpv(毗鄰聚合宗基地、臨路三態:直接≤0.5m/需搭橋≤1.5m/不臨路;一般/防汛道路分開評) |
| 架構 | **子進程編排**(方案一):階段間以 `outputs/<案子>/` 檔案為介面;否決單進程 import(geopandas/gdal柵格/PyQGIS 同進程 = libxml2 與 SQLite 鎖崩潰的既往根源)與重寫新框架 |

## 3. CLI 介面

```powershell
# trust-shp 模式(一鍵到「總區位圖」停點)
python -X utf8 scripts\run_case.py --shp <基地.shp> [--case 案子名]

# 看完 00_總區位圖 後續跑(二選一)
python -X utf8 scripts\run_case.py --shp <基地.shp> --mode single    # 官田模式
python -X utf8 scripts\run_case.py --shp <基地.shp> --mode persite   # 每宗基地一組

# 土地清冊模式(解析後停在 01 核對)
python -X utf8 scripts\run_case.py --list <清冊.xlsx/pdf> [--case 案子名]
# 人工核對 01_中繼檔.xlsx 後續跑
python -X utf8 scripts\run_case.py --list <清冊> --resume [--mode single|persite]

# 共通
--from <步驟>   # 從指定階段重跑(steps: meta|match|fishpv|overview|maps)
--no-qgz / --mem 透傳給 make_maps
```

run_case 本體跑在**系統 Python**;它以 subprocess 呼叫:
- match_parcels / query_landno / shp_to_meta:系統 Python
- fishpv:`漁電共生評估\env.bat python -m fishpv.cli`(QGIS 內建 Python)
- make_maps / 新出圖模組:`python-qgis-ltr.bat`(PyQGIS)

## 4. 資料流

```
【trust-shp 模式】                        【土地清冊模式】
run_case --shp X.shp                      run_case --list 清冊
 ├ A. shp_to_meta(直跑,不停)              ├ A'. 解析→01_中繼檔 ←✋人工核對
 │    (幾何屬性信 shp;不跑 match)         │     (--resume 後)
 │                                        ├ B. match_parcels → 02
 │                                        ├ B2. 比對不到→fallback 鏈(§5.2)→02定稿
 ├ C. fishpv 臨路評估(env.bat 子進程)
 │    輸入:trust-shp=原 shp;清冊=02 撈得的官方宗地幾何(圖層.gpkg 基地層)
 │    輸出:臨路評估/(成果.xlsx、sites.shp、features.geojson、maps/)
 ├ D. 00_總區位圖.png(宗基地聚合上色+S001..編號+電子地圖底)
 │    ←✋使用者選 --mode single | persite
 └ E. 出圖(PyQGIS):六張 ×(1組 或 每宗基地一組)+ .qgz + 05_出圖清單
```

## 5. 關鍵設計

### 5.1 trust-shp 模式的圖層準備
- 基地層:直接取 shp(地號/使分/使類欄;dbf 壞欄名用 dbfread 容錯,同 shp_to_meta)
- 國土功能分區:shp 幾何 ∩ 縣市功能分區 shp(沿用 zone 交集邏輯,不經地號)
- 周邊地籍:照舊按地圖範圍從縣市官方地籍 shp 撈(與比對無關)
- 02 一致性檢核:**無**(信任模式的明示代價,05 清單註記「trust-shp」)

### 5.2 比對不到的自動 fallback 鏈(清冊模式;規則來自清潔隊案 021/038/086、023/063 實例)
1. match_parcels 正常比對失敗的地號 → 自動呼叫 `query_landno.py`(純 HTTP 新舊地號查詢)。
2. **查得完整對應地號**且驗證存在於 2022(111年2月)地籍 shp → 自動回填改用該地號續比對;
   02 備註記「原地號X,依新舊地號對照回填」。
3. **查無**(疑 111年2月後逕為分割/合併,如 2535-1)→ **往上一層取母地號**:改用同段
   「宗地母」同號之所有宗地(母號範圍)作圖;02 備註記「查無對應,以母號範圍示意出圖」。
4. 母號也查無 → 記入 03_無法對應清單,該地號不出圖、不中斷其他。
5. 全程寫 `04_新舊地號對照清單.xlsx` 留痕;不設人工阻斷點(事後可查 04/02 備註)。

### 5.3 六張圖(A4 橫式版型與既有一致:標題/圖例/比例尺/指北針/基地紅框/地號)
| # | 主題 | 來源 | 狀態 |
|---|---|---|---|
| 1 | 非都市使用分區＋都市計畫 | 既有 make_maps | 不動 |
| 2 | 非都市使用地編定＋都市計畫 | 既有 make_maps | 不動 |
| 3 | 國土功能分區 | 既有 make_maps | 不動 |
| 4 | 正射影像＋電子地圖套疊 | 既有 make_maps(PHOTO2+EMAP2 圖磚) | 不動 |
| 5 | **國土利用調查** | LUIMAP 圖磚拼接為底(nlsc_wmts 加 LUIMAP 圖層)＋基地紅框;圖例=fishpv 分類結果中實際出現之官方類別(lu_code/lu_name/RGB 取自 luimap_colortable.csv) | 新增 |
| 6 | **臨路判定** | 正射為底＋道路地物高亮(一般/防汛分色)＋宗基地框色依判定(綠=直接臨路/黃=需搭橋/紅=不臨路)＋最短距離標註;附註列各宗基地臨路摘要 | 新增 |

### 5.4 00_總區位圖(兩模式皆出,為出圖單位的人工停點)
臺灣通用電子地圖為底,fishpv 聚合的宗基地各上一色+標 S001..編號與面積;版型同 A4。
供使用者決定出圖單位。**persite 的語意依模式而異**:
- trust-shp 模式:persite=每「fishpv 宗基地」一組(檔名前綴 `S001_…`);single=整份 shp 一組。
- 清冊模式:persite=每「案場」一組(沿用案場ID前綴,即環保局模式);single=整案一組。
  fishpv 聚合結果在清冊模式僅用於臨路判定與總區位圖,不改變案場分組。

### 5.5 qgz
單一 .qgz:六主題所需圖層依主題分組(含 LUIMAP GeoTIFF、fishpv sites/道路層、
各主題向量層與版面 Layout)。沿用既往教訓:qgz 用檔案圖層(--mem 時記憶體層不入 qgz,
故 persite 大案建議預設不加 --mem、必要時關桌面 QGIS)。

## 6. 錯誤處理
- 各階段獨立可重跑(`--from`);階段產物齊備才進下一階段。
- fishpv 失敗 → 圖 5/6 缺、1-4 照出,05 清單標記「臨路評估失敗」。
- LUIMAP 圖磚抓取失敗 → 圖 5 缺,重試 3 次(同 nlsc_wmts 慣例)。
- 清冊模式解析低信心黃底照舊;trust-shp 模式屬性缺值(如官田 8 筆縣市空白)以全檔多數值補並備註。

## 7. 測試與驗收
- 驗收案例 1(trust-shp):官田 40MW——六張圖與本次手動結果一致;臨路判定 4 宗基地
  結果不變(S001/S002 直接臨路、S003 49.4m、S004 67.7m 不臨路)。
- 驗收案例 2(清冊):清潔隊案抽 3 案場(004 都市、050 山區、104 離島)重跑,1-4 張與
  既有成果一致、5/6 張新產出目視合理。
- fallback 鏈單元測試:mock query_landno 回應,驗證「查得回填」與「母號範圍」兩路徑。

## 8. 非目標(YAGNI)
- 不做 GUI/QGIS 外掛(fishpv 外掛已存在;本流程給終端機)。
- 不改 fishpv 內部演算法(臨路門檻等參數沿用其 config)。
- 不做清冊新版式的自動解析(換版式仍走 AI 寫解析規則,見 免AI批次流程操作手冊)。
- 不合併兩專案 repo;fishpv 以子進程介接,路徑寫在 config。
