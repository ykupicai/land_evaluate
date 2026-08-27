# -*- coding: utf-8 -*-
"""基地都市／非都市組成判定的單測（2026-08-26 使用者定案的三情境）。

① 全都市 → 只出使用分區　② 都市＋非都市 → 兩張都出　③ 全非都市 → 兩張都出

★ 本檔同時把 CLAUDE.md 那條「使分／使類空白**無法**區分都市土地與未登錄地」
   （2026-08-06 使用者親自更正的原則）寫成斷言：空白但不在都市計畫圖層內者，
   一律 UNKNOWN、兩張都出，不可臆測成都市土地而少出一張圖。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import urban_mix as um  # noqa: E402


class TestParcel:
    def test_zone_value_means_nonurban(self):
        assert um.classify_parcel("一般農業區", "農牧用地", False) == "nonurban"
        assert um.classify_parcel("一般農業區", None, True) == "nonurban"   # 有值優先
        assert um.classify_parcel(None, "養殖用地", False) == "nonurban"

    def test_blank_in_urban_plan_is_urban(self):
        assert um.classify_parcel(None, None, True) == "urban"
        assert um.classify_parcel("", "", True) == "urban"

    def test_urban_label_placeholder_counts_as_blank(self):
        """make_maps 會把空白填成 URBAN_LABEL，不可被誤認為「有編定值」。"""
        lab = "都市土地（使用分區另查）"
        assert um.classify_parcel(lab, lab, True) == "urban"
        assert um.classify_parcel(lab, lab, False) == "unknown"

    def test_blank_outside_urban_plan_is_unknown(self):
        """★空白且不在都市計畫圖層內＝可能是未登錄地，不可臆測為都市土地。"""
        assert um.classify_parcel(None, None, False) == "unknown"

    def test_unregistered_excluded(self):
        assert um.classify_parcel(None, None, False, True) == "unregistered"


class TestCase:
    def test_case1_all_urban_skips_landtype(self):
        """情境①：全部都市計畫區 → 只出使用分區。"""
        parcels = [(None, None, True, False)] * 28          # 員林案 28 筆全都市
        assert um.classify(parcels) == um.ALL_URBAN
        assert um.themes_to_skip(um.ALL_URBAN) == {"使用地編定"}

    def test_case2_mixed_outputs_both(self):
        """情境②：都市＋非都市 → 兩張都出。"""
        parcels = [(None, None, True, False),
                   ("一般農業區", "農牧用地", False, False)]
        assert um.classify(parcels) == um.MIXED
        assert um.themes_to_skip(um.MIXED) == set()

    def test_case3_all_nonurban_outputs_both(self):
        """情境③：全部非都市土地 → 兩張都出。"""
        parcels = [("一般農業區", "農牧用地", False, False),
                   ("特定農業區", "養殖用地", False, False)]
        assert um.classify(parcels) == um.ALL_NONURBAN
        assert um.themes_to_skip(um.ALL_NONURBAN) == set()

    def test_urban_plus_unregistered_still_all_urban(self):
        """未登錄地不參與投票：都市＋未登錄地仍算全都市（未登錄地本就無使用地編定）。"""
        parcels = [(None, None, True, False), (None, None, False, True)]
        assert um.classify(parcels) == um.ALL_URBAN

    def test_any_unknown_forces_both(self):
        """★安全側：只要有一筆判定不足，就兩張都出——寧可多一張，不可漏一張。"""
        parcels = [(None, None, True, False), (None, None, False, False)]
        assert um.classify(parcels) == um.UNKNOWN
        assert um.themes_to_skip(um.UNKNOWN) == set()

    def test_all_unregistered_is_unknown(self):
        assert um.classify([(None, None, False, True)] * 3) == um.UNKNOWN

    def test_empty_is_unknown(self):
        assert um.classify([]) == um.UNKNOWN

    def test_three_tuple_accepted(self):
        """呼叫端未帶 unregistered 時仍可用（預設 False）。"""
        assert um.classify([(None, None, True)]) == um.ALL_URBAN
