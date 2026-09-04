# -*- coding: utf-8 -*-
"""農業部地籍框查（`moa_parcel_query`）—— 把 2026-09-04 麻豆客子寮段案的坑寫成斷言。

坑（本專案迄今最貴的留白事故）：舊路徑用**本機快照 Ver 111年2月**當「框內有哪些
地號」的唯一來源，再拿那些地號逐筆去問農業部。港子尾段整段重測改名為客子寮段並
全段重編號，其後又以「和解分割」分出 374-1／374-2（合計 36,338 ㎡、距基地 3.9 m），
在快照裡**沒有任何舊地號對得上** → 從未被送查 → 圖面整片留白，而既有的補救機制
（母號縮水偵測、子號探測）也同時失效（縮水偵測靠同地號比面積、探測用舊段名）。

改法：出圖框直接送空間查詢，**不必事先知道有哪些地號**。本測試鎖住五件事
（全部純合成／monkeypatch，不打網路）：
  1. 服務位址的版次一律動態組出，不可寫死；
  2. 分頁少抓必須拋錯（少抓的症狀就是留白，絕不可靜默）；
  3. rings → Polygon 的外環／內孔判定（裡地不可被填實）；
  4. to_gdf 的欄位與 DBF 10-byte 欄名上限；
  5. 段／小段組字與 cadastre_neighbors 一致（下游 join 才對得上）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

import moa_parcel_query as mpq  # noqa: E402


# ---------------------------------------------------------------- 版次不可寫死
def test_service_url_embeds_version_dynamically(monkeypatch):
    """版次寫在**服務名稱裡**（..._Tiled_115Jun），每兩個月換一次。

    寫死＝兩個月後靜默查到舊版地籍（症狀比留白更陰：圖出得來、但屬性是過期的）。
    """
    class FakeMoa:
        @staticmethod
        def newest_version():
            return "115Aug"

        @staticmethod
        def config(force=False):
            return {"cadastralSoeUrl": "https://x/arcgis/rest/services/",
                    "cadastralSoeToken": "T"}

    monkeypatch.setattr(mpq, "_moa", lambda: FakeMoa)
    url, ver = mpq.service_url()
    assert ver == "115Aug"
    assert url.endswith("CadastralMap/CadastralMap_Tiled_115Aug/MapServer")
    # 指定版次時以指定者為準（回歸／重現舊成果用）
    url2, ver2 = mpq.service_url("113Dec")
    assert ver2 == "113Dec" and "Tiled_113Dec" in url2


# ------------------------------------------------------- 分頁少抓必須拋錯，不可靜默
def _fake_service(monkeypatch, count, pages):
    """count＝服務宣告的總筆數；pages＝每次 query 實際回的 features 批次。"""
    class FakeMoa:
        @staticmethod
        def newest_version():
            return "115Jun"

        @staticmethod
        def config(force=False):
            return {"cadastralSoeUrl": "https://x/", "cadastralSoeToken": "T"}

    monkeypatch.setattr(mpq, "_moa", lambda: FakeMoa)
    monkeypatch.setattr(mpq, "polygon_layers", lambda v=None: [(1, "Taiwan")])
    seq = list(pages)

    def fake_post(url, params, retry_token=True):
        if params.get("returnCountOnly") == "true":
            return {"count": count}
        return {"features": seq.pop(0) if seq else []}

    monkeypatch.setattr(mpq, "_post", fake_post)


def _feat(no8):
    return {"attributes": {"地號": no8, "段": "客子寮", "小段": None},
            "geometry": {"rings": [[(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]]}}


def test_pagination_shortfall_raises(monkeypatch):
    """服務宣告 3 筆、只回 2 筆 → 必須拋錯。

    ★ 這是整組測試最重要的一條：靜默少抓＝圖面留白，而留白會被讀圖的人理解成
      「那裡沒有宗地」，比屬性稍舊嚴重得多。
    """
    _fake_service(monkeypatch, count=3, pages=[[_feat("00010000"), _feat("00020000")], []])
    with pytest.raises(mpq.MoaQueryError, match="分頁不完整"):
        mpq.query_envelope(0, 0, 10, 10)


def test_pagination_complete_ok(monkeypatch):
    """分頁抓齊就正常回傳（分兩頁也要能拼完整）。"""
    _fake_service(monkeypatch, count=3,
                  pages=[[_feat("00010000"), _feat("00020000")], [_feat("00030000")]])
    recs = mpq.query_envelope(0, 0, 10, 10)
    assert len(recs) == 3
    assert {r["地號"] for r in recs} == {"00010000", "00020000", "00030000"}
    assert all(r["_version"] == "115Jun" for r in recs)


def test_zero_count_layer_skipped(monkeypatch):
    """0 筆的圖層直接略過（離島查本島層、本島查澎湖層都會遇到），不可誤判為錯誤。"""
    _fake_service(monkeypatch, count=0, pages=[])
    assert mpq.query_envelope(0, 0, 10, 10) == []


# ------------------------------------------------------------------ 幾何：裡地不可填實
def test_rings_single():
    g = mpq.rings_to_polygon([[(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]])
    assert abs(g.area - 100) < 1e-6


def test_rings_with_hole_keeps_hole():
    """內孔（他人裡地）必須保留——填實會讓權屬圖／綠帶把別人的地畫成基地鄰地的一部分。"""
    outer = [(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]
    hole = [(3, 3), (7, 3), (7, 7), (3, 7), (3, 3)]
    g = mpq.rings_to_polygon([outer, hole])
    assert abs(g.area - (100 - 16)) < 1e-6
    assert not g.contains(Polygon(hole).representative_point())


def test_rings_two_disjoint_parts_kept():
    """同一宗地的兩塊分離範圍要都留住（不可只留最大那塊）。"""
    a = [(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]
    b = [(20, 0), (20, 5), (25, 5), (25, 0), (20, 0)]
    g = mpq.rings_to_polygon([a, b])
    assert abs(g.area - (100 + 25)) < 1e-6


def test_rings_empty():
    assert mpq.rings_to_polygon([]) is None


# ------------------------------------------------------------------- 段名組字一致
def test_section_name_matches_neighbors():
    """與 cadastre_neighbors._section_name 必須一字不差，否則下游 join 對不上。

    ⚠ 段名只做**完整組字**，絕不可去尾比對（民權段 ≠ 民權一段，那是別人的地）。
    """
    import cadastre_neighbors as cn
    for seg, sub in [("客子寮", None), ("考試潭", "鹿寮"), ("五魁", ""), ("海埤", "None")]:
        assert mpq.section_name(seg, sub) == cn._section_name(seg, sub)
    assert mpq.section_name("客子寮", None) == "客子寮段"
    assert mpq.section_name("考試潭", "鹿寮") == "考試潭段鹿寮小段"


# ------------------------------------------------------------ 輸出欄位與 DBF 位元組上限
def test_shp_field_names_within_dbf_10_bytes():
    """★ DBF 欄名上限 **10 bytes**（UTF-8 中文 3 bytes/字 → 最多 3 字）。

    超過會被截斷成壞 UTF-8、**整份 shp 讀不出來**（2026-08-02「所有權人」12 bytes、
    2026-08-27「屬性來源」12 bytes 皆實踩）。新增欄位時這條一定要先過。
    """
    for c in mpq.SHP_COLS:
        assert len(c.encode("utf-8")) <= 10, f"{c} 超過 DBF 欄名 10 bytes 上限"


def test_to_gdf_schema_and_values(monkeypatch):
    """輸出欄位要與 cadastre_fetch 同構（下游 make_maps／greenbelt_ratio 直接吃）。"""
    recs = [{
        "地號": "03740002", "段": "客子寮", "小段": None,
        "縣市": "臺南市", "鄉鎮": "麻豆區",
        "登記面積_平方公尺": 100.0, "使用分區": "一般農業區", "使用類別": "養殖用地",
        "都市計畫區": "非都計", "事務所碼": "DF", "段碼": "4438", "_version": "115Jun",
        "rings": [[(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]],
    }]
    gdf = mpq.to_gdf(recs)
    assert list(mpq.drop_internal(gdf).columns) == mpq.SHP_COLS + ["geometry"]
    r = gdf.iloc[0]
    assert r["地號"] == "374-2"          # 8 碼 → 人看得懂的地號
    assert r["段"] == "客子寮段"
    assert r["使類"] == "養殖用地" and r["使分"] == "一般農業區"
    assert r["都計區"] == "非都計"
    assert r["狀態"] == "OK" and r["版次"] == "115Jun"
    assert abs(r["圖面積"] - 100.0) < 1e-6 and abs(r["偏差pct"]) < 1e-6
    assert r["未登記"] == ""
    assert gdf.crs.to_epsg() == 3826


def test_to_gdf_marks_unregistered():
    """母號 ≥9000 ＝未登記土地，要標記（面積只能用圖面積、不可計入登記面積）。"""
    recs = [{"地號": "90130000", "段": "港子尾", "小段": None,
             "縣市": "臺南市", "鄉鎮": "麻豆區", "_version": "115Jun",
             "rings": [[(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]]}]
    assert mpq.to_gdf(recs).iloc[0]["未登記"] == "是"
