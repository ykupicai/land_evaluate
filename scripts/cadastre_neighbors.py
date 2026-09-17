# -*- coding: utf-8 -*-
"""⓪a′ 周邊地籍取得：**出圖框 → 直接向農業部框查**（2026-09-04 使用者裁示「全改」）

★ 現行主路徑（online）
------------------------------------------------------------------
  1. 由**基地幾何**算出 maxmap 出圖框（`overlay_placement.place_fixed`，與 make_maps
     同一支、同參數含角落避讓），再與 `--buffer` 外擴環聯集成 region。
  2. 把 region 直接送農業部地籍圖層做**空間查詢**（`moa_parcel_query`），一次回傳
     範圍內全部宗地的幾何＋現行屬性。**不必事先知道有哪些地號。**
  3. 排除基地自身（幾何重疊 ≥50%），補權屬（MOI_API_039），寫 `<name>.shp`。
  4. **框內覆蓋率硬指標**：框內未被宗地覆蓋的比例應為 0；有缺就明確警告並寫進
     `<name>_coverage_check.json`，不可靜默留白。

★ 為什麼改（2026-09-04 麻豆客子寮段案）
------------------------------------------------------------------
舊路徑是「本機快照認地號 → 逐筆線上回填」，**候選清單完全由 Ver 111年2月 的快照
決定**：快照沒有的地號從頭到尾不會被查。麻豆案港子尾段整段重測改名為客子寮段並
全段重編號，其後又分割出 374-1／374-2（合計 36,338 ㎡、距基地 3.9 公尺），在快照
裡沒有任何舊地號對得上 → 整片留白。既有補救（母號縮水偵測、子號探測）也同時失效：
縮水偵測靠同地號比面積，整段重編號後兩邊集合不重疊；子號探測用舊段名，現行版查無。

框查實測（同一個出圖框）：154 筆、**框內覆蓋 100.00%**（舊路徑 145 筆／81.91%），
與 ring API 逐筆比對 126 筆 **IoU 全部 = 1.000000**；密集市區大框 1,684 筆 **0.3 秒**
回完（舊路徑逐筆查要數小時，員林案當初正因此放棄即時回填）。

★ 舊路徑保留為 fallback，但它救得了什麼、救不了什麼要講清楚
------------------------------------------------------------------
框查失敗時自動改走舊的「本機快照 → 逐筆線上回填」，`--force-local` 可強制走。

⚠⚠ **fallback 救不了 coagis 全站斷線**（2026-09-04 檢視時發現，先前的說法不夠精確）：

    框查      https://coagis.colife.org.tw/.../CadastralMap_Tiled_<版次>/MapServer
    逐筆回填  https://coagis.colife.org.tw/.../CadastralMap/SOE/MapServer/exts/CoaRESTSOE/...

  **同一台主機、同一份 token、同一個設定檔**（都來自 map.moa.gov.tw 的 config）。
  該站一斷（2026-08-19 湖口案實踩 WinError 10060），兩條路一起斷。
  fallback 真正能救的是**另一種情況**：Tiled MapServer 這一個服務單獨不可用、
  而 SOE 擴充仍活著——例如**版次改名或該服務下架**（版次寫在服務名稱裡、每兩個月
  換一次，這是真實風險），此時逐筆回填仍可跑完。

⚠ fallback 的候選清單受限於本機快照 Ver 111年2月，**快照沒有的地號查不到**，
  成果會有留白（那正是它被降級的原因）；框內覆蓋率會如實報數，交付前務必看。
★ 2026-09-04 起 fallback 不再做「缺口偵測＋子號探測補正」，已移入
  `scripts/legacy/cadastre_snapshot_probe.py`（含七股／潮州的完整脈絡與回歸測試）。

用法（系統 Python，不需 QGIS）：
  python -X utf8 scripts/cadastre_neighbors.py --joined <cadastre/基地.shp>       --out outputs/<案>/cadastre [--buffer 50] [--name 鄰地] [--force-local]

輸出：<out>/<name>.shp（欄位同 cadastre_fetch，另加「都計區」）
  ＋ <out>/<name>_coverage_check.json（框內覆蓋率、資料來源、缺口說明）
  ＋ <out>/<name>_neighbors.json（走 fallback 時才有：發現階段的分組與筆數）
"""
from __future__ import annotations

