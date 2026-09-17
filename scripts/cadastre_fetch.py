# -*- coding: utf-8 -*-
"""橋接 skill `cadastre-check`：免登入取得官方地籍幾何與屬性 → 產線可吃的 shp

★ 用途定位（2026-08-02 實測後定案，務必讀懂再用）
------------------------------------------------------------------
`cadastre-check` 走 **農業部地籍圖資（coagis）＋ 內政部 getLandInfo**，
**免帳號、免驗證碼、無 10.5 秒節流**。布袋306 案七筆逐項對帳結果：

  · 幾何與 TCDGIS 官方 shp **完全相同**——IoU 100.00%、面積差 0.00 ㎡、
    質心位移 0.00 m、頂點數同為 22；七筆的圖面積全部等於 TCDGIS 向量面積（差 <0.01 ㎡）
  · 登記面積 ＝ TCDGIS 標示面積（306 2,500.00／306-1 4,897.97 完全一致）
  · 使用分區／使用地類別 7/7 完全一致  ← **綠帶應留設側判定所需的欄位，這裡就夠**

**權屬（公私別／所有權人／管理者）**：`getLandInfo` 沒有這些欄位，但
**2026-08-02 起改由 `scripts/public_land.py`（內政部 MOI_API_039 公有土地開放資料）補上**，
同樣免登入。本腳本預設會自動帶入（`--no-owner` 可關）。
布袋案七筆與 TCDGIS 逐筆對帳 7/7 正確，含農田水利署與縣政府經管者。
⚠ 「未列 ＝ 私有」是反面推論；⚠ 管理機關可能是改制前舊名。詳見 `public_land.py` docstring。

**仍必須上 TCDGIS 的只剩兩件（見 skill `tcdgis-parcels-to-shp`）**：
  ① **環境敏感地區套疊分析 53 項**——只有平台有。
  ② **離島（澎湖／金門／連江）幾何**——農業部服務無資料，一律回 NOT_FOUND。

因此本腳本的定位是「**免登入先跑一輪**」：幾何、使分使類、權屬、鄰地應留設側判定
都先做完，把 TCDGIS 那次登入縮到只為「環敏」而跑。
**送審交付的基地幾何權威仍以平台官方 shp 為準**（使用者 2026-07-15 鐵律），
本腳本成果請以 `verify` 子命令與平台 shp 對帳後再沿用。

用法（系統 Python，不需 QGIS）
------------------------------------------------------------------
  # 取基地（多筆組成一個基地）
  python -X utf8 scripts/cadastre_fetch.py fetch --county 嘉義縣 --town 布袋鎮 \
      --section 考試潭段鹿寮小段 --landnos 306 --out outputs/<案>/cadastre --name 基地

  # 取鄰地（綠帶應留設側判定用；只需使類，免登入即足夠）
  python -X utf8 scripts/cadastre_fetch.py fetch ... --landnos 305 278 298 391 323 306-1 --name 鄰地

  # 與平台官方 shp 對帳（累積證據，決定能否升為主來源）
  python -X utf8 scripts/cadastre_fetch.py verify --cad outputs/<案>/cadastre/基地.shp \
      --official outputs/<案>/joined/<案>_面積join.shp

輸出：<name>.shp（EPSG:3826、UTF-8）＋ <name>_cadastre.json（完整回傳，含警告）
欄位：地號／使分／使類／縣市／鄉鎮／段／登面積／圖面積／偏差pct／版次／狀態／未登記／
      公私別／所有權／管理者／原地號
      ⚠ DBF 欄名上限 **10 bytes**，故一律短欄名。**「所有權人」是 12 bytes 會被截斷成
        壞 UTF-8、整份 shp 讀不出來**（2026-08-02 實踩）→ 用「所有權」（9 bytes）。
      與 shp-xls-area-join 的 使分／使類 同名，`greenbelt_ratio --neighbours` 可直接吃。
"""
from __future__ import annotations

from config_loader import skill_path

import argparse
import io
import json
import os
import sys

SKILL = str(skill_path("cadastre-check", "scripts"))
CRS = 3826


