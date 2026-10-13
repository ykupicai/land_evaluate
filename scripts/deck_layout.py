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
from PIL import Image

__all__ = [
    "Inches", "Pt", "Emu", "PP_ALIGN", "RGBColor",
    "FONT", "FONT_HEAVY", "FONT_SEMI", "BLACK", "RED", "TOPIC", "ACCENT",
    "CAUTION", "GREY", "HDR", "BORDER", "BLUE", "W", "H",
    "PIC_L", "PIC_T", "PIC_W", "COL_L", "COL_W", "TBL_W", "TBL_SZ",
    "LEGEND", "SUM_L", "SUM_W", "SUM_T", "SUM_COLW", "SUM_SZ", "SUM_HDR",
    "SUM_COLW_1SITE", "SUM_HDR_1SITE",
    "init", "new", "cover", "header", "tb", "set_bullet", "bullets",
    "table", "pic", "sum_page", "save",
]

# ── 樣式 Token（訓練專案「簡報版面設計規範.yaml」定為權威，2026-08-19）──────────
# 字型：思源宋體家族（襯線），取代舊 v1 的微軟正黑體。
FONT = "思源宋體"                    # 內文/表格預設（沿用變數名 FONT 以相容各案 build_deck）
FONT_HEAVY = "思源宋體 Heavy"        # 頁首主標、封面案名
FONT_SEMI = "思源宋體 Medium"        # 頁首副標（2026-09-13 字型三級分工改 Medium；變數名沿用以相容各案 build_deck）
BLACK = RGBColor(0, 0, 0)
RED = RGBColor(0xC0, 0, 0)            # 缺圖警告等
TOPIC = RGBColor(0xA6, 0x51, 0x49)   # 頁首副標（磚紅，YAML section_topic_label.default）
ACCENT = RGBColor(0x4A, 0x8C, 0x7A)  # 頁首色條（灰綠，case_accent；逐案可於 build_deck 覆寫）
CAUTION = RGBColor(0xE9, 0x45, 0x41) # 提醒紅字（YAML caution_text）
GREY = RGBColor(0x80, 0x80, 0x80)
HDR = RGBColor(0xE7, 0xE6, 0xE6)     # 表頭淺灰（YAML table_header_fill）
BORDER = "808080"                     # 表格框線色（YAML table_border，約 0.75pt）
BLUE = RGBColor(0x1F, 0x38, 0x64)    # 封面雙線（YAML cover_footer_line）
W, H = Inches(13.333), Inches(7.5)

# 內頁版式（YAML content_split_page 定為權威：左欄較寬靠左、右圖更寬更靠左、頂到頁首下）
PIC_L, PIC_T, PIC_W = Inches(4.30), Inches(1.00), Inches(8.90)
COL_L, COL_W = Inches(0.12), Inches(4.00)
TBL_W = Inches(3.90)
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


_missing_pics: list = []      # 缺圖登記簿：(頁碼, 檔名)；save() 會據此硬停


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
    _missing_pics.clear()      # 每份簡報獨立計算缺圖
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
    _grid = None  # YAML 定案：純白底，不再用虛線網格 PNG
    _prs = Presentation()
    _prs.slide_width, _prs.slide_height = W, H
    _blank = _prs.slide_layouts[6]


def new():
    """新增一頁。底色純白、無整頁外框、無網格底（YAML 定案）；
    頁首下方的黑分隔線由 header() 繪製，故這裡不必畫任何底。"""
    return _prs.slides.add_slide(_blank)


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


def set_bullet(p, char="■", hang=0.22):
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


def _retype(box, name):
    """把 box 內所有 run 的字型改為 name（tb() 預設用 FONT，頁首/封面需改 Heavy/Medium）。"""
    for p in box.text_frame.paragraphs:
        for r in p.runs:
            r.font.name = name
    return box


