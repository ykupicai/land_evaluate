# -*- coding: utf-8 -*-
"""縣市都市計畫使用分區：免登入接各縣市都發局 ArcGIS 圖台（逐筆分區＋幾何＋建蔽容積）。

★用途定位（2026-08-20 打通，取代 tainan_urbanplan_fetch.py 的單縣市版）
------------------------------------------------------------------
**全國性**的都市計畫使用分區沒有可用的線上逐筆 API（NGIS/luz/NLSC 那幾條路都被官方
關閉或擋人機驗證，見 .claude/rules/出圖上色與分區.md），逐筆屬性只能吃本機
`都市計畫/全台都市計畫土地使用分區.shp`（2019 舊版、粗版，截角/細長道路會判錯）。

**但個別縣市有自己的 ArcGIS Server REST 圖台**，逐筆分區＋幾何＋建蔽率/容積率/計畫案號
都查得到、且是現行版、精度足以正確切出道路截角。已接通並實測的縣市見下方 COUNTY 登錄表。
→ 該縣市有登錄時，都市計畫圖層優先用本腳本接圖台；未登錄或查無時，退回本機 shp。

★一律用 `f=json`（esri json）查詢，不用 geojson——高雄圖台不支援 geojson（回 500），
esri json 三縣市都支援；attributes 的 key 是**真實欄名**（非中文別名），故 fields 登錄
一律填真實欄名（臺南 NAME/LAYER…、高雄 NAME/SHORTNAME/BCRTEXT…、新竹縣 使用分區/建蔽率…）。
幾何走 esri rings，由 `_esri_polygon_to_shapely()` 依環向（順時針＝外環、逆時針＝內洞）還原。

各縣市圖台差異（都是 ArcGIS REST /query，capabilities 皆含 Query）：
  · 臺南市 up.tainan.gov.tw       代理 AuthToken/proxy.ashx?<URL編碼目標>，須帶 Referer；無 token
  · 高雄市 urbangis.kcg.gov.tw    代理 UBA/ProxyPage/proxy.jsp?<原始目標>，須帶 Referer；無 token
  · 新竹縣 imap.hchg.gov.tw       直連 arcgis/rest；★須 token——token 寫死在圖台頁面 HTML
                                  （變數 token2，公用帳號 imapalluser），本腳本抓頁面 regex 取出
座標一律用 EPSG:3826 送查（inSR=3826, outSR=3826，服務端自動轉換；新竹縣服務雖為 3857 亦可）。
輸出正規化為 ASCII 欄名（值仍中文，避免 shp DBF 10-byte 截斷）：
  COUNTY 縣市／ZONE 使用分區／ZONE_USE 用地或別名／BCR 建蔽率／FAR 容積率／
  PLAN_AREA 計畫區名／PLAN_NO 計畫案號／Color1 官方色（"R,G,B"，依 ZONE_USE 反查
  config/urban_plan_legend.yaml；供 make_maps 直接套 styles/都市計畫.qml 上色）。

用法（系統 Python，不需 QGIS）
------------------------------------------------------------------
  python -X utf8 scripts/urbanplan_fetch.py --county 臺南市 \
      --joined outputs/<案>/cadastre/基地.shp --out outputs/<案>/都市計畫_縣市圖台 [--margin 60]
  python -X utf8 scripts/urbanplan_fetch.py --county 高雄市 --bbox <xmin ymin xmax ymax> --out <dir>

產出：<out>/都市計畫_縣市圖台.shp（EPSG:3826，含 Color1）＋ .json（原始 esri json 備查）。
下游：出圖時以此圖層取代本機 `都市計畫/` shp——
  make_maps.py <案> --urban-shp <out>/都市計畫_縣市圖台.shp
  （make_maps 會自動偵測標註欄 ZONE_USE/ZONE 並吃 Color1 上色；未給則用本機全台 shp）。

⚠ 各縣市圖台為都發局現行版、仍屬「參考」；正式分區認定以都發局正式核發文件為準。
⚠ 只支援已登錄縣市；其他縣市各有各的圖台（或無），要新增先照本檔既有縣市方式探勘後補登錄表。
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd
from shapely.geometry import MultiPolygon, Polygon

EPSG = 3826
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 官方色表（都市計畫圖圖例）：分區/用地名 → "R,G,B"。輸出 shp 補一個 Color1 欄，
# 使 make_maps 的 styles/都市計畫.qml（data-defined 吃 Color1）可直接套用、出圖就緒。
# 單一真相＝config/urban_plan_legend.yaml 的 zones: 區塊，這裡不依賴 pyyaml、以 regex 解析。
_LEGEND_YAML = Path(__file__).resolve().parent.parent / "config" / "urban_plan_legend.yaml"


def _load_zone_colors() -> dict:
    """讀 urban_plan_legend.yaml 的 zones: 區塊 → {分區/用地名: 'R,G,B'}。"""
    try:
        txt = _LEGEND_YAML.read_text(encoding="utf-8")
    except OSError:
        return {}
    colors = {}
    in_zones = False
    for line in txt.splitlines():
        if re.match(r"^zones:\s*$", line):
            in_zones = True
            continue
        if in_zones:
            if line and not line[0].isspace():   # 下一個頂層 key → zones 區塊結束
                break
            m = re.match(r"^\s+([^\s#:][^:]*):\s*\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]", line)
            if m:
                colors[m.group(1).strip()] = "%s,%s,%s" % (m.group(2), m.group(3), m.group(4))
    return colors


def _color_for(name, colors: dict):
    """用地/分區名 → 官方色。用地全名（國小學校用地）常為圖例鍵（學校用地）的後綴，
    故 exact 找不到時取「最長的後綴／被包含」匹配鍵。"""
    if not name or not colors:
        return None
    name = name.strip()
    if name in colors:
        return colors[name]
    cands = [k for k in colors if name.endswith(k) or k in name]
    return colors[max(cands, key=len)] if cands else None

# ── 縣市圖台登錄表（新增縣市＝照既有格式補一列，不改主流程）─────────────────
# fields：把圖台各自的欄名映射到本腳本正規化欄位（缺的給 None）。
COUNTY = {
    "臺南市": {
        "query": "https://up.tainan.gov.tw/server/rest/services/Upbm/"
                 "MergePlanUseZoning_97/MapServer/0/query",
        "proxy": "https://up.tainan.gov.tw/AuthToken/proxy.ashx?",
        "proxy_encode": True,   # 此代理吃「URL 編碼後的完整目標」
        "referer": "https://up.tainan.gov.tw/UPBUD_sys/Areas/Map/map?type=upbm&ver=public",
        "fields": {"ZONE": "NAME", "ZONE_USE": "LAYER", "BCR": "BUILDRATE",
                   "FAR": "VOLUMERATE", "PLAN_AREA": "SC_AREACOD", "PLAN_NO": "PLANNO"},
    },
    "高雄市": {
        "query": "https://urbanproxy.kcg.gov.tw/arcgis/rest/services/Test/"
                 "KcgUrbanData_Alias/MapServer/12/query",   # 12=KS_Plan_Full（含建蔽容積）
        "proxy": "https://urbangis.kcg.gov.tw/UBA/ProxyPage/proxy.jsp?",
        "proxy_encode": False,  # 此代理吃「原始目標」（不可再編碼，否則回 HTML 錯誤）
        "referer": "https://urbangis.kcg.gov.tw/UBA/web_page/UBA010100.jsp",
        "fields": {"ZONE": "NAME", "ZONE_USE": "SHORTNAME",
                   "BCR": "BCRTEXT", "FAR": "FARTEXT",
                   "PLAN_AREA": "URBANNAME", "PLAN_NO": None},
    },
    "彰化縣": {
        # 探勘 2026-08-24：ArcGIS REST **完全開放**——免代理、免 token、免 Referer 亦可
        #   （仍帶 Referer 保險）。服務 CHCGMap/CITYPLANS/MapServer/19=USEZONE_FULL（逐宗地
        #   都市計畫使用分區，對應高雄 KS_Plan_Full）；capabilities: Map,Query,Data；
        #   服務宣告 wkid=102443（latestWkid=3826），inSR/outSR=3826 直接可用。全表 9,090 筆。
        # ⚠ 空間查詢一定要用「基地真實座標」——彰化市中心在 x≈201500（不是 21xxxx），
        #   猜錯座標會回 0 筆而誤判服務不可用（探勘時實際踩過）。屬性查詢（where）另可佐證。
        # 欄位：ZONENAME=分區全名（住宅區/農業區/道路用地，供色表反查）、ZONE=分區簡稱
        #   （住/農/道）、URBAN=計畫區名（彰化都市計畫區）、PNO=計畫案號（多空）。
        #   建蔽/容積欄 BULID_CR/BULID_AR 型別為 Integer 但實測絕大多數空白，登錄為 None。
        "query": "https://urbangis.chcg.gov.tw/arcgis/rest/services/CHCGMap/"
                 "CITYPLANS/MapServer/19/query",
        "proxy": None,
        "referer": "https://urbangis.chcg.gov.tw/map/",
        "fields": {"ZONE": "ZONENAME", "ZONE_USE": "ZONE", "BCR": None,
                   "FAR": None, "PLAN_AREA": "URBAN", "PLAN_NO": "PNO"},
    },
    "桃園市": {
        # 探勘 2026-08-24：ArcGIS Server（uparcgis.tycg.gov.tw，**/server/rest** 非 /arcgis/rest）
        #   直連可用、免代理免 token（帶 Referer 保險）。服務 TY_UPGIS/TYMap_SDE/MapServer/2
        #   = 都市計畫使用分區_SDE_UseZoneAll（逐宗地，全表 18,109 筆）；capabilities Map,Query,Data；
        #   SR=3826，inSR/outSR=3826 直接可用；maxRecordCount=1000（site bbox 遠小於，不必分頁）。
        # ⚠ 前端入口是 tymap.tycg.gov.tw/tygispreview/Map（同彰化的 mapPage_main.js 模板）；
        #   彰化 JS 內殘留的舊網址 urbandatasrv.tycg.gov.tw 已失效（404），現行主機為 uparcgis。
        # 欄位：LANDUSE=分區全名（住宅區/工業區/學校用地(文中)…，供色表反查）、
        #   PUBNO=分區簡稱/編號（住/工(十四)/文中(二)…）、B_RATIO/F_RATIO=**建蔽率/容積率
        #   （實測有值，桃園是目前少數建蔽容積齊全的縣市）**、MUPLAN=主要計畫名、PUBNO 兼計畫編號。
        "query": "https://uparcgis.tycg.gov.tw/server/rest/services/TY_UPGIS/"
                 "TYMap_SDE/MapServer/2/query",
        "proxy": None,
        "referer": "https://tymap.tycg.gov.tw/tygispreview/Map",
        "fields": {"ZONE": "LANDUSE", "ZONE_USE": "PUBNO", "BCR": "B_RATIO",
                   "FAR": "F_RATIO", "PLAN_AREA": "MUPLAN", "PLAN_NO": "PUBNO"},
    },
    "臺北市": {
        # 探勘 2026-08-24：都發局 historygis ArcGIS **完全開放**（免代理免 token，帶 Referer 保險）。
        #   服務 UrbanPlan2/UrbanPlan2/MapServer/2=「都市計畫使用分區2(大比例尺)」（逐宗地，現行版）。
        #   ⚠ ?f=json 回應帶 UTF-8 BOM——本檔用 decode(errors=replace) 不受影響（json.loads 容忍）。
        #   服務宣告 SR 3857，inSR/outSR=3826 服務端自動轉換可用。
        # 欄位：使用分區=分區全名（供色表反查）、分區簡稱、建蔽率/容積率（含字、實測齊全）、
        #   分區代碼（PHI/PED/PG…，可當計畫編號欄）。無計畫區名欄→PLAN_AREA 給 None。
        "query": "https://www.historygis.udd.gov.taipei/arcgis/rest/services/"
                 "UrbanPlan2/UrbanPlan2/MapServer/2/query",
        "proxy": None,
        "referer": "https://webgis.udd.gov.taipei/upis_v2",
        "fields": {"ZONE": "使用分區", "ZONE_USE": "分區簡稱", "BCR": "建蔽率",
                   "FAR": "容積率", "PLAN_AREA": None, "PLAN_NO": "分區代碼"},
    },
    "新北市": {
        # 探勘 2026-08-24（服務網址從瀏覽器 XHR＋arcgis_parameter.js 定位）：ArcGIS 在
        #   arcgis.planning.ntpc.gov.tw/**server/rest**，**須 token**——token 寫在一支靜態 JS
        #   檔 NtpcUrbArcgisToken/urban_planning_ntpc_gov_tw.js（var NtpcUrbToken="..."），
        #   本檔以 token_url＋regex 每次即時抓（比寫死耐輪替）。
        #   服務 NTPC_Urban/LandUse_WMS/MapServer/0 = NTPCUPGIS_SDE.dbo.LANDUSE（逐宗地）；
        #   capabilities Map,Query,Data；SR=3826。⚠ 不支援分頁（resultRecordCount 會回 400）。
        # 欄位是 LZ 代號：LZ3=分區全名（供色表反查）、LZ4=分區簡稱、LZ6=建蔽率、LZ7=容積率、
        #   LZ8=計畫區名、LZ1=計畫案名（當計畫案號欄用）。⚠ Uplan(id=1) 只是計畫案界不是分區，勿用。
        "query": "https://arcgis.planning.ntpc.gov.tw/server/rest/services/"
                 "NTPC_Urban/LandUse_WMS/MapServer/0/query",
        "proxy": None,
        "referer": "https://urban.planning.ntpc.gov.tw/NtpcURInfo/",
        "insecure": True,
        "token_url": "https://urban.planning.ntpc.gov.tw/NtpcUrbArcgisToken/"
                     "urban_planning_ntpc_gov_tw.js",
        "token_regex": r'NtpcUrbToken\s*=\s*"([^"]+)"',
        "fields": {"ZONE": "LZ3", "ZONE_USE": "LZ4", "BCR": "LZ6",
                   "FAR": "LZ7", "PLAN_AREA": "LZ8", "PLAN_NO": "LZ1"},
    },
    "花蓮縣": {
        # 探勘 2026-08-24（用瀏覽器抓 XHR 才定位到，靜態 JS 找不到——服務網址由後端動態注入）：
        #   ArcGIS 在 map.hl.gov.tw/arcgis/rest，經代理 HLGIS/proxy_arcgis/proxy.ashx?<原始目標>
        #   （**不編碼**，同高雄）。服務 HLforApplication/HLforApp_UPZONE/0=「都市計畫分區」
        #   （**供申請用版**，逐宗地、全表 5,004 筆）；capabilities Map,Query,Data；SR=3826。
        # ⚠ 花蓮伺服器**憑證鏈不完整**（只送主機憑證、缺中繼），Python 預設 SSL 驗證會
        #   CERTIFICATE_VERIFY_FAILED → 本列標 insecure:True 走未驗證 context（瀏覽器可正常開）。
        # 欄位（中文欄名，同新竹縣）：分區=分區全名、簡稱=分區簡稱、建蔽率/容積率（**含 % 字**、
        #   實測齊全）、都市計畫=計畫書名、土地發展強度法源=變更案名（可當計畫案號欄用）。
        # 另有 HLUP/HLUP、UP/UP_ZONE_3857 兩組亦 Query 可用；選 HLforApp 版因它是「供申請」用途、
        #   欄位含建蔽容積且 SR 已是 3826。
        "query": "https://map.hl.gov.tw/arcgis/rest/services/HLforApplication/"
                 "HLforApp_UPZONE/MapServer/0/query",
        "proxy": "https://map.hl.gov.tw/HLGIS/proxy_arcgis/proxy.ashx?",
        "proxy_encode": False,
        "referer": "https://map.hl.gov.tw/HLUPWeb/GIS/Map/Main",
        "insecure": True,
        "fields": {"ZONE": "分區", "ZONE_USE": "簡稱", "BCR": "建蔽率",
                   "FAR": "容積率", "PLAN_AREA": "都市計畫", "PLAN_NO": "土地發展強度法源"},
    },
    # ── 以下探勘後「無法比照接入 bbox→query 登錄表」，記錄結論避免重試（2026-08-24）──────
    # ⚠ 宜蘭縣 eadgis.e-land.gov.tw/Public/LandUseZone：**land-number 反查 API**（非 ArcGIS
    #   空間 /query），與台中 UPGS、與本檔 bbox 登錄表不同型：
    #     POST /Public/LandUseZone/Query  body townCode=<2碼>&sectCode=<4碼>&landCode=<地號>
    #     → {zoneName 分區, planName 計畫名, projectID 案號, landShape rings(EPSG:3826), landBox}
    #     另有 GetTwons/GetSects/GetLandCodes（級聯下拉）、MutilQuery（landCode1~2 範圍）。
    #   **回幾何＋分區、免登入、段碼為標準 4 碼**（同 section_index.csv），品質好；但入口是
    #   「地段地號→分區」不是「bbox→分區」。無建蔽容積欄。→ 要接需在本檔另開一條 land-number
    #   查詢路徑（可直接吃 ⓪a 已有的地段地號），列後續工項；暫退回本機 shp。
    # ✖ 台東縣 uma.taitung.gov.tw/TTurban/Map/PeopleMap：即使「民眾版」PeopleMap 也 **302 導向
    #   Identity/Account/Login**（需登入）；ttgis.taitung.gov.tw 無開放 arcgis root。agent 不可
    #   代填帳密 → 此圖台自動化走不通，退回本機 shp（如日後找到免登入的公開服務再補）。
    # ── 以下三個圖台探勘後「無法比照接入」，記錄結論避免重試（2026-08-24）─────────
    # ✖ 雲林縣 luz.nlma.gov.tw/nlpdb_yunlin/：只是**內政部全國圖台的檢視器**，其 JS 指向
    #   giss.nlma.gov.tw 的 TCDPDA/URBAN_LANDUSE_ZONE_3857——該服務 capabilities 僅 Map、
    #   identify 回「Requested operation is not supported」（即使帶 ngis token 亦同，與
    #   .claude/rules/出圖上色與分區.md 記載的全國性都計無線上逐筆 API 一致）。雲林 JS 內
    #   **無任何縣級自有 arcgis host**，故此портал 拿不到逐宗地分區。→ 雲林退回本機 shp。
    # ✖ nsp.nlma.gov.tw/ngis/：頁面載入 challenges.cloudflare.com/turnstile（Cloudflare
    #   人機驗證）＋自帶 CloudflareTurnstile.js。繞過 bot 檢測屬禁止事項，此路不可走。
    # ⚠ 臺中市 mcgbm.taichung.gov.tw/tccgup/umap/：都市計畫使用分區以**圖磚快取**
    #   （lohas.taichung.gov.tw .../Tiled3857/URBAN3857，capabilities=None 無 Query）呈現，
    #   逐筆屬性走**自訂 land-number Web API「UPQS」**（mcgbm.../tccgup/UPQS/api/<APIname>/
    #   <地段地號 DataKey>），不是 ArcGIS 空間 /query，與本檔 bbox→query 的接法不同型。
    #   可查詢的 Dynamic3857/URPlan 只有「城鄉發展地區」（國土功能分區級，粗）非逐宗地都計。
    #   → 若要接臺中，須另寫 UPQS 反查（先以地段地號查 DataKey、再取分區），列為後續工項；
    #   暫退回本機 shp。
    # ⚠ 新竹縣：探勘完成、REST /query 可用（URBAN3857/4=新竹縣都市計畫，含使用分區/建蔽/
    #   容積/計畫區/計畫案，SR 3857 可用 outSR=3826 轉出），但 token 機制無法穩定自動化——
    #   token 為**短期輪替＋執行時 JS 動態注入**（非靜態寫在 HTML；瀏覽器初載會即時取新 token
    #   注入 inline 變數 token2，靜態抓 HTML 只拿到已過期/空值，實測回 498 Invalid Token）。
    #   故標 unstable：暫不支援，退回本機 都市計畫/ shp。日後若解出 token 取得（找出注入 token2
    #   的執行時端點／或以 headless 取 live 值），把 unstable 拿掉即可啟用；下列參數皆已驗證正確。
    "新竹縣": {
        "unstable": True,
        "query": "https://imap.hchg.gov.tw/arcgis/rest/services/Tiled3857/"
                 "URBAN3857/MapServer/4/query",
        "proxy": None,
        "referer": "https://imap.hchg.gov.tw/webgis/",
        "token_page": "https://imap.hchg.gov.tw/webgis/",
        "token_warmup": ["https://imap.hchg.gov.tw/"],
        "token_regex": (r"'server'\s*:\s*'https://imap\.hchg\.gov\.tw/arcgis/rest/services'"
                        r"\s*,\s*'token'\s*:\s*'([^']+)'"),
        "fields": {"ZONE": "使用分區", "ZONE_USE": "分區簡稱", "BCR": "建蔽率",
                   "FAR": "容積率", "PLAN_AREA": "計畫區名稱", "PLAN_NO": "計畫案名稱"},
    },
}
_OUTCOLS = ["COUNTY", "ZONE", "ZONE_USE", "BCR", "FAR", "PLAN_AREA", "PLAN_NO"]


def _supported() -> list:
    """目前穩定可用（未標 unstable）的縣市。"""
    return [c for c, v in COUNTY.items() if not v.get("unstable")]


def _ring_signed_area(ring: list) -> float:
    s = 0.0
    for i in range(len(ring) - 1):
        s += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return s / 2.0


def _esri_polygon_to_shapely(geom: dict):
    """esri polygon（rings）→ shapely。esri 慣例：外環順時針（signed area<0）、
    內洞逆時針（>0）。把每個洞指派給包含它的外環。"""
    rings = geom.get("rings") if geom else None
    if not rings:
        return None
    outers, holes = [], []
    for r in rings:
        pts = [(p[0], p[1]) for p in r]
        if len(pts) < 4:
            continue
        (outers if _ring_signed_area(pts) < 0 else holes).append(pts)
    if not outers:                       # 全同向：整批當外環（保守）
        outers, holes = holes, []
    polys = []
    for o in outers:
        po = Polygon(o)
        myholes = [h for h in holes if po.contains(Polygon(h).representative_point())]
        polys.append(Polygon(o, myholes))
    if not polys:
        return None
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


# 部分縣市政府伺服器只送主機憑證、未附中繼憑證（憑證鏈不完整），Python 預設驗證會
# CERTIFICATE_VERIFY_FAILED。對這些站台（登錄表標 insecure:True）以未驗證 context 連線
# ——瀏覽器帶內建中繼庫可正常開，這裡放寬是為了取得公開圖資，非規避人機驗證。
_INSECURE_CTX = ssl.create_default_context()
_INSECURE_CTX.check_hostname = False
_INSECURE_CTX.verify_mode = ssl.CERT_NONE


def _http_get(url: str, referer: str | None = None, timeout: int = 60,
              insecure: bool = False) -> str:
    hdr = {"User-Agent": _UA}
    if referer:
        hdr["Referer"] = referer
        hdr["Origin"] = referer.split("/", 3)[0] + "//" + referer.split("/", 3)[2]
    req = urllib.request.Request(url, headers=hdr)
    ctx = _INSECURE_CTX if insecure else None
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _http_post(url: str, data: dict, referer: str | None = None,
               timeout: int = 45, insecure: bool = False) -> str:
    hdr = {"User-Agent": _UA, "X-Requested-With": "XMLHttpRequest",
           "Content-Type": "application/x-www-form-urlencoded"}
    if referer:
        hdr["Referer"] = referer
        hdr["Origin"] = referer.split("/", 3)[0] + "//" + referer.split("/", 3)[2]
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers=hdr)
    ctx = _INSECURE_CTX if insecure else None
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ── land-number 反查登錄表（與 bbox 的 COUNTY 分開；接法不同型）────────────────
# 這類圖台不吃「bbox→空間查詢」，而是「地段地號→分區＋幾何」——正好吃產線 ⓪a 已有的
# 縣市/鄉鎮/段/地號。新增縣市＝照此格式補一列（endpoints 相對路徑、resp 欄位映射）。
LANDNUM_COUNTY = {
    "宜蘭縣": {
        # 探勘 2026-08-24：eadgis.e-land.gov.tw/Public/LandUseZone 的自訂 API，免登入、
        #   預設 SSL 可用（不必 insecure）。段碼為標準 4 碼（同 section_index.csv）；
        #   landCode 直接吃人類地號（含子號，如 "98-11"）。Query 回 zoneName＋planName＋
        #   projectID(計畫案號)＋landShape(rings, EPSG:3826)。無建蔽容積欄。
        "base": "https://eadgis.e-land.gov.tw",
        "referer": "https://eadgis.e-land.gov.tw/Public/LandUseZone",
        "insecure": False,
        "towns": "/Public/LandUseZone/GetTwons",   # POST(無 body) → [{code,name}]
        "sects": "/Public/LandUseZone/GetSects",   # POST townCode= → {"sects":[{code,name}]}
        "sects_key": "sects",
        "query": "/Public/LandUseZone/Query",      # POST townCode&sectCode&landCode
        "q_params": ("townCode", "sectCode", "landCode"),
        "resp": {"ZONE": "zoneName", "PLAN_AREA": "planName",
                 "PLAN_NO": "projectID", "shape": "landShape"},
        "shape_sr": EPSG,
    },
    "新竹市": {
        # 探勘 2026-08-24（Vue SPA，服務從瀏覽器 XHR＋app.js 定位）：自訂 REST，**token＋兩段式**。
        #   token：POST /HcLanduseMapApi/api/Token/GetToken {} → DATA.token（GUID，隔日失效）；
        #   ★token 放在**每支 API 的 JSON body 的 `Token` 欄**，不是 header（app.js 實測）。
        #   流程：GetSectList（全市段碼，段名→CODE）→ 逐地號 GetWKTBySectLandNo(SectCode,LandNo)
        #   → WKT → GetLanduseListByWkt(Wkt) → landuse(分區名)＋wkt(EPSG:3826)。段碼全市唯一、
        #   不需區碼。預設 SSL 可用。無建蔽容積、無計畫案號欄（只有分區名＋幾何）。
        "handler": "hccg",
        "base": "https://urbangis.hccg.gov.tw",
        "referer": "https://urbangis.hccg.gov.tw/HcUrbanMap/",
        "insecure": False,
        "token_api": "/HcLanduseMapApi/api/Token/GetToken",
        "sects": "/HccgApi/api/MapLocation/GetSectList",
        "wkt_api": "/HccgApi/api/MapLocation/GetWKTBySectLandNo",
        "zone_api": "/HccgApi/api/Landuse/GetLanduseListByWkt",
    },
}
    # ✖ 臺中市 mcgbm.taichung.gov.tw：**分區資料受權限鎖死，暫無法自動化**（2026-08-24 徹查）。
    #   逐宗地分區欄 LUSE 只在 ArcGIS 圖層 Tiled3857/LandAdminNew3857/MapServer/2，該層：
    #     · 無 token → 「Token Required」
    #     · 帶 LANDWA JWT（POST /LANDWA/Api/Token，date-seeded）→ 「Invalid Token」（JWT 是給
    #       LANDWA REST 用，非 arcgis server token）
    #     · 帶 InitMap.js 寫死的 arcgis token（userId tccguser1）→ **403 無權限**
    #   LANDWA REST 的 /LANDWA/api/RALID（帶 JWT，參數 TName/sectid/landid）只回地籍屬性
    #   （面積 AA10／公告現值 AA16／地價 AA17／座標）**沒有分區**；AA11/AA12 恆 null。
    #   可 Query 的 Dynamic3857/URPlan 只有「城鄉發展地區」（國土功能分區級、粗）。
    #   → 台中退回本機 都市計畫/ shp；日後若找到 LUSE 層的合法存取再補。


def _resolve_code(items: list, want: str, code_key="code", name_key="name") -> str | None:
    """從 [{code,name}] 清單解析使用者給的名稱或代碼 → code。
    接受：完全等於 code／完全等於 name／name 去尾「段」「鄉」「鎮」「市」後相符／name 為前綴。"""
    want = str(want).strip()
    for it in items:
        if str(it.get(code_key)) == want:
            return str(it.get(code_key))
    for it in items:
        if it.get(name_key) == want:
            return str(it.get(code_key))
    stripped = re.sub(r"[段鄉鎮市區]$", "", want)
    for it in items:
        nm = re.sub(r"[段鄉鎮市區]$", "", str(it.get(name_key) or ""))
        if nm == stripped or str(it.get(name_key) or "").startswith(want):
            return str(it.get(code_key))
    return None


def _wkt_to_shapely(wkt: str):
    """WKT（POLYGON/MULTIPOLYGON，EPSG:3826）→ shapely。新竹市 API 回 WKT 字串。"""
    if not wkt:
        return None
    try:
        from shapely import wkt as _wkt  # noqa: PLC0415
        g = _wkt.loads(wkt)
        return g if g and not g.is_empty else None
    except Exception:  # noqa: BLE001
        return None


def _http_post_json(url: str, obj: dict, referer: str | None, insecure=False, timeout=45) -> dict:
    hdr = {"User-Agent": _UA, "Content-Type": "application/json"}
    if referer:
        hdr["Referer"] = referer
        hdr["Origin"] = referer.split("/", 3)[0] + "//" + referer.split("/", 3)[2]
    req = urllib.request.Request(url, data=json.dumps(obj).encode(), headers=hdr)
    ctx = _INSECURE_CTX if insecure else None
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _query_hccg(cfg: dict, county: str, sect: str, landnos: list) -> tuple[list, list, list]:
    """新竹市專屬：token＋兩段式（地號→WKT→分區）。段碼全市唯一、不需區碼。"""
    base, ref, insec = cfg["base"], cfg.get("referer"), cfg.get("insecure", False)
    tok = _http_post_json(base + cfg["token_api"], {}, ref, insec)["DATA"]["token"]
    sraw = _http_post_json(base + cfg["sects"], {"Token": tok}, ref, insec)
    sc = _resolve_code(sraw.get("DATA") or [], sect, code_key="CODE", name_key="SECTNAME")
    if not sc:
        raise SystemExit(f"段「{sect}」在新竹市查無段碼。")
    colors = _load_zone_colors()
    rows, geoms, raw = [], [], []
    for ln in landnos:
        ln = str(ln).strip()
        if not ln:
            continue
        w = _http_post_json(base + cfg["wkt_api"],
                            {"SectCode": sc, "LandNo": ln, "Token": tok}, ref, insec)
        data = w.get("DATA") or []
        if not data or not data[0].get("WKT"):
            print(f"  ⚠ 地號 {ln} 查無幾何"); continue
        wkt = data[0]["WKT"]
        z = _http_post_json(base + cfg["zone_api"], {"Wkt": wkt, "Token": tok}, ref, insec)
        raw.append(z)
        zdata = z.get("DATA") or []
        if not zdata:
            print(f"  ⚠ 地號 {ln} 查無分區（可能非都市計畫土地）"); continue
        for item in zdata:                     # 一宗地可能跨多分區，逐段輸出
            g = _wkt_to_shapely(item.get("wkt") or wkt)
            if g is None or g.is_empty:
                continue
            zone = (item.get("landuse") or "").strip()
            rows.append({"COUNTY": county, "ZONE": zone, "ZONE_USE": None,
                         "BCR": None, "FAR": None, "PLAN_AREA": None,
                         "PLAN_NO": None, "LANDNO": ln,
                         "Color1": _color_for(zone, colors)})
            geoms.append(g)
            print(f"  地號 {ln}：{zone}")
    return rows, geoms, raw


def query_by_landno(county: str, town: str, sect: str, landnos: list) -> tuple[list, list, list]:
    """land-number 反查：回 (rows, geoms, raw)，格式與 bbox 路徑的 rows/geoms 一致。"""
    cfg = LANDNUM_COUNTY.get(county)
    if not cfg:
        raise SystemExit(f"縣市「{county}」未登錄 land-number 圖台；"
                         f"已登錄：{list(LANDNUM_COUNTY)}。")
    if cfg.get("handler") == "hccg":           # 新竹市：token＋兩段式，走專屬 handler
        return _query_hccg(cfg, county, sect, landnos)
    base, ref, insec = cfg["base"], cfg.get("referer"), cfg.get("insecure", False)
    towns = json.loads(_http_post(base + cfg["towns"], {}, ref, insecure=insec))
    tc = _resolve_code(towns, town)
    if not tc:
        raise SystemExit(f"鄉鎮「{town}」在 {county} 圖台查無對應代碼。")
    sraw = json.loads(_http_post(base + cfg["sects"], {"townCode": tc}, ref, insecure=insec))
    sects = sraw.get(cfg["sects_key"]) if cfg.get("sects_key") else sraw
    sc = _resolve_code(sects, sect)
    if not sc:
        raise SystemExit(f"段「{sect}」在 {county} {town} 查無對應段碼。")
    zk, pk, nk, shk = (cfg["resp"]["ZONE"], cfg["resp"]["PLAN_AREA"],
                       cfg["resp"]["PLAN_NO"], cfg["resp"]["shape"])
    p1, p2, p3 = cfg["q_params"]
    colors = _load_zone_colors()
    rows, geoms, raw = [], [], []
    for ln in landnos:
        ln = str(ln).strip()
        if not ln:
            continue
        try:
            j = json.loads(_http_post(base + cfg["query"],
                                      {p1: tc, p2: sc, p3: ln}, ref, insecure=insec))
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠ 地號 {ln} 查詢失敗：{type(e).__name__}"); continue
        raw.append(j)
        if not j.get("success", True) or not j.get(shk):
            print(f"  ⚠ 地號 {ln} 查無分區（success={j.get('success')}）"); continue
        g = _esri_polygon_to_shapely({"rings": j[shk]})
        if g is None or g.is_empty:
            print(f"  ⚠ 地號 {ln} 幾何為空"); continue
        zone = (j.get(zk) or "").strip()
        rows.append({"COUNTY": county, "ZONE": zone, "ZONE_USE": None,
                     "BCR": None, "FAR": None,
                     "PLAN_AREA": (j.get(pk) or "").strip(),
                     "PLAN_NO": (j.get(nk) or "").strip(),
                     "LANDNO": ln,
                     "Color1": _color_for(zone, colors)})
        geoms.append(g)
        print(f"  地號 {ln}：{zone}")
    return rows, geoms, raw


def _write_output(out_dir: str, rows: list, geoms: list, raw) -> int:
    """rows+geoms → 都市計畫_縣市圖台.shp（EPSG:3826，含 Color1）＋ .json；印摘要。"""
    if not rows:
        print("⚠ 查無任何都市計畫分區。")
        return 1
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs=f"EPSG:{EPSG}")
    n_nocolor = sum(1 for r in rows if not r.get("Color1"))
    if n_nocolor:
        print(f"  ⚠ {n_nocolor} 筆用地名在官方色表查無對應色（Color1 空白，出圖將無底色）")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shp = out / "都市計畫_縣市圖台.shp"
    gdf.to_file(shp, encoding="utf-8")
    (out / "都市計畫_縣市圖台.json").write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    from collections import Counter  # noqa: PLC0415
    cnt = Counter((r.get("ZONE") or "?") for r in rows)
    print(f"→ {shp}（{len(gdf)} 筆）")
    for name, n in cnt.most_common(12):
        print(f"    {name}：{n} 筆")
    print("⚠ 圖台為都發局現行版、屬參考；正式分區認定以都發局核發文件為準。")
    return 0


def _fetch_token(cfg: dict) -> str | None:
    # (a) token_url：token 寫在一支靜態 JS 檔裡（如新北 var NtpcUrbToken="..."）——
    #     每次即時抓，比寫死值耐輪替。抓不到才報錯。
    if cfg.get("token_url"):
        try:
            js = _http_get(cfg["token_url"], referer=cfg.get("referer"),
                           insecure=cfg.get("insecure", False))
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"{cfg.get('_county', '該縣市')}圖台 token JS 取不到：{type(e).__name__}")
        m = re.search(cfg["token_regex"], js)
        if not m:
            raise SystemExit(f"{cfg.get('_county', '該縣市')}圖台 token JS 版型可能已改，regex 未命中。")
        return m.group(1).strip()
    if not cfg.get("token_page"):
        return None
    # token 為圖台 session 相依、動態注入首次載入的 inline HTML——用 cookie jar 帶
    # session 抓（先 warmup 網站根設 cookie，再抓頁面），才拿得到非空 token。
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    hdr = {"User-Agent": _UA, "Referer": cfg["token_page"]}
    for u in cfg.get("token_warmup", []) + [cfg["token_page"]]:
        try:
            html = opener.open(urllib.request.Request(u, headers=hdr), timeout=60
                               ).read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            html = ""
    m = re.search(cfg["token_regex"], html)
    tok = m.group(1).strip() if m else ""
    if not tok:
        raise SystemExit(
            f"{cfg.get('_county', '該縣市')}圖台 token 取不到有效值（token 為 session "
            "相依、動態注入，靜態抓取取不到）——此縣市自動化尚未穩定，請先退回本機 "
            "都市計畫/ shp（見 .claude/rules/出圖上色與分區.md）。")
    return tok


def _proxied(cfg: dict, target_url: str) -> str:
    proxy = cfg.get("proxy")
    if not proxy:
        return target_url
    if cfg.get("proxy_encode"):
        return proxy + urllib.parse.quote(target_url, safe="")
    return proxy + target_url


def query_zone(county: str, bbox: tuple[float, float, float, float]) -> dict:
    """對 bbox（EPSG:3826）查該縣市圖台都市計畫分區，回 geojson dict（含分頁彙整）。"""
    cfg = COUNTY.get(county)
    if not cfg:
        raise SystemExit(f"縣市「{county}」尚未登錄圖台；已登錄：{_supported()}。"
                         "未登錄縣市請退回本機 都市計畫/ shp。")
    if cfg.get("unstable"):
        raise SystemExit(
            f"{county}圖台已探勘、REST 可查，但 token 為短期輪替＋動態注入、靜態自動化"
            "尚未穩定（詳 urbanplan_fetch.py 登錄表註解）——目前請退回本機 都市計畫/ shp。")
    token = _fetch_token(cfg)
    xmin, ymin, xmax, ymax = bbox
    env = {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax,
           "spatialReference": {"wkid": EPSG}}
    feats: list = []
    offset = 0
    while True:
        params = {
            "geometry": json.dumps(env, ensure_ascii=False),
            "geometryType": "esriGeometryEnvelope",
            "inSR": str(EPSG), "outSR": str(EPSG),
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*", "returnGeometry": "true", "f": "json",
        }
        if offset:                      # ⚠ 部分服務不支援分頁（高雄回 400）——offset=0 時不帶
            params["resultOffset"] = str(offset)
        if token:
            params["token"] = token
        target = cfg["query"] + "?" + urllib.parse.urlencode(params)
        txt = _http_get(_proxied(cfg, target), referer=cfg.get("referer"),
                        insecure=cfg.get("insecure", False))
        try:
            j = json.loads(txt)
        except json.JSONDecodeError:
            raise SystemExit(f"圖台回應非 JSON（前 200 字）：{txt[:200]}")
        if j.get("error"):
            msg = json.dumps(j["error"], ensure_ascii=False)
            if offset and "agination" in msg:   # 分頁不支援：用已取回的即可（site bbox 遠小於 maxRecordCount）
                print(f"  （服務不支援分頁，已取 {len(feats)} 筆；site bbox 遠小於上限，通常已足）")
                break
            raise SystemExit(f"圖台回錯誤：{msg[:200]}")
        got = j.get("features", [])
        feats.extend(got)
        if not j.get("exceededTransferLimit") or not got:
            break
        offset += len(got)
    return {"features": feats}   # esri json：features=[{attributes, geometry:{rings}}]


def _landno_main(a) -> int:
    """land-number 反查縣市的 main 分支。town/sect/landnos 可直接給，或從 --joined 讀。
    ⚠ 部分縣市（新竹市 handler=hccg）段碼全市唯一、不需 town。"""
    need_town = LANDNUM_COUNTY.get(a.county, {}).get("handler") != "hccg"
    town, sect, landnos = a.town, a.sect, list(a.landnos or [])
    if a.joined and not (sect and landnos):
        import sys as _sys  # noqa: PLC0415
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from shp_trust_utils import F_TOWN, F_SEG, F_LANDNO, shp_get  # noqa: PLC0415
        g = gpd.read_file(a.joined)
        recs = g.to_dict("records")
        from collections import Counter  # noqa: PLC0415
        if not town:
            town = Counter(shp_get(r, F_TOWN) for r in recs).most_common(1)[0][0]
        if not sect:
            sect = Counter(shp_get(r, F_SEG) for r in recs).most_common(1)[0][0]
        if not landnos:
            landnos = [shp_get(r, F_LANDNO) for r in recs if shp_get(r, F_LANDNO)]
    if not (sect and landnos) or (need_town and not town):
        raise SystemExit(f"{a.county} 走 land-number 反查，需 "
                         f"{'--town/' if need_town else ''}--sect/--landnos，"
                         "或給含 鄉鎮/段/地號 欄的 --joined 基地 shp。")
    print(f"縣市={a.county}（land-number 反查）　{town or ''} {sect}　{len(landnos)} 筆地號")
    rows, geoms, raw = query_by_landno(a.county, town, sect, landnos)
    return _write_output(a.out, rows, geoms, raw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", required=True,
                    help=f"縣市（bbox 穩定：{_supported()}；land-number：{list(LANDNUM_COUNTY)}）")
    ap.add_argument("--joined", help="基地 shp（bbox 路徑取外擴 bbox；land-number 路徑讀 鄉鎮/段/地號）")
    ap.add_argument("--bbox", nargs=4, type=float,
                    metavar=("XMIN", "YMIN", "XMAX", "YMAX"), help="查詢 bbox（EPSG:3826）")
    ap.add_argument("--town", help="land-number 路徑：鄉鎮名或 2 碼代碼")
    ap.add_argument("--sect", help="land-number 路徑：段名或 4 碼段碼")
    ap.add_argument("--landnos", nargs="+", help="land-number 路徑：地號（可多筆，含子號如 98-11）")
    ap.add_argument("--out", required=True, help="輸出資料夾")
    ap.add_argument("--margin", type=float, default=60.0, help="基地 bbox 外擴公尺（預設 60）")
    a = ap.parse_args()

    if a.county in LANDNUM_COUNTY:      # land-number 反查縣市走另一條路
        return _landno_main(a)

    if a.bbox:
        bbox = tuple(a.bbox)
    elif a.joined:
        g = gpd.read_file(a.joined)
        if g.crs and g.crs.to_epsg() != EPSG:
            g = g.to_crs(EPSG)
        minx, miny, maxx, maxy = g.total_bounds
        m = a.margin
        bbox = (minx - m, miny - m, maxx + m, maxy + m)
    else:
        raise SystemExit("需給 --joined 或 --bbox 其一")

    print(f"縣市={a.county}　查詢範圍(EPSG:3826)={tuple(round(v, 1) for v in bbox)}")
    gj = query_zone(a.county, bbox)
    feats = gj["features"]
    if not feats:
        print("⚠ 圖台在此範圍查無都市計畫分區（可能非該縣市都市計畫區、或超出圖台涵蓋）。")
        return 1

    cfg = COUNTY[a.county]
    fmap = cfg["fields"]
    zone_colors = _load_zone_colors()
    rows, geoms = [], []
    for f in feats:
        p = f.get("attributes", {})
        g = _esri_polygon_to_shapely(f.get("geometry", {}))
        if g is None or g.is_empty:
            continue
        geoms.append(g)
        row = {"COUNTY": a.county}
        for out_col in _OUTCOLS[1:]:
            src = fmap.get(out_col)
            val = p.get(src) if src else None
            row[out_col] = (val.strip() if isinstance(val, str) else val)
        # Color1：先用 ZONE_USE（用地種類）反查官方色，退回 ZONE（分區簡稱）
        row["Color1"] = _color_for(row.get("ZONE_USE"), zone_colors) \
            or _color_for(row.get("ZONE"), zone_colors)
        rows.append(row)
    return _write_output(a.out, rows, geoms, gj)


if __name__ == "__main__":
    sys.exit(main())