def is_unregistered(landno8_or_no: str) -> bool:
    """母號 ≥9000 ＝ **未登記土地**（使用者 2026-08-02 告知，已實證）。

    地政為製作圖資給了地號，但該筆**沒有登記屬性**——無使分／使類／登記面積／所有權。
    本機全台快照（嘉義縣 742,989 筆）驗證：母號 ≥9000 者 11,377 筆，
    **使分／使類 100.0% 空白、登面積 0.0、所有權空白**，段名常直接寫「未登錄地」或「不明」。
    線上實查（東石鄉後埔段 9006-100）：cadastre-check 回 OK、幾何 3,903.82 ㎡、
    但 `registry` 為 None 並警告「內政部查無屬性」。

    ⚠ **母號<9000 且使類空白時不可逕行推論為都市土地**（2026-08-06 新化王公廟小段案，
      使用者更正先前「母號<9000 空白＝都市土地」的錯誤原則：8856 地號母號 3856<9000、
      使分/使類空白，實為未登錄土地，不是都市土地）。getLandInfo 對都市土地與未登錄地
      回傳同樣是空白，兩者無法單靠這支 API 區分——交付文件不可寫定「都市土地」，
      只能寫「無使分/使類屬性、身分待查證」，需要時另查都市計畫圖資或地政資料確認。
    """
    s = "".join(ch for ch in no_mother(landno8_or_no) if ch.isdigit())
    return bool(s) and int(s) >= 9000


def no_mother(landno: str) -> str:
    """取地號母號：'306-1'→'306'、'03060001'→'0306'。"""
    s = str(landno).strip()
    if "-" in s:
        return s.split("-", 1)[0]
    if len(s) == 8 and s.isdigit():
        return s[:4]
    return s


def _load_api():
    if not os.path.isdir(SKILL):
        raise SystemExit(f"找不到 skill cadastre-check：{SKILL}\n"
                         "請確認已安裝（~/.claude/skills/cadastre-check）")
    sys.path.insert(0, SKILL)
    import api  # noqa: PLC0415
    return api