from config_loader import skill_path

import argparse
import json
import os
import sys
import time
import types
import warnings

import geopandas as gpd
import pandas as pd
from shp_trust_utils import find_county_shp
from shapely.geometry import box as _sbox, shape
from shapely.ops import unary_union

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import overlay_placement as op  # noqa: E402  出圖框計算（與 make_maps 同一支）
import moa_parcel_query as mpq  # noqa: E402  ★現行主路徑：出圖框直接向農業部框查

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CAD_DIR = os.path.join(REPO, "地籍圖資")
SKILL = str(skill_path("cadastre-check", "scripts"))
GAP_CHECK_M = 20.0     # 覆蓋率自我檢查的緊鄰帶寬度（小於 --buffer，聚焦「緊鄰」而非全部外擴範圍）
GAP_MIN_AREA = 10.0    # 低於此面積視為浮點誤差/畸零縫隙雜訊，不警告
# ↑ 兩者供 fallback 的 discover() 與 legacy/cadastre_snapshot_probe.py 共用
sys.path.insert(0, HERE)
from cadastre_fetch import _load_api, cmd_fetch  # noqa: E402
# _load_api 與 cadastre_fetch 共用一份（原本兩檔各有幾乎相同的一份）；現僅 legacy 的探測補正會用到。


def _section_name(seg, sub) -> str:
    """本機快照『段』欄不含「段」字、『小段』欄不含「小段」字；查詢與下游 join 要完整組字。

    ★ 實作已統一到 `moa_parcel_query.section_name`（2026-09-04）——框查與本機快照
      兩條路徑必須組出**一字不差**的段名，各寫一份遲早分岔，而分岔的症狀是 join
      靜默落空（不報錯、只是資料對不上）。此處保留薄殼以維持既有呼叫點。
    """
    return mpq.section_name(seg, sub)


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


_TOWN_CACHE: dict[tuple, str] = {}


def _current_town(county: str, town: str) -> str:
    """本機快照的鄉鎮名可能是**改制前舊名**，線上 API 只認現行名 → 整段查詢全滅。

    2026-08-26 員林案實踩：快照（Ver 111年2月）記「員林鎮」，該鎮 2015 年已改制
    「員林市」，農業部 API 回「彰化縣底下查無鄉鎮員林鎮」→ 鄰地一筆都抓不到、
    最終 鄰地.shp 未產出。CLAUDE.md 早記過「快照 鄉鎮 欄有舊制行政區名（員林鎮）」，
    但只有 parse_stage1／match_parcels 這條路徑做了寬鬆比對，⓪a′ 這條沒接上。
    同型還有 頭份鎮→頭份市、六都升格的 板橋市→板橋區 等。

    對法：拿農業部**現行**鄉鎮清單回推——先精確比對（含臺／台正規化），再去尾
    （市/鎮/鄉/區）比對。去尾比對限定在**同一縣市內**才安全：後綴由行政層級決定，
    同縣市不可能同時存在「員林鎮」與「員林市」，故不會誤配到別的鄉鎮。
    ⚠ 這個寬鬆只給鄉鎮用，**不可比照套到段名**——段名去尾比對會把「民權段」配到
    「民權一段」（那是別人的地，見 parcel-roster-verify 的鐵律）。

    查不到或查詢失敗一律原名回傳，不阻斷流程。
    """
    key = (county, town)
    if key in _TOWN_CACHE:
        return _TOWN_CACHE[key]
    out = town
    try:
        if not os.path.isdir(SKILL):
            raise RuntimeError("skill 不在")
        sys.path.insert(0, SKILL)
        import moa  # noqa: PLC0415
        names = moa.towns(moa._pick(moa.counties(), county) or county)
        hit = moa._pick(names, town)
        if not hit:
            stem = moa._norm(town).rstrip("市鎮鄉區")
            cands = [n for n in names if moa._norm(n).rstrip("市鎮鄉區") == stem and stem]
            hit = cands[0] if len(cands) == 1 else None
        if hit and hit != town:
            print(f"  ℹ 鄉鎮改制更名：快照「{town}」→ 現行「{hit}」（依農業部現行清單）")
        out = hit or town
    except Exception:  # noqa: BLE001 — 對不到就用原名，正規化失敗不可阻斷鄰地發現
        pass
    _TOWN_CACHE[key] = out
    return out


