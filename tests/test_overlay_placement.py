# -*- coding: utf-8 -*-
"""overlay_placement（比例尺／圖框／角落避讓的純幾何引擎）。

這顆引擎決定每張成果圖的比例尺與範圍，2026-08-04 起連底圖抓取範圍也照它的
extent（make_env_map／make_extra_maps 修白邊）。程式裡多處註解宣稱「已單元測試」，
本檔補上實體——重點鎖：extent 幾何一致性（中心不動、長寬比＝圖框）、
比例尺階梯單調、place_overlays 對「角落全被基地占住」的病態案回 outside。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import overlay_placement as op  # noqa: E402


BBOX = (189381.0, 2535032.3, 190491.3, 2536657.9)   # 龍崎案實際 bbox


def test_extent_centered_and_aspect():
    scale, (L, R, B, T) = op.compute_scale_extent(BBOX)
    cx, cy = (BBOX[0] + BBOX[2]) / 2, (BBOX[1] + BBOX[3]) / 2
    assert abs((L + R) / 2 - cx) < 1e-6 and abs((T + B) / 2 - cy) < 1e-6
    # 圖框長寬比固定：地面寬高比必等於 MAP_W:MAP_H
    assert abs((R - L) / (T - B) - op.MAP_W / op.MAP_H) < 1e-9


def test_extent_covers_bbox_with_margin():
    _, (L, R, B, T) = op.compute_scale_extent(BBOX)
    assert L < BBOX[0] and R > BBOX[2] and B < BBOX[1] and T > BBOX[3]


def test_scale_monotonic_with_size():
    """基地越大，比例尺分母不得變小（階梯單調）。"""
    small = op.compute_scale_extent((0, 0, 100, 100))[0]
    mid = op.compute_scale_extent((0, 0, 1500, 1500))[0]
    big = op.compute_scale_extent((0, 0, 15000, 15000))[0]
    assert small <= mid <= big


def test_scale_beyond_ladder_rounds_up_to_1000():
    """超出階梯上限的全國級 bbox：取千進位上取整，不是 None。"""
    scale, _ = op.compute_scale_extent((0, 0, 400000, 400000))
    assert scale % 1000 == 0 and scale >= 400000 / (op.MAP_W / 1000.0)


def test_place_overlays_empty_map_picks_corner():
    """基地不佔任何角落 → 疊圖放得下、不退階、outside=False。"""
    pl = op.place_overlays(lambda *_: 0.0, BBOX)
    assert pl.outside is False
    assert pl.scale == op.compute_scale_extent(BBOX)[0]


def test_place_overlays_pathological_returns_outside():
    """病態案：整個圖框都是基地（每個角落矩形都相交）→ outside=True 而非硬塞。"""
    def always_hit(x1, y1, x2, y2):
        return abs((x2 - x1) * (y2 - y1))     # 任何角落矩形都「全滿」
    pl = op.place_overlays(always_hit, BBOX)
    assert pl.outside is True


def test_extent_for_respects_scale():
    (L, R, B, T) = op.extent_for(BBOX, 10000)
    assert abs((R - L) - op.MAP_W / 1000.0 * 10000) < 1e-6
    assert abs((T - B) - op.MAP_H / 1000.0 * 10000) < 1e-6