def header(slide, main, sub, page, chapter=None):
    """頁首（YAML header_bar 定案）：[章節數字]＋灰綠色條＋思源宋體 Heavy 主標(44pt)
    ＋磚紅 Medium 副標(28pt)＋黑分隔線(0.75pt)＋右上頁碼。

    chapter：僅「一級主題轉換」的章首頁才給（壹/貳/參…）；同章子頁不重複顯示章節字，
    只換副標（YAML writing_conventions 末條）。留白時只畫色條＋主標，維持識別。"""
    if chapter:
        cb = tb(slide, Inches(0.0), Inches(0.056), Inches(1.014), Inches(0.841),
                chapter, 44, True, BLACK, PP_ALIGN.CENTER)
        cb.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        _retype(cb, FONT_HEAVY)
    # 灰綠色條
    bar = slide.shapes.add_shape(1, Inches(0.915), Inches(0.108), Inches(0.139),
                                 Inches(0.736))
    bar.fill.solid(); bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background(); bar.shadow.inherit = False
    # 主標（Heavy 44pt，垂直置中於頁首帶）
    mt = tb(slide, Inches(1.13), Inches(0.056), Inches(6.2), Inches(0.841), main, 44,
            True, BLACK)
    mt.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    _retype(mt, FONT_HEAVY)
    # 副標（Medium 28pt 磚紅，緊接主標右側）
    if sub:
        sub_l_in = min(1.13 + 0.62 * len(main) + 0.18, 8.6)
        avail_in = 13.0 - sub_l_in
        # ★2026-08-21 修正：副標長度不一，固定 28pt 常超出可用寬度、往下溢出撞到
        # 分隔線（word_wrap 換行但box高度只留一行）。全形中文字約 1 em 寬
        # （size_pt/72 吋），估需要寬度、超出時等比縮小字級到夠放一行為止
        # （下限 16pt，避免小到不可讀；仍超出則保留 16pt 讓它換行，不強行硬擠）。
        sub_size = 28
        need_in = len(sub) * (sub_size / 72.0)
        if need_in > avail_in > 0:
            sub_size = max(16, int(sub_size * avail_in / need_in))
        sub_l = Inches(sub_l_in)
        st = tb(slide, sub_l, Inches(0.294), Inches(13.0) - sub_l, Inches(0.572), sub,
                sub_size, True, TOPIC)
        st.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        _retype(st, FONT_SEMI)
    # 分隔線（黑 0.75pt，YAML L0.121 W12.891 T0.866）
    ln = slide.shapes.add_shape(1, Inches(0.121), Inches(0.866), W - Inches(0.442),
                                Pt(0.75))
    ln.fill.solid(); ln.fill.fore_color.rgb = BLACK; ln.line.fill.background()
    ln.shadow.inherit = False
    # 頁碼（右上，Heavy）
    pn = tb(slide, W - Inches(1.1), Inches(0.295), Inches(0.9), Inches(0.4), str(page),
            16, False, BLACK, PP_ALIGN.RIGHT)
    _retype(pn, FONT_HEAVY)


def cover(line1, line2, line3, date_str,
          sizes=(32, 30, 34),
          company=("衍古開發顧問有限公司", "Y.KU Planning Intl. Co.")):
    """封面（YAML cover 定案）：三行置中案名（思源宋體 Heavy）＋公司識別＋雙藍線(1F3864,
    2.5+1.0pt)＋日期。company=None 可關閉公司識別。回傳 slide。"""
    s = new()
    for txt, sz, top in [(line1, sizes[0], 2.20), (line2, sizes[1], 2.98),
                         (line3, sizes[2], 3.82)]:
        _retype(tb(s, Inches(0.0), Inches(top), W, Inches(0.75), txt, sz, True, BLACK,
                   PP_ALIGN.CENTER), FONT_HEAVY)
    # 公司識別（雙線上方置中）
    if company:
        cc = tb(s, Inches(0.0), Inches(6.00), W, Inches(0.86), list(company), 16, True,
                BLACK, PP_ALIGN.CENTER)
        ps = cc.text_frame.paragraphs
        for r in ps[0].runs:
            r.font.name = FONT_HEAVY
        for p in ps[1:]:
            for r in p.runs:
                r.font.name = "Times New Roman"; r.font.bold = False; r.font.size = Pt(12)
    # 封面雙藍線（YAML footer_lines：左0.38 寬~12.6）
    for y, wd in [(6.94, 2.5), (6.99, 1.0)]:
        ln = s.shapes.add_shape(1, Inches(0.38), Inches(y), W - Inches(0.76), Pt(wd))
        ln.fill.solid(); ln.fill.fore_color.rgb = BLUE; ln.line.fill.background()
        ln.shadow.inherit = False
    tb(s, Inches(0.0), Inches(7.02), W, Inches(0.4), date_str, 16, False, BLACK,
       PP_ALIGN.CENTER)
    return s