def _county_file(county: str) -> str | None:
    return find_county_shp(county, CAD_DIR)


def map_frame(geom):
    """單一幾何的 maxmap 出圖框（與 make_maps 同一支 place_fixed、同參數、含角落避讓）。

    算不出來回 None（呼叫端須退回 buffer 環並警告）。
    """
    try:
        def _isect(x1, y1, x2, y2, _g=geom):
            it = _g.intersection(_sbox(x1, y1, x2, y2))
            return 0.0 if it.is_empty else it.area
        pl = op.place_fixed(_isect, geom.bounds, map_w=op.MAP2_W, map_h=op.MAP2_H)
        fx1, fx2, fy1, fy2 = pl.extent      # extent=(x1,x2,y1,y2)
        return _sbox(fx1, fy1, fx2, fy2)
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠ 出圖框計算失敗（{type(e).__name__}: {e}）")
        return None


def site_frames(site, sitemap: str | None):
    """逐案場出圖框清單 [(案場ID, frame), ...]。無 sitemap 或對不上時回 []。

    ★ 為什麼需要（2026-09-04 麻豆案實見）：`make_maps --sitemap` 是**逐案場**出圖，
      每個案場有**自己的**出圖框，而該框是以該案場幾何置中、含角落避讓——它可能
      伸出「合併基地」框之外（S001 的 287×200 框北緣就超出合併 574×400 框）。只用
      合併框抓地籍，逐案場圖的邊緣會留白（實測 S001 94.48%、S002 95.26%）。
      故 region 必須是「合併框 ∪ 各案場框 ∪ buffer 環」。
    """
    if not sitemap or not os.path.exists(sitemap):
        return []
    try:
        import shp_trust_utils as SU  # noqa: PLC0415
        smap = SU.load_sitemap(sitemap)
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠ sitemap 讀取失敗，逐案場框略過（{type(e).__name__}: {e}）")
        return []
    col_no = next((c for c in ("地號", "20251201_4") if c in site.columns), None)
    if not col_no:
        print("  ⚠ 基地 shp 無地號欄，逐案場框略過")
        return []
    col_seg = next((c for c in ("段", "20251201_3") if c in site.columns), None)
    groups: dict[str, list] = {}
    for _, r in site.iterrows():
        no = SU.normalize_landno(r[col_no])
        seg = SU.norm_seg(r[col_seg]) if col_seg else ""
        hit = smap.get(f"{seg}|{no}") or smap.get(no)
        if not hit:
            continue
        for sid, _nm in hit:
            groups.setdefault(sid, []).append(r.geometry)
    out = []
    for sid in sorted(groups):
        f = map_frame(unary_union(groups[sid]))
        if f is not None:
            out.append((sid, f))
    return out


def compute_region(joined: str, buffer_m: float, sitemap: str | None = None):
    """回 (region, frames, su, site_gdf)：涵蓋範圍、出圖框清單、基地聯集、基地圖層。

    ★ 涵蓋範圍由「maxmap 出圖框」決定（2026-08-23 永康三民段385 案定案）：固定 buffer
      對細長／小基地會抓不滿框（框寬遠大於 buffer）→ 框內較遠宗地留白。出圖框僅由基地
      幾何決定，可先算框、再用框篩地籍。buffer 仍作下限保證緊鄰收入。
    ★ frames 是**清單**：`[("合併", 框), ("S001", 框), ...]`。逐案場出圖時每個案場有
      自己的框且可能伸出合併框外（2026-09-04 麻豆案實見），全部都要納入涵蓋範圍。
    框全算不出來時 frames 回 []，region 退回純 buffer 環（能跑但框內可能留白，會警告）。
    """
    site = gpd.read_file(joined, engine="pyogrio")
    if site.crs and site.crs.to_epsg() != 3826:
        site = site.to_crs(3826)
    su = site.geometry.union_all()
    ring = su.buffer(buffer_m)
    frames = []
    f0 = map_frame(su)
    if f0 is not None:
        frames.append(("合併", f0))
    frames.extend(site_frames(site, sitemap))
    region = ring
    for _n, f in frames:
        region = region.union(f)
    return region, frames, su, site


