# -*- coding: utf-8 -*-
"""簡報版面引擎 — parcel-briefing-deck 寫作規則（30 條）的**唯一實作處**

★ 案件成果沉澱機制（2026-07-29 建立）：
  各案的 build_deck.py **只寫內容**（頁面文字、表格數據、圖檔名），
  版面、字體、項目符號、總表格式全部 import 本模組。
  版面規則要改（使用者再定案時），**改這裡一次、所有案子重跑即生效**，
  不再於每個案件資料夾各改一份副本（西港/安南時期的做法，兩案重複 130 行）。

案件端最小用法（outputs/<案>/build_deck.py）：
    import os, sys
    HERE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts"))
    from deck_layout import *
    init(HERE, "<案名>評估簡報.pptx")
    s = new(); header(s, "基礎資料", "區位及宗基地劃分", 2)
    bullets(s, COL_L, Inches(2.72), COL_W, ["…", "…"])
    table(s, COL_L, Inches(4.79), TBL_W, rows, col_w=[...], size=TBL_SZ)
    pic(s, "001_<案>_區位圖.png", PIC_L, PIC_T, PIC_W)
    sum_page(10, "法令檢核總表（一）", rows7欄)
    save()

已固化的規則（對應 skill 條號）：
  13 條列用 PowerPoint 項目符號功能（buFont/buChar＋懸掛縮排），非打字「・」
  17 總表 7 欄（S001/S002 拆欄）、12pt、列高自動
  28 交付字串不得含 markdown 標記（save() 會攔檢）
  版式：大圖 L4.93 W8.20／窄左欄 W4.24 12pt／表格 W3.97 10pt／
        總表 L0.27 W12.78 12pt、圖例置標題右側
"""
from __future__ import annotations

import io
import os
import re as _re

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn
from PIL import Image, ImageDraw

__all__ = [
    "Inches", "Pt", "Emu", "PP_ALIGN", "RGBColor",
    "FONT", "BLACK", "RED", "GREY", "HDR", "BLUE", "W", "H",
    "PIC_L", "PIC_T", "PIC_W", "COL_L", "COL_W", "TBL_W", "TBL_SZ",
    "LEGEND", "SUM_L", "SUM_W", "SUM_T", "SUM_COLW", "SUM_SZ", "SUM_HDR",
    "SUM_COLW_1SITE", "SUM_HDR_1SITE",
    "init", "new", "cover", "header", "tb", "set_bullet", "bullets",
    "table", "pic", "sum_page", "save",
]

FONT = "微軟正黑體"
BLACK = RGBColor(0, 0, 0)
RED = RGBColor(0xC0, 0, 0)
GREY = RGBColor(0x80, 0x80, 0x80)
HDR = RGBColor(0xE7, 0xE7, 0xE7)
BLUE = RGBColor(0x1F, 0x4E, 0x79)
W, H = Inches(13.333), Inches(7.5)

# 內頁版式（2026-07-29 使用者手改定案：大圖＋窄左欄，左欄與圖垂直居中）
PIC_L, PIC_T, PIC_W = Inches(4.93), Inches(1.28), Inches(8.20)
COL_L, COL_W = Inches(0.55), Inches(4.24)
TBL_W = Inches(3.97)
TBL_SZ = 10

LEGEND = "○ 具可行性　　△ 需經後續作業／審議認定　　╳ 不具可行性　　－ 非此法規之項目"
# 總表版式（2026-07-29 使用者手改值：加寬、12pt、圖例移標題右側）
SUM_L, SUM_W, SUM_T = Inches(0.27), Inches(12.78), Inches(1.20)
SUM_COLW = [1.00, 1.71, 3.01, 2.90, 2.90, 0.62, 0.62]
SUM_SZ = 12
SUM_HDR = ["評估事項", "相關法規", "法規要求", "S001 基地情形", "S002 基地情形",
           "S001", "S002"]
# 單一案場案件用（2026-07-30 安定六塊寮段案新增）：S002 欄留空浪費 2.9 吋，
# 改把情形欄加寬並多一欄「翻轉條件／須辦事項」——╳ 必須寫出翻轉條件（規則30）
SUM_COLW_1SITE = [1.10, 1.75, 3.40, 3.95, 1.96, 0.62]
SUM_HDR_1SITE = ["評估事項", "相關法規", "法規要求", "基地情形", "翻轉條件／須辦事項", "判定"]