def table(slide, l, t, w, rows, col_w=None, size=11, header_row=True, ncol=None,
          align=None):
    """align：逐欄對齊字串，如 "clll"（c=置中 l=靠左 r=靠右），長度不足者其餘沿用預設。
    預設啟發式＝「非第一欄且字數 ≤12 就置中」，對數值欄很好，但**條文／說明欄會出現
    長句靠左、短句置中的參差**（2026-08-04 保育區頁實見）→ 這種欄位請明確給 align。"""
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
                a = (align or "")[ci] if align and ci < len(align) else None
                if a:
                    p.alignment = {"c": PP_ALIGN.CENTER, "l": PP_ALIGN.LEFT,
                                   "r": PP_ALIGN.RIGHT}.get(a, PP_ALIGN.LEFT)
                else:
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
                clr = fill.makeelement(qn("a:srgbClr"), {"val": BORDER})
                fill.append(clr); el.append(fill); tcPr.append(el)
    return gf


def pic(slide, name, l, t, w):
    """放圖：先找 圖檔_maxmap/，再找案件根目錄；等比縮放至寬 w。

    ★缺圖一律登記到 `_missing_pics`，`save()` 時硬停（2026-08-27 麻豆案實踩）：
    原本只在版面上放一行紅字就繼續，結果兩份變更編定簡報各缺 5 張圖仍「建置成功」，
    要到逐頁目視才發現。簡報每頁的圖是該頁主題的成果，缺圖等同該頁沒有依據——
    這種錯必須在產出當下擋下來，不能靠人眼補救。
    """
    p = os.path.join(_img, name)
    if not os.path.exists(p):
        p = os.path.join(_here, name)
    if not os.path.exists(p):
        _missing_pics.append((len(_prs.slides._sldIdLst), name))
        tb(slide, l, t, w, Inches(0.4), f"（圖檔缺 {name}）", 12, False, RED)
        return
    im = Image.open(p); ratio = im.height / im.width
    slide.shapes.add_picture(p, l, t, width=w, height=Emu(int(w * ratio)))


def sum_page(page, sub, rows, doubt_row=None, top=None, colw=None, doubt_to=None,
             chapter=None, size=None):
    """綜合說明總表頁（預設 7 欄 S001/S002 拆欄、12pt、圖例置標題右側）。
    doubt_row：執行疑義列索引——合併法規～情形數欄、分項走項目符號。
    colw：自訂欄寬（欄數由 rows[0] 長度決定）。單一案場案件用 SUM_COLW_1SITE
    ＋ SUM_HDR_1SITE（6 欄：情形欄加寬、多一欄「翻轉條件／須辦事項」），
    因為把 S002 欄留空只會浪費 2.9 吋版面。
    doubt_to：疑義列合併終點欄索引，預設 欄數-3（7 欄制留兩個判定欄）。"""
    s = new(); header(s, "綜合說明", sub, page, chapter)
    # 圖例置分隔線(0.866)下方一列、靠右（YAML 頁首改大後，原置頁首右側會與 28pt 副標
    # 重疊，2026-08-19 改）。底緣 ~1.16" < 表格起點 SUM_T 1.20"，不壓表。
    tb(s, SUM_L, Inches(0.92), SUM_W, Inches(0.24), LEGEND, 11, False, GREY,
       PP_ALIGN.RIGHT)
    ncol = len(rows[0])
    # size：列數多到 12pt 撐破版面時降一級（2026-09-04 兩基地合併版新增）。
    # ⚠ 降字級只是最後手段，先想能不能精簡文字——總表塞不下常代表話講太長。
    gf = table(s, SUM_L, top or SUM_T, SUM_W, rows,
               col_w=colw or SUM_COLW, size=size or SUM_SZ, ncol=ncol)
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
    if _missing_pics:
        for pg, nm in _missing_pics[:10]:
            print(f"  ✖ 第{pg}頁缺圖：{nm}")
        raise SystemExit(
            f"簡報缺 {len(_missing_pics)} 張圖，未存檔。"
            "請確認圖檔已產出、且檔名與內容檔引用一致"
            "（成果圖命名慣例：<案場ID>_<案場名稱>_<主題>.png）")
    bad = _lint_markdown()
    if bad:
        for pg, t in bad[:10]:
            print(f"  ⚠ 第{pg}頁疑似 markdown 標記殘留：{t!r}")
        raise SystemExit("交付字串含 markdown 標記（規則28），請改用粗體屬性或移除標記")
    _prs.save(_out)
    io.open(_out + ".dump.txt", "w", encoding="utf-8").write(_dump_pptx(_out))
    print("saved", _out)
    print("slides", len(_prs.slides._sldIdLst))
