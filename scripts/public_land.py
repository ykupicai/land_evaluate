# -*- coding: utf-8 -*-
"""公有土地權屬查詢（內政部 MOI_API_039 公有土地開放資料服務）

★ 這支解決了產線最後一個「非上平台不可」的缺口：**權屬**（公私別／所有權人／管理者）。
  2026-08-02 實測，**免帳號免驗證碼**（規格雖標 BasicAuth，伺服器未強制）。

端點（**沒有版本路徑段**，這是我先前猜錯的地方）：
  POST https://openapi.moi.gov.tw/WEBAPI/PublicLandOData/QueryByTown
       body = [{"CITY": "Q", "TOWN": "03", "SEC": "1410"}]      ← 縣市1碼/鄉鎮2碼/段4碼
  POST .../QueryByUnit
       body = [{"UNIT": "QB", "SEC": "1410"}]                    ← 事務所2碼/段4碼
規格：https://cop.moi.gov.tw/Service/OAS_APISwagger/OASFiles/<id>.yaml

**以「段」為查詢單位**，一次回該段全部公有地——對產線很合用：
一次呼叫就拿到整段公有地名冊，再以地號 join 基地與鄰地。

★★ 三個實測要點（不照著做會出錯）
------------------------------------------------------------------
1. **回傳欄位名是全大寫，與 Swagger 規格的駝峰式不符**（規格寫 `OwnerName`，
   實際回 `OWNERNAME`）。照規格寫會全部取到 None——本檔一律用大寫並容錯。
2. **結構比規格多一層**：`RESPONSE[i].NATIONAL.PUBLIC[]`，且 `OWNER` 是**陣列**
   （一筆地可有多個所有權人，含權利範圍分子／分母）。
3. **管理機關名稱可能是改制前的舊名**：實測回「行政院農業委員會農田水利署」，
   而 TCDGIS 現值為「農業部農田水利署」（農委會於 2023-08 改制為農業部）。
   → 交付文字引用管理機關時要用**現行名稱**，或註明資料來源日期。

涵蓋範圍實測（布袋考試潭段鹿寮小段 Q/03/1410，共回 133 筆公有地）
------------------------------------------------------------------
與 TCDGIS 權威值七筆逐筆對帳 **7/7 正確**：
  · 278／298／391 → 命中，中華民國／國有／農田水利署（TCDGIS 同）
  · 323          → 命中，嘉義縣／縣(市)有／嘉義縣政府（TCDGIS 同）
  · 306／306-1／305 → 未列 ＝ 私有（TCDGIS 同）
**含非內政部／非國產署經管者**（農田水利署、縣政府都在內），故可用於全案權屬判定。

⚠ 「未列 ＝ 私有」是**反面推論**。它在本案 3/3 正確，但仍請注意：
  · 這份是**公有土地**清單，未列可能是私有，也可能是該筆尚未納入資料
  · 涉及取得方式／因應手段的關鍵筆數，仍建議以土地登記謄本或 TCDGIS 覆核
  · 未登記土地（母號 ≥9000）本來就不會在此清單內

用法
------------------------------------------------------------------
  python -X utf8 scripts/public_land.py --city Q --town 03 --sec 1410 \
      [--landnos 306 278 323] [--out <案>/cadastre/公有土地.json]
"""
from __future__ import annotations

import argparse
import io
import json
import urllib.error
import urllib.request

API = "https://openapi.moi.gov.tw/WEBAPI/PublicLandOData"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Content-Type": "application/json", "Accept": "application/json"}


def norm_no(no) -> str:
    """地號正規化：'02780000'/'0278-0000' → '278'；'03060001' → '306-1'。"""
    s = str(no).strip().replace("-", "")
    if len(s) == 8 and s.isdigit():
        m, c = int(s[:4]), int(s[4:])
        return f"{m}-{c}" if c else str(m)
    return str(no).strip()


def _g(d: dict, *names):
    """大小寫容錯取值——規格是駝峰、實際回大寫，兩邊都吃。"""
    for n in names:
        for k in (n, n.upper(), n.lower()):
            if k in d and d[k] not in (None, ""):
                return d[k]
    return None


def fetch_section_raw(city: str, town: str, sec: str, unit: str | None = None,
                      timeout: int = 90) -> dict:
    """查一個段，回 API 原始 JSON（已驗 STATUS==1）。給 unit 走 QueryByUnit，否則走 QueryByTown。

    抽出來給 `api_health.py` 直接呼叫——健檢要能看到 NATIONAL／COUNTY 各分支的筆數，
    而且必須與產線走**同一段 HTTP 程式**（urllib＋系統憑證存放區；2026-10-07 code review
    查出健檢用 requests 另組憑證鏈，兩邊對同一主機的 TLS 結果不同）。
    """
    if unit:
        path, body = "/QueryByUnit", [{"UNIT": unit, "SEC": sec}]
    else:
        path, body = "/QueryByTown", [{"CITY": city, "TOWN": town, "SEC": sec}]
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            j = json.loads(f.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}：{e.read().decode('utf-8', 'replace')[:200]}")
    if j.get("STATUS") != 1:
        raise RuntimeError(f"API 回 STATUS={j.get('STATUS')} MESSAGE={j.get('MESSAGE')}")
    return j


