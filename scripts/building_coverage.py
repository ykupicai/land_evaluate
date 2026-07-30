# -*- coding: utf-8 -*-
"""航照判讀現況建蔽率，並回推「留設隔離綠帶後應拆除面積」

★ 建立緣由（2026-07-30 安定六塊寮段特登工廠案）：既存工廠合法化案必問三件事——
  現況建蔽率多少、綠帶範圍內壓到多少建築物（辦法§8 一律須拆）、拆完是否仍超過法定建蔽率。
  這三個數字都要能指到實算來源，不能目測估。

判讀方法（NLSC PHOTO2 正射影像，約 0.15 m/px）：
  屋頂＝亮度高且非植生；植生＝綠色優勢（G 明顯大於 R）；陰影／樹冠＝亮度低。
  形態學去斑（開運算）＋補洞（閉運算）後向量化。
  ⚠ **這是判讀估算，不是建物測量成果**：屋簷外緣與牆線有落差、樹冠會遮蔽屋頂邊緣、
  雨遮／棚架是否計入建築面積屬建築管理認定。交付文件一律載明「航照判讀概估，
  實際建築面積應以建物測量成果或建築師簽證圖說為準」。

建蔽率分母的兩種讀法（都輸出；★讀法Ａ為主要讀法）：
  Ａ 以**基地總面積**為分母 ← **主要讀法**。特定工廠變更編定辦法§2Ⅲ①(一)未達2公頃案
     係「辦理使用地變更編定為特定目的事業用地」，**整個申請範圍（含留設之隔離綠帶）
     均編為特定目的事業用地**（§6②毗連土地納入者亦編為同類別；本辦法未要求綠帶另編
     國土保安用地），故§6⑤建蔽率70%之分母即申請範圍全部面積。
  Ｂ 以**扣除綠帶後之可配置範圍**為分母 ← 保守敏感度。僅當綠帶依個案要求另編為
     國土保安用地等非可建用地時才適用（如管制規則§31-2 之未登記工廠特定地區路徑）。
  ⚠ 留設 30% 綠帶時可配置範圍恰為 70%，與建蔽率上限 70% 數值重合——**這是巧合而非
     同一件事**，勿因此把兩個限制疊加成 49%（讀法Ｂ 即為該疊加結果）。

用法（系統 Python）：
  python scripts/building_coverage.py --joined <面積join.shp> \
      [--ortho <底圖/001_PHOTO2.tif>] [--greenbelt <隔離綠帶/隔離綠帶或設施.shp>] \
      [--max-coverage 0.70] [--bright 110] [--out <案件資料夾>]
"""
from __future__ import annotations

import argparse
import io
import json
import os

import geopandas as gpd
import numpy as np
import rasterio
from PIL import Image
from rasterio.features import shapes
from rasterio.mask import mask
from scipy import ndimage
from shapely import make_valid, union_all
from shapely.geometry import shape


TILE_URL = "https://wmts.nlsc.gov.tw/wmts/{layer}/default/GoogleMapsCompatible/{z}/{r}/{c}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"}
_ORIG = 20037508.342789244


