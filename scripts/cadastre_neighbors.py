# -*- coding: utf-8 -*-
"""⓪a 周邊地籍自動發現＋即時屬性回填（2026-08-06 使用者定案）

★ 建立緣由：既有「周邊地籍」概念（make_maps.py 出圖用）只從**本機縣市快照**
  （Ver 111年2月）撈，僅供製圖時的視覺脈絡，屬性是舊的，不能拿來做分區／使用地
  編定等評估判斷——這正是 2026-08-02 改用 NLSC WMTS 當主題1/2預設底圖的原因。

  現在 ⓪a（cadastre_fetch.py，免登入 getLandInfo）回傳路徑順暢，本模組把「發現
  鄰地」與「取得鄰地即時屬性」分成兩步接起來：
    1. **發現**：本機縣市快照只拿來做**空間識別**（哪些地號在範圍內），不採其屬性——
       地號/段/鄉鎮這些**識別身分**不常變、快照堪用；使分/使類等**屬性**才會過時。
    2. **回填**：把發現到的地號餵回 ⓪a 即時查（getLandInfo），拿到現在的使分／使類／
       登記面積／權屬，跟基地本身用的是同一組免登入權威來源，口徑一致。

  評估範圍外擴 50 公尺內與地籍有交集者全數回傳（不是只挑「明顯相鄰」的幾筆）；
  之後的分區圖、使用地編定圖、周邊使用地分析（adjacent_landuse.py）、隔離綠帶
  應留設側判定（greenbelt_ratio.py --neighbours）都可以直接吃這份「鄰地.shp」，
  取代舊快照或 WMTS 疊圖——WMTS 改列備選（圖磚抓不到、或要快速目視現況套疊時用）。

用法（系統 Python，不需 QGIS）：
  python -X utf8 scripts/cadastre_neighbors.py --joined <cadastre/基地.shp> \
      --out outputs/<案>/cadastre [--buffer 50] [--name 鄰地]

輸出：<out>/<name>.shp（與 cadastre_fetch.py 同欄位：地號/使分/使類/縣市/鄉鎮/段/
  登面積/圖面積/偏差pct/版次/狀態/未登記/公私別/所有權/管理者/原地號）
  ＋ <out>/<name>_neighbors.json（發現階段的分組與筆數，供除錯）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import types
import warnings

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape
from shapely.ops import unary_union

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CAD_DIR = os.path.join(REPO, "地籍圖資")
SKILL = os.path.join(os.path.expanduser("~"), ".claude", "skills",
                     "cadastre-check", "scripts")
GAP_CHECK_M = 20.0     # 覆蓋率自我檢查的緊鄰帶寬度（小於 --buffer，聚焦「緊鄰」而非全部外擴範圍）
GAP_MIN_AREA = 10.0    # 低於此面積視為浮點誤差/畸零縫隙雜訊，不警告
PROBE_MAX_CHILD = 80   # 探測同宗地母號子號時的上限（已見案例最大用到45，留餘裕）
SHRINK_RATIO = 0.9     # 即時回填面積 < 快照面積×此值 → 判為「母號縮水」（就地分割），須探子號
sys.path.insert(0, HERE)
from cadastre_fetch import cmd_fetch  # noqa: E402


def _section_name(seg, sub) -> str:
    """本機快照『段』欄不含「段」字、『小段』欄不含「小段」字；即時查詢 API 要完整組字。"""
    seg = "" if pd.isna(seg) else str(seg).strip()
    sub = "" if pd.isna(sub) else str(sub).strip()
    return f"{seg}段{sub}小段" if sub else f"{seg}段"


def _safe_to_file(gdf, path: str, retries: int = 4, wait: float = 0.6) -> str:
    """寫 shp，遇檔案鎖（WinError 32，桌面 QGIS/PowerPoint/另一支程序開著）先重試，
    仍失敗則改寫 `<stem>_未併.shp` 並**大聲警告、不 crash**——避免探測補正的成果因
    最後一步寫檔被鎖而整批丟失（2026-08-10 潮州案實踩：補正已探到子號，寫回鄰地.shp
    時被鎖 → 整個 run crash、鄰地停在補正前，13-1/13-2/9-1/9-2 全沒進）。"""
    last = None
    for i in range(retries):
        try:
            gdf.to_file(path, encoding="utf-8")
            return path
        except Exception as e:  # noqa: BLE001 — 主要防 PermissionError [WinError 32]
            last = e
            time.sleep(wait)
    alt = path[:-4] + "_未併.shp" if path.lower().endswith(".shp") else path + "_未併.shp"
    try:
        gdf.to_file(alt, encoding="utf-8")
        print(f"⚠⚠ {path} 寫入失敗（{type(last).__name__}：檔案疑被另一程序鎖住）"
              f"→ 已改寫 {alt}，成果未遺失；請關閉佔用程序後手動併回或重跑。")
        return alt
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"✖ {path} 與備援 {alt} 皆寫入失敗：{e}")


def _load_api():
    if not os.path.isdir(SKILL):
        raise SystemExit(f"找不到 skill cadastre-check：{SKILL}")
    sys.path.insert(0, SKILL)
    import api  # noqa: PLC0415
    return api


def _county_file(county: str) -> str | None:
    for c in (county, county.replace("臺", "台"), county.replace("台", "臺")):
        p = os.path.join(CAD_DIR, c + ".shp")
        if os.path.exists(p):
            return p
    return None


def _load_cadlib():
    if not os.path.isdir(SKILL):
        raise SystemExit(f"找不到 skill cadastre-check：{SKILL}")
    sys.path.insert(0, SKILL)
    import cadlib  # noqa: PLC0415
    return cadlib


def discover(joined: str, buffer_m: float, county: str | None) -> tuple[dict, object, object]:
    """回 ({(縣市,鄉鎮,段): [landno,...]}, su, local_check_union)。
    su＝基地聯集幾何；local_check_union＝本機快照在 GAP_CHECK_M 緊鄰帶內的候選
    宗地聯集（含被排除的基地自身候選，供事後覆蓋率比對用，見 check_coverage_gap）。
    """
    cadlib = _load_cadlib()
    site = gpd.read_file(joined, engine="pyogrio")
    if site.crs and site.crs.to_epsg() != 3826:
        site = site.to_crs(3826)
    su = site.geometry.union_all()
    ring = su.buffer(buffer_m)

    if not county:
        for c in ("縣市", "縣市名稱"):
            if c in site.columns:
                county = str(site.iloc[0][c] or "").strip()
                break
    p = _county_file(county or "")
    if not p:
        raise SystemExit(f"找不到 地籍圖資\\{county}.shp（周邊地籍發現需本機地籍快照做"
                         "空間識別；只用它認地號身分，不用它的屬性）")

    sur_all = gpd.read_file(p, engine="pyogrio", bbox=ring.bounds)
    sur_all = sur_all[sur_all.geometry.intersects(ring)]
    # 排除基地本身：與基地重疊面積達自身 50% 以上視為同一宗地（浮點誤差/版本差不可用
    # 地號字串比對，改用幾何重疊比例判斷，較穩健）
    sur_all = sur_all.copy()
    sur_all["_ov"] = sur_all.geometry.intersection(su).area / sur_all.geometry.area.replace(0, 1)
    sur = sur_all[sur_all["_ov"] < 0.5]

    gap_check_ring = su.buffer(GAP_CHECK_M)
    near = sur_all[sur_all.geometry.intersects(gap_check_ring)]
    local_check_union = unary_union(list(near.geometry)) if len(near) else None

    # ★ 本機 shp 的「段」欄不含「段」字、「小段」欄不含「小段」字（如「下山子寮」／
    #   「考試潭」＋「鹿寮」），但即時查詢 API 要的 section 參數要**完整組字**
    #   （「下山子寮段」／「考試潭段鹿寮小段」）——直接拿欄位值去查會回「查無此段」
    #   （2026-08-06 七股案實踩）。段名權威來源見 config/section_index.csv 同構欄位。
    groups: dict[tuple, list[str]] = {}
    for _, r in sur.iterrows():
        no8 = f"{int(r['宗地母']):04d}{int(r['宗地子']):04d}"
        landno = cadlib.format_landno(no8)
        section = _section_name(r["段"], r["小段"])
        key = (str(r["縣市"]).strip(), str(r["鄉鎮"]).strip(), section)
        groups.setdefault(key, []).append(landno)
    for k in groups:
        groups[k] = sorted(set(groups[k]))
    return groups, su, local_check_union, sur


def fetch_all(groups: dict, out_dir: str, name: str = "鄰地") -> tuple[str, "gpd.GeoDataFrame"]:
    os.makedirs(out_dir, exist_ok=True)
    parts = []
    for (county, town, section), landnos in groups.items():
        tag = f"_{name}_{county}{town}{section}".replace("/", "")
        a = types.SimpleNamespace(
            county=county, town=town, section=section, landnos=landnos,
            version=None, no_owner=False, out=out_dir, name=tag)
        print(f"\n=== 發現 {county}{town}{section}：{len(landnos)} 筆鄰地，即時回填屬性 ===")
        # ★ 單段查詢失敗不得阻斷整體合併（2026-08-19 湖口祥湖段案實踩）：cmd_fetch 對
        #   0 成功筆數會 raise SystemExit；若失敗段（常見＝「未登錄地段」9999-xxx，線上
        #   無此段名）排在最後一組，未包 try 會讓迴圈後的 concat/寫檔整段沒跑到——
        #   前面成功段只留分段 _鄰地_<段>.shp，最終 鄰地.shp 卻沒產出。改為逐段容錯。
        try:
            cmd_fetch(a)
        except SystemExit as e:
            print(f"  ⚠ {county}{town}{section} 查詢無成功筆數，略過此段、不阻斷合併（{e}）")
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠ {county}{town}{section} 查詢失敗，略過此段、不阻斷合併"
                  f"（{type(e).__name__}: {e}）")
        shp = os.path.join(out_dir, f"{tag}.shp")
        if os.path.exists(shp):
            parts.append(gpd.read_file(shp, engine="pyogrio"))

    if not parts:
        raise SystemExit("✖ 所有分組皆無成功筆數，未產出鄰地.shp")
    merged = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]
    merged = gpd.GeoDataFrame(merged, crs=parts[0].crs)
    out_shp = _safe_to_file(merged, os.path.join(out_dir, f"{name}.shp"))
    print(f"\n→ {out_shp}（合併 {len(groups)} 段、共 {len(merged)} 筆鄰地）")
    return out_shp, merged


def check_coverage_gap(su, local_check_union, live_gdf):
    """比對「本機快照緊鄰帶內候選宗地聯集」vs「即時回填後的實際聯集」，回缺口警告
    或 None。

    ★ 建立緣由（2026-08-06 七股案實踩）：66-42（交通用地、公路總局管理）緊鄰基地
    （距離0m），discover() 卻完全沒發現它——一開始以為是本機快照整筆漏收，
    但逐一比對後發現：本機快照其實**有**涵蓋那塊面積，只是掛在舊識別「66-23」
    名下（本機快照 66-23 面積 13,318㎡，但即時回填後的 66-23 只剩 8,648㎡登記
    面積）——**66-23 已被重測分割，一部分拆成新地號 66-42，本機快照沒跟上**。
    這代表單純比「本機快照 vs 本機快照自己」（覆蓋率100%）抓不到這種問題，
    必須比「本機快照候選聯集 vs 即時回填後實際聯集」，落差就是「本機快照以為
    在地、實際卻不在 discover() 找到的地號範圍內」的部分——不管成因是本機整筆
    漏收（如真的沒有任何舊識別）還是本機舊識別已被分割（如本案），都會在這裡現形。
    """
    if local_check_union is None or local_check_union.is_empty:
        return None, None
    live_union = unary_union(list(live_gdf.geometry) + [su])
    gap = local_check_union.difference(live_union)
    gap_area = gap.area if not gap.is_empty else 0.0
    if gap_area <= GAP_MIN_AREA:
        return None, None
    warning = (
        f"⚠ 基地 {GAP_CHECK_M:.0f}m 內有 {gap_area:,.1f} ㎡本機地籍快照候選宗地的範圍，"
        f"即時回填後對不上任何實際地號（本機快照 Ver 111年2月，落差常見成因：①快照"
        f"整筆漏收新增地號 ②快照的舊地號已被重測分割成新地號，快照沒跟上——如66-42"
        f"即從66-23分割出來）。不可逕自當作真的沒有相鄰宗地，建議：肉眼比對正射影像"
        f"有無明顯地界、或上 TCDGIS 覆核缺口範圍內的實際地號。")
    return warning, gap


def _gap_pieces(gap, min_area: float = GAP_MIN_AREA) -> list:
    if gap is None or gap.is_empty:
        return []
    geoms = list(gap.geoms) if gap.geom_type == "MultiPolygon" else [gap]
    return [g for g in geoms if g.area > min_area]


def find_probe_targets(pieces: list, county_shp: str, existing_landnos: set) -> dict:
    """對每個顯著缺口，找緊鄰的本機候選宗地母號，作為探測起點。

    ★ 建立緣由（2026-08-06 七股案 67-11 教訓，兩階段）：
    第一階段：check_coverage_gap 抓到落差後，上一版只印警告、要求人工「肉眼比對／
    上TCDGIS」——67-11 那次我人工只認真查了落差最大的一塊，其餘幾塊小的當成
    「resurvey 邊界誤差」略過，結果其中一塊正是真正漏收的地號 → 改成自動探測、
    不靠人工肉眼挑。
    第二階段（同日再度實踩，67-11 仍被漏掉）：**單一 `representative_point()`
    不夠**——第一輪修復前的缺口是一整塊連在一起的大範圍（~9,839㎡，同時涵蓋
    66 系與 67-11 所在區域），單一取樣點只落在 66 系那一側，67 母號完全沒被
    當成候選、探測階段連問都沒問過。改成**用整塊缺口幾何找候選**（
    `local.geometry.distance(piece)`，不是「候選到取樣點的距離」），同一塊缺口
    緊鄰的所有本機候選都會被收進來，不會因為缺口形狀複雜、樣本點只代表局部
    而漏掉另一側的候選母號。
    回 {(縣市,鄉鎮,段): {母號4碼, ...}}。
    """
    targets: dict[tuple, set] = {}
    if not pieces:
        return targets
    bounds = unary_union(pieces).buffer(5).bounds
    local = gpd.read_file(county_shp, engine="pyogrio", bbox=bounds)
    for piece in pieces:
        near = local[local.geometry.distance(piece) < 3.0]
        for _, r in near.iterrows():
            section = _section_name(r["段"], r["小段"])
            key = (str(r["縣市"]).strip(), str(r["鄉鎮"]).strip(), section)
            targets.setdefault(key, set()).add(f"{int(r['宗地母']):04d}")
    return targets


def find_shrunk_mothers(sur, live_gdf, ratio: float = SHRINK_RATIO) -> dict:
    """★母號縮水偵測（2026-08-10 潮州中山段20案定案）——比 find_probe_targets 的
    「缺口幾何鄰近」更精準的探測目標來源。

    成因：本機快照的某母號在 Ver 111年2月後**就地分割**成母號＋數個子號，快照仍是
    分割前的大範圍（如五魁段13 快照10,486㎡→即時4,999㎡；七股66-23 13,318→8,648），
    discover() 只認得舊母號、分割出的子號整批不在快照 → 漏。做法：把快照候選（sur）
    與即時回填後（live_gdf）**逐母號比面積**，即時面積 < 快照×ratio 者判為縮水、回其
    母號作探測起點。

    為何優於「缺口鄰近所有母號」：後者會把緊鄰大缺口但**沒縮水**的母號全收進來（潮州
    案 16 個），每個探 80 子號→極慢（實測逾時），且放大寫檔鎖窗口；本法精準鎖定真正
    縮水的（潮州 2 個：9、13），快又不誤觸。回 {(縣市,鄉鎮,段): {母號4碼}}。
    """
    targets: dict[tuple, set] = {}
    if sur is None or len(sur) == 0 or live_gdf is None or len(live_gdf) == 0:
        return targets
    # 快照逐母號面積（母號 = 宗地母，section 完整組字）
    snap = {}
    for _, r in sur.iterrows():
        key = (str(r["縣市"]).strip(), str(r["鄉鎮"]).strip(),
               _section_name(r["段"], r["小段"]), f"{int(r['宗地母']):04d}")
        snap[key] = snap.get(key, 0.0) + r.geometry.area
    # 即時回填逐母號面積（由 live_gdf 的 地號 解析母號；『段』已是完整「五魁段」字串）
    live = {}
    for _, r in live_gdf.iterrows():
        ln = str(r.get("地號", "")).strip()
        if not ln:
            continue
        mother = ln.split("-")[0]
        try:
            m4 = f"{int(mother):04d}"
        except ValueError:
            continue
        key = (str(r.get("縣市", "")).strip(), str(r.get("鄉鎮", "")).strip(),
               str(r.get("段", "")).strip(), m4)
        live[key] = live.get(key, 0.0) + r.geometry.area
    for key, snap_area in snap.items():
        live_area = live.get(key, 0.0)
        if snap_area > GAP_MIN_AREA and live_area < ratio * snap_area \
                and (snap_area - live_area) > GAP_MIN_AREA:
            county, town, section, m4 = key
            targets.setdefault((county, town, section), set()).add(m4)
    return targets


def probe_siblings(targets: dict, existing_landnos: set, gap_union, log: list) -> dict:
    """對 find_probe_targets 找到的每個宗地母號，批次探測子號 1~PROBE_MAX_CHILD
    是否存在（單一宗地母號一次 api 呼叫查完全部候選子號，不是逐筆查，較省時）。
    只留下「即時查詢成功」且「幾何真的落在缺口範圍內」兩者皆符合的，避免抓到
    同母號但离缺口很遠、其實不相關的子號。回可併入 groups 的
    {(縣市,鄉鎮,段): [landno,...]}。
    """
    api = _load_api()
    found: dict[tuple, list] = {}
    for (county, town, section), mothers in targets.items():
        for mother in sorted(mothers):
            candidates = [f"{int(mother)}-{c}" for c in range(1, PROBE_MAX_CHILD + 1)
                         if f"{int(mother)}-{c}" not in existing_landnos]
            if not candidates:
                continue
            log.append(f"    探測 {county}{town}{section} 母號{int(mother)}："
                      f"子號1~{PROBE_MAX_CHILD}（排除已知 {len(existing_landnos)} 筆）")
            try:
                r = api.query(county, town, section, candidates)
            except Exception as e:  # noqa: BLE001
                log.append(f"      ⚠ 探測失敗：{e}")
                continue
            for rec in r["records"]:
                if not rec["ok"] or not rec["geometry"]:
                    continue
                geom = shape(rec["geometry"])
                if gap_union is not None and geom.distance(gap_union) > 1.0:
                    continue   # 同母號但離這次缺口太遠，不相關，不強行納入
                landno = rec["parcel"]["landno"]
                log.append(f"      ✔ 探測到 {landno}（{geom.area:,.1f} ㎡，"
                          f"落在缺口範圍內，本機快照未收錄）")
                found.setdefault((county, town, section), []).append(landno)
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True, help="基地 shp（cadastre/基地.shp 或申請範圍）")
    ap.add_argument("--out", required=True, help="輸出資料夾")
    ap.add_argument("--buffer", type=float, default=50.0, help="外擴公尺數，預設 50")
    ap.add_argument("--county", default=None, help="縣市（未給則從基地 shp 屬性讀）")
    ap.add_argument("--name", default="鄰地")
    ap.add_argument("--no-auto-resolve", action="store_true",
                    help="關閉缺口自動探測（2026-08-06新增，預設開啟；67-11教訓："
                         "光印警告要求人工肉眼查，人工會漏，改成自動探測子號補回）")
    a = ap.parse_args()

    groups, su, local_check_union, sur = discover(a.joined, a.buffer, a.county)
    total = sum(len(v) for v in groups.values())
    print(f"發現階段：{a.buffer:.0f}m 內共 {total} 筆鄰地，分 {len(groups)} 組"
          f"（縣市/鄉鎮/段不同者不能同一次查詢）")
    for k, v in groups.items():
        print(f"  {'/'.join(k)}：{len(v)} 筆　{v[:10]}{'...' if len(v) > 10 else ''}")

    with open(os.path.join(a.out, f"{a.name}_neighbors.json"), "w", encoding="utf-8") as f:
        json.dump({"/".join(k): v for k, v in groups.items()}, f,
                  ensure_ascii=False, indent=1)

    out_shp, merged = fetch_all(groups, a.out, a.name)
    existing_landnos = {ln for v in groups.values() for ln in v}
    # ★ 基地自身地號也要排除，否則探測子號時會把「基地自己」當成新發現的鄰地
    # 探測回來（2026-08-06 實踩：67-6 本身也被探測命中，因為它不在 groups 裡
    # ——discover() 本來就會排除基地自身，這裡要同樣排除，避免探測階段誤收）。
    site_gdf = gpd.read_file(a.joined, engine="pyogrio")
    if "地號" in site_gdf.columns:
        existing_landnos |= {str(x) for x in site_gdf["地號"].dropna()}

    gap_warning, gap = check_coverage_gap(su, local_check_union, merged)
    # ★母號縮水偵測（潮州案定案）＝精準探測目標；與缺口偵測互補：縮水母號一定伴隨缺口，
    #   但缺口鄰近的所有母號多半沒縮水（潮州 16 個母號僅 2 個真縮水）。以縮水母號為主要
    #   目標，既快又準；缺口幾何仍作為 probe_siblings 的落點過濾與資訊性警告。
    targets_shrunk = find_shrunk_mothers(sur, merged)
    probe_log: list[str] = []
    if (targets_shrunk or gap_warning) and not a.no_auto_resolve:
        if gap_warning:
            print(f"\n{gap_warning}")
        p = _county_file((groups and next(iter(groups))[0]) or a.county or "")
        # 主要目標＝縮水母號；缺口鄰近母號僅在縮水偵測為空時作後備（避免過度探測）
        targets = dict(targets_shrunk)
        if not targets:
            pieces = _gap_pieces(gap)
            targets = find_probe_targets(pieces, p, existing_landnos) if p else {}
        shr = {f"{k[2]}{int(m)}" for k, ms in targets_shrunk.items() for m in ms}
        print(f"→ 自動探測（母號縮水精準鎖定 {len(shr)} 個：{sorted(shr) or '無'}）"
              "，補回就地分割出、快照未收錄的子號...")
        recovered = probe_siblings(targets, existing_landnos, gap, probe_log)
        for line in probe_log:
            print(line)
        if recovered:
            n_new = sum(len(v) for v in recovered.values())
            print(f"→ 探測到 {n_new} 筆本機快照未收錄的地號，併入回填")
            out_shp, merged = fetch_all(recovered, a.out, a.name + "_補正")
            # 併回主檔（★抗鎖：寫檔遇鎖重試/改備援，不可 crash 丟失補正成果——潮州案實踩）
            base = gpd.read_file(os.path.join(a.out, f"{a.name}.shp"), engine="pyogrio")
            merged_all = pd.concat([base, merged], ignore_index=True)
            merged_all = gpd.GeoDataFrame(merged_all, crs=base.crs)
            out_shp = _safe_to_file(merged_all, os.path.join(a.out, f"{a.name}.shp"))
            merged = merged_all
            for k, v in recovered.items():
                groups.setdefault(k, [])
                groups[k] = sorted(set(groups[k]) | set(v))
            print(f"→ {out_shp}（自動補正後合計 {len(merged)} 筆鄰地）")
            # 補正後重新檢查是否還有殘餘缺口
            gap_warning, gap = check_coverage_gap(su, local_check_union, merged)
            if gap_warning:
                print(f"\n（自動補正後仍有殘餘）{gap_warning}")
            else:
                print("→ 自動補正後缺口已補齊，覆蓋率檢查通過")
        else:
            print("→ 探測未找到符合的子號——落差可能是水域/未登記空地/母號≥9000未登記土地"
                 "等真實現象，非本機快照漏收，建議人工核對正射影像確認。")

    with open(os.path.join(a.out, f"{a.name}_coverage_check.json"), "w",
             encoding="utf-8") as f:
        json.dump({"gap_check_m": GAP_CHECK_M, "警告": gap_warning,
                  "探測紀錄": probe_log}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
