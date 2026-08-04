# -*- coding: utf-8 -*-
"""env_prescreen 對帳配對——鎖住 2026-08-04「配錯列產生假★矛盾」的實錯。

實錯：eland 第二級#1 有三個子項（活動斷層／山崩與地滑／土石流），**題目文字完全相同**
（都把三項並列）。用關鍵字比對題目會抓到第一個子項（活動斷層＝免查），把真正的
山崩與地滑（應查）配錯成免查，憑空產生一個假的★不一致。
正解：優先比**子項名**，沒有子項名的單一項目才用題目比。
"""
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from env_prescreen import match_finding, reconcile  # noqa: E402

# eland 第二級#1 的三個子項：題目文字相同、只有 sub_name 不同、應免查不同
GEO3 = [
    {"level": "全國區域計畫法第二級", "item_no": "1", "sub_name": "地質敏感地區（活動斷層）",
     "question": "1、是否位屬地質敏感地區（活動斷層、山崩與地滑、土石流）？",
     "eland_status": "免查", "action": "免查，無需進一步比對"},
    {"level": "全國區域計畫法第二級", "item_no": "1", "sub_name": "地質敏感地區（山崩與地滑）",
     "question": "1、是否位屬地質敏感地區（活動斷層、山崩與地滑、土石流）？",
     "eland_status": "應查", "action": "應查 → TGOS 圖資套疊判斷：有交集，很可能落入該敏感區"},
    {"level": "全國區域計畫法第二級", "item_no": "1", "sub_name": "地質敏感地區（土石流）",
     "question": "1、是否位屬地質敏感地區（活動斷層、山崩與地滑、土石流）？",
     "eland_status": "免查", "action": "免查，無需進一步比對"},
    # 沒有子項名的單一項目：只能用題目比
    {"level": "全國區域計畫法第一級", "item_no": "2", "sub_name": "",
     "question": "2、是否位屬河川區域？", "eland_status": "應查",
     "action": "應查 → TGOS 圖資套疊判斷：無交集（初步排除）"},
]


def test_match_prefers_subname_not_bundled_question():
    h = match_finding(GEO3, "山崩與地滑")
    assert h["sub_name"] == "地質敏感地區（山崩與地滑）"
    assert h["eland_status"] == "應查"      # 不可配成第一個子項的「免查」


def test_match_singleitem_by_question():
    h = match_finding(GEO3, "河川區域")     # 無子項名 → 用題目
    assert h and h["item_no"] == "2"


def test_match_no_hit():
    assert match_finding(GEO3, "完全不存在的項目") is None


def _csv(text):
    p = tempfile.mktemp(suffix=".csv")
    io.open(p, "w", encoding="utf-8-sig").write(text)
    return Path(p)


def test_reconcile_no_false_conflict_on_landslide():
    """平台涉及山崩與地滑、eland 該子項應查、初篩有交集 → 一致，不可是★。"""
    tc = _csv("圖層名稱,套疊面積_m2\n歷次公告地質敏感區-山崩與地滑,312750.90\n")
    rows = reconcile(GEO3, tc)
    r = rows[0]
    assert r["eland應免查"] == "應查"
    assert "★" not in r["是否一致"] and r["是否一致"] == "一致"


def test_reconcile_real_conflict_still_flagged():
    """真矛盾仍要標★：eland 該項免查（依法不涉及）但平台涉及。"""
    findings = [{"level": "第一級", "item_no": "7", "sub_name": "",
                 "question": "7、是否位屬自然保留區？", "eland_status": "免查",
                 "action": "免查，無需進一步比對"}]
    tc = _csv("圖層名稱,套疊面積_m2\n自然保留區,8.88\n")
    r = reconcile(findings, tc)[0]
    assert "★不一致" in r["是否一致"]
