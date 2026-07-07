# -*- coding: utf-8 -*-
"""tests/test_parse_helpers.py — scripts/parse_helpers.py 單元測試"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from parse_helpers import (  # noqa: E402
    LandNoParseError,
    expand_landno_list,
    landno_display,
    load_section_index,
    normalize_county,
    parse_landno,
    parse_landno_verbose,
    resolve_section,
)

SECTION_INDEX_PATH = REPO_ROOT / "地籍圖資" / "段藉.shp"
SKIP_REAL_DATA = not SECTION_INDEX_PATH.exists()


# ---------------------------------------------------------------------------
# normalize_county
# ---------------------------------------------------------------------------


class TestNormalizeCounty:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("台北市", "臺北市"),
            ("台中市", "臺中市"),
            ("台南市", "臺南市"),
            ("台東縣", "臺東縣"),
            ("臺北市", "臺北市"),
            ("新北市", "新北市"),
            ("高雄市", "高雄市"),
            (" 臺中市 ", "臺中市"),
            ("台 北 市", "臺北市"),
            ("台北市　", "臺北市"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_county(raw) == expected

    def test_none(self):
        assert normalize_county(None) is None

    def test_empty_string(self):
        assert normalize_county("") is None

    def test_whitespace_only(self):
        assert normalize_county("   ") is None


# ---------------------------------------------------------------------------
# parse_landno
# ---------------------------------------------------------------------------


class TestParseLandno:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("331-1", ("0331", "0001")),
            ("331", ("0331", "0000")),
            ("1053", ("1053", "0000")),
            ("3", ("0003", "0000")),
            ("3-2", ("0003", "0002")),
            ("331地號", ("0331", "0000")),
            ("331-1地號", ("0331", "0001")),
            ("３３１－１", ("0331", "0001")),  # 全形數字+全形連字號
            ("331之1", ("0331", "0001")),  # 「之」作為子號分隔
            (" 331-1 ", ("0331", "0001")),  # 前後空白
        ],
    )
    def test_valid(self, raw, expected):
        assert parse_landno(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            "   ",
            "abc",
            "343(部分)",  # 部分註記應由 expand_landno_list 先處理，parse_landno 收到會失敗
            "12345",  # 超過4位數
            "331-12345",
        ],
    )
    def test_invalid_returns_none(self, raw):
        assert parse_landno(raw) is None

    def test_verbose_returns_error_with_reason(self):
        result = parse_landno_verbose("abc")
        assert isinstance(result, LandNoParseError)
        assert result.reason
        assert result.raw == "abc"

    def test_verbose_success(self):
        result = parse_landno_verbose("331-1")
        assert result == ("0331", "0001")


# ---------------------------------------------------------------------------
# expand_landno_list
# ---------------------------------------------------------------------------


class TestExpandLandnoList:
    def test_dunhao_separator(self):
        assert expand_landno_list("3、3-2、4") == [
            ("3", False),
            ("3-2", False),
            ("4", False),
        ]

    def test_comma_separator(self):
        assert expand_landno_list("331,331-1") == [
            ("331", False),
            ("331-1", False),
        ]

    def test_fullwidth_comma_and_semicolon(self):
        assert expand_landno_list("331，331-1；336") == [
            ("331", False),
            ("331-1", False),
            ("336", False),
        ]

    def test_partial_annotation(self):
        assert expand_landno_list("343(部分)") == [("343", True)]

    def test_partial_annotation_fullwidth_parens(self):
        assert expand_landno_list("343（部分）") == [("343", True)]

    def test_mixed_list_with_partial(self):
        assert expand_landno_list("331、343(部分)、336-10") == [
            ("331", False),
            ("343", True),
            ("336-10", False),
        ]

    def test_empty(self):
        assert expand_landno_list("") == []
        assert expand_landno_list(None) == []

    def test_trailing_separator_ignored(self):
        assert expand_landno_list("331、") == [("331", False)]

    def test_real_world_sample(self):
        # 設計文件樣本：「331、331-1、336-10」
        assert expand_landno_list("331、331-1、336-10") == [
            ("331", False),
            ("331-1", False),
            ("336-10", False),
        ]
        # 設計文件樣本：「城林段3、3-2…」中的地號部分
        assert expand_landno_list("3、3-2") == [("3", False), ("3-2", False)]


# ---------------------------------------------------------------------------
# landno_display（反向組字串，含 round-trip 檢查）
# ---------------------------------------------------------------------------


class TestLandnoDisplay:
    @pytest.mark.parametrize(
        "mother, child, expected",
        [
            ("0331", "0001", "331-1"),
            ("0331", "0000", "331"),
            ("1053", "0000", "1053"),
            ("0003", "0002", "3-2"),
        ],
    )
    def test_display(self, mother, child, expected):
        assert landno_display(mother, child) == expected

    @pytest.mark.parametrize(
        "raw",
        ["331-1", "331", "1053", "3-2", "9999-9999"],
    )
    def test_round_trip(self, raw):
        mother, child = parse_landno(raw)
        assert landno_display(mother, child) == raw


# ---------------------------------------------------------------------------
# load_section_index / resolve_section — 使用真實 段藉.shp 資料驗證
# ---------------------------------------------------------------------------


@pytest.mark.skipif(SKIP_REAL_DATA, reason="段藉.shp 不存在，略過真實資料測試")
class TestSectionIndexRealData:
    @pytest.fixture(scope="class")
    def section_df(self):
        return load_section_index(str(SECTION_INDEX_PATH))

    def test_load_returns_dataframe_with_expected_rows(self, section_df):
        # 設計文件記載全台段/小段索引為 15,385 筆
        assert len(section_df) == 15385

    def test_load_has_expected_columns(self, section_df):
        for col in ["縣市", "鄉鎮", "段", "小段", "段小段碼"]:
            assert col in section_df.columns

    def test_load_is_cached(self):
        df1 = load_section_index(str(SECTION_INDEX_PATH))
        df2 = load_section_index(str(SECTION_INDEX_PATH))
        assert df1 is df2

    def test_resolve_wugu_gengliao(self, section_df):
        # 新北市 五股區 更寮段 更寮小段 -> 段小段碼 FB0361（實測資料）
        codes = resolve_section(section_df, "新北市", "五股區", "更寮", "更寮")
        assert codes == ["FB0361"]

    def test_resolve_with_taiwan_style_county_input(self, section_df):
        # 索引中縣市多為「臺」開頭，用「台」查詢應正規化後仍能命中
        codes_tai = resolve_section(section_df, "臺北市", "中正區", "永昌", "一")
        codes_taiwan = resolve_section(section_df, "台北市", "中正區", "永昌", "一")
        assert codes_tai == codes_taiwan
        assert codes_tai == ["AA0001"]

    def test_resolve_without_subsection_matches_section_level(self, section_df):
        # 小段為 None 時只比對段：五股區「更寮」段本身即為小段名之一，
        # 至少會回傳段名==小段名那筆
        codes = resolve_section(section_df, "新北市", "五股區", "更寮", None)
        assert "FB0361" in codes

    def test_resolve_not_found(self, section_df):
        codes = resolve_section(section_df, "新北市", "五股區", "不存在的段名", None)
        assert codes == []

    def test_resolve_may_return_multiple_for_ambiguous_section(self, section_df):
        # 城林段（設計文件樣本地名）在多個縣市/鄉鎮可能重複出現；
        # 只驗證回傳為 list 型別、且元素皆為字串（人工確認交由呼叫端處理）
        codes = resolve_section(section_df, "新北市", "五股區", "更寮", None)
        assert isinstance(codes, list)
        assert all(isinstance(c, str) for c in codes)
