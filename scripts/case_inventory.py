"""讀取案件狀態與成果存在性；stdout 輸出索引，不推定結案、不修改案件。"""
import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import re

REPO = Path(__file__).resolve().parent.parent


def declared_status(text):
    # 只看明示狀態列與結案標題，不把「送件前待辦」誤當成本輪評估未完成。
    for line in text.splitlines()[:12]:
        if ('狀態' in line or line.startswith('>') or line.startswith('# ')):
            if '未確認' in line:
                return '待確認'
            if '進行中' in line:
                return '進行中（測試案）' if '測試' in line else '進行中'
            if '已結案' in line:
                return '已結案（依紀錄）'
    if re.search(r'^#+ .*?(?:【結案】|第二次結案)', text, re.M):
        return '已結案（依紀錄）'
    if '非評估案' in text[:700]:
        return '資訊查詢（非評估案）'
    return '待確認'


def inventory(outputs):
    rows = []
    if not outputs.is_dir():
        return rows
    for folder in sorted(outputs.iterdir()):
        if not folder.is_dir() or not re.match(r'^20\d{6}', folder.name):
            continue
        state = folder / 'STATE.md'
        text = state.read_text(encoding='utf-8-sig') if state.is_file() else ''
        rows.append({'case': folder.name, 'state_exists': state.is_file(),
                     'status': declared_status(text),
                     'slides': len(list(folder.glob('*.pptx'))),
                     'maps': len(list(folder.glob('圖檔*/*.png'))),
                     'stats': (folder / '逐案場統計.json').is_file(),
                     'environment': any((folder / p).is_file() for p in
                                        ('環敏NGIS.json', '環敏NGIS.csv', '環境敏感套疊.csv')),
                     'checks': (folder / '禁止事由檢核.json').is_file()})
    return rows


def markdown(rows):
    out = ['# 案件狀態索引', '', f'盤點日期：{date.today().isoformat()}（本機快照，非即時看板）。', '',
           '此表依本機 `outputs/` 產生；有成果檔只代表檔案存在，未驗內容、QA 或正式結案。',
           '「待確認」需由承辦者補認定，不能以簡報存在代替結案指示。狀態權威仍是各案 STATE.md。', '',
           '| 案件 | 紀錄狀態 | STATE | 簡報數 | 圖數 | 統計 | 環敏 | 檢核 |',
           '|---|---|---|---:|---:|---|---|---|']
    for row in rows:
        name = row['case']
        label = f'[{name}](../outputs/{name}/STATE.md)' if row['state_exists'] else name
        out.append(f"| {label} | {row['status']} | " +
                   ('有' if row['state_exists'] else '缺') +
                   f" | {row['slides']} | {row['maps']} | " +
                   ' | '.join('有' if row[k] else '未見' for k in ('stats', 'environment', 'checks')) + ' |')
    out += ['', '統計：' + '；'.join(f'{k} {v}' for k, v in Counter(r['status'] for r in rows).items()),
            '', '範圍：日期開頭的案件資料夾；簡報僅計根目錄，圖數僅計 `圖檔*/*.png`，不含重整後子目錄及其他主題圖。',
            '「未見」只表示未找到約定檔名，不表示完全沒做過。案件資料不進 Git，表內連結需有本機案件副本。',
            '', '更新：`python -X utf8 scripts/case_inventory.py`；僅輸出到終端機。']
    return '\n'.join(out) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--outputs', type=Path, default=REPO / 'outputs')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    rows = inventory(a.outputs)
    print(json.dumps(rows, ensure_ascii=False, indent=2) if a.json else markdown(rows))


if __name__ == '__main__':
    main()
