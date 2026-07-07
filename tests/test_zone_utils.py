# -*- coding: utf-8 -*-
"""zone_utils 單元測試：涵蓋 2026-07-07 實測 16 縣市的所有分區值寫法。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from zone_utils import extract_zones_from_text, normalize_zone


class TestNormalizeZoneFullNames:
    """完整名與其異體。"""

    @pytest.mark.parametrize("raw,expected", [
        # 標準全名（桃園/新竹縣/苗栗/臺中/南投/嘉義縣）
        ("國土保育地區第一類", "國土保育地區第一類"),
        ("城鄉發展地區第二類之三", "城鄉發展地區第二類之三"),
        ("農業發展地區第五類", "農業發展地區第五類"),
        ("海洋資源地區第一類之二", "海洋資源地區第一類之二"),
        # 新北異體：第二之一類
        ("城鄉發展地區第二之一類", "城鄉發展地區第二類之一"),
        # 花蓮異體：海洋「發展」
        ("海洋發展地區第一類之二", "海洋資源地區第一類之二"),
        ("海洋發展地區第三類", "海洋資源地區第三類"),
        # 宜蘭：示意圖尾綴
        ("國土保育地區第一類示意圖", "國土保育地區第一類"),
        ("城鄉發展地區第二類之一示意圖", "城鄉發展地區第二類之一"),
        # 澎湖：省「地區」、第二-一類、第六類
        ("國土保育第一類", "國土保育地區第一類"),
        ("城鄉發展第二-一類", "城鄉發展地區第二類之一"),
        ("海洋資源第一-二類", "海洋資源地區第一類之二"),
        ("農業發展第六類", "農業發展地區第六類"),
    ])
    def test_full_names(self, raw, expected):
        assert normalize_zone(raw) == expected


class TestNormalizeZoneCodes:
    """簡碼與中文數字碼。"""

    @pytest.mark.parametrize("raw,expected", [
        # 新北 分區
        ("國1", "國土保育地區第一類"),
        ("城2-3", "城鄉發展地區第二類之三"),
        ("海1-1", "海洋資源地區第一類之一"),
        ("農5", "農業發展地區第五類"),
        # 雲林 分區Fina
        ("國保1", "國土保育地區第一類"),
        ("城鄉2-1", "城鄉發展地區第二類之一"),
        ("農業1", "農業發展地區第一類"),
        ("海洋2", "海洋資源地區第二類"),
        # 臺南/彰化 二位連寫
        ("城21", "城鄉發展地區第二類之一"),
        ("海11", "海洋資源地區第一類之一"),
        ("城23", "城鄉發展地區第二類之三"),
        # 高雄/屏東
        ("農發1", "農業發展地區第一類"),
        ("城鄉3", "城鄉發展地區第三類"),
        # 臺東 中文數字
        ("國保一", "國土保育地區第一類"),
        ("城一", "城鄉發展地區第一類"),
        ("城二之一", "城鄉發展地區第二類之一"),
        ("城鄉三", "城鄉發展地區第三類"),
        ("農發五", "農業發展地區第五類"),
        ("海一之二", "海洋資源地區第一類之二"),
        # 新北 農四
        ("農四", "農業發展地區第四類"),
    ])
    def test_codes(self, raw, expected):
        assert normalize_zone(raw) == expected


class TestNormalizeZoneRejects:
    """非四大分區值應回 None。"""

    @pytest.mark.parametrize("raw", [
        None, "", "未來發展地區", "其他區位", "新增產業用地", "新曾都市發展用地",
        "陸域", "桃園市國土計畫範圍", "行政區界",
        "國土功能分區20200501",  # 檔名帶日期，數字過多
        "彰化出海道路_漁業設施設置範圍_clip",
        "海底纜線或管道設置範圍_纜線_clip",
    ])
    def test_rejects(self, raw):
        assert normalize_zone(raw) is None


class TestExtractZonesFromText:
    """從原表國土功能分區欄文字抽標準分區名。"""

    def test_simple(self):
        assert extract_zones_from_text("城鄉發展地區第二類之三") == ["城鄉發展地區第二類之三"]

    def test_grouped_by_parcel(self):
        text = "農業發展地區第二類(3、3-2、5-1地號)、城鄉發展地區第一類(4、5-2地號)"
        assert extract_zones_from_text(text) == ["農業發展地區第二類", "城鄉發展地區第一類"]

    def test_arabic_digits(self):
        text = "部分國土保育地區第2類,部分農業發展地區第2類"
        assert extract_zones_from_text(text) == ["國土保育地區第二類", "農業發展地區第二類"]

    def test_dedup_and_order(self):
        text = "國土保育地區第一類、農業發展地區第二類(5地號)、國土保育地區第一類(6、7地號)"
        assert extract_zones_from_text(text) == ["國土保育地區第一類", "農業發展地區第二類"]

    def test_unresolvable(self):
        assert extract_zones_from_text("無法查詢") == []
        assert extract_zones_from_text(None) == []

    def test_pingtung_note_text(self):
        # 086 高樹鄉：文字含查詢說明，不應誤抽
        assert extract_zones_from_text("1.系統查無東振段2.國土資策雲查詢為一般農業區") == []

    def test_nongfa_4_yuan(self):
        # 「農業發展地區第四類(原)」的 (原) 註記不影響抽取
        assert extract_zones_from_text("農業發展地區第四類(原)(985地號)") == ["農業發展地區第四類"]
