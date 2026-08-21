# -*- coding: utf-8 -*-
"""latlon_to_parcel 純函式離線測試（不碰網路／不讀圖資）。

網路相依部分（農業部 identify／正查／收斂探測）靠三個真實案例端到端驗證，見 SKILL 的「已驗證」。
這裡鎖住 grep 不到、只能靠踩的解析規則：座標抓哪一組、段名一定要含小段。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import latlon_to_parcel as L  # noqa: E402


# --- extract_lonlat：座標要抓地標本身(!3d!4d)，不是地圖中心(@) ---

def test_plain_latlon_string():
    lon, lat, src = L.extract_lonlat("22.9243646,120.3469031")
    assert (lon, lat) == (120.3469031, 22.9243646)
    assert "座標" in src


def test_full_google_url_prefers_place_point():
    # @ 是地圖中心(22.9285…)、!3d!4d 是地標(22.9243…)；必須取後者
    url = ("https://www.google.com/maps/place/x/@22.9285847,120.3357024,2321m/"
           "data=!3m1!1e3!8m2!3d22.9243646!4d120.3469031")
    lon, lat, src = L.extract_lonlat(url)
    assert (lon, lat) == (120.3469031, 22.9243646)
    assert "!3d!4d" in src
    assert not src.startswith("⚠")


def test_url_falls_back_to_center_with_warning():
    url = "https://www.google.com/maps/place/x/@22.9285847,120.3357024,2321m/data=!3m1!1e3"
    lon, lat, src = L.extract_lonlat(url)
    assert (lon, lat) == (120.3357024, 22.9285847)
    assert src.startswith("⚠")   # 只有中心點時要標警告


# --- full_section_name：段名一定要含小段（漏了會整筆查無） ---

def test_section_name_with_subsection():
    assert L.full_section_name("新化", "王公廟") == "新化段王公廟小段"


def test_section_name_no_subsection():
    assert L.full_section_name("深坑子", None) == "深坑子段"
    assert L.full_section_name("深坑子", "nan") == "深坑子段"


def test_section_name_already_suffixed():
    assert L.full_section_name("蚶子寮段", "蚶子寮小段") == "蚶子寮段蚶子寮小段"


# --- 縣市檔名正規化：屬性「臺南市」→ 本機檔名「台南市」 ---

def test_county_filename_normalizes_tai():
    # 屬性回「臺南市」，本機檔名是「台南市」——正規化須把「臺」換「台」
    fn = "臺南市"
    for a, b in L._TW.items():
        fn = fn.replace(a, b)
    assert fn == "台南市"
