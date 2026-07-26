# -*- coding: utf-8 -*-
"""從 02_比對結果 產生「批次地籍匯入選取」上傳用 xlsx。

平台範本欄位：項次 / 縣市名稱 / 鄉鎮市區 / 地段名稱 / 地段代碼 / 地號
其中「地段代碼」＝我方 02 的「小段碼」(如 FB0361)。
地號原文含範圍(860~873、628-10~628-17)、(部分)、補件描述文字，需展開/清理成逐筆地號。
"""
import re
from pathlib import Path

import pandas as pd
from openpyxl import Workbook

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "outputs" / "20260707-清潔隊部資訊" / "02_比對結果.xlsx"
OUT = Path(r"C:\Users\user\Downloads\地籍清冊_20260708.xlsx")


def expand_landno(raw: str):
    """把一個地號原文字串展開成逐筆地號 list。回傳 (list, 未解析註記)。"""
    s = str(raw).strip()
    # 去除全形/半形括號註記（如 (部分)、（原...）、（母號範圍））
    s = re.sub(r"[（(][^)）]*[)）]", "", s).strip()
    if not s:
        return [], "空"
    # 範圍 A~B
    if "~" in s:
        a, b = [x.strip() for x in s.split("~", 1)]
        ma, mb = re.match(r"^(\d+)(?:-(\d+))?$", a), re.match(r"^(\d+)(?:-(\d+))?$", b)
        if ma and mb:
            am, ac = int(ma.group(1)), ma.group(2)
            bm, bc = int(mb.group(1)), mb.group(2)
            if ac is None and bc is None and 0 <= bm - am < 2000:
                return [str(x) for x in range(am, bm + 1)], ""
            if ac is not None and bc is not None:
                if am == bm and 0 <= int(bc) - int(ac) < 2000:  # 同母號、子號範圍
                    return [f"{am}-{c}" for c in range(int(ac), int(bc) + 1)], ""
                if ac == bc and 0 <= bm - am < 2000:              # 同子號、母號範圍
                    return [f"{m}-{ac}" for m in range(am, bm + 1)], ""
        return [a, b], f"範圍無法展開,只取兩端:{s}"
    # 單筆
    if re.match(r"^\d+(-\d+)?$", s):
        return [s], ""
    return [s], f"非標準地號格式,原樣保留:{s}"


def main():
    df = pd.read_excel(SRC, dtype=str)
    df = df[df["比對狀態"] == "成功"]
    rows, warns = [], []
    seen = set()
    for _, r in df.iterrows():
        code = str(r["小段碼"] or "").split("；")[0].split(";")[0].strip()
        if not code or code == "nan":
            warns.append(f"案場{r['案場ID']} {r['地號原文']}：無小段碼,略過")
            continue
        seg = str(r["段"] or "").strip()
        seg_name = seg + ("段" if seg and not seg.endswith("段") else "")
        ss = "" if pd.isna(r["小段"]) else str(r["小段"]).strip()
        if ss and ss not in ("None", "nan"):
            seg_name += ss + "小段"
        nums, w = expand_landno(r["地號原文"])
        if w:
            warns.append(f"案場{r['案場ID']} {code} 「{r['地號原文']}」：{w}")
        for n in nums:
            key = (code, n)
            if key in seen:
                continue
            seen.add(key)
            rows.append([str(r["縣市"] or ""), str(r["鄉鎮區"] or ""), seg_name, code, n])

    wb = Workbook()
    ws = wb.active
    ws.append(["項次", "縣市名稱", "鄉鎮市區", "地段名稱", "地段代碼", "地號"])
    for i, row in enumerate(rows, 1):
        ws.append([i] + row)
    wb.save(OUT)
    print(f"已產生 {OUT}")
    print(f"總筆數：{len(rows)}（不重複 地段代碼+地號）")
    print(f"涉及地段代碼數：{len(set(r[3] for r in rows))}")
    if warns:
        print(f"\n注意事項 {len(warns)} 則：")
        for w in warns[:40]:
            print("  -", w)


if __name__ == "__main__":
    main()
