# -*- coding: utf-8 -*-
"""從衍古知識庫更新「法令檢核規則庫」唯讀快取（2026-11-12 規則庫移交知識庫後建立）。

規則庫由知識庫唯一維護（Z:\\...\\70_法規索引\\法令檢核規則庫\\），本專案只讀它匯出的 CSV。
本腳本把 CSV 複製到 config/規則庫快取/，並寫 .版本.txt（來源、修改時間、MD5、筆數）。
測試（tests/test_rule_ids.py）只讀快取，不連 NAS。

用法：python -X utf8 scripts/sync_rule_library.py [--src <csv>]
NAS 未掛載時印警告並保留既有快取，不中斷。
"""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import io
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = (r"Z:\8. AI協作\知識庫\衍古\03_案件經驗知識層\70_法規索引"
       r"\法令檢核規則庫\法令檢核規則庫.csv")
CACHE_DIR = os.path.join(ROOT, "config", "規則庫快取")
CACHE = os.path.join(CACHE_DIR, "法令檢核規則庫.csv")
RULE_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-(?=[A-Z0-9-]*\d)[A-Z0-9]+(-[A-Z0-9]+)*$")  # 第一個連字號後須含數字（與知識庫盤點一致）


def load_rule_ids(path: str = CACHE) -> set[str]:
    """讀快取 CSV 的 rule_id 欄。純函式，可單測。"""
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {r["rule_id"].strip() for r in csv.DictReader(f) if r.get("rule_id")}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", default=SRC)
    a = ap.parse_args()
    if not os.path.exists(a.src):
        print(f"⚠ 讀不到知識庫匯出（NAS 未掛載？）：{a.src}\n  保留既有快取，不更新。")
        return 0
    data = open(a.src, "rb").read()
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    ids = [r["rule_id"].strip() for r in rows]
    bad = [i for i in ids if not RULE_ID_RE.match(i)]
    dup = {i for i in ids if ids.count(i) > 1}
    if bad or dup:
        raise SystemExit(f"匯出檔異常，不更新快取：格式不符 {bad[:5]}；重複 {sorted(dup)[:5]}")
    os.makedirs(CACHE_DIR, exist_ok=True)
    shutil.copy2(a.src, CACHE)
    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(a.src)).strftime("%Y-%m-%d %H:%M")
    with open(os.path.join(CACHE_DIR, ".版本.txt"), "w", encoding="utf-8") as f:
        f.write(f"來源：{a.src}\n來源修改時間：{mtime}\nMD5：{hashlib.md5(data).hexdigest()}\n"
                f"規則數：{len(ids)}\n同步時間：{datetime.datetime.now():%Y-%m-%d %H:%M}\n"
                "維護者：衍古知識庫（本專案唯讀；要新增或修改規則請回訊知識庫，不要改這份快取）\n")
    print(f"✔ 已更新快取：{len(ids)} 條（來源 {mtime}）→ {CACHE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
