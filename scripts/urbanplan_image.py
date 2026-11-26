# -*- coding: utf-8 -*-
r"""縣市都發局「都市計畫使用分區圖」官方彩繪圖（ArcGIS export）→ GeoTIFF。

**與 `urbanplan_fetch.py` 是分工，不是替代**（2026-09-14 使用者定案）：

| | `urbanplan_fetch.py` | 本檔 |
|---|---|---|
| 取到什麼 | **向量**逐宗地＋屬性（分區全名/簡稱/建蔽/容積） | 官方**彩繪圖**（伺服器算好的 PNG） |
| 用途 | **計算**（面積、占比、逐筆分區、圖例全名） | **出圖**（圖面外觀與官網一致） |
| 臺北市服務 | `UrbanPlan2/UrbanPlan2/MapServer/2`（該站 JS 註解＝「使用分區服務」） | `Urban/UrbanPlan/MapServer`（該站圖層清單「都市計畫使用分區圖」） |

為什麼出圖要改吃官方彩繪圖：臺北市圖台的向量 renderer **對住宅區與商業區整類回
`0,0,0`（黑）**（75 筆中 45 筆），照著色會把標的分區畫成全黑不可讀，先前只能在案件層
自行補色（見 `.claude/rules/出圖上色與分區.md`）。官方彩繪圖沒有這個問題——它就是
官網上看到的那張圖（住宅區黃、保護區淡綠…），且**分區簡稱標註已內建在圖上**。

⚠ 兩邊都要留：彩繪圖是點陣、沒有屬性，**面積與占比一律仍以向量計算**；本檔只負責圖面。

實測要點（2026-09-14）
  ‧ 服務是 `singleFusedMapCache`（圖磚），但 **`/export` 仍可用**，可直接指定 bbox 與
    `imageSR=3826`，伺服器端轉投影，省去自己拼圖磚與換算 3857↔3826。
  ‧ `maxImageWidth/Height = 4096`——超過會被伺服器截掉，本檔自動夾在 4096 以內。
  ‧ 大比例尺／小比例尺是**兩個圖層**（臺北市：1＝大比例尺、0＝小比例尺）；出圖尺度
    通常 ≤1/10000，故預設取大比例尺層。
  ‧ 要帶 `Referer`（同 urbanplan_fetch 的既有經驗），免 token。

用法：
  python scripts/urbanplan_image.py --county 臺北市 --joined <基地.shp> --margin 300 \
      --out <案>/底圖/都市計畫官方圖.tif
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

# 縣市登錄表：只登錄「官方彩繪圖服務」；沒登錄的縣市回 None，呼叫端維持向量著色。
IMAGE_COUNTY = {
    "臺北市": {
        "export": "https://www.historygis.udd.gov.taipei/arcgis/rest/services/"
                  "Urban/UrbanPlan/MapServer/export",
        "referer": "https://webgis.udd.gov.taipei/upis_v2",
        # 圖層 1＝都市計畫使用分區(大比例尺)、0＝(小比例尺)、2＝行政區
        "layer_large": "show:1",
        "layer_small": "show:0",
        "large_when_scale_le": 10000,
        "max_px": 4096,
        "title": "都市計畫使用分區圖（臺北市都發局官方圖）",
        # 同一份彩繪圖的**可查詢向量層**（大比例尺）：欄位只有簡稱（保／住3），
        # 用途＝補「計算用向量圖層」的破洞，見 fill_zoning_gaps()
        "query": "https://www.historygis.udd.gov.taipei/arcgis/rest/services/"
                 "Urban/UrbanPlan/MapServer/1/query",
        "query_field": "NAME",
    },
}


def fill_zoning_gaps(county: str, zoning_shp, site_shp, out_shp,
                     min_gap_m2: float = 1.0, timeout: int = 120):
    """用彩繪圖那套的可查詢向量層，補「計算用向量圖層」在基地內的破洞。

    ★2026-09-14 內湖案實見：`UrbanPlan2/UrbanPlan2/MapServer/2`（計算用、屬性最全）
    在北安段一小段 14-1 上**整筆沒有多邊形**——該點逐點查詢回 0 筆，而官方彩繪圖與
    `Urban/UrbanPlan/MapServer/1` 都回「保護區」。若不補，該案分區組成會變成
    「基地 96.76% 有分區、保護區 96.58%」，把一筆本來就是保護區的地寫成無分區。

    作法：算出基地扣掉計算用圖層覆蓋後的**缺口**，只對缺口查詢顯示用圖層，
    把補進來的多邊形標 `_來源='顯示用圖層(補洞)'`，其餘維持原樣——**補了什麼、
    補多少，交付時要看得出來**（不可靜默混入）。

    回傳 (補洞面積㎡, 補洞筆數)；沒有破洞就原樣輸出、回 (0.0, 0)。
    """
    import json
    import geopandas as gpd
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    cfg = IMAGE_COUNTY.get(_norm(county))
    if cfg is None or not cfg.get("query"):
        raise SystemExit(f"{county} 未登錄顯示用可查詢圖層，無法補洞")
    zon = gpd.read_file(zoning_shp)
    zon = zon.to_crs(3826) if zon.crs else zon.set_crs(3826)
    site = gpd.read_file(site_shp)
    site = site.to_crs(3826) if site.crs else site.set_crs(3826)
    su = unary_union(list(site.geometry))
    covered = unary_union([g.buffer(0) for g in zon.geometry])
    gap = su.difference(covered)
    if gap.is_empty or gap.area < min_gap_m2:
        zon.to_file(out_shp, encoding="utf-8")
        print(f"  分區圖層在基地內無破洞（缺口 {getattr(gap, 'area', 0.0):.2f} ㎡）")
        return 0.0, 0
    x0, y0, x1, y1 = gap.bounds
    q = {"geometry": f"{x0 - 5},{y0 - 5},{x1 + 5},{y1 + 5}",
         "geometryType": "esriGeometryEnvelope", "inSR": "3826", "outSR": "3826",
         "spatialRel": "esriSpatialRelIntersects", "outFields": "*",
         "returnGeometry": "true", "f": "json"}
    req = urllib.request.Request(
        cfg["query"] + "?" + urllib.parse.urlencode(q),
        headers={"User-Agent": "Mozilla/5.0", "Referer": cfg["referer"]})
    d = json.loads(urllib.request.urlopen(req, timeout=timeout).read()
                   .decode("utf-8-sig", errors="replace"))
    # 顯示用圖層只有簡稱（保／住3）；用計算用圖層現有的「簡稱→全名」對照翻成全名，
    # 否則統計表會同時出現「保」與「保護區」兩列、看起來像兩種分區。
    short2full = {}
    if "ZONE_USE" in zon.columns and "ZONE" in zon.columns:
        for _, r in zon.iterrows():
            su_, fu_ = str(r["ZONE_USE"]).strip(), str(r["ZONE"]).strip()
            if su_ and fu_:
                short2full.setdefault(su_, fu_)
    rows, geoms = [], []
    for f in d.get("features", []):
        g = unary_union([Polygon(r).buffer(0) for r in f["geometry"]["rings"]])
        part = g.intersection(gap)
        if part.is_empty or part.area < min_gap_m2:
            continue
        short = str(f["attributes"].get(cfg["query_field"], "")).strip()
        rows.append({"ZONE": short2full.get(short, short), "ZONE_USE": short,
                     "_來源": "顯示用圖層(補洞)"})
        geoms.append(part)
    if not geoms:
        zon.to_file(out_shp, encoding="utf-8")
        print(f"  缺口 {gap.area:.1f} ㎡，但顯示用圖層在該處也無資料 → 未補")
        return 0.0, 0
    add = gpd.GeoDataFrame(rows, geometry=geoms, crs=3826)
    zon["_來源"] = "計算用圖層"
    import pandas as pd
    out = gpd.GeoDataFrame(pd.concat([zon, add], ignore_index=True), crs=3826)
    out.to_file(out_shp, encoding="utf-8")
    a = float(sum(g.area for g in geoms))
    print(f"  ✔ 補洞 {len(geoms)} 筆／{a:,.1f} ㎡（基地缺口 {gap.area:,.1f} ㎡）→ {out_shp}")
    for r, g in zip(rows, geoms):
        print(f"      {r['ZONE']}　{g.area:,.1f} ㎡")
    return a, len(geoms)


# ══ 全國備援：內政部國土管理署「全國都市計畫使用分區」服務（2026-11-26 布袋見龍段876 案接入）══
# 縣市沒有登錄彩繪圖服務、且本機 `都市計畫/` 向量 shp 也不在（NAS 未掛、新機器）時的出圖備援。
# 服務 capabilities 只有 Map（query/identify 被關，見 .claude/rules/出圖上色與分區.md），
# 但 Map 含 /export 與 /legend——**只能出圖，拿不到屬性**，分區面積／占比無法計算。
# 實測要點：
#   ‧ 圖層 3（1/2,501～1/50,000）＝分區**填色**；圖層 4（≤1/2,500）＝只畫**框線＋分區名稱**、不填色。
#     基本圖常在 1/750～1/2,000，直接取只會拿到空白框線圖 → 填色另以 ≥1/2,600 的比例尺取圖
#     再最近鄰放大，疊上出圖比例尺的框線與名稱（填色邊緣的鋸齒被框線蓋住）。
#   ‧ 降 dpi 騙伺服器換比例尺**無效**（實測照樣不畫填色），只能真的放大 bbox/縮小 size。
#   ‧ 需 token：NGIS 頁面公開的 layerToken（skill ngis-sensitive 的 `ngis.fetch_token()`），每次重取。
#   ‧ 資料建置 2026-06-30、每雙數月更新——比本機 2019 版 shp 新。服務自述「僅可作為參考，
#     實際圖形應以各縣市政府核發之使用分區證明為準」，交付須註明。
NATIONAL = {
    "base": "https://giss.nlma.gov.tw/tcdmap/rest/services/TCDPDA/"
            "URBAN_LANDUSE_ZONE_3857/MapServer",
    "referer": "https://nsp.nlma.gov.tw/ngis/",
    "fill_layer": 3, "label_layer": 4,
    "fill_min_scale": 2600, "fill_max_scale": 50000, "label_max_scale": 2500,
    "max_px": 4096,
    "title": "都市計畫使用分區圖（內政部國土管理署全國服務）",
}


def _national_token() -> str:
    sys.path.insert(0, str(HERE))
    from config_loader import skill_path  # noqa: PLC0415
    p = str(skill_path("ngis-sensitive", "scripts"))
    if p not in sys.path:
        sys.path.insert(0, p)
    import ngis  # noqa: PLC0415
    return ngis.fetch_token()


def _http_png(url: str, params: dict, referer: str, timeout: int) -> bytes:
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": "Mozilla/5.0", "Referer": referer})
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    if not raw or raw[:4] != b"\x89PNG":
        raise RuntimeError(f"全國都市計畫服務回傳非 PNG（{len(raw)} bytes，可能是錯誤訊息或 token 失效）")
    return raw


def _png_rgba(raw: bytes):
    """PNG bytes → numpy (h, w, 4) uint8（用 GDAL 解，QGIS python 不保證有 PIL）。"""
    import numpy as np
    from osgeo import gdal
    gdal.UseExceptions()
    name = f"/vsimem/_up_{id(raw)}.png"
    gdal.FileFromMemBuffer(name, raw)
    exp = name + ".rgba.tif"
    try:
        ds = gdal.Open(name)
        if ds.RasterCount == 1 and ds.GetRasterBand(1).GetColorTable() is not None:
            ds = gdal.Translate(exp, ds, rgbExpand="rgba")   # 調色盤 PNG（圖例色票常見）展開
        bands = [ds.GetRasterBand(i + 1).ReadAsArray() for i in range(ds.RasterCount)]
        ds = None
    finally:
        gdal.Unlink(name)
        try:
            gdal.Unlink(exp)
        except Exception:  # noqa: BLE001
            pass
    if len(bands) < 3:
        raise RuntimeError(f"全國都市計畫服務回傳 {len(bands)} 波段 PNG（預期 RGBA）")
    arr = np.dstack(bands[:4] if len(bands) >= 4 else bands[:3] + [np.full_like(bands[0], 255)])
    return arr.astype("uint8")


def national_scale(bbox, px_w: int, dpi: int) -> float:
    """以 bbox 寬度、像素寬與 dpi 反推伺服器會用的繪圖比例尺分母（純函式，供單測）。"""
    w_m = float(bbox[2]) - float(bbox[0])
    return w_m / (px_w / float(dpi) * 0.0254)


def _fill_size(bbox, scale: float, dpi: int) -> tuple:
    """填色圖（圖層3）要用的 (比例尺分母, 寬px, 高px)：比例尺夾進圖層3顯示範圍（純函式）。"""
    cfg = NATIONAL
    w_m = float(bbox[2]) - float(bbox[0])
    h_m = float(bbox[3]) - float(bbox[1])
    s_fill = min(max(float(scale), cfg["fill_min_scale"]), cfg["fill_max_scale"])
    fw = max(2, round(w_m / (s_fill * 0.0254 / dpi)))
    fh = max(2, round(fw * h_m / w_m))
    return s_fill, fw, fh


def fetch_national_zone_image(bbox, out_tif, target_res_m: float = 0.17, dpi: int = 300,
                              timeout: int = 120, token: str | None = None,
                              scale: float | None = None) -> str:
    """全國都市計畫使用分區 → GeoTIFF(EPSG:3826, RGBA)。填色（圖層3）＋框線名稱（圖層4）合成。

    ⚠ `scale` 要給**出圖比例尺分母**：解析度改由 scale×0.0254/dpi 決定，伺服器才會照
    該比例尺繪字；沿用固定 0.17 m/px 時，1/750 圖上的分區名稱會被放大近三倍（876 案首版實見）。
    """
    if scale:
        target_res_m = float(scale) * 0.0254 / dpi
    import numpy as np
    from osgeo import gdal
    gdal.UseExceptions()
    cfg = NATIONAL
    xmin, ymin, xmax, ymax = (float(v) for v in bbox)
    w_m, h_m = xmax - xmin, ymax - ymin
    if w_m <= 0 or h_m <= 0:
        raise ValueError(f"bbox 不合法：{bbox}")
    cap = int(cfg["max_px"])
    res = max(target_res_m, w_m / cap, h_m / cap)
    px_w, px_h = max(2, round(w_m / res)), max(2, round(h_m / res))
    tok = token or _national_token()
    url = cfg["base"] + "/export"
    common = {"bbox": f"{xmin},{ymin},{xmax},{ymax}", "bboxSR": "3826", "imageSR": "3826",
              "format": "png32", "transparent": "true", "f": "image", "token": tok}
    scale = national_scale(bbox, px_w, dpi)

    # ① 填色：比例尺夾進圖層3的顯示範圍
    s_fill, fw, fh = _fill_size(bbox, scale, dpi)
    fill = _png_rgba(_http_png(url, {**common, "size": f"{fw},{fh}", "dpi": str(dpi),
                                     "layers": f"show:{cfg['fill_layer']}"},
                               cfg["referer"], timeout))
    if fill.shape[1] != px_w or fill.shape[0] != px_h:   # 最近鄰放大（不混色，色票才對得上圖例）
        ri = (np.arange(px_h) * fill.shape[0] // px_h).clip(0, fill.shape[0] - 1)
        ci = (np.arange(px_w) * fill.shape[1] // px_w).clip(0, fill.shape[1] - 1)
        fill = fill[ri][:, ci]
    out = fill.astype("float32")

    # ② 框線＋分區名稱：只有出圖比例尺 ≤1/2,500 時圖層4才畫
    if scale <= cfg["label_max_scale"]:
        lab = _png_rgba(_http_png(url, {**common, "size": f"{px_w},{px_h}", "dpi": str(dpi),
                                        "layers": f"show:{cfg['label_layer']}"},
                                  cfg["referer"], timeout)).astype("float32")
        a = lab[..., 3:4] / 255.0
        rgb = lab[..., :3] * a + out[..., :3] * (1 - a)
        alpha = np.maximum(out[..., 3:4], lab[..., 3:4])
        out = np.concatenate([rgb, alpha], axis=2)
    out = out.round().clip(0, 255).astype("uint8")

    out_tif = str(out_tif)
    Path(out_tif).parent.mkdir(parents=True, exist_ok=True)
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(out_tif, px_w, px_h, 4, gdal.GDT_Byte, options=["COMPRESS=DEFLATE"])
    ds.SetGeoTransform((xmin, w_m / px_w, 0, ymax, 0, -h_m / px_h))
    from osgeo import osr
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3826)
    ds.SetProjection(srs.ExportToWkt())
    for i in range(4):
        ds.GetRasterBand(i + 1).WriteArray(out[..., i])
    ds.GetRasterBand(4).SetColorInterpretation(gdal.GCI_AlphaBand)
    ds = None
    print(f"  全國都市計畫分區圖 {px_w}x{px_h} px（{w_m / px_w:.2f} m/px；填色取 1/{s_fill:,.0f}、"
          f"出圖 1/{scale:,.0f}）→ {Path(out_tif).name}")
    return out_tif


def national_legend(timeout: int = 60, token: str | None = None) -> list:
    """服務官方圖例（圖層3）→ [(分區名, (r,g,b)), ...]；色票取 swatch 中心像元。"""
    import base64
    import json
    cfg = NATIONAL
    tok = token or _national_token()
    req = urllib.request.Request(
        cfg["base"] + "/legend?" + urllib.parse.urlencode({"f": "json", "token": tok}),
        headers={"User-Agent": "Mozilla/5.0", "Referer": cfg["referer"]})
    d = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))
    out = []
    for lyr in d.get("layers", []):
        if lyr.get("layerId") != cfg["fill_layer"]:
            continue
        for item in lyr.get("legend", []):
            label = str(item.get("label", "")).strip()
            if not label or not item.get("imageData"):
                continue
            arr = _png_rgba(base64.b64decode(item["imageData"]))
            h, w = arr.shape[:2]
            r, g, b, a = (int(v) for v in arr[h // 2, w // 2])
            if a > 0:
                out.append((label, (r, g, b)))
    return out


def group_label(labels: list, keep: int = 2) -> str:
    """同色分區群組 → 圖例短名（純函式，供單測）：前 keep 個名稱以「、」連接，其餘併成「等」。"""
    names = list(dict.fromkeys(str(x).strip() for x in labels if str(x).strip()))
    if len(names) <= keep:
        return "、".join(names)
    return "、".join(names[:keep]) + "等"


def legend_color_groups(tif: str, legend: list, min_frac: float = 0.002) -> list:
    """官方圖例中，**圖面實際出現**（像元占比 ≥ min_frac）的顏色群組
    → [(同色分區名清單, rgb, 占比), ...]，依占比由大到小。

    ⚠ 該服務的官方色表是依「分區類別」配色：115 類只有 14 種顏色（工業區／鹽田區／
    港埠專用區…同為棕色；保護區／農業區／保存區…同為黃綠），**光看顏色分不出是哪一類**，
    要再用 `national_present_legend` 逐類篩選確認。
    白色（其他使用分區／道路用地等，與空白難以區分）不列入。
    """
    import numpy as np
    from osgeo import gdal
    gdal.UseExceptions()
    ds = gdal.Open(str(tif))
    arr = np.dstack([ds.GetRasterBand(i + 1).ReadAsArray() for i in range(ds.RasterCount)])
    opaque = arr[..., 3] > 200 if arr.shape[2] >= 4 else np.ones(arr.shape[:2], bool)
    total = int(opaque.sum()) or 1
    by_rgb: dict = {}
    for label, rgb in legend:
        by_rgb.setdefault(tuple(rgb), []).append(label)
    rows = []
    for rgb, labels in by_rgb.items():
        if rgb == (255, 255, 255):
            continue
        hit = opaque & (arr[..., 0] == rgb[0]) & (arr[..., 1] == rgb[1]) & (arr[..., 2] == rgb[2])
        frac = int(hit.sum()) / total
        if frac >= min_frac:
            rows.append((list(dict.fromkeys(labels)), rgb, frac))
    rows.sort(key=lambda r: -r[2])
    return rows


def category_filter(label: str) -> str:
    """分區類別 → export layerDefs 的定義式（單引號跳脫；純函式，供單測）。"""
    return "分區類別='" + str(label).replace("'", "''") + "'"


def national_present_legend(bbox, tif: str, legend: list, scale: float, dpi: int = 300,
                            token: str | None = None, min_frac: float = 0.002,
                            timeout: int = 60) -> list:
    """圖例只列**圖框內實際出現**的分區類別 → [(分區類別, rgb), ...]（使用者 2026-11-26 定案：
    「有涉及到的就好，本案僅涉及工業區，鹽田區就不要放」）。

    作法：先由顏色找出出現的色群，再對色群內每個類別用 export 的 `layerDefs`
    （`分區類別='X'`）只畫該類，看圖框內有沒有像元——服務 capabilities 只有 Map，
    但 export 的定義式篩選可用（876 案實測：工業區 97.8%、鹽田區／農業區／保護區 0）。
    某色群逐類篩選全落空（篩選失效）時，退回 `group_label` 的群組名，不讓該色從圖例消失。
    """
    import json
    import numpy as np
    cfg = NATIONAL
    tok = token or _national_token()
    xmin, ymin, xmax, ymax = (float(v) for v in bbox)
    _s, fw, fh = _fill_size(bbox, scale, dpi)
    url = cfg["base"] + "/export"
    common = {"bbox": f"{xmin},{ymin},{xmax},{ymax}", "bboxSR": "3826", "imageSR": "3826",
              "size": f"{fw},{fh}", "dpi": str(dpi), "format": "png32", "transparent": "true",
              "f": "image", "token": tok, "layers": f"show:{cfg['fill_layer']}"}
    out = []
    for labels, rgb, gfrac in legend_color_groups(tif, legend, min_frac):
        hits = []
        for lab in labels:
            defs = json.dumps({str(cfg["fill_layer"]): category_filter(lab)}, ensure_ascii=False)
            arr = _png_rgba(_http_png(url, {**common, "layerDefs": defs}, cfg["referer"], timeout))
            frac = float((arr[..., 3] > 200).mean())
            if frac >= min_frac:
                hits.append((frac, lab, rgb))
        out.extend(hits or [(gfrac, group_label(labels), rgb)])
    out.sort(key=lambda r: -r[0])
    return [(lab, rgb) for _f, lab, rgb in out]


def opaque_at(tif: str, pts: list) -> list:
    """各點 (x, y)[EPSG:3826] 在全國分區圖上是否有分區色（非透明）→ [bool, ...]。
    透明＝該處不在任何都市計畫區內；供「全區屬都市計畫區→略過使用地編定」判定。"""
    from osgeo import gdal
    gdal.UseExceptions()
    ds = gdal.Open(str(tif))
    inv = gdal.InvGeoTransform(ds.GetGeoTransform())
    a_band = ds.GetRasterBand(4) if ds.RasterCount >= 4 else None
    res = []
    for x, y in pts:
        col, row = (int(v) for v in gdal.ApplyGeoTransform(inv, float(x), float(y)))
        if a_band is None or not (0 <= col < ds.RasterXSize and 0 <= row < ds.RasterYSize):
            res.append(False)
            continue
        res.append(int(a_band.ReadAsArray(col, row, 1, 1)[0][0]) > 200)
    return res


def legend_in_image(tif: str, legend: list, min_frac: float = 0.002) -> list:
    """（不連網的退路）圖面出現的色群 → [(群組短名, rgb), ...]；正式出圖用 `national_present_legend`。"""
    return [(group_label(labels), rgb)
            for labels, rgb, _f in legend_color_groups(tif, legend, min_frac)]


def county_supported(county: str) -> bool:
    return _norm(county) in IMAGE_COUNTY


def _norm(county: str) -> str:
    c = (county or "").strip()
    return c.replace("台", "臺") if c else c


def fetch_zone_image(county: str, bbox, out_tif, scale: float | None = None,
                     target_res_m: float = 0.17, dpi: int = 300,
                     timeout: int = 120) -> str | None:
    """把該縣市官方分區彩繪圖切一塊存成 GeoTIFF(EPSG:3826)；不支援的縣市回 None。

    `bbox`＝(xmin, ymin, xmax, ymax) EPSG:3826；`scale` 給了就用來挑大/小比例尺圖層。
    `target_res_m`＝目標地面解析度（公尺/像素），預設 0.17 m≈1/2000 圖在 300dpi 的細緻度。

    ⚠ **`dpi` 一定要跟出圖 DPI 一致（預設 300）**：ArcGIS 由 `bbox`＋`size`＋`dpi` 反推
    繪圖比例尺，再據以決定**符號與標註字級**。不帶 dpi（伺服器預設 96）等於叫它以
    1/642 的螢幕尺度繪製，字級是為那個尺度設計的——貼進 1/2000 的 A4 成果圖後
    分區簡稱小到看不清（2026-09-14 首版實見）。帶 dpi=300 時伺服器才會照 1/2000 出圖。
    """
    cfg = IMAGE_COUNTY.get(_norm(county))
    if cfg is None:
        return None
    xmin, ymin, xmax, ymax = (float(v) for v in bbox)
    w_m, h_m = xmax - xmin, ymax - ymin
    if w_m <= 0 or h_m <= 0:
        raise ValueError(f"bbox 不合法：{bbox}")
    cap = int(cfg.get("max_px", 4096))
    px_w = max(2, min(cap, round(w_m / target_res_m)))
    px_h = max(2, min(cap, round(h_m / target_res_m)))
    layers = cfg["layer_large"]
    if scale is not None and scale > cfg.get("large_when_scale_le", 10000):
        layers = cfg["layer_small"]
    q = {
        "bbox": f"{xmin},{ymin},{xmax},{ymax}",
        "bboxSR": "3826", "imageSR": "3826",
        "size": f"{px_w},{px_h}", "dpi": str(int(dpi)),
        "format": "png32", "transparent": "true",
        "layers": layers, "f": "image",
    }
    req = urllib.request.Request(
        cfg["export"] + "?" + urllib.parse.urlencode(q),
        headers={"User-Agent": "Mozilla/5.0", "Referer": cfg["referer"]})
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    if not raw or raw[:4] != b"\x89PNG":
        raise RuntimeError(f"{county} 官方分區圖回傳非 PNG（{len(raw)} bytes，可能是錯誤訊息）")

    from osgeo import gdal  # 僅此處需要；呼叫端多半已在 QGIS python
    gdal.UseExceptions()
    out_tif = str(out_tif)
    Path(out_tif).parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        tf.write(raw)
        png = tf.name
    try:
        # PNG 本身無地理資訊：用 outputBounds 直接把 bbox 釘上去（伺服器已照此 bbox 出圖）
        gdal.Translate(out_tif, png, outputSRS="EPSG:3826",
                       outputBounds=[xmin, ymax, xmax, ymin])
    finally:
        Path(png).unlink(missing_ok=True)
    print(f"  官方分區彩繪圖 {px_w}x{px_h} px（{w_m / px_w:.2f} m/px）→ {Path(out_tif).name}")
    return out_tif


def sample_zone_colors(tif: str, geoms_by_zone: dict, samples: int = 12) -> dict:
    """從官方彩繪圖取樣每個分區名的實際顏色 → {分區名: (r, g, b)}。

    為什麼要取樣而不是讀向量的 `Color1`：臺北市圖台的 `Color1` 對住宅區／商業區整類
    回 `0,0,0`（黑），圖例會把住一/住二/住三全部併成一格黑色——**圖例與圖面對不上**。
    改成直接量「圖上這塊實際被畫成什麼顏色」，圖例必然與官方彩繪圖一致，
    也不必再維護案件層的補色 shp（2026-09-14）。

    `geoms_by_zone`＝{分區名: [shapely/QgsGeometry 皆可的取樣點 (x, y) 清單]}。
    取每個分區多點取樣後的眾數色；全白（255,255,255，通常是計畫道路留白）不列入。
    """
    from collections import Counter

    from osgeo import gdal
    gdal.UseExceptions()
    ds = gdal.Open(str(tif))
    if ds is None:
        return {}
    gt = ds.GetGeoTransform()
    inv = gdal.InvGeoTransform(gt)
    band_n = min(3, ds.RasterCount)
    out: dict = {}
    for zone, pts in geoms_by_zone.items():
        cnt: Counter = Counter()
        for x, y in pts[:samples]:
            col, row = gdal.ApplyGeoTransform(inv, float(x), float(y))
            col, row = int(col), int(row)
            if not (0 <= col < ds.RasterXSize and 0 <= row < ds.RasterYSize):
                continue
            rgb = tuple(int(ds.GetRasterBand(b + 1).ReadAsArray(col, row, 1, 1)[0][0])
                        for b in range(band_n))
            if len(rgb) == 3 and rgb != (255, 255, 255):
                cnt[rgb] += 1
        if cnt:
            out[zone] = cnt.most_common(1)[0][0]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", required=True, help=f"縣市（已登錄：{list(IMAGE_COUNTY)}）")
    ap.add_argument("--joined", default=None, help="基地 shp（取外擴 bbox）")
    ap.add_argument("--bbox", nargs=4, type=float, default=None,
                    metavar=("XMIN", "YMIN", "XMAX", "YMAX"), help="EPSG:3826 範圍")
    ap.add_argument("--margin", type=float, default=300.0, help="基地 bbox 外擴公尺")
    ap.add_argument("--out", required=True, help="輸出 GeoTIFF")
    a = ap.parse_args()
    if not county_supported(a.county):
        sys.exit(f"縣市未登錄官方分區彩繪圖服務：{a.county}（已登錄：{list(IMAGE_COUNTY)}）")
    if a.bbox:
        bbox = tuple(a.bbox)
    elif a.joined:
        import geopandas as gpd
        g = gpd.read_file(a.joined)
        g = g.to_crs(3826) if g.crs else g.set_crs(3826)
        x0, y0, x1, y1 = g.total_bounds
        bbox = (x0 - a.margin, y0 - a.margin, x1 + a.margin, y1 + a.margin)
    else:
        sys.exit("要給 --joined 或 --bbox")
    fetch_zone_image(a.county, bbox, a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
