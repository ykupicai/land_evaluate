import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import area_label_logic as al


def test_dup_detected_even_if_first_has_blank_or_zero_area():
    """錯法：先出現且面積空白／0 者不留紀錄，後出現的正值會被保留（與順序有關）。"""
    for first in (None, "", 0, "abc"):
        a = al.build_registered_index([("12", first), ("12", 500), ("13", 40)])
        b = al.build_registered_index([("12", 500), ("12", first), ("13", 40)])
        assert a == b == {"13": 40.0}


def test_unique_positive_kept_zero_dropped():
    idx = al.build_registered_index([("1", "100.5"), ("2", 0), ("3", None), (" 4 ", 7)])
    assert idx == {"1": 100.5, "4": 7.0}


def test_sum_registered_falls_back_to_geometry_area():
    total, n_geo, n = al.sum_registered([("1", 90.0), ("2", 50.0)], {"1": 100.0})
    assert (total, n_geo, n) == (150.0, 1, 2)


def test_label_text_variants():
    t = al.area_label_text(150.0, 1, 2)
    assert "含圖面積計算" in t and "0.0150 公頃" in t
    assert "（圖面積）" in al.area_label_text(10, 2, 2)
    assert "含圖面積" not in al.area_label_text(10, 0, 2)
    assert "基地面積合計（8處）" in al.area_label_text(10, 0, 2, total_mode=True, n_total_parts=8)
    # force_total 不改標題
    assert al.area_label_text(10, 0, 2, total_mode=True, force_total=True).startswith("基地面積\n")


def test_total_mode():
    assert al.is_total_mode(2, True, 6)
    assert al.is_total_mode(7, False, 6)
    assert not al.is_total_mode(6, False, 6)


def test_field_lists_aligned_with_persite_stats():
    ps = pytest.importorskip("persite_stats")
    assert tuple(al.REG_COLS) == tuple(ps._REG_COLS)


def test_pick_field():
    assert al.pick_field(["x", "標示面積"], al.REG_COLS) == "標示面積"
    assert al.pick_field(["x"], al.REG_COLS) is None


def test_make_maps_guards_empty_parts():
    """site_area_labels 在 parts 為空時須先回 []（max([]) 會 ValueError）。QGIS 不可用，以原始碼斷言。"""
    src = (Path(__file__).resolve().parents[1] / 'scripts' / 'make_maps.py').read_text(encoding='utf-8')
    i = src.index("def site_area_labels")
    body = src[i:i + 2500]
    assert body.index("if not parts:") < body.index("max(parts")


def test_reg_cols_include_biaoshi_area_with_priority():
    """行為變更鎖定：只有「標示面積」欄時，基地面積標註改用該欄；有「登面積」則仍以它優先。"""
    assert al.pick_field(["地號", "標示面積"], al.REG_COLS) == "標示面積"
    assert al.pick_field(["標示面積", "登記面積"], al.REG_COLS) == "登記面積"
    assert al.pick_field(["標示面積", "登面積", "登記面積"], al.REG_COLS) == "登面積"
    idx = al.build_registered_index([("1", 123.4)])
    assert al.sum_registered([("1", 99.0)], idx)[0] == 123.4
