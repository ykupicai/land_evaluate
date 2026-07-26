# -*- coding: utf-8 -*-
"""補正清冊：平台匯入失敗5筆的修正(用現況段名/代碼/地號)。
上傳此檔即可把這幾筆補進平台(311 已成功者不需重傳)。
民權一段代碼待補(事務所 GB + 4位流水)。
"""
from pathlib import Path
from openpyxl import Workbook

OUT = Path(r"C:\Users\user\Downloads\地籍清冊_20260708_補正.xlsx")

# (縣市, 鄉鎮, 地段名稱, 地段代碼, [地號...])
ROWS = [
    ("臺中市", "潭子區", "寶興段", "BH6754",
     ["176", "178", "180", "192", "195", "196", "197", "200", "201"]),
    ("屏東縣", "高樹鄉", "東振段", "TB1365", ["1215"]),
    # 礁溪民權一段：327→368、309-1→358、330→367；代碼待補(GB+流水)
    ("宜蘭縣", "礁溪鄉", "民權一段", "GB1010", ["368", "358", "367"]),
]


def main():
    wb = Workbook()
    ws = wb.active
    ws.title = "補正清冊"
    ws.append(["項次", "縣市名稱", "鄉鎮市區", "地段名稱", "地段代碼", "地號"])
    i = 0
    for county, town, seg, code, nums in ROWS:
        for n in nums:
            i += 1
            ws.append([i, county, town, seg, code, n])
    wb.save(OUT)
    print(f"已產生 {OUT}（{i} 列）")


if __name__ == "__main__":
    main()
