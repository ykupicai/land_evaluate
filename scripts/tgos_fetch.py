# -*- coding: utf-8 -*-
"""TGOS 本機環境敏感圖資換版下載（2026-08-05 建）。

為什麼要它（NR23 教訓）：本機 `環境敏感相關/` 多為 105~106 年快照，其後**新增／修正**
的範圍一律查不到——龍崎案本機 106 年版自然保留區只到 NR21，不含 110.07.30 公告的
NR23 龍崎牛埔惡地（149 公頃、第一級），逐筆距離算出「13 公里外、未涉及」，與平台
「套疊 8.88 ㎡」方向相反。換上 TGOS 115年第2版（1152）後本機重算 8.88 ㎡ 與平台一字不差。

TGOS 國家空間資料資產（`www.tgos.tw/tgos/NgdaMap`，**免登入**）對部分圖層提供新版 SHP 的
**直接下載連結**（`/MDE/VirtualDir_TC/Product/<guid>/<檔名>.zip`）。本腳本把這些連結
固化，一鍵下載＋解壓到對應 `環境敏感相關/` 夾＋寫 `.版本.txt`。

用法：
  python -X utf8 scripts/tgos_fetch.py --list
  python -X utf8 scripts/tgos_fetch.py --layer 自然保留區
  python -X utf8 scripts/tgos_fetch.py --all

⚠ 下載後若該圖層在 skill 單一登記表 `tgos_layers.LOCAL_LAYERS` 有登錄，第二階段本機套疊
  即自動用新版（解析依序取第一個存在的 shp，新版夾在前）。新夾名要與 LOCAL_LAYERS 的
  路徑一致，換版後記得回頭核對 `tests/test_env_registry.py` 的版本順序斷言。
⚠ TGOS 有些圖層**只提供 WMS 無 SHP**（山崩與地滑就是），拿不到向量，不在此清單。
"""
from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV_ROOT = REPO / "環境敏感相關"
sys.path.insert(0, str(Path.home() / ".claude" / "skills"
                       / "env-sensitivity-check" / "scripts"))

# TGOS 可下載 SHP 的本機環敏圖層。url 與 skill tgos_layers.py 的遠端源一致（同一份 zip）。
# dest＝相對 環境敏感相關/ 的目標夾（與 LOCAL_LAYERS 的 shp 路徑對齊）。
TGOS_LAYERS = [
    {"name": "自然保留區",
     "url": "https://www.tgos.tw:443/MDE/VirtualDir_TC/Product/"
            "4fbee0f6-e2e2-4deb-93f3-eeefa6659d81/自然保留區1152_SHP.zip",
     "dest": "第一級/1-07 自然保留區 (1152_115年第2版)",
     "version": "115年第2版（農業部林業及自然保育署，公布2026-05-29，EPSG:3826）",
     "note": "NR23 龍崎牛埔惡地：106年版查不到，換1152後與平台8.88㎡一字不差。"
             "LOCAL_LAYERS 已把此夾排第一、106年版留 fallback（test_env_registry 鎖順序）"},
    {"name": "野生動物保護區",
     "url": "https://www.tgos.tw:443/MDE/VirtualDir_TC/Product/"
            "54693a28-60f4-435d-9509-039f0b23dd94/mapdata202307040547.zip",
     "dest": "第一級/1-08 野生動物保護區 (TGOS)",
     "version": "農業部林業及自然保育署（TGOS）", "note": ""},
    {"name": "野生動物重要棲息環境",
     "url": "https://www.tgos.tw:443/MDE/VirtualDir_TC/Product/"
            "2a45414e-619a-420c-8830-3b19f2f38ce7/陸域野生動物重要棲息環境1151_SHP.zip",
     "dest": "第一級/1-09 野生動物重要棲息環境 (1151)",
     "version": "115年第1版（TGOS）", "note": ""},
    {"name": "自然保護區",
     "url": "https://www.tgos.tw:443/MDE/VirtualDir_TC/Product/"
            "69fdf5ae-434e-4e7a-b61b-568426f0b359/自然保護區1151_SHP.zip",
     "dest": "第一級/1-10 自然保護區 (1151)",
     "version": "115年第1版（TGOS）", "note": ""},
]


def _extract_zip(content: bytes, dest: Path):
    """解壓；TGOS zip 檔名多為 UTF-8/中文，遇 cp437 亂碼再轉 big5（與 TCDGIS 同型）。"""
    dest.mkdir(parents=True, exist_ok=True)
    names = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for info in zf.infolist():
            raw = info.filename
            try:
                fn = raw.encode("cp437").decode("big5")   # 亂碼還原
            except (UnicodeEncodeError, UnicodeDecodeError):
                fn = raw
            if fn.endswith("/"):
                continue
            target = dest / Path(fn).name
            target.write_bytes(zf.read(info))
            names.append(target.name)
    return names


def fetch(entry: dict) -> Path:
    from govtls import gov_session  # noqa: PLC0415
    s = gov_session()
    ua = {"User-Agent": "Mozilla/5.0 env-sensitivity-check/1.0"}
    print(f"下載 {entry['name']} …")
    r = s.get(entry["url"], timeout=180, headers=ua)
    r.raise_for_status()
    if r.content[:2] != b"PK":
        ct = r.headers.get("Content-Type", "?")
        sys.exit(f"回應不是 zip（Content-Type={ct}、{len(r.content)} bytes）——TGOS 連結可能改版。")
    dest = ENV_ROOT / entry["dest"]
    names = _extract_zip(r.content, dest)
    shp = [n for n in names if n.lower().endswith(".shp")]
    (dest / ".版本.txt").write_text(
        f"{entry['version']}\n來源：{entry['url']}\nshp：{'、'.join(shp)}\n"
        f"下載工具：scripts/tgos_fetch.py\n備註：{entry.get('note', '')}\n",
        encoding="utf-8")
    print(f"  → {dest}（{len(names)} 檔，shp：{shp}）")
    return dest


def main():
    ap = argparse.ArgumentParser(description="TGOS 本機環敏圖資換版下載")
    ap.add_argument("--layer", help="只下載指定圖層（名稱見 --list）")
    ap.add_argument("--all", action="store_true", help="下載全部登錄的 TGOS 圖層")
    ap.add_argument("--list", action="store_true", help="列出可下載圖層")
    a = ap.parse_args()

    if a.list or not (a.layer or a.all):
        print("可下載的 TGOS 本機環敏圖層：")
        for e in TGOS_LAYERS:
            print(f"  {e['name']:16} → 環境敏感相關/{e['dest']}\n"
                  f"      {e['version']}")
        return

    targets = TGOS_LAYERS if a.all else [e for e in TGOS_LAYERS if e["name"] == a.layer]
    if not targets:
        sys.exit(f"查無圖層 {a.layer!r}，見 --list")
    for e in targets:
        fetch(e)
    print("完成。若換了版本夾名，回頭核對 tests/test_env_registry.py 的版本順序斷言。")


if __name__ == "__main__":
    main()
