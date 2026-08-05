# -*- coding: utf-8 -*-
"""環境敏感地區「應免查範圍」PDF → 結構化 JSON（初篩用，2026-08-05 建）。

輸入＝內政部國土管理署「環境敏感地區單一窗口查詢平台」→「應免查範圍查詢」→
右上『匯出全部項目』下載的 PDF（例：config/環敏應免查範圍_115-0731.pdf）。
這是線上 eland「應免查範圍查詢」的**離線全表**，用它做初篩取代線上查詢
（離線、權威、有版本日期、不受 eland SSL／快取污染坑影響）。

PDF 結構（實測 v4.7、485 頁）：
  ── 前段：查詢說明（prose，跳過）
  ── 三分節：「第N級環境敏感地區應查或免查詢範圍」（N＝一/二/三）
  ── 每筆資料列＝ 4 欄，抽成文字後為連續四段：
        item_no（純 1~2 位數字一行）
        項目名稱（一行，如「河川區域」「地質敏感區（山崩與地滑）」）
        應查 或 免查（一行）
        行政區域或地段清單（多行，跨頁；行內以「、」分隔，斷行處是中途斷字）

★ 語意（初篩判定的關鍵）：清單列的是「**該狀態**」的行政區；未列者取**相反狀態**。
  - status=應查、清單=[A,B]：A/B 應查，其餘免查（不涉及）。
  - status=免查、清單=[C,D]：C/D 免查，其餘應查。
  （洪水平原一級管制區 status=應查 只列新北 4 區＝只有那 4 區應查；洪氾區一級管制區
   status=免查 列 22 縣市＝各縣市皆免查。兩者互為印證。）

★ 粒度三種並存：縣市／縣市+鄉鎮／縣市+鄉鎮+段（如「花蓮縣秀林鄉人道山段」）。
  比對邏輯見 exempt_lookup.py（基地的 縣市／縣市鄉鎮／縣市鄉鎮段 任一 ∈ 清單即命中）。

用法：
  python -X utf8 scripts/parse_exempt_pdf.py config/環敏應免查範圍_115-0731.pdf \
      -o config/環敏應免查範圍.json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import fitz  # PyMuPDF

_LEVEL = {"一": "第一級", "二": "第二級", "三": "第三級"}
_HEADER = re.compile(r"第([一二三])級環境敏感地區應查或免查詢範圍")
_NOISE = re.compile(r"第\d+頁／共\d+頁|環境敏感地區單一窗口查詢平台|"
                    r"環境敏感地區應免查範圍資料|資料更新時間|製作日期")


_NAME_MAX_LINES = 4   # 項目名稱最多佔幾行（長名會換行，如「國家公園區內之特別景觀區、生態保護區」）


def _row_head(lines, i):
    """lines[i] 若為資料列起點，回 (name, status, regions_start_idx)，否則 None。

    結構：純數字 item_no → **名稱（1~N 行，長名會換行）** → 應查/免查 → 行政區清單。
    ⚠ 名稱可跨行是踩過的坑：原本假設 item_no 後第 2 行即狀態，會讓長名項（#6 國家公園、
      #23 森林、#26 優良農地…）整筆漏掉、其行政區被併進前一項，污染初篩。
    """
    if i + 2 >= len(lines) or not re.fullmatch(r"\d{1,2}", lines[i]):
        return None
    for k in range(1, _NAME_MAX_LINES + 1):
        j = i + k
        if j >= len(lines):
            return None
        if lines[j] in ("應查", "免查"):
            name_lines = lines[i + 1:j]
            if not name_lines or any(re.fullmatch(r"\d+", x) for x in name_lines):
                return None                      # 名稱不可為空、不可含純數字行
            return "".join(name_lines), lines[j], j + 1
        if re.fullmatch(r"\d{1,2}", lines[j]):   # 還沒遇到狀態就碰到下一個數字 → 不是列
            return None
    return None


def _is_row_head(lines, i):
    return _row_head(lines, i) is not None


def parse(pdf_path: Path) -> dict:
    doc = fitz.open(pdf_path)
    lines = []
    for pg in range(doc.page_count):
        for ln in doc[pg].get_text().split("\n"):
            s = ln.strip()
            if s:
                lines.append(s)

    version = None
    for s in lines[:8]:
        m = re.search(r"資料更新時間：(.+)", s)
        if m:
            version = m.group(1).strip()
            break

    items = []
    level = None
    i, n = 0, len(lines)
    while i < n:
        s = lines[i]
        mh = _HEADER.match(s)
        if mh:
            level = _LEVEL[mh.group(1)]
            i += 1
            continue
        head = _row_head(lines, i) if level else None
        if head:
            name, status, rstart = head
            item_no = lines[i]
            regs, j = [], rstart
            while j < n:
                t = lines[j]
                if _is_row_head(lines, j) or _HEADER.match(t):
                    break
                if _NOISE.search(t):          # 頁尾/頁首雜訊：跳過但不中斷（清單會跨頁）
                    j += 1
                    continue
                regs.append(t)
                j += 1
            regions = [r for r in "".join(regs).split("、") if r.strip()]
            items.append({"level": level, "item_no": item_no, "name": name,
                          "status": status, "regions": regions})
            i = j
            continue
        i += 1

    return {
        "version": version,
        "source": "環境敏感地區單一窗口查詢平台『應免查範圍查詢』匯出全部項目",
        "parsed_from": Path(pdf_path).name,
        "n_items": len(items),
        "items": items,
    }


def main():
    ap = argparse.ArgumentParser(description="環敏應免查範圍 PDF → JSON")
    ap.add_argument("pdf", help="匯出的 PDF 路徑")
    ap.add_argument("-o", "--out", required=True, help="輸出 JSON 路徑")
    args = ap.parse_args()

    result = parse(Path(args.pdf))
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1),
                              encoding="utf-8")

    # 摘要（人工核對用）
    print(f"版本：{result['version']}　項目數：{result['n_items']}")
    by_level = {}
    for it in result["items"]:
        by_level.setdefault(it["level"], 0)
        by_level[it["level"]] += 1
    print("分級：", dict(by_level))
    empty = [f'{it["level"]}#{it["item_no"]} {it["name"]}'
             for it in result["items"] if not it["regions"]]
    if empty:
        print(f"⚠ {len(empty)} 項清單為空（需檢查解析）：", empty[:10])
    print("→", args.out)


if __name__ == "__main__":
    main()
