# -*- coding: utf-8 -*-
"""隔離綠帶面積計算式圖說：以三角形分割「非隔離綠帶區域」反算綠帶面積

★ 建立緣由（2026-08-01 使用者定案）：實務上送審必須用**計算式**說明隔離綠帶面積，
  而非只給一個 GIS 實算數字。慣用公式為

      基地面積 － 非隔離綠帶或設施之面積 ＝ 隔離綠帶或設施之面積

  其中「非隔離綠帶之面積」以**劃三角形**逐一計算（編號、底、高、面積，末列合計），
  這樣審查者可以用底×高÷2 逐項複核。故本腳本：
    1. 以受約束 Delaunay 三角化（shapely.constrained_delaunay_triangles，
       精確貼合凹形邊界）分割「可配置範圍」＝非隔離綠帶區域
    2. 每個三角形取**最長邊為底**、高＝2×面積÷底（標準測量表達）
    3. 出面積計算表（CSV/JSON）＋三角形 shp（帶編號）
    4. 出**計算式圖說 PNG**：編號三角形＋綠帶＋基地框，並在同一張圖上列出公式、
       代入數值、算出比例並判斷是否達法定門檻

  用線圖而非套航照的地圖，是因為這是「計算式圖說」——審查者要看的是幾何分割與
  數字能不能對得上，底圖會干擾判讀。基地現況請看正射套疊圖與國土利用調查圖。

編號規則：北→南、西→東（同 build_sites_sitemap 的確定性編號，重跑不變）。

用法（系統 Python，不需 QGIS）：
  python scripts/greenbelt_triangles.py --joined <面積join.shp> --out outputs/<案>
  選項：--greenbelt/--usable 指定幾何（預設取 <out>/隔離綠帶/）、--ratio 0.30、--tag
"""
import argparse
import io
import json
import os
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                  # noqa: E402
import shapely                                                   # noqa: E402
from matplotlib.patches import Polygon as MplPolygon             # noqa: E402
from shapely.geometry import Polygon                             # noqa: E402
from shapely.ops import unary_union                              # noqa: E402

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
CRS = 3826
plt.rcParams["font.family"] = "Microsoft JhengHei"
plt.rcParams["axes.unicode_minus"] = False


def _read(path):
    g = gpd.read_file(path)
    if g.crs is None:
        g = g.set_crs(CRS)
    return unary_union(g.to_crs(CRS).geometry.tolist())


def triangulate(geom):
    """受約束三角化；回傳依 北→南、西→東 排序之三角形清單。"""
    coll = shapely.constrained_delaunay_triangles(geom)
    # ⚠ 門檻不可設到 1e-9：幾何差集會在邊界留下針狀頂點，切出「高 0.00、面積 0.00」
    #   的退化三角形，列進送審計算表會被質疑（2026-08-01 實踩）。
    #   0.01 ㎡ 以下對合計的影響遠小於閘門容差，濾掉不影響正確性。
    tris = [t for t in shapely.get_parts(coll).tolist() if t.area >= 0.01]
    # 確定性排序：質心 y 由大到小（北→南）、同緯度時 x 由小到大（西→東）
    tris.sort(key=lambda t: (-round(t.centroid.y, 2), round(t.centroid.x, 2)))
    return tris


def base_height(tri: Polygon):
    """最長邊為底，高＝2A/底。
    回傳 (底, 高, 面積, (底起點, 底終點), 對頂點, 垂足)——後三者供圖上標註尺寸線。"""
    pts = list(tri.exterior.coords)[:3]
    sides = []
    for i in range(3):
        p, q = pts[i], pts[(i + 1) % 3]
        apex = pts[(i + 2) % 3]
        sides.append((((q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2) ** 0.5, p, q, apex))
    b, p, q, apex = max(sides, key=lambda s: s[0])
    a = tri.area
    h = 2.0 * a / b if b else 0.0
    # 垂足：apex 投影到底線 pq 上
    if b:
        t = (((apex[0] - p[0]) * (q[0] - p[0]) + (apex[1] - p[1]) * (q[1] - p[1])) / (b * b))
        foot = (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]))
    else:
        foot = p
    return b, h, a, (p, q), apex, foot


def _unit(p, q):
    dx, dy = q[0] - p[0], q[1] - p[1]
    L = (dx * dx + dy * dy) ** 0.5 or 1.0
    return dx / L, dy / L