def frame_coverage(frames, gdf, su) -> dict:
    """逐個出圖框量「被宗地（鄰地＋基地）覆蓋的比例 %」。**新路徑的硬指標**。

    框查回的就是框內全部宗地，覆蓋率理應 100%；不足即代表該處農業部圖層真的沒有
    宗地（水域、未登錄地、離島無資料等），必須明講，不可讓讀圖的人以為那裡沒有地。
    ⚠ 逐案場出圖時要量的是**各案場自己的框**，量合併框會漏掉案場框伸出去的邊緣
      （2026-09-04 麻豆案：合併框 100%，但 S001 只有 94.48%）。
    frames 可傳單一幾何（相容舊呼叫）或 [(名稱, 框), ...]；回 {名稱: 覆蓋率%}。
    """
    if frames is None:
        return {}
    if not isinstance(frames, list):
        frames = [("合併", frames)]
    parts = [g for g in getattr(gdf, "geometry", []) if g is not None and not g.is_empty]
    if su is not None and not su.is_empty:
        parts.append(su)
    u = unary_union(parts) if parts else None
    out = {}
    for name, f in frames:
        if f is None or f.is_empty:
            continue
        out[name] = 0.0 if u is None else u.intersection(f).area / f.area * 100.0
    return out


def discover_online(joined: str, buffer_m: float, version: str | None = None,
                    no_owner: bool = False, sitemap: str | None = None):
    """★現行主路徑：出圖框直接向農業部框查，回 (gdf, region, frames, su)。

    不經本機快照、不必事先知道地號，故不受「快照沒收錄」「整段重測改號」影響
    （2026-09-04 麻豆案定案，詳見模組 docstring）。
    """
    region, frames, su, site = compute_region(joined, buffer_m, sitemap)
    url, ver = mpq.service_url(version)
    x1, y1, x2, y2 = region.bounds
    names = "、".join(n for n, _f in frames) or "無（僅 buffer）"
    print(f"框查：地籍版次 {ver}，範圍 {x2 - x1:,.0f}×{y2 - y1:,.0f} m"
          f"（出圖框：{names}）")
    n = mpq.count_envelope(x1, y1, x2, y2, version)
    print(f"  範圍內宗地 {n} 筆，取回中…")
    recs = mpq.query_region(region, version, progress=print)
    gdf = mpq.to_gdf(recs, ver)
    if len(gdf) == 0:
        raise mpq.MoaQueryError("框查回 0 筆——該範圍農業部圖層無資料（離島？），"
                                "或服務異常")
    # 排除基地自身：與基地重疊達自身 50% 以上視為同一宗地。★用幾何不用地號字串——
    # 重測改號、異體字、段名變更都會讓字串比對失效（本專案反覆踩過的坑）。
    ov = gdf.geometry.intersection(su).area / gdf.geometry.area.replace(0, 1)
    gdf = gdf[ov < 0.5].reset_index(drop=True)
    if not no_owner:
        gdf = mpq.attach_ownership(gdf)
    assert site is not None
    return gdf, region, frames, su


