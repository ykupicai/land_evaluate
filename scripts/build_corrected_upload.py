# -*- coding: utf-8 -*-
"""產出修正版地籍清冊：
 Sheet1 有效清冊：原 316 筆扣除平台匯入失敗的 5 筆 = 311 筆(已確認平台可匯入)。
 Sheet2 待補正：5 筆錯誤的診斷與正確段名/地號(地段代碼待 MMS 查現況碼)。
"""
from pathlib import Path
import pandas as pd
from openpyxl import Workbook

SRC = Path(r"C:\Users\user\Downloads\地籍清冊_20260708.xlsx")
OUT = Path(r"C:\Users\user\Downloads\地籍清冊_20260708_修正版.xlsx")

# 平台回報失敗的 5 筆 (地段代碼, 地號)
BAD = {("BH6710", "461"), ("TB0213", "707"),
       ("GB0381", "327"), ("GB0381", "309-1"), ("GB0381", "330")}

# 待補正明細(依錯誤原因)：正確現況段名/地號；地段代碼需查 MMS 現況
PENDING = [
    # 縣市, 鄉鎮, 錯誤段(我方111/2), 錯誤地號, 診斷, 正確現況段名, 正確地號們
    ["臺中市", "潭子區", "聚興段新興小段(BH6710)", "461", "重測改名:平台現況為寶興段;應改用寶興段原始地號",
     "寶興段", "176、178、180、192、195、196、197、200、201"],
    ["屏東縣", "高樹鄉", "東振新段(TB0213)", "707", "重測改名:平台現況為東振段;應改用東振段原始地號",
     "東振段", "1215"],
    ["宜蘭縣", "礁溪鄉", "民權段(GB0381)", "327", "段碼正確但地號現況查無(111/2後異動);需地政新舊地號查詢", "民權段", "(待查新地號)"],
    ["宜蘭縣", "礁溪鄉", "民權段(GB0381)", "309-1", "同上,地號異動", "民權段", "(待查新地號)"],
    ["宜蘭縣", "礁溪鄉", "民權段(GB0381)", "330", "同上,地號異動", "民權段", "(待查新地號)"],
]


def main():
    df = pd.read_excel(SRC, dtype=str)
    keep = df[~df.apply(lambda r: (str(r["地段代碼"]), str(r["地號"])) in BAD, axis=1)].copy()
    keep["項次"] = range(1, len(keep) + 1)

    wb = Workbook()
    ws = wb.active
    ws.title = "有效清冊"
    ws.append(list(keep.columns))
    for _, r in keep.iterrows():
        ws.append([r[c] for c in keep.columns])

    ws2 = wb.create_sheet("待補正5筆")
    ws2.append(["縣市", "鄉鎮市區", "原(我方111年2月)段/代碼", "原地號", "診斷", "正確現況段名", "正確地號"])
    for row in PENDING:
        ws2.append(row)

    wb.save(OUT)
    print(f"已產生 {OUT}")
    print(f"有效清冊 {len(keep)} 筆；待補正 {len(PENDING)} 筆")


if __name__ == "__main__":
    main()