def fetch_wmts(bounds_3826, layer="BUILDX", z=20, margin=25.0):
    """抓 NLSC WMTS 圖磚並拼成 (RGBA陣列, rasterio.Affine, EPSG:3857)。

    ★ BUILDX＝「臺灣通用電子地圖加值分棟建物框」：**橘色(255,128,0)點狀虛線外框、
    透明背景**，不是填色面。故取得後須「膨脹接合點列 → 填洞 → 侵蝕還原」才得面。
    """
    import math
    import urllib.request
    from PIL import Image
    from pyproj import Transformer
    from rasterio.transform import Affine

    tf = Transformer.from_crs(3826, 4326, always_xy=True)
    xmin, ymin, xmax, ymax = bounds_3826
    lon0, lat0 = tf.transform(xmin - margin, ymin - margin)
    lon1, lat1 = tf.transform(xmax + margin, ymax + margin)

    def d2t(lon, lat):
        n = 2 ** z
        return ((lon + 180) / 360 * n,
                (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat)))
                 / math.pi) / 2 * n)

    x0, y0 = d2t(lon0, lat1)
    x1, y1 = d2t(lon1, lat0)
    c0, c1, r0, r1 = int(x0), int(x1), int(y0), int(y1)
    n_t = (c1 - c0 + 1) * (r1 - r0 + 1)
    if n_t > 400:
        raise SystemExit(f"圖磚數 {n_t} 過多，請縮小範圍或降低 zoom")
    W, H = (c1 - c0 + 1) * 256, (r1 - r0 + 1) * 256
    mos = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ok = 0
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            url = TILE_URL.format(layer=layer, z=z, r=r, c=c)
            try:
                d = urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=45).read()
                mos.paste(Image.open(io.BytesIO(d)).convert("RGBA"),
                          ((c - c0) * 256, (r - r0) * 256))
                ok += 1
            except Exception:  # noqa: BLE001,PERF203
                pass
    res = 2 * _ORIG / (256 * 2 ** z)
    tr = Affine(res, 0, -_ORIG + c0 * 256 * res, 0, -res, _ORIG - r0 * 256 * res)
    print(f"  {layer} z{z}：圖磚 {ok}/{n_t}、{W}x{H} px、解析度 {res:.3f} m/px")
    return np.array(mos), tr, "EPSG:3857"


def frames_to_polys(rgba, bridge=3, min_m2=4.0, px=None):
    """點狀虛線外框 → 面遮罩：膨脹接合 → 填洞 → 侵蝕還原。"""
    line = rgba[..., 3] > 0
    st = np.ones((3, 3), bool)
    grown = ndimage.binary_dilation(line, st, iterations=bridge)
    filled = ndimage.binary_fill_holes(grown)
    solid = ndimage.binary_erosion(filled, st, iterations=bridge)
    if px:
        lab, n = ndimage.label(solid)
        if n:
            sizes = ndimage.sum(solid, lab, range(1, n + 1))
            drop = [i + 1 for i, s in enumerate(sizes) if s * px < min_m2]
            if drop:
                solid = solid & ~np.isin(lab, drop)
    return solid


def classify_roof(arr, bright=110, veg_margin=8):
    """arr: (H,W,3) uint8 → 屋頂布林遮罩。"""
    r, g, b = arr[..., 0].astype(int), arr[..., 1].astype(int), arr[..., 2].astype(int)
    v = (r + g + b) / 3.0
    veg = (g - r) > veg_margin              # 綠色優勢＝植生
    nodata = (r == 0) & (g == 0) & (b == 0)
    roof = (v >= bright) & (~veg) & (~nodata)
    return roof, nodata


