# -*- coding: utf-8 -*-
"""把「直接給的基地 shp」轉成管線用 01_中繼檔.xlsx（把整份 shp 當一個案場）。

用途：當使用者直接提供含地籍屬性的基地 shp（段號=小段碼+宗地母+宗地子、地號、
縣市、鄉鎮、段、使分、使類）時，免經 PDF 解析，直接產中繼檔 → match_parcels → make_maps。

用法：python -X utf8 scripts/shp_to_meta.py <dbf路徑> <案子名> <案場名稱>
DBF 以 dbfread 讀（容忍截斷欄名、值以 UTF-8 解）。幾何不需要（make_maps 由縣市地籍 shp
依 小段碼+母+子 撈官方幾何）。
"""
import re
import sys
from pathlib import Path

from dbfread import DBF
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
# load_sitemap 統一用 shp_trust_utils 版（鍵為正規化地號，查詢端須同以
# normalize_landno 正規化），消除先前的重複實作與零填/全形差異漏配
from shp_trust_utils import load_sitemap, normalize_landno  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

COLS = ["案場ID", "案場名稱", "縣市", "鄉鎮區", "段", "小段", "地號原文",
        "宗地母", "宗地子", "部分註記", "輸入使用分區", "輸入使用地目",
        "輸入國土功能分區", "解析信心", "備註"]


def norm_city(s):
    return (s or "").replace("巿", "市").strip()


def pick(rec, *keys):
    for k in keys:
        if k in rec and rec[k] not in (None, ""):
            return str(rec[k]).strip()
    return ""


def main():
    dbf_path, case, name = sys.argv[1], sys.argv[2], sys.argv[3]
    sitemap = {}
    if len(sys.argv) > 5 and sys.argv[4] == "--sitemap":
        sitemap = load_sitemap(sys.argv[5])
    t = DBF(dbf_path, encoding="utf-8", char_decode_errors="replace")
    fields = t.field_names
    recs = list(t)
    # 欄位對應（此 shp：段號/地號/段別 + 20251201_1..8 為 縣市/鄉鎮/段/地號/面積/使分/使類/所有權人）
    def g(rec, name_opts):
        for n in name_opts:
            if n in rec and rec[n] not in (None, ""):
                return str(rec[n]).strip()
        return ""
    out = REPO / "outputs" / case
    out.mkdir(parents=True, exist_ok=True)
    # 縣市/鄉鎮/段 缺值時以全檔多數值補（部分宗地屬性合併不完整）
    from collections import Counter
    cnt_c = Counter(norm_city(g(r, ["20251201_1"])) for r in recs)
    cnt_t = Counter(g(r, ["20251201_2"]) for r in recs)
    def top(c):
        vals = [(k, v) for k, v in c.items() if k]
        return max(vals, key=lambda x: x[1])[0] if vals else ""
    def_c, def_t = top(cnt_c), top(cnt_t)
    wb = Workbook(); ws = wb.active; ws.append(COLS)
    n = 0
    hit = 0
    cids_used = set()
    for r in recs:
        seg_code = g(r, ["段號"])            # DF434401220000
        # 無 14 碼段號者仍列入 01（人可讀清冊/留痕）；宗地母/子留空。
        # 出圖不靠 01，trust-shp 模式走 make_maps --trust-shp 直讀 shp 幾何。
        mother, child = (seg_code[6:10], seg_code[10:14]) if len(seg_code) >= 14 else ("", "")
        landno = g(r, ["地號", "20251201_4"])
        county = norm_city(g(r, ["20251201_1"])) or def_c
        town = g(r, ["20251201_2"]) or def_t
        seg = g(r, ["段別", "20251201_3"])
        seg = re.sub(r"段$", "", seg)  # section_index 段名不含「段」字尾
        zone = g(r, ["20251201_6"])          # 使分
        ltype = g(r, ["20251201_7"])         # 使類
        pair = sitemap.get(normalize_landno(landno)) if sitemap else None
        if pair:
            hit += 1
            cid, cname = pair
        else:
            cid, cname = "001", name
        cids_used.add(cid)
        ws.append([cid, cname, county, town, seg, "", landno,
                   mother, child, "", zone, ltype, "", "高", "由基地shp直接匯入"])
        n += 1
    wb.save(out / "01_中繼檔.xlsx")
    if sitemap:
        if hit == 0:
            print(f"已產生 {out/'01_中繼檔.xlsx'}（{n} 宗地，案場 001={name}）")
            print("[警告] sitemap 全未命中，請檢查地號字串格式")
        else:
            print(f"已產生 {out/'01_中繼檔.xlsx'}（{n} 宗地，sitemap 命中 {hit}/{n}，分派 {len(cids_used)} 個案場）")
    else:
        print(f"已產生 {out/'01_中繼檔.xlsx'}（{n} 宗地，案場 001={name}）")
    print(f"縣市/鄉鎮/段 範例：{county}/{town}/{seg}")


if __name__ == "__main__":
    main()