# ── 模組狀態（init() 設定）─────────────────────────────
_prs = None
_blank = None
_grid = None
_here = None
_img = None
_out = None


def _grid_png(case_dir: str) -> str:
    p = os.path.join(case_dir, "_grid_bg.png")
    if os.path.exists(p):
        return p
    im = Image.new("RGB", (1333, 750), "white")
    d = ImageDraw.Draw(im)
    for x in range(0, 1333, 24):
        for y in range(0, 750, 3):
            d.point((x, y), fill=(228, 228, 228))
    for y in range(0, 750, 24):
        for x in range(0, 1333, 3):
            d.point((x, y), fill=(228, 228, 228))
    im.save(p)
    return p


def _dump_pptx(path: str) -> str:
    """簡報內容指紋：逐頁逐 shape 的幾何與文字（防蓋改鎖用）。"""
    from pptx import Presentation as _P
    EMU = 914400.0
    out = []
    prs = _P(path)
    for i, slide in enumerate(prs.slides, 1):
        out.append(f"S{i}")
        for sh in slide.shapes:
            try:
                out.append("  %.2f,%.2f,%.2f %s" % (sh.left / EMU, sh.top / EMU,
                                                    sh.width / EMU, sh.shape_type))
            except Exception:  # noqa: BLE001
                out.append("  ? " + str(sh.shape_type))
            if sh.has_text_frame:
                for para in sh.text_frame.paragraphs:
                    t = "".join(r.text for r in para.runs)
                    if t.strip():
                        out.append("    " + t)
            if getattr(sh, "has_table", False) and sh.has_table:
                for row in sh.table.rows:
                    out.append("    |" + "|".join(c.text.replace("\n", "\\n")
                                                  for c in row.cells))
    return "\n".join(out)


def init(case_dir: str, out_name: str, img_subdir: str = "圖檔_maxmap",
         force: bool = False):
    """開一份新簡報。case_dir＝案件資料夾（outputs/<案>）。

    ★防蓋改鎖（2026-07-29 實錯後新增）：save() 會同時寫 `<pptx>.dump.txt` 指紋。
    下次 init() 時若磁碟上的 pptx 與指紋不符＝**使用者在 PowerPoint 手改過**，
    直接硬停——先把手改內容回寫進 build_deck.py（或確認要放棄）再 init(force=True)。
    安南案 2026-07-29 就是重建前沒比對，把使用者整輪手改蓋掉。"""
    global _prs, _blank, _grid, _here, _img, _out
    out_path = os.path.join(case_dir, out_name)
    fp_path = out_path + ".dump.txt"
    if not force and os.path.exists(out_path) and os.path.exists(fp_path):
        try:
            now = _dump_pptx(out_path)
            old = io.open(fp_path, encoding="utf-8").read()
        except Exception:  # noqa: BLE001 — 指紋讀不出來就不擋
            now = old = None
        if now is not None and now != old:
            raise SystemExit(
                "★ 磁碟上的簡報與上次腳本輸出不一致——使用者已在 PowerPoint 手改過！\n"
                "  重建會蓋掉手改。請先：\n"
                "  1) 用 dump 比對找出手改處，回寫進 build_deck.py\n"
                "  2) 確認無誤後改用 init(..., force=True) 重建\n"
                f"  指紋檔：{fp_path}")
    _here = case_dir
    _img = os.path.join(case_dir, img_subdir)
    _out = os.path.join(case_dir, out_name)
    _grid = _grid_png(case_dir)
    _prs = Presentation()
    _prs.slide_width, _prs.slide_height = W, H
    _blank = _prs.slide_layouts[6]


def _bg(slide):
    slide.shapes.add_picture(_grid, 0, 0, width=W, height=H)
    fr = slide.shapes.add_shape(1, Inches(0.14), Inches(0.14), W - Inches(0.28),
                                H - Inches(0.28))
    fr.fill.background(); fr.line.color.rgb = BLACK; fr.line.width = Pt(1.25)
    fr.shadow.inherit = False


def new():
    """新增一頁（虛線網格底＋黑外框）。"""
    s = _prs.slides.add_slide(_blank)
    _bg(s)
    return s


