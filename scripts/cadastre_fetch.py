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

**但它拿不到的三件事，仍必須上 TCDGIS（見 skill `tcdgis-parcels-to-shp`）**：
  ① **公私別／所有權人／管理者**——`getLandInfo` 回傳的 20 個欄位裡沒有。
     因應手段完全取決於權屬（西港案：國有水利地買不到，只能申請搭版橋），
     **不可用本腳本的成果去推論權屬**。
  ② **環境敏感地區套疊分析 53 項**——只有平台有。
  ③ **離島（澎湖／金門／連江）幾何**——農業部服務無資料，一律回 NOT_FOUND。

因此本腳本的定位是「**免登入先跑一輪**」：幾何、使分使類、鄰地應留設側判定都先做完，
把 TCDGIS 那次登入縮到只為「權屬＋環敏」而跑。
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
欄位：地號／使分／使類／縣市／鄉鎮／段／登面積／圖面積／偏差pct／版次／狀態／原地號
      （⚠ DBF 欄名上限 10 bytes，故一律短欄名；與 shp-xls-area-join 的 使分／使類 同名，
        greenbelt_ratio --neighbours 可直接吃）
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

SKILL = os.path.join(os.path.expanduser("~"), ".claude", "skills",
                     "cadastre-check", "scripts")
CRS = 3826


def is_unregistered(landno8_or_no: str) -> bool:
    """母號 ≥9000 ＝ **未登記土地**（使用者 2026-08-02 告知，已實證）。

    地政為製作圖資給了地號，但該筆**沒有登記屬性**——無使分／使類／登記面積／所有權。
    本機全台快照（嘉義縣 742,989 筆）驗證：母號 ≥9000 者 11,377 筆，
    **使分／使類 100.0% 空白、登面積 0.0、所有權空白**，段名常直接寫「未登錄地」或「不明」。
    線上實查（東石鄉後埔段 9006-100）：cadastre-check 回 OK、幾何 3,903.82 ㎡、
    但 `registry` 為 None 並警告「內政部查無屬性」。

    ⚠ **空白使類不可推論為都市土地**：母號<9000 的空白率是 29.6%（那些才是都市土地，
      getLandInfo 的使分／使類只適用非都市土地），9xxx 是另一個成因。兩者處置不同。
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
    r = api.query(a.county, a.town, a.section, a.landnos, version=a.version)
    print(f"地籍版次 {r['version']}（schema {r['schema']}）　"
          f"查 {r['summary']['total']} 筆：成功 {r['summary']['ok']}、"
          f"失敗 {r['summary']['failed']}")

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
        # ⚠ 使分／使類為空有**兩種完全不同的成因**，處置也不同，不可混為一談：
        #   ① 母號 ≥9000 ＝ **未登記土地**（見 is_unregistered）——本來就沒有登記屬性
        #   ② 母號 <9000 而空白 ＝ **都市土地**（getLandInfo 只適用非都市土地）
        #      → 分區要另查都市計畫圖資（本產線走 都市計畫/*.shp 的 Color1）
        #   空值一律原樣留著，不可填「未知」之類的假值。
        unreg = is_unregistered(p["landno"])
        rows.append({
            "地號": p["landno"], "使分": reg.get("zone") or "",
            "使類": reg.get("land_type") or "",
            "縣市": p["city"], "鄉鎮": p["town"], "段": p["section"],
            "登面積": reg.get("area"), "圖面積": rec.get("map_area"),
            "偏差pct": rec.get("deviation_pct"), "版次": r["version"],
            "狀態": st, "未登記": "是" if unreg else "",
            "原地號": (rec["resurveyed_from"] or {}).get("landno") or "",
        })
        geoms.append(shape(rec["geometry"]))
        dev = rec.get("deviation_pct")
        if reg.get("land_type"):
            lab = reg["land_type"]
        elif unreg:
            lab = "(未登記土地)"
        else:
            lab = "(無屬性:都市土地?)"
        print(f"  ✔ {p['landno']:<11}{st:<11}{lab:<14}"
              f"登記 {reg.get('area')}　圖 {rec.get('map_area')}"
              + (f"（{dev:+.2f}%）" if dev is not None else ""))
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
        print(f"\n⚠ 另有 {n_blank} 筆使分／使類空白但母號 <9000 ＝ **都市土地**"
              "（getLandInfo 只適用非都市土地）：分區請另查都市計畫圖資。")
    print("\n⚠ 權屬（公私別／所有權人／管理者）本管線取不到，"
          "要寫因應手段前必須另上 TCDGIS 或調謄本。")
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
    f.set_defaults(fn=cmd_fetch)

    v = sub.add_parser("verify", help="與平台官方 shp 對帳（IoU／面積／質心）")
    v.add_argument("--cad", required=True, help="本腳本產出的 shp")
    v.add_argument("--official", required=True, help="TCDGIS 官方 面積join.shp")
    v.set_defaults(fn=cmd_verify)

    a = ap.parse_args()
    raise SystemExit(a.fn(a))


if __name__ == "__main__":
    main()
