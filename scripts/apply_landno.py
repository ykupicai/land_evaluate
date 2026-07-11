# -*- coding: utf-8 -*-
"""fallback 鏈寫檔端:把 04_新舊地號對照 回填進 01_中繼檔(spec §5.2)。

- 查詢狀態=成功:該列改用查得的段/小段/地號(宗地母/子重算),原值記備註。
- 查詢狀態=查無:該列標「比對模式=母號範圍」(match_parcels 會撈同段同宗地母全部宗地),
  地號原文改為母號,原值記備註。
用法:python -X utf8 scripts/apply_landno.py <案子名>
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COLS_EXTRA = "比對模式"


def split_section(name: str) -> tuple[str, str]:
    """'聚興段新興小段'→('聚興','新興');'後壁段'→('後壁','');'民權一段'→('民權一','')。"""
    name = (name or "").strip()
    m = re.match(r"^(.*?)段(?:(.*?)小段)?$", name)
    if not m:
        return name, ""
    return m.group(1), (m.group(2) or "")


def _parse_no(s: str) -> tuple[str, str]:
    s = str(s).strip().split(".")[0]
    m = re.match(r"^(\d+)(?:-(\d+))?$", s)
    if not m:
        return "", ""
    return f"{int(m.group(1)):04d}", (f"{int(m.group(2)):04d}" if m.group(2) else "0000")


def apply_corrections(meta_rows: list[dict], lookup_rows: list[dict]):
    """依 04 修正 01 列。key=(案場ID, 原地號)。回傳 (新列list, 統計dict)。"""
    idx = {}
    for lr in lookup_rows:
        idx[(str(lr.get("案場ID", "")).strip().zfill(3),
             str(lr.get("原地號", "")).strip())] = lr
    stats = {"exact": 0, "mother": 0, "skip": 0}
    out = []
    for row in meta_rows:
        r = dict(row)
        key = (str(r.get("案場ID", "")).strip().zfill(3),
               str(r.get("地號原文", "")).strip())
        lr = idx.get(key)
        if lr is None:
            out.append(r)
            continue
        orig = f"{r['段']}{('段' if r['段'] else '')}{(str(r['小段']) + '小段') if str(r.get('小段') or '').strip() not in ('', 'None', 'nan') else ''}{r['地號原文']}"
        if str(lr.get("查詢狀態", "")).strip() == "成功":
            seg, sub = split_section(str(lr.get("查得新段名", "")))
            mother, child = _parse_no(lr.get("查得新地號", ""))
            if not seg or not mother:
                stats["skip"] += 1
                out.append(r)
                continue
            r["段"], r["小段"], r["地號原文"] = seg, sub, str(lr["查得新地號"]).strip()
            r["宗地母"], r["宗地子"] = mother, child
            r["備註"] = (str(r.get("備註") or "") +
                         f"｜原{orig},依新舊地號對照回填").lstrip("｜")
            stats["exact"] += 1
        else:  # 查無 → 母號範圍
            mother = str(r.get("宗地母", "")).strip().split(".")[0]
            if not mother:
                stats["skip"] += 1
                out.append(r)
                continue
            r[COLS_EXTRA] = "母號範圍"
            r["地號原文"] = str(int(mother))
            r["宗地母"], r["宗地子"] = mother.zfill(4), ""
            r["備註"] = (str(r.get("備註") or "") +
                         f"｜原{orig}查無對應,以母號範圍示意出圖").lstrip("｜")
            stats["mother"] += 1
        out.append(r)
    return out, stats


def main():
    from openpyxl import Workbook, load_workbook
    case = sys.argv[1]
    d = REPO / "outputs" / case
    def read(p):
        ws = load_workbook(p, read_only=True, data_only=True).active
        rows = list(ws.iter_rows(values_only=True))
        hdr = [str(c) for c in rows[0]]
        return [dict(zip(hdr, [("" if v is None else v) for v in row]))
                for row in rows[1:] if any(v is not None for v in row)], hdr
    meta, hdr = read(d / "01_中繼檔.xlsx")
    lookup, _ = read(d / "04_新舊地號對照清單.xlsx")
    out, stats = apply_corrections(meta, lookup)
    if COLS_EXTRA not in hdr:
        hdr = hdr + [COLS_EXTRA]
    wb = Workbook(); ws = wb.active
    ws.append(hdr)
    for r in out:
        ws.append([r.get(h, "") for h in hdr])
    wb.save(d / "01_中繼檔.xlsx")
    print(f"回填完成:精確 {stats['exact']}、母號範圍 {stats['mother']}、略過 {stats['skip']}")


if __name__ == "__main__":
    main()
