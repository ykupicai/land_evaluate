# 崩塌／山崩／土石流圖資的兩條 API

2026-09-13 打通（臺北市內湖保護區變更住宅區案）。兩條都**免登入、回向量**，
補上本機 `環境敏感相關/` 沒有的坡地災害圖層。案件層腳本範例：
`outputs/20260911_西湖北安段保護區變更住宅區/_崩塌土石流套疊圖.py`。

---

## ① 山崩雲 API（經濟部地質調查及礦業管理中心）

```
GET https://landslide.geologycloud.tw/map/api/opendata/lsdata/<ep>
    ?filename=<縣市>&coordinate=TWD97&filetype=json&limit=<n>
```

| ep | 圖層 | 臺北市筆數 |
|---|---|---|
| `history` | 歷史山崩目錄（合併多類型判釋結果） | 1,738 |
| `lidar` | 光達判釋潛在崩塌（具地表變形徵兆） | 2 |
| `geologicalsensitiveareas` | 山崩與地滑地質敏感區 | 1,274 |
| `dislope` | **順向坡區** | 417 |

另有 `/lsyears`（多年期歷史山崩目錄，Y1980~Y2015 逐年）與 `/jetlink/...`（WMTS 圖磚）。

**縣市參數只吃 17 個值**：臺北市／新北市／桃園市／臺中市／臺南市／高雄市／基隆市／
新竹縣市／苗栗縣／南投縣／彰化縣／雲林縣／嘉義縣市／屏東縣／宜蘭縣／花蓮縣／臺東縣
（新竹、嘉義是縣市合併成一個值；無離島）。

### 三個坑

1. ⚠⚠ **`limit` 預設只回 10 筆**（規格寫「預設輸入10筆提供測試」）。不帶就**靜默少抓**，
   第一次試打就是這樣只拿到 10 筆還以為資料就這麼少。一律帶 `limit=999999`。
2. ⚠ **參數名是 `coordinate`／`filetype`／`limit`**——Swagger 說明文字裡出現的
   `coor`／`ftype` 是舊例，用了會被忽略而落回預設。
3. ⚠ Swagger 頁是 JS 注入，`/swagger/api-docs/api` 回的是 HTML。**真正的規格在
   `/swagger/api-docs/swagger-ui-init.js` 的 `swaggerDoc` 物件**裡。

`coordinate=TWD97` 即 **EPSG:3826**，回 GeoJSON FeatureCollection，免再投影。
`history` 帶 `TYPE`（淺層崩塌…）、`SLIDE_KIND`（B岩屑崩滑…）、`AP_DATE`（判釋日期）。

---

## ② 農村發展及水土保持署 向量圖磚（MVT）

```
GET https://gis.ardswc.gov.tw/api/ardswc/vectortiles/shp/<layer>/{z}/{y}/{x}.pbf
```

| layer | 圖層 | 權限 |
|---|---|---|
| `debris_flow` | 土石流潛勢溪流（113年 1,736 條） | 開放 |
| `debris_affect` | 土石流潛勢溪流影響範圍 | 開放 |
| `debris_watershed` | 土石流潛勢溪流集水區 | 開放 |
| `potential_landslide` | 大規模崩塌潛勢區 | 開放 |
| `potential_landslide_affect` | 大規模崩塌潛勢區影響範圍 | 開放 |
| `landslide` | 全臺崩塌地（112年） | 開放 |
| `event_landslide_inventories` | 事件型崩塌目錄（112年） | 開放 |

清單來源：`https://data.ardswc.gov.tw/Data/OpenData/Api`（頁面是 JS 渲染，
抓 HTML 後去標籤再找 `gis.ardswc.gov.tw/api/ardswc/vectortiles` 字串即可）。

### 三個坑

1. ⚠⚠ **網址是 `{z}/{y}/{x}`（y 在 x 前）**，與一般 XYZ 相反。用一般順序會全部 404
   或回空，看起來像「這個範圍沒資料」。
2. ⚠ **用 GDAL 的 MVT 驅動解碼**：`gdal.OpenEx("MVT:" + tile.pbf)`（需 QGIS python，
   系統 python 沒有 osgeo）。出來是 EPSG:3857，要轉 3826。
3. ⚠⚠ **MVT 逐磚切裂，同一物件會跨磚重複**，要依物件 ID（`debrisno`）dissolve 合併；
   **`dissolve` 必須 `dropna=False`**——屬性欄常有 None，pandas groupby 預設會把整列
   丟掉，第一版就是這樣把全部物件變成 0 件。

---

## 交付口徑（不可省）

兩個平臺都聲明其資料**「僅可用於調查、研究、分析、規劃，不可應用於法規作業」**。
→ 成果只作**交叉核對與現況說明**，法定認定仍以 NGIS／eland／主管機關查復為準。
（圖面上要不要印這句話由使用者決定；內湖案的裁示是**不印在圖上、改由交付文字承載**。）

---

## 內湖案的實算（示範用，數字見該案 STATE.md 第十二節）

| 圖層 | 件數 | 距基地 | 與基地交集 |
|---|---|---|---|
| 山崩與地滑地質敏感區 | 6 | 0 m | 275.1 ㎡（1.64%） |
| **順向坡區** | 4 | 0 m | 102.9 ㎡（0.61%） |
| 歷史山崩目錄 | 15 | **1.9 m** | 0 ㎡ |
| 土石流潛勢溪流 北市DF050 | 1 | 175.7 m | 0 ㎡ |
| 大規模崩塌潛勢區／全臺崩塌地／事件型崩塌目錄 | — | 範圍內無資料 | — |

**為什麼值得跑**：
- **順向坡區**是本機 `環境敏感相關/` **完全沒有**的一層，卻是邊坡穩定的直接因子——
  臺北市保護區處理原則第四點的「無外圍邊坡崩塌」要件直接指向它。
- 山崩與地滑 275.1 ㎡ 與本機圖層 275.07 ㎡ 幾乎一致，可作**交叉驗證**。
- 「無歷史山崩」這種要件，**基地內 0 不等於沒事**——本案最近一處只有 1.9 公尺，
  緊貼基地北界，審查方調同一份圖資就會看到。要正面回應，不能只寫「無」。
