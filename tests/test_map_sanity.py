# -*- coding: utf-8 -*-
"""出圖自我檢查的單測——把「靜默出錯」那三次實錯寫成斷言。

這條產線最貴的失敗是「回報成功、圖卻是空的」。本檔鎖住兩件事：
  1. 該擋的要擋：圖層 0 筆（龍崎案）、成品圖整片空白（底圖被鎖）→ 必須判失敗。
  2. **不該擋的絕不能擋**：資訊不足、主題不在把關範圍、PIL 缺席、正常的圖
     → 一律放行。誤殺會把整條產線擋掉，比漏抓更貴。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest  # noqa: E402

import map_sanity as ms  # noqa: E402


class TestThemeLayers:
    """出圖前的圖層筆數檢查。"""

    def test_longqi_case_zero_zone_features_blocked(self):
        """2026-08-04 龍崎案：縣市欄空白 → 功能分區 0 筆，主題3 出成空白圖卻記「成功」。
        現在必須擋下來。"""
        counts = {"基地地籍": 4, "國土功能分區": 0}
        reason = ms.check_theme_layers("國土功能分區", counts)
        assert reason is not None
        assert "國土功能分區" in reason and "0 筆" in reason

    def test_zero_neighbour_blocks_zoning_themes(self):
        counts = {"基地地籍": 4, "宗地-使用分區": 0, "宗地-使用地編定": 0}
        assert ms.check_theme_layers("使用分區", counts) is not None
        assert ms.check_theme_layers("使用地編定", counts) is not None

    def test_empty_base_blocks_every_theme(self):
        """基地 0 筆＝這個案場整個沒抓到，任何主題都不該出圖。"""
        counts = {"基地地籍": 0, "宗地-使用分區": 99}
        for theme in ("使用分區", "使用地編定", "國土功能分區", "正射套疊", "臨路判定"):
            assert ms.check_theme_layers(theme, counts) is not None, theme

    def test_normal_counts_pass(self):
        counts = {"基地地籍": 4, "宗地-使用分區": 634, "宗地-使用地編定": 634,
                  "國土功能分區": 4}
        for theme in ("使用分區", "使用地編定", "國土功能分區"):
            assert ms.check_theme_layers(theme, counts) is None, theme

    def test_missing_info_never_blocks(self):
        """取不到筆數（dict 沒這個 key）一律放行——資訊不足不可誤殺。"""
        assert ms.check_theme_layers("使用分區", {}) is None
        assert ms.check_theme_layers("國土功能分區", {"基地地籍": 4}) is None

    def test_unguarded_themes_pass_through(self):
        """正射套疊／國土利用調查／臨路判定在 make_maps 主題迴圈裡本來就各有前置
        判斷，本模組不重複攔（避免兩套判準漂移）。"""
        counts = {"基地地籍": 4, "宗地-使用分區": 0}
        for theme in ("正射套疊", "國土利用調查", "臨路判定"):
            assert ms.check_theme_layers(theme, counts) is None, theme


class TestBlankImage:
    """出圖後的成品空白檢查。"""

    @staticmethod
    def _png(tmp_path, name, painter):
        Image = pytest.importorskip("PIL.Image")
        im = Image.new("RGB", (400, 300), (255, 255, 255))
        painter(im)
        p = tmp_path / name
        im.save(p)
        return p

    def test_all_white_flagged(self, tmp_path):
        """底圖被鎖／tif 壞掉時的樣子：整張純白。"""
        p = self._png(tmp_path, "blank.png", lambda im: None)
        reason = ms.check_blank_image(p)
        assert reason is not None and "空白" in reason

    def test_normal_map_passes(self, tmp_path):
        """有主題色的正常成果圖不可被誤判。畫一塊佔約 1/4 的色塊即足以通過。"""
        def paint(im):
            for x in range(0, 200):
                for y in range(0, 150):
                    im.putpixel((x, y), (200, 160, 90))
        p = self._png(tmp_path, "normal.png", paint)
        assert ms.check_blank_image(p) is None

    def test_sparse_but_real_map_passes(self, tmp_path):
        """內容稀疏但不是空白（圖例框＋比例尺＋紅框那種程度）仍須放行——
        門檻的任務是抓「完全空白」，不是抓「稀疏」。"""
        def paint(im):
            for x in range(0, 400):        # 一條橫線 ≈ 2% 面積
                for y in range(0, 8):
                    im.putpixel((x, y), (192, 0, 0))
        p = self._png(tmp_path, "sparse.png", paint)
        assert ms.check_blank_image(p) is None

    def test_unreadable_file_never_blocks(self, tmp_path):
        """讀不開不代表圖是壞的（交給既有的檔案存在性閘門）——檢查本身不可製造失敗。"""
        p = tmp_path / "nope.png"
        assert ms.check_blank_image(p) is None
        bad = tmp_path / "bad.png"
        bad.write_bytes(b"not a png")
        assert ms.check_blank_image(bad) is None

    def test_pil_missing_skips_check(self, tmp_path, monkeypatch):
        """PIL 缺席時靜默跳過，不可讓出圖流程掛掉。"""
        monkeypatch.setitem(sys.modules, "PIL", None)
        monkeypatch.setitem(sys.modules, "PIL.Image", None)
        assert ms.check_blank_image(tmp_path / "whatever.png") is None