def cmd_fetch(a):
    api = _load_api()
    import sys as _s
    _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    r = api.query(a.county, a.town, a.section, a.landnos, version=a.version)
    print(f"地籍版次 {r['version']}（schema {r['schema']}）　"
          f"查 {r['summary']['total']} 筆：成功 {r['summary']['ok']}、"
          f"失敗 {r['summary']['failed']}")
    # 權屬：以**段**為單位查一次公有土地名冊，再以地號 join。
    # 用 QueryByUnit（事務所2碼＋段4碼）而非 QueryByTown——api 回傳有 office/sec，
    # 但沒有鄉鎮 2 碼，走事務所這條剛好對得上，少一次代碼轉換。
    own_idx, own_msg = {}, None
    if not a.no_owner:
        p0 = next((rec["parcel"] for rec in r["records"] if rec["ok"] and rec["parcel"]),
                  None)
        if p0:
            try:
                import public_land as PL
                rows = PL.query_section(None, None, p0["sec"], unit=p0["office"])
                for rw in rows:
                    os_ = PL.owners(rw)
                    if os_:
                        o = os_[0]
                        own_idx[PL.norm_no(PL._g(rw, "No") or "")] = (
                            o["公私別"], o["所有權人"], o["管理者"], len(os_))
                own_msg = (f"公有土地名冊 {len(rows)} 筆"
                           f"（事務所 {p0['office']}／段 {p0['sec']}）")
            except Exception as e:  # noqa: BLE001
                own_msg = f"⚠ 權屬查詢失敗（{type(e).__name__}: {e}），權屬欄留空"
    if own_msg:
        print(f"權屬來源：{own_msg}")

    import geopandas as gpd
    from shapely.geometry import shape

    rows, geoms, bad = [], [], []
    for rec in r["records"]:
        q, st = rec["query"], rec["status"]
        if not rec["ok"] or not rec["geometry"]:
            bad.append((q["landno"], st, rec.get("message", "")))
            print(f"  ✖ {q['landno']:<11}{st}　{rec.get('message', '')}")
            for n in rec["notes"]:
                print(f"      {n}")
            continue
        p, reg = rec["parcel"], (rec["registry"] or {})
        # ⚠ 使分／使類為空至少有**兩種不同成因**，處置不同，不可混為一談：
        #   ① 母號 ≥9000 ＝ **未登記土地**（見 is_unregistered）——本來就沒有登記屬性
        #   ② 母號 <9000 而空白 ＝ **都市土地或未登錄土地皆有可能**（getLandInfo 對兩者
        #      回傳同樣是空白，無法單靠這支 API 區分——2026-08-06 新化王公廟小段 8856
        #      案例證實，勿逕行標記為都市土地；分區/身分待另查都市計畫圖資或地政資料）
        #   空值一律原樣留著，不可填「未知」之類的假值。
        unreg = is_unregistered(p["landno"])
        # 權屬：命中公有土地名冊＝公有；未命中＝私有（**反面推論**，關鍵筆數請覆核）。
        # 未登記土地本來就不在該名冊，故不下私有的判斷。
        ow = own_idx.get(p["landno"])
        if ow:
            pri, onm, omgr = ow[0], ow[1], ow[2]
        elif unreg or not own_idx:
            pri = onm = omgr = ""
        else:
            pri, onm, omgr = "私(推論)", "", ""
        rows.append({
            "地號": p["landno"], "使分": reg.get("zone") or "",
            "使類": reg.get("land_type") or "",
            "縣市": p["city"], "鄉鎮": p["town"], "段": p["section"],
            "登面積": reg.get("area"), "圖面積": rec.get("map_area"),
            "偏差pct": rec.get("deviation_pct"), "版次": r["version"],
            "狀態": st, "未登記": "是" if unreg else "",
            "公私別": pri, "所有權": onm, "管理者": omgr,
            "原地號": (rec["resurveyed_from"] or {}).get("landno") or "",
        })
        geoms.append(shape(rec["geometry"]))
        dev = rec.get("deviation_pct")
        if reg.get("land_type"):
            lab = reg["land_type"]
        elif unreg:
            lab = "(未登記土地)"
        else:
            lab = "(無屬性:都市土地或未登錄地，待查證)"
        print(f"  ✔ {p['landno']:<11}{st:<11}{lab:<14}"
              f"登記 {reg.get('area')}　圖 {rec.get('map_area')}"
              + (f"（{dev:+.2f}%）" if dev is not None else ""))
        if ow:
            print(f"      權屬 {ow[0]}／{ow[1]}"
                  + (f"／管理者 {ow[2]}" if ow[2] else "")
                  + ("　⚠ 多所有權人" if len(ow) > 3 and ow[3] > 1 else ""))
        elif pri:
            print(f"      權屬 {pri}（未列於公有土地名冊）")
        if rec["resurveyed_from"]:
            print(f"      ↳ 原查詢 {rec['resurveyed_from']['landno']} 已重測改號")
        for w in rec["warnings"]:
            print(f"      ⚠ {w}")

    os.makedirs(a.out, exist_ok=True)
    io.open(os.path.join(a.out, f"{a.name}_cadastre.json"), "w",
            encoding="utf-8").write(json.dumps(r, ensure_ascii=False, indent=2))
    if not rows:
        raise SystemExit("✖ 無任何成功筆數，未產出 shp")
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs=f"EPSG:{CRS}")
    shp = os.path.join(a.out, f"{a.name}.shp")
    gdf.to_file(shp, encoding="utf-8")
    print(f"\n→ {shp}（{len(rows)} 筆，總圖面積 {gdf.geometry.area.sum():,.2f} ㎡）")
    print(f"→ {os.path.join(a.out, a.name + '_cadastre.json')}")
    if bad:
        print(f"\n⚠ {len(bad)} 筆未取得，**不可當成「該地不存在」**——"
              "MISMATCH 的正確解讀是「此地號在該版次不存在（可能已分割或段名變更）」；"
              "離島一律無幾何。需要時上 TCDGIS 覆核。")
    n_unreg = sum(1 for x in rows if x["未登記"])
    if n_unreg:
        print(f"\n⚠ 其中 {n_unreg} 筆為**未登記土地**（母號 ≥9000）：地政為製作圖資給了地號，"
              "但無使分／使類／登記面積／所有權。\n"
              "   ・面積一律用 圖面積（沒有登記面積可用）\n"
              "   ・綠帶留設側判定：未登錄地**不屬**要點§2② 六類農業用地 → 該側免留設（正確行為）\n"
              "   ・不可把它算進申請範圍的登記面積，也不可當成都市土地")
    n_blank = sum(1 for x in rows if not x["使類"] and not x["未登記"])
    if n_blank:
        print(f"\n⚠ 另有 {n_blank} 筆使分／使類空白但母號 <9000：**可能是都市土地，也可能是"
              "未登錄土地**，getLandInfo 對兩者回傳同樣是空白、無法區分（2026-08-06 已更正"
              "先前「一律當都市土地」的錯誤原則）——身分待另查都市計畫圖資或地政資料，"
              "交付文件不可逕行寫定為都市土地。")
    if own_idx:
        n_pub = sum(1 for x in rows if x["公私別"] and "推論" not in x["公私別"])
        print(f"\n權屬：公有 {n_pub} 筆（含所有權人與管理者）、"
              f"其餘以「未列於公有土地名冊」推論為私有。")
        print("  ⚠ 「未列＝私有」是反面推論；涉及取得方式／因應手段的關鍵筆數，"
              "仍建議以土地登記謄本或 TCDGIS 覆核。")
        print("  ⚠ 管理機關可能是改制前舊名（實測回「行政院農業委員會農田水利署」，"
              "現行為「農業部農田水利署」）——交付文字請用現行名稱。")
    else:
        print("\n⚠ 未取得權屬（--no-owner 或查詢失敗）：寫因應手段前必須另查。")
    return 1 if bad else 0


