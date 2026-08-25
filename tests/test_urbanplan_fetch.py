# -*- coding: utf-8 -*-
"""urbanplan_fetch 純函式單測（不觸網）：登錄表健全性、_resolve_code、esri 幾何還原、色表反查。

網路查詢（bbox 五縣市／land-number 宜蘭）另以手動 --county 實跑驗證，不進 CI（避免相依外部圖台）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import urbanplan_fetch as uf


class TestRegistryIntegrity:
    """登錄表本身的健全性——新增縣市最容易漏欄位或把兩張表混用。"""

    def test_bbox_counties_have_required_keys(self):
        for county, cfg in uf.COUNTY.items():
            assert "query" in cfg, county
            assert "fields" in cfg, county
            for k in ("ZONE", "ZONE_USE", "BCR", "FAR", "PLAN_AREA", "PLAN_NO"):
                assert k in cfg["fields"], f"{county} 缺 fields.{k}"

    def test_landnum_counties_have_required_keys(self):
        for county, cfg in uf.LANDNUM_COUNTY.items():
            assert "base" in cfg and "sects" in cfg, county
            if cfg.get("handler") == "hccg":          # 新竹市：token＋兩段式，欄位不同
                for k in ("token_api", "wkt_api", "zone_api"):
                    assert k in cfg, f"{county} 缺 {k}"
            else:                                     # 宜蘭型：單一 Query 端點
                for k in ("towns", "query", "q_params", "resp"):
                    assert k in cfg, f"{county} 缺 {k}"
                for k in ("ZONE", "PLAN_AREA", "PLAN_NO", "shape"):
                    assert k in cfg["resp"], f"{county} 缺 resp.{k}"
                assert len(cfg["q_params"]) == 3, county

    def test_two_registries_disjoint(self):
        # 同一縣市不可同時登在兩張表（接法互斥，會讓 main 分流出錯）
        assert not (set(uf.COUNTY) & set(uf.LANDNUM_COUNTY))

    def test_supported_excludes_unstable(self):
        sup = uf._supported()
        assert "彰化縣" in sup and "桃園市" in sup and "花蓮縣" in sup
        assert "新竹縣" not in sup  # unstable（token 動態注入）


class TestQueryFallback:
    """臺南主計層查無 → fallback 細部計畫層（2026-08-23 北安段現有巷道改道案）。"""

    def test_tainan_has_fallback(self):
        assert "query_fallback" in uf.COUNTY["臺南市"]

    def test_falls_back_when_primary_empty(self, monkeypatch):
        cfg = uf.COUNTY["臺南市"]
        calls = []

        def fake_layer(c, url, env, token):
            calls.append(url)
            return [] if url == cfg["query"] else [{"attributes": {"NAME": "住四"},
                                                    "geometry": {"rings": []}}]
        monkeypatch.setattr(uf, "_fetch_token", lambda c: None)
        monkeypatch.setattr(uf, "_query_layer", fake_layer)
        out = uf.query_zone("臺南市", (0, 0, 1, 1))
        assert len(out["features"]) == 1                       # 用了細計 fallback
        assert calls == [cfg["query"], cfg["query_fallback"]]  # 先主計、再細計

    def test_no_fallback_when_primary_has_data(self, monkeypatch):
        cfg = uf.COUNTY["臺南市"]
        calls = []

        def fake_layer(c, url, env, token):
            calls.append(url)
            return [{"attributes": {"NAME": "農"}, "geometry": {"rings": []}}]
        monkeypatch.setattr(uf, "_fetch_token", lambda c: None)
        monkeypatch.setattr(uf, "_query_layer", fake_layer)
        uf.query_zone("臺南市", (0, 0, 1, 1))
        assert calls == [cfg["query"]]                         # 主計有值就不 fallback


class TestResolveCode:
    TOWNS = [{"code": "01", "name": "宜蘭市"}, {"code": "06", "name": "羅東鎮"}]
    SECTS = [{"code": "0495", "name": "宜興段"}, {"code": "0005", "name": "十六份段"}]

    def test_exact_code(self):
        assert uf._resolve_code(self.TOWNS, "01") == "01"

    def test_exact_name(self):
        assert uf._resolve_code(self.TOWNS, "宜蘭市") == "01"

    def test_name_without_suffix(self):
        # 「宜蘭」→ 去尾比對到「宜蘭市」；「宜興」→「宜興段」
        assert uf._resolve_code(self.TOWNS, "宜蘭") == "01"
        assert uf._resolve_code(self.SECTS, "宜興") == "0495"

    def test_sect_code_direct(self):
        assert uf._resolve_code(self.SECTS, "0495") == "0495"

    def test_not_found(self):
        assert uf._resolve_code(self.TOWNS, "花蓮市") is None


class TestEsriGeometry:
    def test_landshape_single_ring_to_polygon(self):
        # 宜蘭 landShape 是 rings（無 SR 包裝），_esri_polygon_to_shapely 應能還原
        rings = [[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]]
        g = uf._esri_polygon_to_shapely({"rings": rings})
        assert g is not None and not g.is_empty
        assert abs(g.area - 100.0) < 1e-6

    def test_empty_geometry_returns_none(self):
        assert uf._esri_polygon_to_shapely({}) is None
        assert uf._esri_polygon_to_shapely({"rings": []}) is None


class TestColorLookup:
    def test_zone_color_or_none(self):
        colors = uf._load_zone_colors()
        # 色表必然含常見分區；找不到回 None（不丟例外）
        assert uf._color_for("住宅區", colors) is not None
        assert uf._color_for("這不是分區xyz", colors) is None
        assert uf._color_for(None, colors) is None
