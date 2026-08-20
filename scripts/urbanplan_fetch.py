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


def _http_get(url: str, referer: str | None = None, timeout: int = 60) -> str:
    hdr = {"User-Agent": _UA}
    if referer:
        hdr["Referer"] = referer
        hdr["Origin"] = referer.split("/", 3)[0] + "//" + referer.split("/", 3)[2]
    req = urllib.request.Request(url, headers=hdr)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _fetch_token(cfg: dict) -> str | None:
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
        txt = _http_get(_proxied(cfg, target), referer=cfg.get("referer"))
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", required=True, help=f"縣市（穩定可用：{_supported()}）")
    ap.add_argument("--joined", help="基地 shp（取 bbox 外擴查詢）")
    ap.add_argument("--bbox", nargs=4, type=float,
                    metavar=("XMIN", "YMIN", "XMAX", "YMAX"), help="查詢 bbox（EPSG:3826）")
    ap.add_argument("--out", required=True, help="輸出資料夾")
    ap.add_argument("--margin", type=float, default=60.0, help="基地 bbox 外擴公尺（預設 60）")
    a = ap.parse_args()

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
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs=f"EPSG:{EPSG}")
    n_nocolor = sum(1 for r in rows if not r.get("Color1"))
    if n_nocolor:
        print(f"  ⚠ {n_nocolor} 筆用地名在官方色表查無對應色（Color1 空白，出圖將無底色）")

    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    shp = out_dir / "都市計畫_縣市圖台.shp"
    gdf.to_file(shp, encoding="utf-8")
    (out_dir / "都市計畫_縣市圖台.json").write_text(
        json.dumps(gj, ensure_ascii=False), encoding="utf-8")

    from collections import Counter  # noqa: PLC0415
    cnt = Counter((r.get("ZONE") or "?") for r in rows)
    print(f"→ {shp}（{len(gdf)} 筆）")
    for name, n in cnt.most_common(12):
        print(f"    {name}：{n} 筆")
    print("⚠ 圖台為都發局現行版、屬參考；正式分區認定以都發局核發文件為準。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