def discover(joined: str, buffer_m: float, county: str | None) -> tuple[dict, object, object]:
    """【fallback】本機快照認地號 → 逐筆線上回填（2026-09-04 起降為備援）。

    回 ({(縣市,鄉鎮,段): [landno,...]}, su, local_check_union, sur)。
    su＝基地聯集幾何；local_check_union＝本機快照在 GAP_CHECK_M 緊鄰帶內的候選
    宗地聯集（含被排除的基地自身候選，供事後覆蓋率比對用，見 check_coverage_gap）。

    ⚠ 本路徑的候選清單受限於快照（Ver 111年2月），快照沒有的地號不會被查到；
      只在框查失敗（農業部斷線）或 --force-local 時使用。
    """
    cadlib = mpq._cadlib()   # 與框查共用同一支載入器
    region, _frames, su, site = compute_region(joined, buffer_m)

    if not county:
        for c in ("縣市", "縣市名稱"):
            if c in site.columns:
                county = str(site.iloc[0][c] or "").strip()
                break
    p = _county_file(county or "")
    if not p:
        raise SystemExit(f"找不到 地籍圖資\\{county}.shp（周邊地籍發現需本機地籍快照做"
                         "空間識別；只用它認地號身分，不用它的屬性）")

    sur_all = gpd.read_file(p, engine="pyogrio", bbox=region.bounds)
    sur_all = sur_all[sur_all.geometry.intersects(region)]
    # 排除基地本身：與基地重疊面積達自身 50% 以上視為同一宗地（浮點誤差/版本差不可用
    # 地號字串比對，改用幾何重疊比例判斷，較穩健）
    sur_all = sur_all.copy()
    # 快照鄉鎮名可能是改制前舊名 → 一次正規化成現行名，讓分組鍵、母號縮水偵測與線上
    # 查詢三者用同一套名稱（只改這裡，下游全部沿用，見 _current_town）。
    if "鄉鎮" in sur_all.columns:
        sur_all["鄉鎮"] = [
            _current_town(county or "", str(t or "").strip())
            for t in sur_all["鄉鎮"]]
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
    # ★ 逐筆重試沒回來的地號（2026-08-27 麻豆客子寮段案）：整段成功不代表每筆都成功。
    #   該案 149 筆送查、只回 140 筆，圖上基地旁大片留白。逐筆看失敗原因分兩類：
    #     ① **暫時性**——重測改號要退回 MOI 新舊地號 API，該 API 會 read operation
    #        timed out。實測重試即成功（港子尾段11 → 客子寮段314 水利用地）。
    #     ② **農業部資料本身沒有**——新舊地號對照解得出新地號，但農業部查無其幾何
    #        （港子尾段18→客子寮段380、19-1→378、16-3→375；API 回別筆地，被 14 碼
    #        閘門正確拒絕）。這類重試無效，只能如實報出。
    #   ⚠ 缺的宗地一律**明確列出**，不可靜默留白——留白會被讀成「那裡沒有宗地」。
    def _returned(df):
        """已回來的「請求地號」集合。★重測改號時回傳的是**新地號**（港子尾段11 →
        客子寮段314），只比 `地號` 欄會把它判成沒回來、整批重抓（2026-08-27 實錯：
        149 筆送查卻併出 266 筆）。`cadastre_fetch` 有記 `原地號`，優先用它。"""
        out = set()
        for col in ("原地號", "地號"):
            if col in df.columns:
                out |= {str(x).strip() for x in df[col] if str(x).strip()
                        and str(x).strip().lower() != "nan"}
        return out

    got = _returned(merged)
    missing = {k: [ln for ln in v if str(ln).strip() not in got]
               for k, v in groups.items()}
    missing = {k: v for k, v in missing.items() if v}
    if missing:
        n_miss = sum(len(v) for v in missing.values())
        print(f"\n→ 首輪有 {n_miss} 筆未回，逐段重試一次（多為新舊地號 API 逾時）…")
        retried = []
        for (county, town, section), landnos in missing.items():
            tag = f"_{name}_{county}{town}{section}_retry".replace("/", "")
            a2 = types.SimpleNamespace(
                county=county, town=town, section=section, landnos=landnos,
                version=None, no_owner=True, out=out_dir, name=tag)
            try:
                cmd_fetch(a2)
            except SystemExit as e:      # cmd_fetch 對 0 成功筆數丟 SystemExit
                print(f"  ⚠ {section} 重試仍無成功筆數（{e}）")
            except Exception as e:  # noqa: BLE001
                print(f"  ⚠ {section} 重試仍失敗（{type(e).__name__}）")
            shp2 = os.path.join(out_dir, f"{tag}.shp")
            if os.path.exists(shp2):
                retried.append(gpd.read_file(shp2, engine="pyogrio"))
        if retried:
            add = pd.concat(retried, ignore_index=True)
            before = len(merged)
            merged = gpd.GeoDataFrame(
                pd.concat([merged, add], ignore_index=True), crs=merged.crs)
            if "地號" in merged.columns:      # 去重：同一宗地兩輪都回時只留一筆
                merged = merged.drop_duplicates(subset=["段", "地號"], keep="first")
                merged = gpd.GeoDataFrame(merged.reset_index(drop=True), crs=add.crs)
            print(f"  ✔ 重試救回 {len(merged) - before} 筆")
        got = _returned(merged)
        still = {"/".join(k): [ln for ln in v if str(ln).strip() not in got]
                 for k, v in missing.items()}
        still = {k: v for k, v in still.items() if v}
        if still:
            n_still = sum(len(v) for v in still.values())
            print(f"  ✖ 仍有 {n_still} 筆線上取不到（農業部無其幾何，非本工具可解）：")
            for k2, v2 in still.items():
                print(f"      {k2}：{'、'.join(map(str, v2))}")
            print("    → 圖面該處會留白。如需完整圖面，須上 TCDGIS 平台取官方 shp"
                  "（需登入），或向地政事務所調閱。")
            with open(os.path.join(out_dir, f"{name}_線上取不到.json"),
                      "w", encoding="utf-8") as f:
                json.dump(still, f, ensure_ascii=False, indent=1)

    out_shp = _safe_to_file(merged, os.path.join(out_dir, f"{name}.shp"))
    print(f"\n→ {out_shp}（合併 {len(groups)} 段、共 {len(merged)} 筆鄰地）")
    return out_shp, merged


