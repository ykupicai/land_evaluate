# -*- coding: utf-8 -*-
"""農業部地籍「空間範圍查詢」——吃一個範圍，直接回該範圍內全部宗地的幾何＋屬性。

★ 建立緣由（2026-09-04 麻豆客子寮段案定案，使用者裁示「全改」）
------------------------------------------------------------------
在此之前，`cadastre_neighbors.py` 取周邊地籍走的是兩段式：
  ① 用**本機地籍快照**（`地籍圖資/<縣市>.shp`，Ver 111年2月）認出「框內有哪些地號」
  ② 拿那些地號逐筆去問農業部 ring API（LandAddressToLocation_ring）換現行幾何屬性

這條路的**結構性缺陷**是「候選清單完全由一份三年前的快照決定」：快照沒有的地號，
從頭到尾不會被查詢。麻豆案實錯——港子尾段整段重測改名為客子寮段並全段重編號，
其後又以「和解分割」（113-11-29）分出 **374-1（12,112㎡）／374-2（24,225㎡）**，
兩筆合計 36,338 ㎡ 就在基地西側 3.9 公尺處，**在快照裡沒有任何舊地號對得上**，
於是從未被送查、圖上整片留白。既有的補救機制（母號縮水偵測、子號探測）也一起失效：
縮水偵測靠「同一地號兩邊比面積」，整段重編號後兩邊地號集合完全不重疊；子號探測
用的是舊段名，該段在現行版查無。

本模組改走農業部同一台 ArcGIS 上的**地籍圖層本體**（先前未發現）：

  <cadastralSoeUrl>CadastralMap/CadastralMap_Tiled_<版次>/MapServer
      layer 0 = Penghu、layer 1 = Taiwan
      capabilities: Map,Query,Data　maxRecordCount: 2000　supportsPagination: true

送一個 envelope 就回範圍內全部宗地，**不必事先知道有哪些地號**。麻豆案實測：
  · 出圖框（574×400m）→ 154 筆、**框內覆蓋 100.00%**（原做法 145 筆／81.91%）
  · 與 ring API 逐筆比對 126 筆，**IoU 全部 = 1.000000**、登記面積完全相同
    → 不是近似值，是同一份資料的另一個出入口
  · 員林那種密集市區大框（2km×1.4km、1,684 筆帶幾何）**0.3 秒**回完
    （原做法逐筆查要數小時，員林案當初正是因此被迫放棄即時回填）

屬性欄位比 getLandInfo 還多，含 **使用分區／使用類別／登記面積／公告現值地價／
都市計畫區**，故周邊地籍不必再逐筆打 getLandInfo。

★ 仍需另外處理的三件事（本模組不做）
------------------------------------------------------------------
1. **權屬**（公私別／所有權人／管理者）：本圖層沒有這些欄位，仍走
   `public_land.py`（MOI_API_039，免登入）。本模組提供 `attach_ownership()` 代勞。
2. **基地本身**：使用者給的是地段地號，仍須 `cadastre_fetch.py` 逐筆查
   （含重測改號追蹤）。只有**鄰地**改成框查。
3. **未登錄地／母號≥9000**：本圖層是否收錄視個案而定；一律以「框內覆蓋率」
   當硬指標，不足 100% 就明確警告，不可靜默留白。

★ 與 ring API 的安全性差異（重要）
------------------------------------------------------------------
ring API 會做**模糊比對**，查不到就回別筆地（實測會跨縣市），所以那條路必須有
14 碼閘門。**空間查詢沒有模糊比對**——回什麼就是框裡有什麼，故不需該閘門。
但仍保留兩道自我檢查：①回傳筆數 vs `returnCountOnly` 應一致（分頁沒漏頁）
②框內覆蓋率（呼叫端做）。

用法
------------------------------------------------------------------
  import moa_parcel_query as mpq
  recs = mpq.query_region(shapely_geom)             # 或 query_envelope(x1,y1,x2,y2)
  gdf  = mpq.to_gdf(recs)                           # 欄位同 cadastre_fetch 輸出
  gdf  = mpq.attach_ownership(gdf)                  # 補公私別／所有權／管理者

  CLI（除錯用）：
  python -X utf8 scripts/moa_parcel_query.py --bbox 169621 2567716 170195 2568116 \
      --out <資料夾> --name 框查
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(os.path.expanduser("~"), ".claude", "skills",
                     "cadastre-check", "scripts")

TIMEOUT = 120
PAGE = 2000              # 服務端 maxRecordCount，取滿以減少往返
REFERER = "https://map.moa.gov.tw/farmland/survey.html"
UA = "Mozilla/5.0"

# 這批服務的憑證鏈在部分環境驗不過（與 skill cadastre-check 的 moa.py 同源問題）。
# 傳輸內容是公開圖資，且回傳值都會經過欄位與覆蓋率檢查，故關閉驗證換取可用性。
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

_layer_cache: dict[str, list] = {}


class MoaQueryError(RuntimeError):
    pass


def _moa():
    """借用 skill cadastre-check 的 moa（token 與版次清單的權威來源）。"""
    if not os.path.isdir(SKILL):
        raise MoaQueryError(f"找不到 skill cadastre-check：{SKILL}")
    if SKILL not in sys.path:
        sys.path.insert(0, SKILL)
    import moa  # noqa: PLC0415
    return moa


def _cadlib():
    if not os.path.isdir(SKILL):
        raise MoaQueryError(f"找不到 skill cadastre-check：{SKILL}")
    if SKILL not in sys.path:
        sys.path.insert(0, SKILL)
    import cadlib  # noqa: PLC0415
    return cadlib


def newest_version() -> str:
    return _moa().newest_version()


def service_url(version: str | None = None) -> str:
    """地籍圖層 MapServer 的完整位址。

    ⚠ 版次寫在**服務名稱裡**（..._Tiled_115Jun），一律由 moa.newest_version()
    動態組出，不可寫死——版次每兩個月換一次。
    """
    m = _moa()
    ver = version or m.newest_version()
    base = m.config()["cadastralSoeUrl"]
    return f"{base}CadastralMap/CadastralMap_Tiled_{ver}/MapServer", ver


def _post(url: str, params: dict, retry_token: bool = True):
    """POST 查詢（geometry JSON 可能很長，一律用 POST 不用 GET）。

    token 會輪替；回應帶 Token 字樣的 error 時重抓一次 config 再試。
    """
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": UA, "Referer": REFERER,
        "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX) as r:
            j = json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise MoaQueryError(f"連線失敗：{e}")
    except ValueError as e:
        raise MoaQueryError(f"回應不是合法 JSON：{e}")
    if isinstance(j, dict) and j.get("error"):
        msg = str(j["error"].get("message", ""))
        if retry_token and "Token" in msg:
            _moa().config(force=True)
            params = dict(params, token=_moa().config()["cadastralSoeToken"])
            return _post(url, params, retry_token=False)
        raise MoaQueryError(f"查詢失敗：{msg}")
    return j


def polygon_layers(version: str | None = None) -> list[tuple[int, str]]:
    """該版次服務底下的宗地面圖層（實測 0=Penghu、1=Taiwan）。

    不寫死 id：離島與本島分屬不同圖層，且日後可能增減。一律兩層都查再合併，
    因為呼叫端只知道座標、不知道該用哪一層。
    """
    url, ver = service_url(version)
    if ver in _layer_cache:
        return _layer_cache[ver]
    m = _moa()
    j = _post(url, {"f": "json", "token": m.config()["cadastralSoeToken"]})
    out = [(l["id"], l["name"]) for l in (j.get("layers") or [])]
    if not out:
        raise MoaQueryError(f"版次 {ver} 的地籍服務沒有任何圖層（服務可能改版）")
    _layer_cache[ver] = out
    return out


def _envelope(x1, y1, x2, y2) -> str:
    return json.dumps({"xmin": x1, "ymin": y1, "xmax": x2, "ymax": y2,
                       "spatialReference": {"wkid": 3826}})


def count_envelope(x1, y1, x2, y2, version: str | None = None) -> int:
    """先數筆數——大框開跑前用來評估成本，也是分頁完整性的對照基準。"""
    url, _ = service_url(version)
    tok = _moa().config()["cadastralSoeToken"]
    total = 0
    for lid, _name in polygon_layers(version):
        j = _post(f"{url}/{lid}/query", {
            "f": "json", "token": tok, "geometryType": "esriGeometryEnvelope",
            "geometry": _envelope(x1, y1, x2, y2), "inSR": 3826,
            "spatialRel": "esriSpatialRelIntersects", "returnCountOnly": "true"})
        total += int(j.get("count") or 0)
    return total


def query_envelope(x1, y1, x2, y2, version: str | None = None,
                   progress=None) -> list[dict]:
    """回 envelope 內全部宗地：[{**屬性, "rings": [[(x,y)...]]}, ...]（EPSG:3826）。

    ★ 分頁完整性自我檢查：先 returnCountOnly 拿總數，抓完比對；不符即拋錯，
      **不可靜默少抓**（少抓的症狀就是圖面留白，正是本模組要根除的問題）。
    """
    url, ver = service_url(version)
    tok = _moa().config()["cadastralSoeToken"]
    out: list[dict] = []
    for lid, name in polygon_layers(version):
        want = int(_post(f"{url}/{lid}/query", {
            "f": "json", "token": tok, "geometryType": "esriGeometryEnvelope",
            "geometry": _envelope(x1, y1, x2, y2), "inSR": 3826,
            "spatialRel": "esriSpatialRelIntersects",
            "returnCountOnly": "true"}).get("count") or 0)
        if not want:
            continue
        if progress:
            progress(f"  圖層 {name}：{want} 筆，分頁取回…")
        got = 0
        offset = 0
        while offset < want:
            j = _post(f"{url}/{lid}/query", {
                "f": "json", "token": tok, "geometryType": "esriGeometryEnvelope",
                "geometry": _envelope(x1, y1, x2, y2), "inSR": 3826, "outSR": 3826,
                "spatialRel": "esriSpatialRelIntersects", "outFields": "*",
                "returnGeometry": "true",
                "resultOffset": offset, "resultRecordCount": PAGE})
            feats = j.get("features") or []
            if not feats:
                break
            for f in feats:
                a = dict(f.get("attributes") or {})
                a["rings"] = (f.get("geometry") or {}).get("rings") or []
                a["_layer"] = name
                a["_version"] = ver
                out.append(a)
            got += len(feats)
            offset += len(feats)
        if got != want:
            raise MoaQueryError(
                f"圖層 {name} 分頁不完整：宣告 {want} 筆、實得 {got} 筆。"
                "不可少抓（會造成圖面留白），請重試或縮小範圍。")
    return out


def query_region(region, version: str | None = None, progress=None) -> list[dict]:
    """吃任意 shapely 幾何（出圖框 ∪ buffer 環），回真正與之相交的宗地。

    做法＝先用 bounds 打 envelope 查詢，再以 shapely 精確過濾。不直接送
    esriGeometryPolygon：envelope 查詢的服務端行為單純、序列化風險低，
    而過濾在本機做既精確又免除幾何格式相容問題。
    """
    from shapely.geometry import Polygon  # noqa: PLC0415
    x1, y1, x2, y2 = region.bounds
    recs = query_envelope(x1, y1, x2, y2, version, progress)
    keep = []
    for a in recs:
        g = rings_to_polygon(a["rings"])
        if g is None or g.is_empty:
            continue
        if g.intersects(region):
            a["_geom"] = g
            keep.append(a)
    assert Polygon  # 明示相依，避免 linter 誤刪 import
    return keep


def rings_to_polygon(rings):
    """ArcGIS rings → shapely Polygon（外環＋內孔）。

    ArcGIS 慣例：順時鐘＝外環、逆時鐘＝內孔。實務上單一宗地多為單環；
    有孔（裡地）者以帶符號面積判斷，最大面積者為外環，其餘視為孔。
    """
    from shapely.geometry import Polygon, MultiPolygon  # noqa: PLC0415
    if not rings:
        return None
    if len(rings) == 1:
        return Polygon(rings[0])
    polys = [Polygon(r) for r in rings]
    areas = [p.area for p in polys]
    outer_i = areas.index(max(areas))
    outer = polys[outer_i]
    holes = [rings[i] for i in range(len(rings)) if i != outer_i
             and outer.contains(Polygon(rings[i]).representative_point())]
    if holes:
        rest = [rings[i] for i in range(len(rings)) if i != outer_i
                and rings[i] not in holes]
        p = Polygon(rings[outer_i], holes)
        if rest:
            return MultiPolygon([p] + [Polygon(r) for r in rest]).buffer(0)
        return p
    return MultiPolygon(polys).buffer(0)


def _blank(v) -> bool:
    """空值判定：None／NaN／空字串／**字串化的 'none'、'nan'** 都算空。

    ⚠ 後兩者是實務上的坑——pyogrio 讀出的 null 是 None，但經過 str()／JSON 往返
    後會變成字面的 "None"／"nan"，直接拿去組字會生出「海埤段None小段」這種不存在
    的段名，下游 join 全部落空且不會報錯。
    """
    if v is None:
        return True
    try:
        if v != v:          # NaN
            return True
    except Exception:       # noqa: BLE001
        pass
    return str(v).strip().lower() in ("", "none", "nan", "null")


def section_name(seg, sub) -> str:
    """『段』欄不含「段」字、『小段』欄不含「小段」字 → 組成 API／下游用的完整段名。

    客子寮 → 客子寮段；考試潭＋鹿寮 → 考試潭段鹿寮小段。
    本模組與本機快照欄位同構，故 `cadastre_neighbors._section_name` 直接委派到這裡
    ——兩邊各寫一份遲早會分岔，而分岔的症狀是 join 靜默落空。
    ⚠ 只做**完整組字**，絕不可去尾比對（民權段 ≠ 民權一段，那是別人的地）。
    """
    s = "" if _blank(seg) else str(seg).strip()
    b = "" if _blank(sub) else str(sub).strip()
    if not s:
        return ""
    return f"{s}段{b}小段" if b else f"{s}段"


def to_gdf(recs: list[dict], version: str | None = None):
    """轉成與 `cadastre_fetch.py` 輸出**同欄位**的 GeoDataFrame，下游可直接沿用。

    欄位：地號 使分 使類 縣市 鄉鎮 段 登面積 圖面積 偏差pct 版次 狀態 未登記
          公私別 所有權 管理者 原地號 都計區
    ⚠ DBF 欄名上限 **10 bytes**（UTF-8 中文 3 bytes/字 → 最多 3 字）。
      「都市計畫區」12 bytes 會被截斷成壞 UTF-8、整份 shp 讀不出來，故取「都計區」。
    """
    import geopandas as gpd  # noqa: PLC0415
    from cadastre_fetch import is_unregistered  # noqa: PLC0415
    C = _cadlib()

    rows, geoms = [], []
    for a in recs:
        g = a.get("_geom") or rings_to_polygon(a.get("rings"))
        if g is None or g.is_empty:
            continue
        no8 = str(a.get("地號") or "").strip()
        landno = C.format_landno(no8) if no8 else ""
        reg = a.get("登記面積_平方公尺")
        area = g.area
        try:
            dev = round((area - float(reg)) / float(reg) * 100, 4) if reg else None
        except (TypeError, ValueError, ZeroDivisionError):
            dev = None
        rows.append({
            "地號": landno,
            "使分": a.get("使用分區") or "",
            "使類": a.get("使用類別") or "",
            "縣市": a.get("縣市") or "",
            "鄉鎮": a.get("鄉鎮") or "",
            "段": section_name(a.get("段"), a.get("小段")),
            "登面積": reg,
            "圖面積": round(area, 4),
            "偏差pct": dev,
            "版次": a.get("_version") or version or "",
            "狀態": "OK",
            "未登記": "是" if landno and is_unregistered(landno) else "",
            "公私別": "", "所有權": "", "管理者": "",
            "原地號": "",
            "都計區": a.get("都市計畫區") or "",
            # 權屬 join 用（不寫進 shp）
            "_office": a.get("事務所碼") or "", "_sec": a.get("段碼") or "",
        })
        geoms.append(g)
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:3826")
    return gdf


def attach_ownership(gdf, quiet: bool = False):
    """以（事務所碼, 段碼）為單位查 MOI_API_039 公有土地名冊，回填公私別／所有權／管理者。

    ⚠ 「未列＝私有」是**反面推論**（與 cadastre_fetch 同一套判準），關鍵筆數仍須
      以謄本或 TCDGIS 覆核；未登記土地本來就不在名冊，不下私有的判斷。
    查詢失敗只印警告、欄位留空，不阻斷——留空會顯示為「無法判定」，比誤標安全。
    """
    if gdf is None or len(gdf) == 0:
        return gdf
    if "_office" not in gdf.columns:
        return gdf
    import public_land as PL  # noqa: PLC0415

    pairs = sorted({(str(o), str(s)) for o, s in zip(gdf["_office"], gdf["_sec"])
                    if str(o).strip() and str(s).strip()})
    idx: dict[tuple, tuple] = {}
    for office, sec in pairs:
        try:
            recs = PL.query_section(None, None, sec, unit=office)
        except Exception as e:  # noqa: BLE001
            if not quiet:
                print(f"  ⚠ 權屬查詢失敗（事務所 {office}／段 {sec}："
                      f"{type(e).__name__}），該段權屬欄留空")
            continue
        for rw in recs:
            os_ = PL.owners(rw)
            if os_:
                o = os_[0]
                idx[(office, sec, PL.norm_no(PL._g(rw, "No") or ""))] = (
                    o["公私別"], o["所有權人"], o["管理者"])
        if not quiet:
            print(f"  權屬：事務所 {office}／段 {sec} 公有土地名冊 {len(recs)} 筆")

    have_roster = {(o, s) for (o, s, _n) in idx}
    pri, onm, omg = [], [], []
    for _, r in gdf.iterrows():
        key = (str(r["_office"]), str(r["_sec"]), str(r["地號"]))
        hit = idx.get(key)
        if hit:
            pri.append(hit[0]); onm.append(hit[1]); omg.append(hit[2])
        elif r["未登記"] == "是" or (str(r["_office"]), str(r["_sec"])) not in have_roster:
            pri.append(""); onm.append(""); omg.append("")
        else:
            pri.append("私(推論)"); onm.append(""); omg.append("")
    gdf = gdf.copy()
    gdf["公私別"], gdf["所有權"], gdf["管理者"] = pri, onm, omg
    return gdf


SHP_COLS = ["地號", "使分", "使類", "縣市", "鄉鎮", "段", "登面積", "圖面積",
            "偏差pct", "版次", "狀態", "未登記", "公私別", "所有權", "管理者",
            "原地號", "都計區"]


def drop_internal(gdf):
    """去掉 `_` 開頭的內部欄位（DBF 不需要，且中文欄名長度限制只管留下的那些）。"""
    keep = [c for c in gdf.columns if not str(c).startswith("_")]
    return gdf[keep]


def main():
    import argparse
    import geopandas as gpd  # noqa: PLC0415
    ap = argparse.ArgumentParser(description="農業部地籍空間範圍查詢（除錯用 CLI）")
    ap.add_argument("--bbox", nargs=4, type=float, required=True,
                    metavar=("XMIN", "YMIN", "XMAX", "YMAX"), help="EPSG:3826")
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default="框查")
    ap.add_argument("--version", default=None, help="地籍版次，省略＝最新")
    ap.add_argument("--no-owner", action="store_true")
    a = ap.parse_args()

    url, ver = service_url(a.version)
    print(f"地籍版次 {ver}　{url}")
    x1, y1, x2, y2 = a.bbox
    n = count_envelope(x1, y1, x2, y2, a.version)
    print(f"框內宗地 {n} 筆，取回中…")
    recs = query_envelope(x1, y1, x2, y2, a.version, progress=print)
    gdf = to_gdf(recs, ver)
    if not a.no_owner:
        gdf = attach_ownership(gdf)
    os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, f"{a.name}.shp")
    drop_internal(gdf).to_file(p, encoding="utf-8", engine="pyogrio")
    print(f"→ {p}（{len(gdf)} 筆，總圖面積 {gdf.geometry.area.sum():,.2f} ㎡）")
    assert gpd


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    main()