def clean(mask_bool, px_area, min_m2=4.0):
    """去斑（開）＋補洞（閉）＋剔除小於 min_m2 的碎塊。"""
    st = np.ones((3, 3), bool)
    m = ndimage.binary_opening(mask_bool, st, iterations=2)
    m = ndimage.binary_closing(m, st, iterations=3)
    m = ndimage.binary_fill_holes(m)
    lab, n = ndimage.label(m)
    if n:
        sizes = ndimage.sum(m, lab, range(1, n + 1))
        drop = {i + 1 for i, s in enumerate(sizes) if s * px_area < min_m2}
        if drop:
            m = m & ~np.isin(lab, list(drop))
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True)
    ap.add_argument("--ortho", default=None)
    ap.add_argument("--greenbelt", default=None)
    ap.add_argument("--max-coverage", type=float, default=0.70, help="法定建蔽率上限")
    ap.add_argument("--bright", type=float, default=110, help="屋頂亮度門檻(0-255)")
    ap.add_argument("--source", choices=["ortho", "buildx"], default="ortho",
                    help="建物來源：ortho＝航照判讀（預設）；buildx＝國土測繪中心分棟建物框")
    ap.add_argument("--zoom", type=int, default=20, help="buildx 圖磚 zoom，預設 20（約0.13m/px）")
    ap.add_argument("--bridge", type=int, default=3, help="buildx 點狀虛線接合膨脹 px，預設 3")
    ap.add_argument("--tag", default=None, help="輸出檔名後綴（做方案比較用，如 方案二）")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    case = a.out or os.path.dirname(os.path.dirname(os.path.abspath(a.joined)))
    ortho = a.ortho or os.path.join(case, "底圖", "001_PHOTO2.tif")
    gb = a.greenbelt or os.path.join(case, "隔離綠帶", "隔離綠帶或設施.shp")

    site = gpd.read_file(a.joined, engine="pyogrio")
    if site.crs and site.crs.to_epsg() != 3826:
        site = site.to_crs(3826)
    su3826 = make_valid(union_all([make_valid(x) for x in site.geometry]))
    A = su3826.area

    if a.source == "buildx":
        rgba, tr, crs_r = fetch_wmts(su3826.bounds, layer="BUILDX", z=a.zoom)
        px = abs(tr.a) * abs(tr.e)
        roof = frames_to_polys(rgba, bridge=a.bridge, px=px)
        arr = rgba[..., :3].copy()
        arr[rgba[..., 3] == 0] = 255                     # 透明底改白，供對照圖用
        src_note = f"BUILDX 分棟建物框（z{a.zoom}，點列接合 {a.bridge} px）"
    else:
        if not os.path.exists(ortho):
            raise SystemExit(f"找不到正射影像 {ortho}（先跑 make_maps 產底圖）")
        with rasterio.open(ortho) as src:
            su_r = gpd.GeoSeries([su3826], crs=3826).to_crs(src.crs).iloc[0]
            img, tr = mask(src, [su_r], crop=True, filled=True, nodata=0)
            px = abs(tr.a) * abs(tr.e)
            arr = np.transpose(img, (1, 2, 0))
            roof, _ = classify_roof(arr, a.bright)
            roof = clean(roof, px)
            crs_r = src.crs
        src_note = f"{os.path.basename(ortho)} 航照判讀（亮度門檻 {a.bright:.0f}）"
    polys = [shape(s) for s, v in shapes(roof.astype(np.uint8), mask=roof,
                                         transform=tr) if v == 1]

    bld = gpd.GeoDataFrame(geometry=polys, crs=crs_r).to_crs(3826)
    bld["geometry"] = bld.geometry.apply(make_valid)
    bld = bld[bld.geometry.area > 1]
    bld["geometry"] = bld.geometry.intersection(su3826)     # 不外溢基地
    bld = bld[~bld.geometry.is_empty]
    bu = make_valid(union_all(list(bld.geometry)))
    B = bu.area

    log = [f"現況建蔽率（來源：{src_note}，解析度 {abs(tr.a):.3f} m/px）",
           ("★ BUILDX 為官方分棟建物框加值產品，仍屬圖資非建物測量成果；"
            "框線為點狀虛線經接合填面，邊界有 ±像元級誤差"
            if a.source == "buildx" else
            "★ 判讀概估，非建物測量成果；屋簷/樹冠遮蔽與雨遮棚架認定會影響結果"),
           "",
           f"基地面積　　　　{A:,.2f} m²",
           f"判讀建築面積　　{B:,.2f} m²（{len(bld)} 個區塊）",
           f"現況建蔽率　　　{B/A*100:.2f}%　（法定上限 {a.max_coverage*100:.0f}%）"
           + ("　✗ 已超過" if B / A > a.max_coverage else "　✓ 未超過")]

    res = {"基地_m2": round(A, 2), "判讀建築面積_m2": round(B, 2),
           "現況建蔽率_pct": round(B / A * 100, 2),
           "法定建蔽率上限_pct": a.max_coverage * 100,
           "建物來源": a.source, "來源說明": src_note,
           "解析度_m": round(abs(tr.a), 3),
           "口徑": ("官方分棟建物框加值產品，非建物測量成果"
                  if a.source == "buildx" else "航照判讀概估，非建物測量成果")}

    if os.path.exists(gb):
        green = make_valid(union_all(
            [make_valid(x) for x in gpd.read_file(gb, engine="pyogrio").to_crs(3826).geometry]))
        G = green.area
        inner = make_valid(su3826.difference(green))
        in_green = bu.intersection(green).area          # 綠帶內建物＝必拆（辦法§8）
        remain = B - in_green                          # 拆完綠帶內建物後剩餘
        capA = A * a.max_coverage                      # 讀法Ａ：分母＝基地
        capB = inner.area * a.max_coverage             # 讀法Ｂ：分母＝可配置範圍
        extraA = max(0.0, remain - capA)
        extraB = max(0.0, remain - capB)
        log += ["",
                f"隔離綠帶或設施　{G:,.2f} m²（占基地 {G/A*100:.2f}%）",
                f"可配置範圍　　　{inner.area:,.2f} m²",
                "",
                "── 應拆除面積 ──",
                f"① 綠帶範圍內既有建築物（辦法§8 應拆）　　{in_green:,.2f} m²",
                f"   拆除①後剩餘建築面積　　　　　　　　　{remain:,.2f} m²",
                f"② ★主要讀法Ａ（分母＝基地 {A:,.0f} m²）上限 {capA:,.2f} m²"
                f" → 尚須再拆 {extraA:,.2f} m²",
                f"   保守讀法Ｂ（分母＝可配置範圍 {inner.area:,.0f} m²）上限 {capB:,.2f} m²"
                f" → 尚須再拆 {extraB:,.2f} m²（僅綠帶另編非可建用地時適用）",
                f"③ 合計應拆除　★讀法Ａ {in_green+extraA:,.2f} m²"
                f"／讀法Ｂ {in_green+extraB:,.2f} m²",
                f"   拆後建蔽率　★讀法Ａ {min(remain,capA)/A*100:.2f}%"
                f"／讀法Ｂ {min(remain,capB)/A*100:.2f}%（均以基地為分母表示）",
                "",
                "※ 辦法§8：綠帶範圍內應拆除之既有建築物，得載明拆除範圍並出具切結書申請"
                "「緩拆」，最遲於申請廠房使用執照前拆除並完成綠帶設置，經工業主管機關會同"
                "農業主管機關勘驗後，才核發農業用地變更使用同意文件。"]
        res.update({"隔離綠帶_m2": round(G, 2), "可配置範圍_m2": round(inner.area, 2),
                    "綠帶內建築面積_m2": round(in_green, 2),
                    "拆綠帶後剩餘建築_m2": round(remain, 2),
                    "讀法A_分母基地": {"上限_m2": round(capA, 2),
                                 "尚須再拆_m2": round(extraA, 2),
                                 "合計應拆_m2": round(in_green + extraA, 2)},
                    "讀法B_分母可配置範圍": {"上限_m2": round(capB, 2),
                                    "尚須再拆_m2": round(extraB, 2),
                                    "合計應拆_m2": round(in_green + extraB, 2)}})

    d = os.path.join(case, "建蔽率判讀")
    os.makedirs(d, exist_ok=True)
    tag = f"_{a.tag}" if a.tag else ""
    gpd.GeoDataFrame({"name": [f"現況建築物({a.source})"], "面積m2": [round(B, 2)]},
                     geometry=[bu], crs=3826).to_file(
        os.path.join(d, f"現況建築物_{a.source}{tag}.shp"), encoding="utf-8")
    # 判讀對照圖：原影像 vs 遮罩
    ov = arr.copy()
    ov[roof] = (0.45 * ov[roof] + 0.55 * np.array([255, 40, 40])).astype(np.uint8)
    Image.fromarray(np.hstack([arr, ov])).save(
        os.path.join(d, f"判讀對照_{a.source}{tag}.png"))

    txt = "\n".join(log)
    print(txt)
    io.open(os.path.join(case, f"建蔽率判讀{tag}.txt"), "w", encoding="utf-8").write(txt)
    io.open(os.path.join(case, f"建蔽率判讀{tag}.json"), "w", encoding="utf-8").write(
        json.dumps(res, ensure_ascii=False, indent=1))
    print(f"\n→ {d}\\現況建築物_{a.source}{tag}.shp、判讀對照_{a.source}{tag}.png")
    print(f"→ {case}\\建蔽率判讀{tag}.txt / .json")


if __name__ == "__main__":
    main()