def draw_dims(axis, tri, dm, no, fs, off, show_no=True, outside=False,
              lines_only=False):
    """標出底與高的尺寸線與數值。

    底＝最長邊（實線）；高＝對頂點到底線之垂線（虛線）。
    off＝標註偏移量（資料單位），依該圖的顯示比例給。
    outside=True：數字標在三角形**外側**並拉引線——放大明細框空間充裕，
      壓在細長三角形上會蓋住圖形本身（2026-08-01 使用者指正）。"""
    (p, q), apex, foot = dm["底線"], dm["頂點"], dm["垂足"]
    ux, uy = _unit(p, q)
    nx, ny = -uy, ux                       # 底線法向
    # 法向朝三角形內部（指向對頂點）
    if (apex[0] - p[0]) * nx + (apex[1] - p[1]) * ny < 0:
        nx, ny = -nx, -ny
    # 底線畫在三角形**內側**（往對頂點偏移一點），才不會蓋住三角形本身的藍色邊；
    # 相鄰三角形共用邊，若直接描邊會把整張圖染成紅色網格（2026-08-01 實踩）。
    ib = off * 0.55
    axis.plot([p[0] + nx * ib, q[0] + nx * ib], [p[1] + ny * ib, q[1] + ny * ib],
              color="#C00000", lw=1.1, solid_capstyle="butt", zorder=6)
    axis.plot([apex[0], foot[0] + nx * ib], [apex[1], foot[1] + ny * ib],
              color="#C00000", lw=0.9, ls=(0, (3.5, 2)), zorder=6)
    # 標註只寫數字（單位公尺）；哪個是底、哪個是高靠線型區分（實線／虛線），
    # 寫「底 x.xx」會讓字串長一倍，小三角形全部塞不下。
    bx = dict(boxstyle="square,pad=0.06", fc="white", ec="none", alpha=0.8)
    cen = tri.centroid

    def inside(pt, frac=0.55):
        """標註點必須落在三角形內，否則往形心拉——否則細長三角形的標註會跑到
        基地外或壓到鄰接三角形的標註（2026-08-01 實踩）。"""
        from shapely.geometry import Point
        if tri.contains(Point(pt)):
            return pt
        return (pt[0] + (cen.x - pt[0]) * frac, pt[1] + (cen.y - pt[1]) * frac)

    if lines_only:                 # 只畫尺寸線，數字由呼叫端另行定位
        return
    bmid = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
    hmid = ((apex[0] + foot[0]) / 2, (apex[1] + foot[1]) / 2)
    hx, hy = _unit(foot, apex)
    if outside:
        # 底：往背對頂點方向（三角形外）；高：往遠離形心的一側。
        # ⚠ 細長三角形的底中點與高中點很接近，只往外推兩個數字會疊在一起
        #   （2026-08-01 T06 實踩）→ 再沿底線方向往相反兩端分開。
        sep = dm["底"] * 0.22
        mb = (bmid[0] - nx * off * 2.0 + ux * sep, bmid[1] - ny * off * 2.0 + uy * sep)
        px, py = -hy, hx
        if ((hmid[0] + px * off - cen.x) ** 2 + (hmid[1] + py * off - cen.y) ** 2 <
                (hmid[0] - px * off - cen.x) ** 2 + (hmid[1] - py * off - cen.y) ** 2):
            px, py = -px, -py
        mh = (hmid[0] + px * off * 2.4 - ux * sep, hmid[1] + py * off * 2.4 - uy * sep)
        for m0, m1 in ((bmid, mb), (hmid, mh)):        # 引線
            axis.plot([m0[0], m1[0]], [m0[1], m1[1]], color="#C00000",
                      lw=0.6, ls=(0, (1.5, 1.5)), zorder=6)
    else:
        mb = inside((bmid[0] + nx * off, bmid[1] + ny * off))
        mh = inside((hmid[0] - hy * off * 1.2, hmid[1] + hx * off * 1.2))
    axis.text(mb[0], mb[1], f"{dm['底']:.2f}", fontsize=fs, color="#C00000",
              ha="center", va="center", zorder=7, bbox=bx)
    axis.text(mh[0], mh[1], f"{dm['高']:.2f}", fontsize=fs, color="#C00000",
              ha="center", va="center", zorder=7, style="italic", bbox=bx)
    if show_no:
        c = tri.centroid
        axis.text(c.x, c.y, no, fontsize=fs + 0.8, color="#003D82",
                  ha="center", va="center", zorder=8, fontweight="bold", bbox=bx)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True, help="基地 shp（算基地面積）")
    ap.add_argument("--out", required=True, help="案件資料夾 outputs/<案>")
    ap.add_argument("--greenbelt", default=None)
    ap.add_argument("--usable", default=None, help="非隔離綠帶區域（可配置範圍）")
    ap.add_argument("--ratio", type=float, default=0.30, help="法定面積比，預設 0.30")
    ap.add_argument("--retained", default=None, metavar="保留.shp",
                    help="部分變更編定時分割出去、維持農業用地之範圍（如涉及公路處）。"
                         "給了會在圖上以斜線區塊呈現，並在公式區標明其面積與"
                         "「不納入申請範圍」。")
    ap.add_argument("--facility", default=None, metavar="隔離設施.shp",
                    help="臨路側之隔離設施範圍（greenbelt_ratio --facility-near 產出）。"
                         "未給時自動找 <out>/隔離綠帶/隔離設施.shp。給了會與隔離綠帶"
                         "分色並列圖例（使用者 2026-08-01 定案：臨路側做隔離設施、"
                         "其他地方做隔離綠帶；定義依非都市土地使用管制規則§11 通路、"
                         "空地等非建築之開放性設施，兩者均計入面積比）。⚠ 僅適用"
                         "隔離綠帶（<2公頃變更編定）情境，緩衝綠帶（開發許可）情境"
                         "不套用，見 .claude/rules/隔離綠帶與緩衝綠帶判準.md。")
    ap.add_argument("--no-width", action="store_true",
                    help="不標綠帶寬度（預設會沿基地邊界逐段標示垂直寬度）")
    ap.add_argument("--tag", default=None, help="輸出檔名後綴（做方案比較用）")
    a = ap.parse_args()

    out = Path(a.out)
    gb_dir = out / "隔離綠帶"
    gb_p = a.greenbelt or str(gb_dir / "隔離綠帶或設施.shp")
    us_p = a.usable or str(gb_dir / "可配置範圍.shp")
    for p in (a.joined, gb_p, us_p):
        if not os.path.exists(p):
            raise SystemExit(f"找不到：{p}（請先跑 scripts/greenbelt_ratio.py）")

    site = _read(a.joined)
    green = _read(gb_p)
    usable = _read(us_p)
    A_site, A_green, A_use = site.area, green.area, usable.area
    suf = f"_{a.tag}" if a.tag else ""

    # 隔離設施（臨路側）／隔離綠帶（其餘）分類：兩者相加即為 green
    fac_p = a.facility or str(gb_dir / "隔離設施.shp")
    facility = _read(fac_p) if os.path.exists(fac_p) else None
    belt = green.difference(facility) if facility is not None else green

    tris = triangulate(usable)
    rows, total, dims = [], 0.0, []
    for i, t in enumerate(tris, 1):
        b, h, ar, bs, apex, foot = base_height(t)
        rows.append({"編號": f"T{i:02d}", "底_m": round(b, 2), "高_m": round(h, 2),
                     "面積_m2": round(ar, 2)})
        dims.append({"底線": bs, "頂點": apex, "垂足": foot, "底": b, "高": h})
        total += ar

    # ★ 正確性閘門：三角形面積合計必須等於非綠帶區域面積，否則分割有漏
    gap = abs(total - A_use)
    print(f"非隔離綠帶區域 {A_use:,.2f} m² → 分割為 {len(tris)} 個三角形，"
          f"合計 {total:,.2f} m²（差 {gap:.4f} m²）")
    if gap > max(0.05, A_use * 1e-6):
        raise SystemExit(f"✗ 三角形合計與區域面積不符（差 {gap:.4f} m²），分割有漏，請檢查幾何")
    print("  ✓ 分割無漏，可作為計算式依據")

    derived = A_site - total          # 依公式反算之綠帶面積
    pct = derived / A_site * 100.0
    ok = derived >= A_site * a.ratio
    print(f"\n計算式：基地 {A_site:,.2f} － 非綠帶 {total:,.2f} ＝ 綠帶 {derived:,.2f} m²")
    print(f"        {derived:,.2f} ÷ {A_site:,.2f} ＝ {pct:.2f}% "
          f"{'≥' if ok else '<'} {a.ratio*100:.0f}% → {'✓ 符合' if ok else '✗ 不符'}")
    d2 = abs(derived - A_green)
    print(f"  與 greenbelt_ratio 實算綠帶 {A_green:,.2f} m² 相差 {d2:.4f} m²"
          + ("（一致）" if d2 <= 0.05 else "　⚠ 不一致，請查明"))

    # ── 輸出表與幾何 ────────────────────────────────────────────────
    csv_p = out / f"隔離綠帶計算式{suf}.csv"
    with io.open(csv_p, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write("編號,底(m),高(m),面積(m²)\n")
        for r in rows:
            fh.write(f"{r['編號']},{r['底_m']:.2f},{r['高_m']:.2f},{r['面積_m2']:.2f}\n")
        fh.write(f"合計,,,{total:.2f}\n")
    js = {"基地面積_m2": round(A_site, 2), "非綠帶面積_m2": round(total, 2),
          "綠帶面積_公式_m2": round(derived, 2), "綠帶面積_實算_m2": round(A_green, 2),
          "綠帶占比_pct": round(pct, 2), "法定比_pct": a.ratio * 100,
          "是否達標": bool(ok), "三角形數": len(tris), "三角形": rows}
    io.open(out / f"隔離綠帶計算式{suf}.json", "w", encoding="utf-8").write(
        json.dumps(js, ensure_ascii=False, indent=2))
    gdf = gpd.GeoDataFrame({"編號": [r["編號"] for r in rows]},
                           geometry=tris, crs=f"EPSG:{CRS}")
    gdf.to_file(gb_dir / f"非綠帶三角形{suf}.shp", encoding="utf-8")

    # ── 計算式圖說 ──────────────────────────────────────────────────
    # ⚠ 版面務必用 gridspec 拆兩個子圖：左圖為等比例座標軸（又寬又扁），
    #   若把公式／表格用 transAxes 分數塞在同一個等比例軸上，分數對應的實際高度
    #   與直覺不符，公式框會壓到表格（2026-08-01 實踩兩次）。
    fig = plt.figure(figsize=(11.69, 8.27), dpi=200)          # A4 橫
    # ★ 版面：主圖**橫跨整個上方**。基地多為橫長形（本案約 2:1），若把主圖塞進
    #   接近正方的半版欄位，等比例後只用得到一半空間、標註全部擠在一起
    #   （2026-08-01 實踩）。下方一列放公式／明細框／計算表。
    #   明細框只在有過小三角形時才佔欄。
    gs = fig.add_gridspec(2, 3, height_ratios=[2.55, 1.45],
                          width_ratios=[1.02, 0.88, 1.24],
                          wspace=0.06, hspace=0.06,
                          left=0.02, right=0.985, top=0.97, bottom=0.04)
    ax = fig.add_subplot(gs[0, :])
    axr = fig.add_subplot(gs[1, 2]); axr.axis("off")
    gx, gy = [], []
    # 隔離綠帶（綠）與隔離設施（灰藍）分色；兩者均計入面積比，分色只為說明性質
    for geom_, fc, ec in ((belt, "#4C9A2A", "#2E5E19"),
                          (facility, "#9FB8D0", "#3B6E9E")):
        if geom_ is None or geom_.is_empty:
            continue
        for poly in (shapely.get_parts(geom_).tolist()
                     if geom_.geom_type != "Polygon" else [geom_]):
            ax.add_patch(MplPolygon(list(poly.exterior.coords), closed=True,
                                    facecolor=fc, edgecolor=ec,
                                    alpha=0.55, linewidth=1.0, zorder=2))
    for t in tris:
        ax.add_patch(MplPolygon(list(t.exterior.coords), closed=True,
                                facecolor="#FFFFFF", edgecolor="#0057B8",
                                alpha=0.85, linewidth=0.9, zorder=3))
    retained = _read(a.retained) if a.retained else None
    if retained is not None:
        for poly in (shapely.get_parts(retained).tolist()
                     if retained.geom_type != "Polygon" else [retained]):
            ax.add_patch(MplPolygon(list(poly.exterior.coords), closed=True,
                                    facecolor="#BFBFBF", edgecolor="#666666",
                                    hatch="//", alpha=0.55, linewidth=1.0, zorder=1))
            xs, ys = poly.exterior.xy
            gx += list(xs); gy += list(ys)
    for poly in (shapely.get_parts(site).tolist() if site.geom_type != "Polygon"
                 else [site]):
        xs, ys = poly.exterior.xy
        ax.plot(xs, ys, color="#D00000", linewidth=2.0, zorder=4)
        gx += list(xs); gy += list(ys)
    ax.set_aspect("equal"); ax.axis("off")
    w, h = max(gx) - min(gx), max(gy) - min(gy)
    ax.set_xlim(min(gx) - w * 0.04, max(gx) + w * 0.04)
    ax.set_ylim(min(gy) - h * 0.06, max(gy) + h * 0.06)

    # 判斷哪些三角形塞不下尺寸標註：以「高」與內切圓半徑對比文字高度（資料單位）
    FS = 5.0
    bb = ax.get_position()
    data_per_in = (w * 1.08) / (bb.width * 11.69)
    txt_h = (FS / 72.0) * data_per_in                 # 一行文字高（資料單位）
    off = txt_h * 0.85
    small = []
    for t, r, dm in zip(tris, rows, dims):
        inr = 2.0 * t.area / (t.length or 1.0)        # 內切圓半徑
        if dm["高"] < txt_h * 2.2 or inr < txt_h * 0.95:
            small.append((r["編號"], t, dm))
            c = t.centroid
            ax.text(c.x, c.y, r["編號"], fontsize=FS + 0.8, color="#003D82",
                    ha="center", va="center", zorder=8, fontweight="bold",
                    bbox=dict(boxstyle="square,pad=0.06", fc="white",
                              ec="none", alpha=0.8))
        else:
            draw_dims(ax, t, dm, r["編號"], FS, off)

    # ── 綠帶寬度標註（使用者 2026-08-01 要求）──────────────────────────────
    # 沿基地邊界逐段量「該段到可配置範圍之垂直距離」＝該側綠帶寬度。
    # ⚠ 不要沿邊界密集取樣後全部標出——同一直線段寬度相同，標十幾次只會糊成一團；
    #   一段一個數字（送審圖的慣例），且僅標實際有留設綠帶的段。
    widths = []
    if not a.no_width:
        from shapely.geometry import LineString, Point as _Pt
        from shapely.ops import nearest_points
        segs = []
        for poly in (shapely.get_parts(site).tolist()
                     if site.geom_type != "Polygon" else [site]):
            cs = list(poly.exterior.coords)
            segs += [(cs[i], cs[i + 1]) for i in range(len(cs) - 1)]
        for p0, p1 in segs:
            L = ((p1[0] - p0[0]) ** 2 + (p1[1] - p0[1]) ** 2) ** 0.5
            if L < 3.0:
                continue                       # 太短的邊標了也看不清
            mid = _Pt((p0[0] + p1[0]) / 2, (p1[1] + p0[1]) / 2)
            d = usable.distance(mid)
            if d < 0.15:
                continue                       # 該側未留設綠帶（非應留設側）
            _, q = nearest_points(mid, usable)
            widths.append((d, mid, q, L))
        widths.sort(key=lambda t: -t[3])
        shown = []
        for d, mid, q, _L in widths:
            if any(mid.distance(m2) < w * 0.10 for _d, m2, _q, _l in shown):
                continue                       # 同一角落兩段，只留較長者
            shown.append((d, mid, q, _L))
            ax.annotate("", xy=(q.x, q.y), xytext=(mid.x, mid.y),
                        arrowprops=dict(arrowstyle="<->", color="#1B5E20",
                                        lw=0.9, shrinkA=0, shrinkB=0), zorder=7)
            ux, uy = _unit((mid.x, mid.y), (q.x, q.y))
            px, py = -uy, ux                   # 沿邊界方向，把字挪開箭頭
            ax.text(mid.x + ux * d * 0.5 + px * off * 1.6,
                    mid.y + uy * d * 0.5 + py * off * 1.6,
                    f"寬 {d:.2f}", fontsize=FS + 0.2, color="#1B5E20",
                    ha="center", va="center", zorder=8,
                    bbox=dict(boxstyle="square,pad=0.08", fc="white",
                              ec="#1B5E20", lw=0.4, alpha=0.9))
        widths = shown

    # ── 圖例 ────────────────────────────────────────────────────────────
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    leg = [Patch(facecolor="#4C9A2A", edgecolor="#2E5E19", alpha=0.55,
                 label=f"隔離綠帶　{belt.area:,.2f} ㎡"),
           Patch(facecolor="#FFFFFF", edgecolor="#0057B8",
                 label="非隔離綠帶或設施之區域（三角形分割）"),
           Line2D([], [], color="#D00000", lw=2.0, label="申請範圍界線"),
           Line2D([], [], color="#1B5E20", lw=0.9, label="綠帶或設施寬度（公尺）")]
    if facility is not None and not facility.is_empty:
        leg.insert(1, Patch(facecolor="#9FB8D0", edgecolor="#3B6E9E", alpha=0.55,
                            label=f"隔離設施（臨路側）　{facility.area:,.2f} ㎡"))
    if retained is not None:
        leg.insert(len(leg) - 1,
                   Patch(facecolor="#BFBFBF", edgecolor="#666666", hatch="//",
                         alpha=0.55, label="分割後維持農業用地（不納入申請範圍）"))
    ax.legend(handles=leg, loc="upper left", fontsize=6.4, framealpha=0.92,
              borderpad=0.5, labelspacing=0.45, handlelength=1.6).set_zorder(9)

    # ⚠ 微軟正黑體缺「≥」字（會印成空心方框）→ 一律用中文「以上／未達」表達
    pre = ""
    if retained is not None:
        pre = ("部分變更編定：涉及公路處分割後維持農業用地，不納入申請範圍\n"
               f"　原地號 {A_site + retained.area:,.2f} － 保留（斜線區）"
               f"{retained.area:,.2f} ＝ 申請範圍 {A_site:,.2f} ㎡\n\n")
    formula = pre + (
        "隔離綠帶或設施面積計算式\n\n"
        "　基地面積 － 非隔離綠帶或設施之面積 ＝ 隔離綠帶或設施之面積\n"
        f"　{A_site:,.2f} － {total:,.2f} ＝ {derived:,.2f} ㎡\n\n"
        "比例檢核\n\n"
        f"　{derived:,.2f} ÷ {A_site:,.2f} ＝ {pct:.2f}%\n"
        f"　法定下限 {a.ratio*100:.0f}%，本案 {pct:.2f}%，"
        f"{'已達法定下限' if ok else '未達法定下限'} → {'符合' if ok else '不符'}\n\n"
        f"非隔離綠帶區域分割為 {len(tris)} 個三角形，逐一計算如右表。\n"
        "圖上紅色實線為底、紅色虛線為高，綠色雙箭頭為留設寬度，數字單位公尺。"
    )
    if facility is not None and not facility.is_empty:
        # ⚠ 這段必須自行折行：公式框寬度由最長一行決定，不折行會把右側計算表壓掉
        #   （2026-08-01 實踩）。中文無空白，textwrap 無效，逐句手動斷行。
        formula += ("\n\n性質區分\n\n"
                    f"　臨路側留設隔離設施 {facility.area:,.2f} ㎡，\n"
                    f"　其餘留設隔離綠帶 {belt.area:,.2f} ㎡，合計即上開面積。\n"
                    "　隔離設施依非都市土地使用管制規則第11條得為通路、空地等\n"
                    "　非建築之開放性設施，與隔離綠帶同計入應留設面積。")
    if small:
        formula += ("\n圖面過小標不下尺寸者（" + "、".join(n for n, _t, _d in small)
                    + "）另以放大圖框標示。")
    axf = fig.add_subplot(gs[1, 0] if small else gs[1, 0:2]); axf.axis("off")
    axf.text(0.0, 1.0, formula, transform=axf.transAxes, va="top", ha="left",
             fontsize=8.4, linespacing=1.32,
             bbox=dict(boxstyle="round,pad=0.45", facecolor="#F7F7F7",
                       edgecolor="#888888", linewidth=0.8))

    # 面積計算表：列數多時橫向分欄，避免壓成一團不可讀
    hdr = ["編號", "底(m)", "高(m)", "面積(㎡)"]
    body = [[r["編號"], f"{r['底_m']:.2f}", f"{r['高_m']:.2f}", f"{r['面積_m2']:.2f}"]
            for r in rows]
    ncol_tbl = 1 if len(body) <= 20 else (2 if len(body) <= 44 else 3)
    per = -(-len(body) // ncol_tbl)                     # 每欄列數（向上取整）
    cw = 1.0 / ncol_tbl
    for k in range(ncol_tbl):
        chunk = body[k * per:(k + 1) * per]
        if not chunk:
            continue
        if k == ncol_tbl - 1:                          # 合計列放最後一欄末端
            chunk = chunk + [["合計", "", "", f"{total:.2f}"]]
        tb = axr.table(cellText=chunk, colLabels=hdr, cellLoc="center",
                       bbox=[k * cw + 0.008, 0.0, cw - 0.016,
                             0.60 * (len(chunk) + 1) / (per + 1)])
        tb.auto_set_font_size(False); tb.set_fontsize(6.4)
        last = len(chunk)
        for (rr, _cc), cell in tb.get_celld().items():
            cell.set_linewidth(0.4)
            if rr == 0 or (k == ncol_tbl - 1 and rr == last):
                cell.set_facecolor("#E7E7E7")
                cell.set_text_props(fontweight="bold")
    # ── 過小三角形：主圖下方獨立一排明細框，各自放大到填滿框 ──────────────
    if small:
        sub = gs[1, 1].subgridspec(1, len(small), wspace=0.16)
        for k, (no, t, dm) in enumerate(small):
            iax = fig.add_subplot(sub[0, k])
            b0, b1, b2, b3 = t.bounds
            cx, cy = (b0 + b2) / 2, (b1 + b3) / 2
            half = max(b2 - b0, b3 - b1) / 2 * 1.95 + 0.8   # 外標需要更多留白
            iax.set_xlim(cx - half, cx + half)
            iax.set_ylim(cy - half, cy + half)
            iax.set_aspect("equal")
            iax.add_patch(MplPolygon(list(t.exterior.coords), closed=True,
                                     facecolor="#FFFFFF", edgecolor="#0057B8",
                                     linewidth=1.2, zorder=3))
            fs2 = 7.5 if len(small) <= 4 else 6.5
            # 明細框每格只放一個三角形 → 數字固定標在左下／右上角並拉引線。
            # ⚠ 細長片用「幾何偏移」定位不可靠：形心幾乎落在線上，方向判斷不穩，
            #   數字仍會壓在圖形上（2026-08-01 使用者指正後兩次實踩）。
            draw_dims(iax, t, dm, no, fs2, 0.0, show_no=False, lines_only=True)
            (bp, bq), ap_, ft = dm["底線"], dm["頂點"], dm["垂足"]
            arrow = dict(arrowstyle="-", color="#C00000", lw=0.6,
                         linestyle=":", shrinkA=1, shrinkB=1)
            iax.annotate(f"底 {dm['底']:.2f}",
                         xy=((bp[0] + bq[0]) / 2, (bp[1] + bq[1]) / 2), xycoords="data",
                         xytext=(0.04, 0.06), textcoords="axes fraction",
                         fontsize=fs2, color="#C00000", ha="left", va="bottom",
                         arrowprops=arrow, zorder=8)
            iax.annotate(f"高 {dm['高']:.2f}",
                         xy=((ap_[0] + ft[0]) / 2, (ap_[1] + ft[1]) / 2), xycoords="data",
                         xytext=(0.96, 0.90), textcoords="axes fraction",
                         fontsize=fs2, color="#C00000", ha="right", va="top",
                         style="italic", arrowprops=arrow, zorder=8)
            iax.set_title(f"{no}　面積 {t.area:.2f} ㎡", fontsize=fs2,
                          color="#003D82", pad=2.5)
            iax.set_xticks([]); iax.set_yticks([])
            for sp in iax.spines.values():
                sp.set_edgecolor("#AAAAAA"); sp.set_linewidth(0.8)
            iax.patch.set_facecolor("#FCFCFC")

    png = out / "圖檔_maxmap" / f"001_{out.name}_綠帶計算式{suf}.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"\n→ {csv_p}")
    print(f"→ {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