def tb(slide, l, t, w, h, txt, size=14, bold=False, color=BLACK, align=PP_ALIGN.LEFT,
       space_after=4, line=1.25):
    box = slide.shapes.add_textbox(l, t, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Emu(0)
    tf.margin_top = tf.margin_bottom = Emu(0)
    lines = txt.split("\n") if isinstance(txt, str) else txt
    for i, s in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(space_after)
        p.line_spacing = line
        r = p.add_run(); r.text = s
        r.font.name = FONT; r.font.size = Pt(size); r.font.bold = bold
        r.font.color.rgb = color
    return box


def set_bullet(p, char="・", hang=0.22):
    """PowerPoint 項目符號功能（規則13）。buFont/buChar 須排在 a:defRPr 之前。"""
    pPr = p._p.get_or_add_pPr()
    pPr.set("marL", str(Emu(Inches(hang))))
    pPr.set("indent", str(-Emu(Inches(hang))))
    bu_font = pPr.makeelement(qn("a:buFont"), {"typeface": FONT})
    bu_char = pPr.makeelement(qn("a:buChar"), {"char": char})
    anchor = pPr.find(qn("a:defRPr"))
    if anchor is None:
        pPr.append(bu_font); pPr.append(bu_char)
    else:
        anchor.addprevious(bu_font); anchor.addprevious(bu_char)


def bullets(slide, l, t, w, items, size=12, h=None):
    box = tb(slide, l, t, w, h or Inches(0.3 * len(items)), list(items), size,
             space_after=6, line=1.3)
    for p in box.text_frame.paragraphs:
        set_bullet(p)
    return box


def header(slide, main, sub, page):
    tb(slide, Inches(0.55), Inches(0.42), Inches(5.2), Inches(0.5), main, 24, True, BLACK)
    if sub:
        tb(slide, Inches(0.55) + Inches(0.42 * len(main)), Inches(0.52), Inches(5.0),
           Inches(0.4), sub, 16, True, RED)
    ln = slide.shapes.add_shape(1, Inches(0.55), Inches(1.02), W - Inches(1.1), Pt(1.2))
    ln.fill.solid(); ln.fill.fore_color.rgb = BLACK; ln.line.fill.background()
    ln.shadow.inherit = False
    tb(slide, W - Inches(1.0), Inches(0.45), Inches(0.5), Inches(0.3), str(page), 12,
       False, GREY, PP_ALIGN.RIGHT)


def cover(line1, line2, line3, date_str,
          sizes=(32, 30, 34)):
    """封面：三行置中案名＋雙藍線＋日期（v1 定案樣式）。回傳 slide。"""
    s = new()
    tb(s, Inches(1.2), Inches(2.35), W - Inches(2.4), Inches(0.7), line1, sizes[0], True,
       BLACK, PP_ALIGN.CENTER)
    tb(s, Inches(1.2), Inches(3.12), W - Inches(2.4), Inches(0.7), line2, sizes[1], True,
       BLACK, PP_ALIGN.CENTER)
    tb(s, Inches(1.2), Inches(3.95), W - Inches(2.4), Inches(0.8), line3, sizes[2], True,
       BLACK, PP_ALIGN.CENTER)
    for y, wd in [(6.30, 2.5), (6.40, 1.0)]:
        ln = s.shapes.add_shape(1, Inches(1.6), Inches(y), W - Inches(3.2), Pt(wd))
        ln.fill.solid(); ln.fill.fore_color.rgb = BLUE; ln.line.fill.background()
        ln.shadow.inherit = False
    tb(s, Inches(1.2), Inches(6.62), W - Inches(2.4), Inches(0.4), date_str, 14,
       False, BLACK, PP_ALIGN.CENTER)
    return s


def table(slide, l, t, w, rows, col_w=None, size=11, header_row=True, ncol=None):
    nc = ncol or len(rows[0])
    rows = [list(r)[:nc] + [""] * max(0, nc - len(r)) for r in rows]
    nr = len(rows)
    gf = slide.shapes.add_table(nr, nc, l, t, w, Inches(0.30 * nr))
    tbl = gf.table
    if col_w:
        tot = sum(col_w)
        for i, cw in enumerate(col_w):
            tbl.columns[i].width = Emu(int(w * cw / tot))
    for ri, row in enumerate(rows):
        tbl.rows[ri].height = Inches(0.28)
        for ci, val in enumerate(row):
            c = tbl.cell(ri, ci)
            c.text = str(val)
            c.vertical_anchor = MSO_ANCHOR.MIDDLE
            c.margin_left = c.margin_right = Inches(0.04)
            c.margin_top = c.margin_bottom = Emu(0)
            c.fill.solid()
            c.fill.fore_color.rgb = HDR if (header_row and ri == 0) else RGBColor(255, 255, 255)
            for p in c.text_frame.paragraphs:
                p.alignment = (PP_ALIGN.CENTER if (ci > 0 and len(str(val)) <= 12)
                               else PP_ALIGN.LEFT)
                p.line_spacing = 1.0
                for r in p.runs:
                    r.font.name = FONT; r.font.size = Pt(size)
                    r.font.bold = (header_row and ri == 0)
                    r.font.color.rgb = BLACK
            tcPr = c._tc.get_or_add_tcPr()
            for tag in ("a:lnL", "a:lnR", "a:lnT", "a:lnB"):
                el = tcPr.makeelement(qn(tag), {"w": "9525", "cap": "flat"})
                fill = el.makeelement(qn("a:solidFill"), {})
                clr = fill.makeelement(qn("a:srgbClr"), {"val": "BFBFBF"})
                fill.append(clr); el.append(fill); tcPr.append(el)
    return gf


def pic(slide, name, l, t, w):
    """放圖：先找 圖檔_maxmap/，再找案件根目錄；等比縮放至寬 w。"""
    p = os.path.join(_img, name)
    if not os.path.exists(p):
        p = os.path.join(_here, name)
    if not os.path.exists(p):
        tb(slide, l, t, w, Inches(0.4), f"（圖檔缺 {name}）", 12, False, RED)
        return
    im = Image.open(p); ratio = im.height / im.width
    slide.shapes.add_picture(p, l, t, width=w, height=Emu(int(w * ratio)))


def sum_page(page, sub, rows, doubt_row=None, top=None, colw=None, doubt_to=None):
    """綜合說明總表頁（預設 7 欄 S001/S002 拆欄、12pt、圖例置標題右側）。
    doubt_row：執行疑義列索引——合併法規～情形數欄、分項走項目符號。
    colw：自訂欄寬（欄數由 rows[0] 長度決定）。單一案場案件用 SUM_COLW_1SITE
    ＋ SUM_HDR_1SITE（6 欄：情形欄加寬、多一欄「翻轉條件／須辦事項」），
    因為把 S002 欄留空只會浪費 2.9 吋版面。
    doubt_to：疑義列合併終點欄索引，預設 欄數-3（7 欄制留兩個判定欄）。"""
    s = new(); header(s, "綜合說明", sub, page)
    # 圖例框寬須 ≤ 版面右緣：4.47+8.60=13.07 < 13.33（原 9.00 會溢出 0.14"，
    # 目視雖未切字但形狀跑出畫布，2026-07-31 修正）
    tb(s, Inches(4.47), Inches(0.67), Inches(8.60), Inches(0.3), LEGEND, 11, False, GREY)
    ncol = len(rows[0])
    gf = table(s, SUM_L, top or SUM_T, SUM_W, rows,
               col_w=colw or SUM_COLW, size=SUM_SZ, ncol=ncol)
    if doubt_row is not None:
        t = gf.table
        t.cell(doubt_row, 1).merge(t.cell(doubt_row, doubt_to if doubt_to is not None else ncol - 3))
        for p in t.cell(doubt_row, 1).text_frame.paragraphs:
            p.alignment = PP_ALIGN.LEFT
            set_bullet(p, hang=0.16)
    return s


def _lint_markdown():
    """規則28：交付字串不得含 markdown 標記（安南案實錯：`**` 字面印上簡報）。"""
    bad = []
    for i, slide in enumerate(_prs.slides, 1):
        for sh in slide.shapes:
            texts = []
            if sh.has_text_frame:
                texts.append(sh.text_frame.text)
            if getattr(sh, "has_table", False) and sh.has_table:
                for row in sh.table.rows:
                    for c in row.cells:
                        texts.append(c.text)
            for t in texts:
                if _re.search(r"\*\*|(?<!#)##|`", t or ""):
                    bad.append((i, (t or "")[:40]))
    return bad


def save():
    bad = _lint_markdown()
    if bad:
        for pg, t in bad[:10]:
            print(f"  ⚠ 第{pg}頁疑似 markdown 標記殘留：{t!r}")
        raise SystemExit("交付字串含 markdown 標記（規則28），請改用粗體屬性或移除標記")
    _prs.save(_out)
    io.open(_out + ".dump.txt", "w", encoding="utf-8").write(_dump_pptx(_out))
    print("saved", _out)
    print("slides", len(_prs.slides._sldIdLst))
