# -*- coding: utf-8 -*-
"""全國都市計畫使用分區服務備援（urbanplan_image.NATIONAL）的純函式回歸測試。

連網的取圖／圖例（fetch_national_zone_image、national_legend）不在此測，
2026-11-26 以布袋見龍段876 案實際出圖驗證（乙種工業區）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import urbanplan_image as ui  # noqa: E402


def test_group_label_short_list_kept_whole():
    assert ui.group_label(["工業區"]) == "工業區"
    assert ui.group_label(["保護區", "農業區"]) == "保護區、農業區"


def test_group_label_long_list_truncated_with_deng():
    assert ui.group_label(["工業區", "鹽田區", "港埠專用區"]) == "工業區、鹽田區等"


def test_group_label_dedup_and_blank():
    assert ui.group_label(["工業區", "工業區", " ", "鹽田區"]) == "工業區、鹽田區"


def test_national_scale_matches_print_scale():
    # 1/750、300dpi → 每像素 0.0635 m；寬 190.5 m → 3000 px
    bbox = (0.0, 0.0, 190.5, 100.0)
    assert abs(ui.national_scale(bbox, 3000, 300) - 750.0) < 1e-6


def test_fill_scale_window_matches_service_layers():
    # 圖層3（填色）1/2,501～1/50,000、圖層4（框線名稱）≤1/2,500：取填色的比例尺必須落在圖層3範圍內
    n = ui.NATIONAL
    assert n["fill_min_scale"] > n["label_max_scale"]
    assert n["fill_min_scale"] >= 2501 and n["fill_max_scale"] <= 50000


def test_category_filter_escapes_quote():
    assert ui.category_filter("工業區") == "分區類別='工業區'"
    assert ui.category_filter("a'b") == "分區類別='a''b'"


def test_fill_size_clamps_into_fill_layer_range():
    bbox = (0.0, 0.0, 200.0, 140.0)
    s, fw, fh = ui._fill_size(bbox, 750, 300)        # 1/750 → 填色改用 1/2,600
    assert s == ui.NATIONAL["fill_min_scale"]
    assert fw > 0 and abs(fh / fw - 0.7) < 0.01
    s2, _, _ = ui._fill_size(bbox, 80000, 300)       # 太小比例尺 → 夾到 1/50,000
    assert s2 == ui.NATIONAL["fill_max_scale"]
