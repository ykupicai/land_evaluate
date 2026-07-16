# -*- coding: utf-8 -*-
"""圖檔_maxmap/ 攤平檔 → 依案場分資料夾:<案場ID>_<案場名稱>/<主題>.png
並同步改寫 05_出圖清單_maxmap.csv/.xlsx 的 檔名 欄(相對路徑)。

用法:python reorg_sheets_persite.py <案outputs目錄>
(案場ID 可為數字或英數,如 3、S012)"""
import re
import shutil
import sys
from pathlib import Path

import pandas as pd

if len(sys.argv) != 2:
    sys.exit("用法:python reorg_sheets_persite.py <案outputs目錄(含 圖檔_maxmap)>")
BASE = Path(sys.argv[1])
IMG = BASE / '圖檔_maxmap'
if not IMG.is_dir():
    sys.exit(f"找不到 {IMG}")

PAT = re.compile(r'^([A-Za-z0-9]+)_(.+)_([^_]+)\.png$')

moved = 0
for png in sorted(IMG.glob('*.png')):
    m = PAT.match(png.name)
    if not m:
        print('[skip] 無法解析:', png.name)
        continue
    cid, cname, theme = m.groups()
    sub = IMG / f'{cid}_{cname}'
    sub.mkdir(exist_ok=True)
    shutil.move(str(png), str(sub / f'{theme}.png'))
    moved += 1
print(f'移動 {moved} 張')

csvf = BASE / '05_出圖清單_maxmap.csv'
df = pd.read_csv(csvf, encoding='utf-8-sig')
def newpath(row):
    old = str(row['檔名'])
    m = PAT.match(Path(old).name)
    if not m:
        return old
    cid, cname, theme = m.groups()
    return f'{cid}_{cname}/{theme}.png'
df['檔名'] = df.apply(newpath, axis=1)
df.to_csv(csvf, index=False, encoding='utf-8-sig')
df.to_excel(BASE / '05_出圖清單_maxmap.xlsx', index=False)
print(f'05 清單已更新 {len(df)} 列')
