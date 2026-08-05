# -*- coding: utf-8 -*-
"""離線 PDF 初篩（應免查範圍）—— 把 2026-08-05 的驗證與踩過的坑寫成斷言。

鎖住三件事：
  1. exempt_source 判定語意：清單列的是「該狀態」的行政區，命中→該狀態、未命中→相反。
  2. 粒度三種（縣市／縣市鄉鎮／縣市鄉鎮段）與 臺/台 變體都要命中。
  3. parse_exempt_pdf 的多行項目名偵測（長名換行不可漏列——龍崎案漏 #6/#23/#26、
     其行政區被併進前一項、污染初篩，即此坑）。
  4. SUPPLEMENT（PDF 缺列項一律應查）不可被靜默移除。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path.home() / ".claude" / "skills" / "env-sensitivity-check" / "scripts"))

import exempt_source as ES  # noqa: E402
import parse_exempt_pdf as PP  # noqa: E402


def _data():
    """最小假資料集：涵蓋 應查列/免查列、三種粒度、第三級。"""
    return {"version": "115年07月31日", "items": [
        # 應查列：命中→應查、未命中→免查
        {"level": "第一級", "item_no": "2", "name": "河川區域", "status": "應查",
         "regions": ["臺南市龍崎區", "高雄市那瑪夏區"]},
        # 免查列：命中→免查、未命中→應查（山崩地滑就是這型）
        {"level": "第二級", "item_no": "1", "name": "地質敏感區（山崩與地滑）", "status": "免查",
         "regions": ["臺南市新市區", "臺北市大安區"]},
        # 縣市級粒度
        {"level": "第二級", "item_no": "3", "name": "嚴重地層下陷地區", "status": "應查",
         "regions": ["雲林縣", "彰化縣"]},
        # 段級粒度
        {"level": "第一級", "item_no": "20", "name": "飲用水水源水質保護區", "status": "應查",
         "regions": ["臺南市龍崎區番社段"]},
        # 第三級 → 應對映成「海岸管理法」
        {"level": "第三級", "item_no": "1", "name": "近岸海域", "status": "免查",
         "regions": ["澎湖縣"]},
    ]}


def _by(items, name):
    return next(it for it in items if name in it["sub_name"])


def test_ying_clause_hit_and_miss():
    """應查列：命中鄉鎮→應查；未命中→免查。"""
    r = ES.query("臺南市", "龍崎區", ["番社段"], data=_data())
    assert _by(r["items"], "河川區域")["status"] == "應查"          # 龍崎在清單
    r2 = ES.query("臺南市", "新市區", ["社內段"], data=_data())
    assert _by(r2["items"], "河川區域")["status"] == "免查"         # 新市不在→相反


def test_exempt_clause_opposite():
    """免查列：命中→免查；未命中→應查（山崩地滑龍崎未列免查清單＝應查）。"""
    r = ES.query("臺南市", "龍崎區", ["番社段"], data=_data())
    assert _by(r["items"], "山崩與地滑")["status"] == "應查"        # 龍崎不在免查清單→應查
    r2 = ES.query("臺南市", "新市區", [], data=_data())
    assert _by(r2["items"], "山崩與地滑")["status"] == "免查"       # 新市在免查清單→免查


def test_county_level_granularity():
    r = ES.query("雲林縣", "斗六市", [], data=_data())
    assert _by(r["items"], "嚴重地層下陷")["status"] == "應查"       # 縣市級命中


def test_section_level_granularity():
    """段級：同鄉鎮不同段不得誤命中。"""
    hit = ES.query("臺南市", "龍崎區", ["番社段"], data=_data())
    miss = ES.query("臺南市", "龍崎區", ["中坑子段"], data=_data())
    assert _by(hit["items"], "飲用水")["status"] == "應查"          # 番社段在清單
    assert _by(miss["items"], "飲用水")["status"] == "免查"         # 中坑子段不在→相反


def test_tai_variant():
    """臺/台 變體：清單寫『臺南市』，查『台南市』也要命中。"""
    r = ES.query("台南市", "龍崎區", ["番社段"], data=_data())
    assert _by(r["items"], "河川區域")["status"] == "應查"


def test_level3_maps_to_coastal():
    r = ES.query("臺南市", "龍崎區", [], data=_data())
    assert _by(r["items"], "近岸海域")["level"] == "海岸管理法"


def test_supplement_always_ying():
    """PDF 缺列項（森林系/莫拉克）一律應查，且來源標 supplement。"""
    r = ES.query("臺南市", "龍崎區", [], data=_data())
    sup = [it for it in r["items"] if it["source"] == "supplement"]
    assert sup, "SUPPLEMENT 不可為空（PDF 缺列項會被靜默漏查）"
    assert all(it["status"] == "應查" for it in sup)
    names = {it["sub_name"] for it in sup}
    assert any("國有林" in n for n in names) and any("莫拉克" in n for n in names)


# ── parse_exempt_pdf：多行項目名偵測（長名換行不可漏列）──────────────────
def test_rowhead_single_line_name():
    lines = ["2", "河川區域", "應查", "臺南市龍崎區、高雄市那瑪夏區"]
    assert PP._row_head(lines, 0) == ("河川區域", "應查", 3)


def test_rowhead_multiline_name():
    """長名換行（#6 國家公園）：名稱跨兩行也要正確抓到，回傳合併名。"""
    lines = ["6", "國家公園區內之特別景觀區、", "生態保護區", "應查", "花蓮縣秀林鄉"]
    got = PP._row_head(lines, 0)
    assert got == ("國家公園區內之特別景觀區、生態保護區", "應查", 4)


def test_rowhead_not_a_row():
    """區域清單裡的一般文字不可被誤判為列起點。"""
    lines = ["臺南市龍崎區", "臺南市新市區", "高雄市那瑪夏區"]
    assert PP._row_head(lines, 0) is None


def test_rowhead_digit_without_status():
    """數字後很快又是數字、沒有應查/免查 → 不是列。"""
    lines = ["3", "3", "洪水平原一級管制區", "應查", "新北市三重區"]
    assert PP._row_head(lines, 0) is None