def cmd_verify(a):
    """與平台官方 shp 對帳：IoU、面積差、質心位移、頂點數。

    這支存在的理由：使用者鐵律是「基地幾何一律用平台官方最新 shp」。要把
    cadastre-check 升為主來源，得先有逐案對帳證據，不是憑一次巧合。
    """
    import geopandas as gpd
    from shapely.ops import unary_union

    def U(p):
        g = gpd.read_file(p)
        g = g.to_crs(CRS) if g.crs else g.set_crs(CRS)
        return unary_union([x for x in g.geometry if x is not None])

    cad, off = U(a.cad), U(a.official)
    inter = cad.intersection(off).area
    union = cad.union(off).area
    iou = inter / union * 100 if union else 0.0
    print(f"平台官方   {off.area:12,.2f} ㎡")
    print(f"cadastre   {cad.area:12,.2f} ㎡　差 {cad.area - off.area:+.2f} ㎡"
          f"（{(cad.area / off.area - 1) * 100:+.4f}%）" if off.area else "")
    print(f"IoU        {iou:12.4f}%")
    print(f"質心位移   {off.centroid.distance(cad.centroid):12.4f} m")
    ok = iou >= 99.9 and abs(cad.area - off.area) <= max(0.5, off.area * 0.001)
    print("\n" + ("✓ 兩者實質相同（IoU ≥99.9% 且面積差 ≤0.1%）——本案可互為佐證"
                  if ok else
                  "✖ 有實質差異：**以平台官方為準**，並把本案記進 STATE.md 供累積判斷"))
    return 0 if ok else 2


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="免登入取地籍幾何與屬性 → shp")
    f.add_argument("--county", required=True)
    f.add_argument("--town", required=True)
    f.add_argument("--section", required=True, help="段名（含小段，如 考試潭段鹿寮小段）")
    f.add_argument("--landnos", required=True, nargs="+", help="地號，可多筆")
    f.add_argument("--out", required=True, help="輸出資料夾，建議 outputs/<案>/cadastre")
    f.add_argument("--name", default="基地", help="輸出檔名，預設 基地（鄰地請給 鄰地）")
    f.add_argument("--version", default=None, help="地籍版次，省略＝最新（建議省略）")
    f.add_argument("--no-owner", action="store_true",
                   help="不查權屬（預設會用 public_land.py 免登入查公有土地名冊）")
    f.set_defaults(fn=cmd_fetch)

    v = sub.add_parser("verify", help="與平台官方 shp 對帳（IoU／面積／質心）")
    v.add_argument("--cad", required=True, help="本腳本產出的 shp")
    v.add_argument("--official", required=True, help="TCDGIS 官方 面積join.shp")
    v.set_defaults(fn=cmd_verify)

    a = ap.parse_args()
    raise SystemExit(a.fn(a))


if __name__ == "__main__":
    main()