def run_local(a):
    """【fallback】舊路徑：本機快照認地號 → 逐筆線上回填。

    只在框查失敗或 --force-local 時走。回 (merged_gdf, su, frames, info)。

    ⚠ **這條路救不了 coagis 全站斷線**：框查與本路徑的逐筆查詢打的是**同一台主機**
      （coagis.colife.org.tw）、同一份 token、同一個設定檔。它只在「Tiled MapServer
      這一個服務單獨不可用、而 SOE 擴充仍活著」時有效——例如版次改名或該服務下架
      （版次寫在服務名稱裡，每兩個月換一次，這是真實風險）。
    ⚠ 本路徑的候選清單受限於本機快照 Ver 111年2月，**快照沒有的地號查不到**，
      成果會有留白；框內覆蓋率會如實報出來，交付前務必看那個數字。
    ★ 2026-09-04 起不再做「缺口偵測＋子號探測補正」（已移入
      `scripts/legacy/cadastre_snapshot_probe.py`）：那套機械的前提是「候選清單受限於
      快照、需要自動猜補」，走到 fallback 時本來就接受降級，而覆蓋率會把留白講清楚，
      猜補只會讓人以為資料已經完整。
    """
    region, frames, su0, _site = compute_region(a.joined, a.buffer, a.sitemap)
    assert region is not None and su0 is not None
    groups, su, _local_check_union, _sur = discover(a.joined, a.buffer, a.county)
    total = sum(len(v) for v in groups.values())
    print(f"發現階段（本機快照 Ver 111年2月）：出圖框涵蓋（buffer≥{a.buffer:.0f}m 下限）"
          f"共 {total} 筆，分 {len(groups)} 組（縣市/鄉鎮/段不同者不能同一次查詢）")
    for k, v in groups.items():
        print(f"  {'/'.join(k)}：{len(v)} 筆　{v[:10]}{'...' if len(v) > 10 else ''}")

    with open(os.path.join(a.out, f"{a.name}_neighbors.json"), "w", encoding="utf-8") as f:
        json.dump({"/".join(k): v for k, v in groups.items()}, f,
                  ensure_ascii=False, indent=1)

    _out_shp, merged = fetch_all(groups, a.out, a.name)
    info = {"資料來源": "本機快照候選＋逐筆線上回填（fallback，受限於 Ver 111年2月 快照）"}
    return merged, su, frames, info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True, help="基地 shp（cadastre/基地.shp 或申請範圍）")
    ap.add_argument("--out", required=True, help="輸出資料夾")
    ap.add_argument("--buffer", type=float, default=50.0,
                    help="緊鄰下限外擴公尺（預設 50）；實際涵蓋以 maxmap 出圖框為準"
                         "（框大於此值時以框為準，確保框內不留白）")
    ap.add_argument("--county", default=None,
                    help="縣市（未給則從基地 shp 屬性讀；僅 fallback 路徑需要）")
    ap.add_argument("--name", default="鄰地")
    ap.add_argument("--version", default=None, help="地籍版次，省略＝最新（建議省略）")
    ap.add_argument("--no-owner", action="store_true",
                    help="不查權屬（預設會用 public_land.py 免登入查公有土地名冊）")
    ap.add_argument("--force-local", action="store_true",
                    help="強制走 fallback 舊路徑（本機快照＋逐筆回填），用於重現舊成果或比對")
    ap.add_argument("--sitemap", default=None, metavar="sitemap.csv",
                    help="逐案場出圖用的 sitemap（案場定義/sitemap.csv）。★給了才會把"
                         "**各案場自己的出圖框**納入涵蓋範圍——逐案場圖的框以該案場置中、"
                         "可能伸出合併基地框之外，只用合併框會讓案場圖邊緣留白"
                         "（2026-09-04 麻豆案實見 S001 僅 94.48%%）")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    src, merged, su, frame, info = None, None, None, None, {}
    if not a.force_local:
        try:
            merged, _region, frames, su = discover_online(
                a.joined, a.buffer, a.version, a.no_owner, a.sitemap)
            src = "農業部框查（出圖框空間查詢）"
            info = {"資料來源": src, "版次": str(merged["版次"].iloc[0]) if len(merged) else ""}
        except Exception as e:  # noqa: BLE001
            # ★ 農業部 coagis 會間歇性斷線（2026-08-19 湖口案實踩）。斷線不是程式錯誤，
            #   改走 fallback 讓案子跑得完，但**必須明講資料來源已降級**。
            print(f"\n⚠ 框查失敗（{type(e).__name__}: {e}）"
                  "\n  → 改走 fallback：本機快照候選＋逐筆線上回填。"
                  "\n    ⚠ 逐筆回填打的是**同一台主機**（coagis），該站全站斷線時"
                  "本路徑同樣會失敗；"
                  "\n       能救的是「Tiled 服務單獨不可用（如版次改名）」那種情況。"
                  "\n    ⚠ 候選清單受限於快照 Ver 111年2月，快照未收錄的地號查不到、"
                  "圖面會留白；請看下方框內覆蓋率，服務恢復後建議重跑。")
    if merged is None:
        merged, su, frames, info = run_local(a)
        src = info["資料來源"]
    else:
        out_shp = _safe_to_file(mpq.drop_internal(merged),
                                os.path.join(a.out, f"{a.name}.shp"))
        print(f"\n→ {out_shp}（{len(merged)} 筆鄰地，來源：{src}）")

    # ★ 框內覆蓋率＝新路徑的硬指標（舊路徑也一併量，兩者可直接比較）。
    #   逐案場出圖時必須逐框量——合併框 100% 不代表各案場框都滿（麻豆案實見）。
    covs = frame_coverage(frames, merged, su)
    info["框內覆蓋率_pct"] = {k: round(v, 2) for k, v in covs.items()}
    if not covs:
        info["結論"] = "出圖框不可用，未計算覆蓋率"
        print("⚠ 出圖框不可用，未計算覆蓋率")
    else:
        worst = min(covs, key=covs.get)
        for k, v in covs.items():
            print(f"  框「{k}」覆蓋率 {v:.2f}%")
        if covs[worst] >= 99.95:
            info["結論"] = "全部出圖框內無留白"
            print("✔ 全部出圖框已填滿，無留白")
        else:
            msg = (f"⚠ 出圖框「{worst}」覆蓋率僅 {covs[worst]:.2f}%，有 "
                   f"{100 - covs[worst]:.2f}% 留白。成因可能是：①該處農業部地籍圖層"
                   "確實無宗地（水域、未登錄地）②離島（本服務 Penghu 層以外無資料）"
                   "③涵蓋範圍未納入該案場的出圖框（逐案場出圖請加 --sitemap）。"
                   "**留白處必須在交付文件說明，不可讓讀圖者誤以為那裡沒有土地**；"
                   "需要完整圖面請上 TCDGIS 取官方 shp（需登入）。")
            info["結論"] = msg
            print(msg)

    with open(os.path.join(a.out, f"{a.name}_coverage_check.json"), "w",
              encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
