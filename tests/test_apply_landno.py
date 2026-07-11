# -*- coding: utf-8 -*-
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from apply_landno import apply_corrections, split_section


def _meta(landno="1-1", seg="坑子口", sub="後壁厝", cid="021"):
    return {"案場ID": cid, "案場名稱": "測試隊", "縣市": "桃園市", "鄉鎮區": "蘆竹區",
            "段": seg, "小段": sub, "地號原文": landno, "宗地母": "0001", "宗地子": "0001",
            "部分註記": "", "輸入使用分區": "森林區", "輸入使用地目": "林業用地",
            "輸入國土功能分區": "", "解析信心": "高", "備註": ""}


def test_split_section():
    assert split_section("後壁段") == ("後壁", "")
    assert split_section("聚興段新興小段") == ("聚興", "新興")
    assert split_section("民權一段") == ("民權一", "")


def test_exact_backfill():
    rows = [_meta()]
    lookup = [{"案場ID": "021", "原段/小段": "坑子口段後壁厝小段", "原地號": "1-1",
               "查得新段名": "後壁段", "查得新地號": "12", "查詢狀態": "成功", "備註": ""}]
    out, stats = apply_corrections(rows, lookup)
    assert stats == {"exact": 1, "mother": 0, "skip": 0}
    r = out[0]
    assert (r["段"], r["小段"], r["地號原文"]) == ("後壁", "", "12")
    assert (r["宗地母"], r["宗地子"]) == ("0012", "0000")
    assert "原坑子口" not in r["地號原文"] and "1-1" in r["備註"]


def test_mother_fallback():
    rows = [_meta(landno="2535-1", seg="草漯", sub="", cid="023")]
    rows[0]["宗地母"], rows[0]["宗地子"] = "2535", "0001"
    lookup = [{"案場ID": "023", "原段/小段": "草漯段", "原地號": "2535-1",
               "查得新段名": "", "查得新地號": "", "查詢狀態": "查無", "備註": ""}]
    out, stats = apply_corrections(rows, lookup)
    assert stats == {"exact": 0, "mother": 1, "skip": 0}
    r = out[0]
    assert r.get("比對模式") == "母號範圍"
    assert r["宗地母"] == "2535" and r["宗地子"] == ""
    assert "母號範圍" in r["備註"]


def test_untouched_rows_pass_through():
    rows = [_meta(landno="99", cid="001")]
    out, stats = apply_corrections(rows, [])
    assert out == rows and stats == {"exact": 0, "mother": 0, "skip": 0}