def branch_counts(j: dict) -> dict:
    """原始 JSON 各權屬分支（NATIONAL／PROVINCE／COUNTY／VILLAGE…）的公有地筆數。"""
    counts: dict = {}
    for grp in j.get("RESPONSE") or []:
        for k, v in grp.items():
            if isinstance(v, dict) and ("PUBLIC" in v or "public" in v):
                counts[k] = counts.get(k, 0) + len(v.get("PUBLIC") or v.get("public") or [])
    return counts


def query_section(city: str, town: str, sec: str, unit: str | None = None,
                  timeout: int = 90) -> list[dict]:
    """查一個段的全部公有地（所有權屬分支合併）。"""
    j = fetch_section_raw(city, town, sec, unit, timeout)
    out = []
    # ⚠ 結構比規格多一層：RESPONSE[i].NATIONAL.PUBLIC[]（另有 COUNTY 等分支，要全部走過）
    for grp in j.get("RESPONSE") or []:
        for v in grp.values():
            if isinstance(v, dict) and ("PUBLIC" in v or "public" in v):
                out += (v.get("PUBLIC") or v.get("public") or [])
    return out


def owners(rec: dict) -> list[dict]:
    """攤平所有權人（OWNER 是陣列，一筆地可有多人）。"""
    ow = rec.get("OWNER") or rec.get("Owner") or []
    if isinstance(ow, dict):
        ow = [ow]
    res = []
    for o in ow:
        res.append({
            "所有權人": _g(o, "OwnerName") or "",
            "公私別": _g(o, "OwnerType") or "",
            "管理者": _g(o, "ManagerName") or "",
            "權利範圍": (f"{_g(o, 'Numerator') or ''}/{_g(o, 'Denominator') or ''}"
                         if _g(o, "Denominator") else _g(o, "RightType") or ""),
        })
    return res


def index_by_landno(recs: list[dict]) -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for r in recs:
        idx.setdefault(norm_no(_g(r, "No") or ""), []).append(r)
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", help="縣市代碼 1 碼（如 Q＝嘉義縣）")
    ap.add_argument("--town", help="鄉鎮代碼 2 碼")
    ap.add_argument("--unit", help="事務所代碼 2 碼（給了就走 QueryByUnit）")
    ap.add_argument("--sec", required=True, help="段代碼 4 碼")
    ap.add_argument("--landnos", nargs="*", default=None,
                    help="只列這些地號（省略＝列全段）")
    ap.add_argument("--out", default=None, help="另存 JSON")
    a = ap.parse_args()
    if not a.unit and not (a.city and a.town):
        raise SystemExit("需給 --city ＋ --town，或改給 --unit")

    recs = query_section(a.city, a.town, a.sec, a.unit)
    print(f"段 {a.sec} 公有土地 {len(recs)} 筆"
          + (f"（{_g(recs[0], 'SCName') or ''}）" if recs else ""))
    idx = index_by_landno(recs)

    if a.landnos:
        print(f"\n{'地號':<9}{'判定':<12}{'所有權人':<10}{'公私別':<10}{'管理者'}")
        print("-" * 74)
        for no in a.landnos:
            hit = idx.get(norm_no(no))
            if not hit:
                print(f"{no:<9}{'未列→私有':<12}"
                      + "（反面推論；關鍵筆數請以謄本或 TCDGIS 覆核）")
                continue
            for r in hit:
                for o in owners(r):
                    print(f"{no:<9}{'公有':<12}{o['所有權人']:<10}"
                          f"{o['公私別']:<10}{o['管理者']}")
    else:
        cnt: dict[str, int] = {}
        for r in recs:
            for o in owners(r):
                k = f"{o['公私別']}／{o['所有權人']}"
                cnt[k] = cnt.get(k, 0) + 1
        print("\n[全段公私別分布]")
        for k, v in sorted(cnt.items(), key=lambda x: -x[1]):
            print(f"  {k:<28}{v:>5}")

    if a.out:
        io.open(a.out, "w", encoding="utf-8").write(
            json.dumps({"查詢": vars(a), "筆數": len(recs), "資料": recs},
                       ensure_ascii=False, indent=2))
        print(f"\n→ {a.out}")
    print("\n⚠ 管理機關可能是改制前舊名（實測回「行政院農業委員會農田水利署」，"
          "現行為「農業部農田水利署」）——交付文字請用現行名稱。")


if __name__ == "__main__":
    main()
