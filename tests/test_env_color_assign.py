# -*- coding: utf-8 -*-
"""env_color_assign（環敏套疊圖配色）——把 2026-09-14 內湖保護區案的撞色事故鎖成斷言。

事故：山坡地規則層固定色 (148,103,189) 剛好等於色環第 3 格；捷運禁限建沒有固定色、
依「命中順序的索引」對色環取模，剛好也落在第 3 格 → 兩層在圖上變成一模一樣的紫色，
圖例也分不出誰是誰。修法＝改用「跳過已占用顏色」的配色，而不是索引直接對色環取模。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from env_color_assign import assign_layer_colors, is_full_coverage  # noqa: E402

PALETTE = [(214, 39, 40), (255, 127, 14), (148, 103, 189), (23, 190, 207),
           (188, 189, 34), (227, 119, 194), (44, 160, 44), (31, 119, 180)]


def test_無語意色圖層不可撞上前面圖層的語意色():
    """內湖案原始情境重現：民航(無色)、山坡地(=palette[2])、捷運(無色)、山崩地滑(=palette[1])。"""
    resolved = [
        ("民航禁限建", "a.shp", {"rgb": None}),
        ("山坡地", "b.shp", {"rgb": (148, 103, 189)}),
        ("捷運禁限建", "c.shp", {"rgb": None}),
        ("山崩地滑", "d.shp", {"rgb": (255, 127, 14)}),
    ]
    colors = assign_layer_colors(resolved, PALETTE)
    assert colors["山坡地"] == (148, 103, 189)
    assert colors["山崩地滑"] == (255, 127, 14)
    # 核心斷言：捷運不可以撞上山坡地的語意色（舊版索引式取色會在這裡失敗）
    assert colors["捷運禁限建"] != colors["山坡地"]
    # 全部圖層彼此都要可區辨
    assert len(set(colors.values())) == len(colors)


def test_全部無語意色時依序取色且不重複():
    resolved = [(f"層{i}", "x.shp", {"rgb": None}) for i in range(4)]
    colors = assign_layer_colors(resolved, PALETTE)
    assert list(colors.values()) == PALETTE[:4]


def test_rule為None的命中項被略過():
    resolved = [("查無對應圖資", None, None), ("山坡地", "b.shp", {"rgb": (148, 103, 189)})]
    colors = assign_layer_colors(resolved, PALETTE)
    assert "查無對應圖資" not in colors
    assert colors["山坡地"] == (148, 103, 189)


def test_色環全被語意色占滿仍能運作不crash():
    resolved = [(f"語意{i}", "x.shp", {"rgb": PALETTE[i]}) for i in range(len(PALETTE))]
    resolved.append(("多出來的無色層", "y.shp", {"rgb": None}))
    colors = assign_layer_colors(resolved, PALETTE)
    assert len(colors) == len(PALETTE) + 1


# ── 全覆蓋改畫斜線的判定（2026-09-24 阿里山案：圖層切成多塊就判不成全覆蓋） ──────
SITE = 828_129.93


def test_實際覆蓋100_且有其他圖層_改畫斜線():
    assert is_full_coverage(SITE, SITE, n_hits=8)


def test_單一命中項即使100也維持填色():
    """只有一層時改外框，邊界多半在圖框外、畫面近乎全空（湖口案）。"""
    assert not is_full_coverage(SITE, SITE, n_hits=1)


def test_未達99不改():
    assert not is_full_coverage(0.95 * SITE, SITE, n_hits=8)   # 森林區 95%
    assert is_full_coverage(0.99 * SITE, SITE, n_hits=2)       # 門檻含等於


def test_要傳實際覆蓋而不是NGIS單列面積():
    """錯法鎖成斷言：NGIS 自來水回兩列 818,802.20＋9,327.76，最大列只有 98.87%——
    拿它判定會漏判；實際 dissolve 覆蓋 828,129.93（100%）才是正確輸入。"""
    rows = [818_802.20, 9_327.76]
    assert not is_full_coverage(max(rows), SITE, n_hits=8)      # 舊錯法：漏判
    assert is_full_coverage(SITE, SITE, n_hits=8)               # 正解：實際覆蓋


def test_基地面積為零不判全覆蓋():
    assert not is_full_coverage(0.0, 0.0, n_hits=3)
