# -*- coding: utf-8 -*-
"""把出圖成果依案場序號(案場ID)分資料夾整理。

將 outputs/<案子>/圖檔/<序號>_<案場名>_<主題>.png
整理為     outputs/<案子>/出圖成果/<序號>_<案場名>/<主題>.png

用法：python organize_outputs.py [案子資料夾名]（預設 20260707-清潔隊部資訊）
"""
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CASE = sys.argv[1] if len(sys.argv) > 1 else "20260707-清潔隊部資訊"
SRC = REPO / "outputs" / CASE / "圖檔"
DST = REPO / "outputs" / CASE / "出圖成果"

# 檔名：<序號>_<案場名>_<主題>.png
PAT = re.compile(r"^(?P<id>\d+)_(?P<name>.+)_(?P<theme>使用分區|使用地編定|國土功能分區|正射套疊)\.png$")


def main():
    if not SRC.exists():
        sys.exit(f"找不到圖檔資料夾：{SRC}")
    DST.mkdir(parents=True, exist_ok=True)
    n_file = 0
    folders = {}
    for png in sorted(SRC.glob("*.png")):
        m = PAT.match(png.name)
        if not m:
            print(f"[略過] 檔名不符規則：{png.name}")
            continue
        cid, name, theme = m["id"], m["name"], m["theme"]
        folder = DST / f"{cid}_{name}"
        folder.mkdir(exist_ok=True)
        shutil.copy2(png, folder / f"{theme}.png")
        folders.setdefault(f"{cid}_{name}", []).append(theme)
        n_file += 1
    print(f"已整理 {n_file} 張圖到 {len(folders)} 個案場資料夾（{DST}）")
    incomplete = {k: v for k, v in folders.items() if len(v) < 4}
    if incomplete:
        print(f"[注意] 未滿4張的案場 {len(incomplete)} 個：")
        for k, v in sorted(incomplete.items()):
            print(f"  {k}: {sorted(v)}")


if __name__ == "__main__":
    main()
