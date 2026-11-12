# -*- coding: utf-8 -*-
"""公有土地名冊 0 筆／失敗的區分（2026-11 基隆案：重測後新段碼名冊 0 筆，舊段有 324 筆）
＋ persite_stats 臨路成果欄位被人工改成文字不得讓整份統計寫不出來。純邏輯、mock API。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import public_land as PL  # noqa: E402
import persite_stats as ps  # noqa: E402


def _rec(no, name="國有"):
    return {"No": no, "OWNER": [{"OwnerName": name, "OWNERNAME": name, "ManagerName": "國產署",
                                  "MANAGERNAME": "國產署"}]}


def _fetch_factory(table):
    def fetch(_c, _t, sec, unit=None):
        v = table[(unit, sec)]
        if isinstance(v, Exception):
            raise v
        return v
    return fetch


def test_zero_roster_with_control_is_not_private_and_flagged():
    fetch = _fetch_factory({("CD", "0206"): [], ("CD", "0053"): [_rec("0001-0000")] * 3})
    lines = []
    idx, have, st = PL.query_rosters([("CD", "0206")], fetch=fetch,
                                     candidates=lambda o, s: ["0053"], say=lines.append)
    assert ("CD", "0206") not in have, "0 筆的段不可進 have_roster（否則會被推論為私有）"
    assert st[("CD", "0206")]["state"] == "empty_confirmed"
    assert any("查詢成功但 0 筆" in l and "不可推論為私有" in l for l in lines)


def test_zero_roster_without_working_control_is_suspect_or_unverified():
    fetch = _fetch_factory({("CD", "0206"): [], ("CD", "0053"): RuntimeError("boom")})
    _, _, st = PL.query_rosters([("CD", "0206")], fetch=fetch,
                                candidates=lambda o, s: ["0053"], say=lambda *_: None)
    assert st[("CD", "0206")]["state"] == "empty_suspect"
    fetch2 = _fetch_factory({("CD", "0206"): []})
    _, _, st2 = PL.query_rosters([("CD", "0206")], fetch=fetch2,
                                 candidates=lambda o, s: [], say=lambda *_: None)
    assert st2[("CD", "0206")]["state"] == "empty_unverified"


def test_failure_distinguished_from_zero_in_summary():
    fetch = _fetch_factory({("CD", "0206"): [], ("CD", "0208"): TimeoutError("t"),
                            ("CD", "0053"): [_rec("0010-0000")]})
    idx, have, st = PL.query_rosters([("CD", "0206"), ("CD", "0208"), ("CD", "0053")],
                                     fetch=fetch, candidates=lambda o, s: [], say=lambda *_: None)
    assert st[("CD", "0208")]["state"] == "failed"
    assert st[("CD", "0206")]["state"] == "empty_confirmed"   # 同批 0053 有資料當對照組
    assert have == {("CD", "0053")}
    s = PL.roster_summary(st)
    assert "查詢失敗：CD/0208" in s and "查詢成功但名冊 0 筆" in s and "CD/0206" in s


def test_different_sections_grouped_by_own_office_sec():
    """同批不同段碼各自查名冊，索引鍵含 (office, sec)。"""
    fetch = _fetch_factory({("CD", "0053"): [_rec("0010-0000")], ("CD", "0054"): [_rec("0010-0000")]})
    idx, have, _ = PL.query_rosters([("CD", "0053"), ("CD", "0054")], fetch=fetch,
                                    say=lambda *_: None)
    assert ("CD", "0053", "10") in idx and ("CD", "0054", "10") in idx
    assert have == {("CD", "0053"), ("CD", "0054")}


# ── persite_stats ───────────────────────────────────────────────────────────
def test_road_row_text_value_does_not_crash():
    r = {"臨路狀態": "直接臨路", "鄰接路寬中位數_m": "須重測（原值含鐵路）",
         "陸域面積_m2": 1234.4, "水域面積_m2": "", "主要地形": "平地"}
    out = ps.road_row_stats(r)
    assert out["鄰接路寬中位數_m"] == "須重測（原值含鐵路）"
    assert "鄰接路寬中位數_m備註" in out
    assert out["陸域_m2"] == 1234.0 and out["水域_m2"] is None


def test_road_row_numeric_unchanged_and_missing_columns_ok():
    out = ps.road_row_stats({"臨路狀態": "A", "鄰接路寬中位數_m": 8.126, "陸域面積_m2": 10.6,
                             "水域面積_m2": 0})
    assert out["鄰接路寬中位數_m"] == 8.13 and out["陸域_m2"] == 11.0 and out["水域_m2"] == 0.0
    assert not any(k.endswith("備註") for k in out)
    out2 = ps.road_row_stats({})            # 欄位全缺不得 KeyError
    assert out2["鄰接路寬中位數_m"] is None and out2["陸域_m2"] is None
