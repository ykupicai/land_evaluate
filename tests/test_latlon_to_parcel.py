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


# --- D：/maps/search/lat,+lon 與 ?q=/query= 座標（2026-08-21 芳寶段案漏解） ---

def test_url_maps_search_coord():
    # 短網址展開後常成 /maps/search/<lat>,+<lon>；分隔含「,+」要能解，且非地圖中心不標警告
    url = "https://www.google.com/maps/search/23.893428,+120.326831?entry=tts&g_ep=xxx"
    lon, lat, src = L.extract_lonlat(url)
    assert (lon, lat) == (120.326831, 23.893428)
    assert not src.startswith("⚠")
    assert "search" in src or "搜尋" in src


def test_url_query_param_coord():
    url = "https://maps.google.com/?q=24.123456,120.654321&z=17"
    lon, lat, src = L.extract_lonlat(url)
    assert (lon, lat) == (120.654321, 24.123456)
    assert not src.startswith("⚠")


def test_url_3d4d_still_wins_over_search():
    # 有地標點位(!3d!4d)時仍優先，不被 /search、@ 或 q= 蓋掉
    url = ("https://www.google.com/maps/search/1.0,+2.0/@22.9285847,120.3357024,"
           "2321m/data=!3m1!8m2!3d22.9243646!4d120.3469031?q=9.9,9.9")
    lon, lat, src = L.extract_lonlat(url)
    assert (lon, lat) == (120.3469031, 22.9243646)
    assert "!3d!4d" in src


# --- A：hit_or_near 近界容差（圖釘落界址外緣數公尺視為命中，本案 1.47m） ---

def test_hit_or_near_contains():
    from shapely.geometry import Polygon, Point
    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    ok, edge, near = L.hit_or_near(poly, Point(5, 5), tol=5.0)
    assert ok and not near               # 內部：命中、非近界外側


def test_hit_or_near_just_outside_within_tol():
    from shapely.geometry import Polygon, Point
    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    ok, edge, near = L.hit_or_near(poly, Point(11.47, 5), tol=5.0)
    assert ok and near                   # 外緣 1.47m ≤ 5m：命中且標近界
    assert round(edge, 2) == 1.47


def test_hit_or_near_too_far():
    from shapely.geometry import Polygon, Point
    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    ok, edge, near = L.hit_or_near(poly, Point(20, 5), tol=5.0)
    assert not ok and not near           # 外緣 10m > 5m：不命中


def test_hit_or_near_none():
    from shapely.geometry import Point
    assert L.hit_or_near(None, Point(0, 0)) == (False, None, False)


# --- landno_to_no8：母子號 → 8 碼（新舊地號 API 用） ---

def test_landno_to_no8():
    assert L.landno_to_no8("755") == "07550000"
    assert L.landno_to_no8("755-1") == "07550001"
    assert L.landno_to_no8("12-345") == "00120345"
