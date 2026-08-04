# -*- coding: utf-8 -*-
"""⓪b 環敏免登入初篩 ＋ 與 TCDGIS 平台結果對帳（橋接 skill `env-sensitivity-check`）。

用法（系統 Python）：
  python -X utf8 scripts\\env_prescreen.py --case <案名> \\
      [--county 臺南市 --town 龍崎區 --section 番社段] \\
      [--landnos 56-4 58 ...] [--cadastre <案>\\cadastre\\基地.shp]

沒給行政區與地號時，自動從 `outputs/<案>/cadastre/基地.shp` 讀（⓪a 的產出）。

★ 這一步的定位（2026-08-04 接上）：
  eland「應免查範圍查詢」是**免登入**的法定應免查清單，加上 skill 的自動套疊，
  可以在還沒佔用 TCDGIS 登入之前，就先知道哪些項目**依法免查**、哪些**應查但初步無交集**、
  哪些**應查且有交集或須洽機關**。它把 ①b 登入那一步從「發現」變成「確認」。

★ eland「應免查範圍查詢」怎麼讀（使用者 2026-08-04 定案，別再誤解）：
  它回傳的成果是**「可免查的行政區清單」**。你的行政區**沒出現在成果**＝不在免查名單
  ＝該項「應查」；出現＝「免查」＝該行政區依法不涉及該敏感地。
  → 因此對某項目而言：**eland「免查」＝基地依法不涉及**（不是「行政區層級、粒度較粗、
    不代表基地」——那是錯的理解）。

⚠ 三個口徑，交付文件照寫：
  1. **本步驟是初步自我篩檢，不是正式判定。** 正式用途（環評、雜項執照、開發許可）
     仍以 eland 正式行文查復或 TCDGIS 平台查詢結果為準。
  2. eland「免查」＝依法不涉及；與初篩「查了圖資但沒交集」是兩回事，報告分開寫。
  3. 兩來源方向相反時（含 eland免查 × 平台涉及）**是真矛盾、不可淡化**：一律以平台／
     正式查復為準，並把矛盾與可能原因（eland 未更新？平台圖層是潛勢而非已公告？）
     寫進報告。本專案已有前例：本機圖資比平台舊會出現方向相反的結論（NR23 自然保留區）。

產出（outputs/<案>/）：
  環境敏感初篩.csv    eland 85 列 × 應免查／自動套疊結果（逐列）
  環境敏感初篩.json   skill 的完整結構化結果（供程式化引用）
  環境敏感對帳.csv    ★ 有 環境敏感套疊.csv（①b 平台結果）時才產：兩來源逐項對帳
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = Path(os.path.expanduser("~")) / ".claude" / "skills" / "env-sensitivity-check"


def _load_skill_api():
    """載入 skill 的 api.run。以獨立模組名載入 cadastre-check 的 api 是 skill 內部的事，
    這裡只要把 scripts 目錄放進 sys.path 即可（skill 的 SKILL.md 有記這個坑）。"""
    p = SKILL / "scripts"
    if not p.is_dir():
        sys.exit(f"[env_prescreen] 找不到 skill：{p}\n"
                 "  這一步依賴 skill `env-sensitivity-check`（免登入 eland 查詢＋自動套疊）。")
    sys.path.insert(0, str(p))
    try:
        from api import run  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        sys.exit(f"[env_prescreen] 載入 skill api 失敗：{type(e).__name__}: {e}")
    return run


def read_site(shp: Path):
    """從 ⓪a 的 cadastre/基地.shp 讀行政區與地號（欄名沿用 cadastre_fetch 的中文短欄名）。"""
    from dbfread import DBF  # noqa: PLC0415
    dbf = shp.with_suffix(".dbf")
    recs = list(DBF(str(dbf), encoding="utf-8", char_decode_errors="replace"))
    if not recs:
        sys.exit(f"[env_prescreen] {dbf} 沒有記錄")

    def g(r, *names):
        for n in names:
            v = str(r.get(n, "") or "").strip()
            if v:
                return v
        return ""

    county = g(recs[0], "縣市", "縣市名稱")
    town = g(recs[0], "鄉鎮", "鄉鎮區", "鄉鎮市區")
    section = re.sub(r"^\[\w+\]", "", g(recs[0], "段", "段別", "地段名稱"))
    nos = [g(r, "地號") for r in recs]
    return county, town, section, [n for n in nos if n]


# 平台 53 項名稱 → eland 題目關鍵字（只放能明確對上的；對不上就留空，不硬湊）
# ⚠ 兩邊命名體系不同（平台是圖層名、eland 是法定項目問句），只在字面明確對應時才配。
TCDGIS_TO_ELAND = {
    "特定水土保持區": "特定水土保持區",
    "中央管河川區域": "河川區域",
    "區域排水設施範圍": "區域排水設施範圍",
    "自然保留區": "自然保留區",
    "野生動物保護區": "野生動物保護區",
    "野生動物重要棲息環境": "野生動物重要棲息環境",
    "自然保護區": "自然保護區",
    "國際級重要濕地": "國際級重要濕地",
    "古蹟保存區": "古蹟保存區",
    "考古遺址": "考古遺址",
    "重要聚落建築群": "重要聚落建築群",
    "飲用水水源水質保護區": "飲用水水源水質保護區",
    "飲用水取水口一定距離內之地區": "飲用水取水口一定距離內之地區",
    "國有林事業區圖": "國有林事業區",
    "全台保安林分布概略圖": "保安林",
    "區域計畫劃定之森林區": "森林",
    "大專院校實驗林地": "大專院校實驗林地",
    "林業試驗所管轄試驗林範圍": "林業試驗林地",
    "溫泉露頭及其一定範圍": "溫泉露頭",
    "水產動植物繁殖保育區": "水產動植物繁殖保育區",
    "歷次公告地質敏感區-活動斷層": "活動斷層",
    "歷次公告地質敏感區-山崩與地滑": "山崩與地滑",
    "歷次公告地質敏感區-地質遺跡": "地質遺跡",
    "歷次公告地質敏感區-地下水補注": "地下水補注",
    "土石流潛勢溪流地區": "土石流潛勢溪流地區",
    "山坡地": "山坡地",
    "海域區": "海域區",
    "自來水水質水量保護區": "自來水水質水量保護區",
    "礦區(場)": "礦區",
    "礦業保留區": "礦業保留區",
    "人工魚礁區及保護礁區": "人工魚礁區及保護礁區",
    "歷史建築": "歷史建築",
    "聚落建築群": "聚落建築群",
    "文化景觀": "文化景觀",
    "公路兩側禁建限建地區(高速公路)": "高速公路兩側禁建限建地區",
    "大眾捷運系統兩側禁建限建地區": "大眾捷運系統兩側禁建限建地區",
    "鐵路兩側限建地區(高速鐵路)": "高速鐵路兩側限建地區",
    "氣象法之禁止或限制建築地區": "氣象法之禁止或限制建築地區",
    "電信法之禁止或限制建築地區": "電信法之禁止或限制建築地區",
    "民用航空法之禁止或限制建築地區或高度管制範圍": "民用航空法之禁止或限制建築地區",
    "核子反應器設施周圍之禁制區及低密度人口區": "核子反應器設施周圍之禁制區",
    "水庫集水區(供家用或供公共給水)": "水庫集水區（供家用或供公共給水）",
    "水庫集水區(非供家用或非供公共給水)": "水庫集水區（非供家用或非供公共給水）",
    "淹水潛勢圖_定量降雨24小時500mm": "淹水潛勢",
}


def match_finding(findings: list, kw: str):
    """依關鍵字找對應的 eland 列。

    ⚠ **不可比對題目文字**（2026-08-04 龍崎案實錯）：同一題號的多個子項，題目文字
    往往把所有子項並列（例：第二級#1 三個子項的題目都是「是否位屬地質敏感地區
    （活動斷層、山崩與地滑、土石流）？」）。用關鍵字比題目會抓到**第一個子項**，
    而非真正對應的那個——把「山崩與地滑（應查）」配成「活動斷層（免查）」，
    憑空產生一個假的★不一致。
    → 先比**子項名**（精準）；沒有子項名的單一項目（如河川區域）才用題目比。
    """
    for f in findings:
        if kw in (f.get("sub_name") or ""):
            return f
    for f in findings:
        if not (f.get("sub_name") or "") and kw in (f.get("question") or ""):
            return f
    return None


def reconcile(findings: list, tcdgis_csv: Path) -> list[dict]:
    """把平台 53 項與 eland/初篩結果逐項對帳。回傳可寫 CSV 的列。

    只做「明確對得上」的配對；配不上者照實標「無對應」，不猜。
    """
    rows = []
    with io.open(tcdgis_csv, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            name = (r.get("圖層名稱") or "").strip()
            area = (r.get("套疊面積_m2") or "").strip()
            plat = "涉及" if area else "未涉及"
            kw = TCDGIS_TO_ELAND.get(name)
            h = match_finding(findings, kw) if kw else None
            if h is None:
                rows.append({"平台圖層名稱": name, "平台結果": plat, "平台面積_m2": area,
                             "eland項目": "（無對應）", "eland應免查": "",
                             "初篩結果": "", "是否一致": "無法比對"})
                continue
            st = h.get("eland_status") or ""
            act = str(h.get("action") or "")
            if st == "免查":
                pre = "依法免查"
            elif "有交集" in act:
                pre = "有交集"
            elif "無交集" in act or "初步排除" in act:
                pre = "無交集"
            elif "錯誤" in act:
                pre = "套疊錯誤（不可視為排除）"
            else:
                pre = "須洽機關"
            # 一致性判定（★＝真方向衝突，一律以平台為準並在報告標註查證）。
            #   ⚠ eland「免查」是**法定應免查範圍**，語意＝「該行政區依法不涉及該敏感地」
            #     （使用者 2026-08-04 定案：「應免查範圍查詢」回傳的是可免查的行政區清單，
            #      你的行政區**沒出現在成果**＝不在免查名單＝應查；出現＝免查＝不涉及）。
            #     所以 **eland免查 × 平台涉及 是真矛盾，不是粒度差異**——例：eland 判某區
            #     免查山崩地滑、平台卻套出涉及，代表兩來源對「已公告地質敏感區」的認定不同
            #     （eland 未更新？平台圖層是潛勢而非已公告？），必須查證，不可淡化。
            #   ⚠ 初篩「須洽機關／套疊錯誤」＝初篩**沒有可用圖資或抓取失敗**，不是判斷，
            #     標「初篩未涵蓋」，不算一致也不算衝突。
            if plat == "涉及":
                if pre == "有交集":
                    ok = "一致"
                elif pre == "無交集":
                    ok = "★不一致（初篩套疊判無交集、平台涉及）"   # 查圖資版本／範圍
                elif pre == "依法免查":
                    ok = "★不一致（eland判該區免查=依法不涉及、平台卻涉及）"  # 真矛盾
                else:
                    ok = "初篩未涵蓋（以平台為準）"
            else:  # 平台未涉及
                if pre in ("依法免查", "無交集"):
                    ok = "一致"
                elif pre == "有交集":
                    ok = "★不一致（初篩判有交集、平台未涉及）"   # 真衝突
                else:
                    ok = "初篩未涵蓋"
            rows.append({"平台圖層名稱": name, "平台結果": plat, "平台面積_m2": area,
                         "eland項目": (h.get("sub_name") or h.get("question") or "")[:60],
                         "eland應免查": st, "初篩結果": pre, "是否一致": ok})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True, help="案名（outputs/<案名>）")
    ap.add_argument("--cadastre", default=None,
                    help="⓪a 產出的基地 shp（預設 outputs/<案>/cadastre/基地.shp）")
    ap.add_argument("--county"), ap.add_argument("--town"), ap.add_argument("--section")
    ap.add_argument("--landnos", nargs="*", default=None)
    ap.add_argument("--village", default=None, help="村里（可提高 eland 判定精度）")
    ap.add_argument("--max-parcels", type=int, default=8,
                    help="送進套疊比對的地號上限（每筆都要逐圖層相交，預設 8 筆代表基地）")
    a = ap.parse_args()

    case_dir = REPO / "outputs" / a.case
    case_dir.mkdir(parents=True, exist_ok=True)
    county, town, section, nos = a.county, a.town, a.section, a.landnos
    if not all([county, town, section]) or not nos:
        shp = Path(a.cadastre) if a.cadastre else (case_dir / "cadastre" / "基地.shp")
        if not shp.exists():
            sys.exit(f"[env_prescreen] 未給行政區/地號，且找不到 {shp}\n"
                     "  先跑 ⓪a（run_case --fetch 或 cadastre_fetch.py fetch）")
        c2, t2, s2, n2 = read_site(shp)
        county, town, section = county or c2, town or t2, section or s2
        nos = nos or n2
        print(f"[env_prescreen] 自 {shp.name} 讀得 {county}{town}{section}，{len(nos)} 筆地號")

    use = nos[: a.max_parcels]
    if len(nos) > len(use):
        print(f"[env_prescreen] ⚠ 地號 {len(nos)} 筆，僅取前 {len(use)} 筆做套疊"
              f"（eland 應免查是按行政區、不受此影響；要全部比對加 --max-parcels）")

    run = _load_skill_api()
    print(f"[env_prescreen] 呼叫 skill env-sensitivity-check：{county}{town}{section} {use}")
    res = run(county, town, section, use, village=a.village)

    (case_dir / "環境敏感初篩.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    findings = res.get("findings") or []
    with io.open(case_dir / "環境敏感初篩.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["級別", "題號", "子項", "題目", "應免查", "初篩結論", "查復機關"])
        for f in findings:
            w.writerow([f.get("level"), f.get("item_no"), f.get("sub_name"),
                        re.sub(r"^\d+、", "", str(f.get("question") or "")),
                        f.get("eland_status"), f.get("action"), f.get("authority")])
    s = res.get("summary") or {}
    print("[env_prescreen] 初篩摘要：" + "、".join(f"{k} {v}" for k, v in s.items() if v))

    tc = case_dir / "環境敏感套疊.csv"
    if tc.exists():
        rows = reconcile(findings, tc)
        with io.open(case_dir / "環境敏感對帳.csv", "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        bad = [r for r in rows if r["是否一致"].startswith("★")]
        agree = [r for r in rows if r["是否一致"] == "一致"]
        print(f"[env_prescreen] 對帳 {len(rows)} 列：一致 {len(agree)}、"
              f"★真衝突 {len(bad)}、其餘為粒度差異／初篩未涵蓋（皆以平台為準）")
        for r in bad:
            print(f"    ★ {r['平台圖層名稱']}：平台{r['平台結果']}"
                  f"（{r['平台面積_m2']} ㎡）vs {r['是否一致']} → 查圖資版本／範圍，以平台為準")
    else:
        print(f"[env_prescreen] （尚無 {tc.name}，跳過對帳；①b 平台查詢後可重跑本步產對帳表）")
    print(f"→ {case_dir / '環境敏感初篩.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
