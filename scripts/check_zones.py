# -*- coding: utf-8 -*-
"""禁止事由檢核引擎（YAML 驅動）— 評估「第0步」的機械化

★ 案件成果沉澱機制（2026-07-29 建立）：
  以前每個案子手寫一支 `_check_xxx.py`（安南案的 _check_tuzichang / _check_breed），
  法條清單存在寫的人腦中，漏了就漏了（養殖漁業生產區實錯）。
  現在：**清單寫在 config/檢核清單/*.yaml**，本引擎逐項執行——
  換用途只寫 YAML 不寫碼；新案發現的新條款／新圖層，加進 YAML 即成為所有後案的資產。

用法（系統 Python 即可，不需 QGIS）：
  python scripts/check_zones.py --joined <面積join.shp> \
      --checklist 土資場_臺南市 _共通_農業用地變更 \
      [--sites <臨路評估/sites.shp>] [--out <案件資料夾>]

YAML 項目類型：
  overlay      圖層套疊（觸及→依「級別」判 ╳/△），逐案場輸出面積與佔比
  distance     距離門檻（「距離_m」內有無圖徵）
  area         面積門檻（非山坡地/山坡地雙門檻，山坡地屬性由「山坡地圖層」套疊判定）
  attribute    屬性檢核（基地 shp 某欄位含某值，例：使分＝特定農業區）
  settlement   十戶以上村莊或聚落（門牌坐標開放資料聚類實算，見 settlement_check.py）
  api_distance 國土測繪中心環域 API（醫療／文教／鄰避設施，見 nlsc_facility.py）
  inquiry      圖資查不到、必須函詢（明列機關，輸出到「須函詢」清單——不臆測）
  note         純敘述提醒（無圖資可套疊，判斷已寫在備註裡，如時程約束、程序要件）
  osm_distance OSM Overpass 單類別距離查詢（目前僅「營區」）。判定恆為須函詢，
               OSM 結果只附參考距離，不可取代官方確認（見 osm_sensitive_poi.py）

引擎內建的教訓（皆本專案實錯）：
  ・逐圖層印**檔案日期**，逾「陳舊警告_年」（預設 3 年）即警告
    →「圖資一律確認版本年度」：安南案用 2016 年舊檔把「100%涉及」判成「未涉及」
  ・無 .prj 時假定 CRS 前先驗 bounds 落在台灣本島 TWD97 TM2 範圍，不符即硬停
  ・編碼自動嘗試 utf-8 / big5 / cp950（BREED47 舊檔是 UTF-8、114 年版是 Big5）
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import sys

import geopandas as gpd
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # settlement_check / nlsc_facility
CFG_DIR = os.path.join(REPO, "config", "檢核清單")
TWD97_BOUNDS = (60_000, 2_400_000, 420_000, 2_900_000)  # 台灣本島 TM2 粗略範圍

# ★ 法令檢核規則庫已知缺口（2026-08-06 建）：v9 草稿本身載明還缺這幾類，若案子
#   剛好用到，規則庫查不到不代表真的沒有規定——只是還沒建表，須另行查證原始法規。
#   補進規則庫後從這裡刪除該項。
RULEBASE_KNOWN_GAPS = [
    "水土保持法§12（僅個別案子如龍崎案在專屬 YAML 裡手動補過，尚未併入共通規則庫）",
    "高雄市都市計畫土地使用分區管制規則§18附表一",
    "縣市自訂可擴充層（各縣市地方自治條例；已建：臺南市土資場、"
    "臺南市農業設施容許使用委任區公所 LOCAL-TNN-001。**他縣市委任規定不可沿用臺南市那筆**）",
]
# 2026-08-07 移除「農業設施容許使用審查辦法」一項：規則庫 v9d 已建入 AGF-001~005
#   （M0020022 §6/§7/§12/§13附表一農糧產品加工室/§4§5），該缺口已填。
# ⚠ 另有兩筆「已建但核對狀態未達權威」，不列為缺口但引用前要注意（詳規則庫導讀）：
#   AGF-FN01（農糧字第1021046207號函，無公開全文）、
#   LOCAL-TNN-001（府農務字第1130169295B號公告，PDF 連結已 HTTP 410）。


def _county_variants(name: str) -> list[str]:
    out = [name]
    if "臺" in name:
        out.append(name.replace("臺", "台"))
    if "台" in name:
        out.append(name.replace("台", "臺"))
    return out


def _resolve_paths(item: dict, county: str) -> list[str]:
    raw = item.get("圖層") or []
    if isinstance(raw, str):
        raw = [raw]
    got = []
    for tmpl in raw:
        cands = ([tmpl.format(county=c) for c in _county_variants(county)]
                 if "{county}" in tmpl else [tmpl])
        for p in cands:
            full = p if os.path.isabs(p) or p.startswith("\\\\") \
                else os.path.join(REPO, p)
            if os.path.exists(full) and full not in got:
                got.append(full)
    return got


def _load_layer(path: str, item: dict, log: list) -> gpd.GeoDataFrame | None:
    encs = [item.get("編碼"), "utf-8", "big5", "cp950", None]
    g = None
    for enc in encs:
        try:
            g = gpd.read_file(path, engine="pyogrio",
                              **({"encoding": enc} if enc else {}))
            break
        except Exception:  # noqa: BLE001
            continue
    if g is None:
        log.append(f"    ⚠ 讀取失敗：{path}")
        return None
    # 圖資版本教訓：逐層印檔案日期，過舊即警告
    mtime = datetime.date.fromtimestamp(os.path.getmtime(path))
    age_y = (datetime.date.today() - mtime).days / 365.25
    warn_y = float(item.get("陳舊警告_年", 3))
    stale = f"　⚠ 已逾 {warn_y:.0f} 年，確認是否有新版！" if age_y > warn_y else ""
    log.append(f"    圖資：{os.path.basename(path)}（{mtime}）{stale}")
    if g.crs is None:
        assume = item.get("無prj假定", 3826)
        b = g.total_bounds
        if not (TWD97_BOUNDS[0] <= b[0] and b[2] <= TWD97_BOUNDS[2]
                and TWD97_BOUNDS[1] <= b[1] and b[3] <= TWD97_BOUNDS[3]):
            log.append(f"    ✗ 無 .prj 且 bounds 不像 TWD97 TM2（{b}）——"
                       f"不得假定 EPSG:{assume}，本項改列須人工確認")
            return None
        g = g.set_crs(assume)
        log.append(f"    無 .prj → bounds 驗證後假定 EPSG:{assume}")
    elif g.crs.to_epsg() != 3826:
        g = g.to_crs(3826)
    return g


def _per_site(sites, geom_fn) -> list[str]:
    out = []
    if sites is None:
        return out
    for _, s in sites.iterrows():
        out.append(geom_fn(s["site_id"], s.geometry))
    return out


# ★ 「反向」門檻方向的關鍵字提示（2026-08-06 建）——只是提示，不是判定，因為龍崎案
#   曾經 YAML 已寫 反向:true 但引擎忽略、又曾在別處忘了寫。條號/備註含這些字樣時，
#   跟目前的 反向 設定對照一下，兜不起來就在 log 印一行提醒人工核對。
_CEILING_HINTS = ("以上應", "以上者", "以上實施", "累積")   # 逾門檻才觸發 → 該是 反向:true
_FLOOR_HINTS = ("不得少於", "下限", "至少", "不得低於")     # 達門檻才符合 → 該是 反向:false/省略


def _reverse_hint(item, rev, log):
    text = f"{item.get('條號', '')} {item.get('備註', '')}"
    ceiling = any(k in text for k in _CEILING_HINTS)
    floor = any(k in text for k in _FLOOR_HINTS)
    if ceiling and not rev:
        log.append("    ⚠ 條號/備註含「逾門檻觸發」字樣但 反向 未設為 true，"
                    "請核對此項是否該用 反向:true（例：環評40公頃以上）")
    elif floor and rev:
        log.append("    ⚠ 條號/備註含「下限/不得少於」字樣但 反向 設為 true，"
                    "請核對此項門檻方向是否設反了")


def run_item(item, site_gdf, su, tot, sites, county, log, results):
    typ = item["類型"]
    name = item.get("名稱", item.get("id", "?"))
    ref = item.get("條號", "")
    mark = item.get("級別", "△")
    rec = {"名稱": name, "條號": ref, "類型": typ, "級別": mark}

    # ★ 翻轉條件（2026-08-06 建，安定六塊寮段案教訓）：機械判定容易停在條文表面，
    #   有已知但書/例外款的項目在 YAML 加這個欄位，不管判定結果是什麼都提醒查核，
    #   避免像 sfa_zone 那樣「條文原則╳，但有行政院核定計畫時該改判△」被漏讀。
    if item.get("翻轉條件"):
        log.append(f"  ⚠ {name}（{ref}）有已知翻轉條件，開案先確認是否成立："
                   f"{item['翻轉條件']}")

    # ★ 2026-08-06 修：note 型（純敘述提醒，無圖資可套疊，判斷已寫在備註裡）先前
    #   不是文件列的 7 種類型之一，YAML 卻有 4 項在用（社福設施_臺南市.yaml），
    #   之前會落到最後 overlay/distance 的通用分支、印成「找不到圖層檔案」——
    #   看起來像資料缺失，實際上是條款本身就沒有可套疊的圖資，是引擎沒認得這個型別。
    #   後壁南海段社福案重跑時發現（2026-08-06）。
    if typ == "note":
        log.append(f"  ・{name}（{ref}）：{str(item.get('備註', '')).strip()}")
        rec["判定"] = mark
        results.append(rec)
        return

    if typ == "inquiry":
        log.append(f"  ？ {name}（{ref}）：圖資查不到 → 須函詢 {item.get('機關', '主管機關')}"
                   f"{('。' + item['備註']) if item.get('備註') else ''}")
        rec["判定"] = "須函詢"
        results.append(rec)
        return

    if typ == "attribute":
        # 欄位可給多個候選名（tcdgis 匯出叫「使用分區」、match_parcels 叫「使分」）
        cands = item["欄位"] if isinstance(item["欄位"], list) else [item["欄位"]]
        fld = next((f for f in cands if f in site_gdf.columns), None)
        val = item["含值"]
        if fld is None:
            log.append(f"  ？ {name}（{ref}）：基地 shp 無 {cands} 任一欄 → 須人工確認")
            rec["判定"] = "欄位缺失須確認"
        else:
            hit = site_gdf[site_gdf[fld].astype(str).str.contains(val, na=False)]
            if len(hit):
                log.append(f"  ✗ {name}（{ref}）：{len(hit)} 筆 {fld} 含「{val}」"
                           f"（地號 {sorted(hit['地號'].astype(str))}）→ {mark}")
                rec["判定"] = mark
                # ★ 逐案場拆分（2026-08-06 建）：多案場批次跑時，overlay 型早就有
                #   逐案場面積，attribute 型之前只知道「整批有沒有」、不知道是哪個
                #   案場，多案場案子要另外人工比對。用地號跟 sites 空間relate回去。
                persite_hit = _per_site(sites, lambda sid, g: sid
                                        if hit.geometry.intersects(g).any() else None)
                persite_hit = [s for s in persite_hit if s]
                if persite_hit:
                    log.append(f"      涉及案場：{persite_hit}")
                    rec["逐案場"] = persite_hit
            else:
                log.append(f"  ✓ {name}（{ref}）：無 {fld} 含「{val}」")
                rec["判定"] = "未觸及"
        results.append(rec)
        return

    if typ == "settlement":
        # 「十戶以上之村莊或聚落」：門牌點聚類→群內達門檻才算聚落→量基地到該聚落最短距離
        # ★ 不是「數 N 公尺內幾個門牌」，兩者結論可能相反（見 settlement_check.py 開頭）
        import settlement_check as sc
        dist = float(item.get("距離_m", 200))
        min_n = int(item.get("最少戶數", 10))
        eps = item.get("群聚門檻_m", [30, 50, 100])
        eps = eps if isinstance(eps, list) else [eps]
        log.append(f"  ・{name}（{ref}）：{min_n} 戶以上聚落 > {dist:,.0f}m")
        try:
            r = sc.check(su, sites, county, dist, min_n, [float(e) for e in eps],
                         log, item.get("門牌資料"))
        except FileNotFoundError as e:  # noqa: PERF203
            log.append(f"    ？ {e} → 本項改列須人工確認")
            rec["判定"] = "缺門牌資料須確認"
            results.append(rec)
            return
        v = r["全案"]["判定"]
        rec["判定"] = mark if v == "觸及" else ("未觸及" if v == "未觸及" else "須人工確認")
        rec["聚落"] = r
        log.append(f"    → {name}：{rec['判定']}")
        # ★ 逐案場拆分（2026-08-06 建）：settlement_check.check() 本來就算了
        #   逐案場（見 r["逐案場"]），只是原本沒印到 log/txt，只藏在 json 裡，
        #   多案場批次跑時人工要另外翻 json 才知道是哪個案場觸及。改成順手印出。
        for sid, sr in r.get("逐案場", {}).items():
            if sr.get("判定") == "觸及":
                log.append(f"      案場 {sid}：觸及")
        results.append(rec)
        return

    if typ == "api_distance":
        import nlsc_facility as nf
        dist = float(item.get("距離_m", 200))
        eps_list = item.get("端點", ["med", "edu"])
        log.append(f"  ・{name}（{ref}）：門檻 {dist:,.0f}m（設施到基地面實距）")
        r = nf.nearest(su, eps_list, dist, log)
        hit = [k for k, v in r.items() if v["判定"] == "觸及"]
        fail = [k for k, v in r.items() if v["判定"] == "API失敗須確認"]
        if hit:
            rec["判定"] = "參考" if mark == "參考" else mark
        elif fail:
            rec["判定"] = "API失敗須確認"
        else:
            rec["判定"] = "未觸及"
        rec["設施"] = r
        log.append(f"    → {name}：{rec['判定']}")
        # ★ 逐案場拆分（2026-08-06 建）：只在整案已觸及時才逐案場查，避免對每個
        #   案場都打一次國土測繪中心 API（多案場批次案無謂增加外部呼叫次數）。
        if hit and sites is not None:
            persite_hit = []
            for _, s in sites.iterrows():
                rs = nf.nearest(s.geometry, eps_list, dist, log)
                if any(v["判定"] == "觸及" for v in rs.values()):
                    persite_hit.append(s["site_id"])
            if persite_hit:
                log.append(f"      涉及案場：{persite_hit}")
                rec["逐案場"] = persite_hit
        results.append(rec)
        return

    if typ == "osm_distance":
        # OSM Overpass 單類別距離查詢（2026-08-06 建）——目前僅「營區」：機密設施無
        # 公開權威圖資，NLSC 環域 API 也不涵蓋（見 api_distance），OSM 群眾協作資料
        # 可當參考距離，但**判定恆為須函詢**，不因 OSM 查無就判未觸及（見
        # osm_sensitive_poi.nearest_category docstring）。
        import osm_sensitive_poi as osp
        dist = float(item.get("距離_m", 200))
        category = item.get("OSM類別", "營區")
        log.append(f"  ・{name}（{ref}）：門檻 {dist:,.0f}m（OSM 參考距離，非官方確認）")
        r = osp.nearest_category(su, category, dist, log)
        rec["判定"] = "須函詢"
        rec["OSM參考"] = r
        log.append(f"    → {name}：須函詢（OSM 僅供參考，見上）")
        results.append(rec)
        return

    if typ == "area":
        # 山坡地屬性 → 決定門檻（area 型的圖層鍵是「山坡地圖層」，不走通用 paths 檢查）
        slope_paths = _resolve_paths({"圖層": item.get("山坡地圖層", [])}, county)
        in_slope = False
        for p in slope_paths:
            g = _load_layer(p, item, log)
            if g is not None and len(g):
                in_slope = bool(g.geometry.intersects(su).any())
        th = item["山坡地_公頃"] if in_slope else item["非山坡地_公頃"]
        ha = tot / 10000
        # ★ 兩種相反的門檻語意，`反向: true` 切換（2026-08-02 龍崎光電案補）：
        #   預設（反向 false）＝「**達**門檻才符合」——如管制規則§52-1 山坡地 ≥10 公頃、
        #     土資場自治條例§18①1 面積下限。
        #   反向 true＝「**逾**門檻即觸發」——如環評法§5Ⅲ⑨「40 公頃以上應實施環評」、
        #     各種面積上限。沒有這個開關時只能寫成前者，輸出會印成「✓ 符合」，
        #     與「逾門檻＝應辦環評」的真意完全相反（本案實踩，YAML 已寫 反向 但引擎忽略）。
        rev = bool(item.get("反向"))
        _reverse_hint(item, rev, log)
        ok = (ha < th) if rev else (ha >= th)
        how = f"逾 {th} 公頃即觸發" if rev else f"門檻 {th} 公頃"
        res = ("未逾門檻" if ok else f"**逾門檻**→{mark}") if rev else \
              ("符合" if ok else mark)
        log.append(f"  {'✓' if ok else '✗'} {name}（{ref}）：基地 {ha:.4f} 公頃、"
                   f"{'位於' if in_slope else '非'}山坡地 → {how} → {res}")
        rec["判定"] = ("未逾門檻" if ok else mark) if rev else ("符合" if ok else mark)
        rec["公頃"] = round(ha, 4)
        rec["門檻公頃"] = th
        rec["反向"] = rev
        if not ok and item.get("備註"):
            log.append("      " + str(item["備註"]).strip().replace("\n", " "))
        results.append(rec)
        return

    # overlay / distance：合併所有路徑的圖層
    paths = _resolve_paths(item, county)
    if not paths:
        log.append(f"  ？ {name}（{ref}）：找不到圖層檔案 → 須補圖資或人工確認"
                   f"（樣板：{item.get('圖層')}）")
        rec["判定"] = "缺圖資須確認"
        results.append(rec)
        return
    hit_area = 0.0
    nearest = None
    persite = {}
    for p in paths:
        g = _load_layer(p, item, log)
        if g is None or len(g) == 0:
            continue
        bbox_target = su.buffer(item.get("距離_m", 0) + 50) if typ == "distance" else su
        cand = g[g.geometry.intersects(bbox_target.envelope.buffer(1000))]
        if typ == "overlay":
            ov = cand.geometry.intersection(su).area
            hit_area += float(ov.sum())
            d = float(g.geometry.distance(su).min()) if len(g) else None
            nearest = d if nearest is None else min(nearest, d)
            if sites is not None and float(ov.sum()) > 1:
                for _, s in sites.iterrows():
                    a2 = float(cand.geometry.intersection(s.geometry).area.sum())
                    persite[s["site_id"]] = persite.get(s["site_id"], 0.0) + a2
        else:  # distance
            dist = item["距離_m"]
            within = cand[cand.geometry.distance(su) <= dist]
            hit_area += len(within)
            d = float(g.geometry.distance(su).min()) if len(g) else None
            nearest = d if nearest is None else min(nearest, d)

    if typ == "overlay":
        if hit_area > 1:
            pct = 100 * hit_area / tot
            log.append(f"  ✗ {name}（{ref}）：套疊 {hit_area:,.2f} m²"
                       f"（占基地 {pct:.1f}%）→ {mark}"
                       f"{('。' + item['備註']) if item.get('備註') else ''}")
            for sid, a2 in persite.items():
                log.append(f"      {sid}：{a2:,.2f} m²")
            rec["判定"] = mark
            rec["套疊_m2"] = round(hit_area, 2)
            rec["逐案場"] = {k: round(v, 2) for k, v in persite.items()}
        else:
            near_s = f"（最近 {nearest:,.0f} m）" if nearest is not None else ""
            log.append(f"  ✓ {name}（{ref}）：未觸及{near_s}")
            rec["判定"] = "未觸及"
            rec["最近_m"] = round(nearest, 1) if nearest is not None else None
    else:
        dist = item["距離_m"]
        if hit_area:
            log.append(f"  ✗ {name}（{ref}）：{dist:,.0f} m 內有 {int(hit_area)} 筆"
                       f"（最近 {nearest:,.1f} m）→ {mark}")
            rec["判定"] = mark
        else:
            near_s = f"（最近 {nearest:,.0f} m）" if nearest is not None else ""
            log.append(f"  ✓ {name}（{ref}）：{dist:,.0f} m 內無{near_s}")
            rec["判定"] = "未觸及"
    results.append(rec)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--joined", required=True, help="基地 shp（②的面積join）")
    ap.add_argument("--checklist", nargs="+", required=True,
                    help="config/檢核清單/ 下的 yaml 名（免副檔名，可多份）")
    ap.add_argument("--sites", default=None, help="案場面 shp（有則逐案場輸出）")
    ap.add_argument("--out", default=None, help="輸出資料夾（預設＝基地 shp 上兩層）")
    a = ap.parse_args()

    site = gpd.read_file(a.joined, engine="pyogrio")
    su = site.geometry.union_all()
    # ★ 基地面積用 **dissolve 後的面積**，不可逐筆相加（2026-08-02 龍崎光電案實踩）：
    #   官方界址環對「內含飛地」的宗地，回的是**含飛地在內**的整圈範圍——龍崎案
    #   112-19 的環 100% 包住 108／109／140-5／140-6／140-7 五筆獨立宗地，112-17 包住 60。
    #   逐筆相加會把飛地重複計一次（本案多算 8,140 ㎡），分母被灌大 →
    #   **套疊占比被系統性低估**（山坡地實為 100%，卻印成 98.5%），
    #   而「全區位於」與「部分位於」在法律論述上差很多。
    #   佐證：扣除飛地後 112-19 的圖登偏差由 +9.41% 降到 +2.04%、112-17 由 +0.32% 降到 +0.08%。
    tot = float(su.area)
    raw = float(site.area.sum())
    enclave_note = None
    if raw - tot > max(1.0, tot * 1e-6):
        enclave_note = (f"⚠ 宗地幾何含內含飛地或重疊：逐筆相加 {raw:,.2f} m²、"
                        f"dissolve 後 {tot:,.2f} m²（差 {raw - tot:,.2f}）。"
                        f"**以 dissolve 面積為基地面積**，避免飛地被重複計入分母。")
    sites = gpd.read_file(a.sites, engine="pyogrio") if a.sites else None
    county = ""
    for c in ("縣市名稱", "縣市"):
        if c in site.columns:
            county = str(site.iloc[0][c] or "").strip()
            break
    out_dir = a.out or os.path.dirname(os.path.dirname(os.path.abspath(a.joined)))

    log, results = [], []
    log.append(f"基地 {len(site)} 筆、{tot:,.2f} m² = {tot/10000:.4f} 公頃　縣市：{county}")
    if enclave_note:
        log.append(enclave_note)
    log.append(f"檢核清單：{a.checklist}\n")
    loaded_cfgs = {}
    for cl in a.checklist:
        p = os.path.join(CFG_DIR, cl + ".yaml")
        if not os.path.exists(p):
            raise SystemExit(f"找不到檢核清單：{p}")
        cfg = yaml.safe_load(io.open(p, encoding="utf-8"))
        loaded_cfgs[cl] = cfg
        log.append(f"== {cfg.get('用途', cl)} ==（{cl}.yaml"
                   f"{('，' + cfg['法源版本']) if cfg.get('法源版本') else ''}）")
        # ★ 法源版本陳舊警告（2026-08-06 建，仿圖資的「陳舊警告_年」機制）：
        #   圖資有版本過舊提醒，法條文字本身（門檻數字/條號）修法後卻沒有——
        #   全靠「查了新法規？」這句人工觸發。YAML 加 法源查核日期 才能提醒。
        chk_date = cfg.get("法源查核日期")
        if chk_date:
            try:
                d = datetime.date.fromisoformat(str(chk_date))
                age_y = (datetime.date.today() - d).days / 365.25
                warn_y = float(cfg.get("法源陳舊警告_年", 2))
                if age_y > warn_y:
                    log.append(f"    ⚠ 法源查核日期 {d} 已逾 {warn_y:.0f} 年，"
                               f"建議核對母法/要點是否已修正")
            except ValueError:
                log.append(f"    ⚠ 法源查核日期格式錯誤：{chk_date}（應為 YYYY-MM-DD）")
        else:
            log.append("    ？ 本清單未標 法源查核日期，無法判斷法源是否過舊")
        for item in cfg.get("檢核項目", []):
            run_item(item, site, su, tot, sites, county, log, results)
        log.append("")

    # ★ 搭配清單提醒（2026-08-06 建，社福案漏帶 _共通_農業用地變更 的假設情境）：
    #   只提醒不擋——某些情境確實只想跑局部清單。
    for cl, cfg in loaded_cfgs.items():
        for need in cfg.get("搭配清單", []) or []:
            if need not in a.checklist:
                log.append(f"⚠ {cl}.yaml 建議搭配 {need} 一起跑，本次未帶入"
                           f"（若為刻意省略可忽略）")

    # 總結
    bad = [r for r in results if r.get("判定") in ("╳",)]
    warn = [r for r in results if r.get("判定") in ("△",)]
    ask = [r for r in results if "函詢" in str(r.get("判定")) or "確認" in str(r.get("判定"))]
    log.append("== 總結 ==")
    log.append(f"  ╳ 級觸及 {len(bad)} 項：{[r['名稱'] for r in bad] or '無'}")
    log.append(f"  △ 級觸及 {len(warn)} 項：{[r['名稱'] for r in warn] or '無'}")
    log.append(f"  須函詢／須確認 {len(ask)} 項：{[r['名稱'] for r in ask] or '無'}")
    if bad:
        log.append("  ★ 有 ╳ 級障礙——依評估順序規則 A1，先解決或確認翻轉條件，"
                   "再投入其他細項檢核")
    log.append("")
    log.append("  法令檢核規則庫已知缺口（若本案用途/縣市剛好落在這幾類，規則庫查不到"
               "不代表沒有規定，須另行查證）：")
    for gap in RULEBASE_KNOWN_GAPS:
        log.append(f"    ・{gap}")

    txt = "\n".join(log)
    print(txt)
    os.makedirs(out_dir, exist_ok=True)
    io.open(os.path.join(out_dir, "禁止事由檢核.txt"), "w",
            encoding="utf-8").write(txt)
    io.open(os.path.join(out_dir, "禁止事由檢核.json"), "w",
            encoding="utf-8").write(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"\n→ {os.path.join(out_dir, '禁止事由檢核.txt')} / .json")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
