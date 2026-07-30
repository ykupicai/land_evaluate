# -*- coding: utf-8 -*-
"""聚落判定單元測試 — 把「十戶以上之村莊或聚落」的法律解釋鎖在程式裡

這些測試存在的理由：判定法有兩種很自然、但會得出錯誤（甚至相反）結論的簡化寫法，
未來任何人「優化」成那兩種寫法都會被這裡擋下來。詳見 scripts/settlement_check.py 開頭。
"""
import os
import sys

import geopandas as gpd
import pytest
from shapely.geometry import Point, box

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "scripts"))
import settlement_check as sc  # noqa: E402


def addr(coords, 村里="測試里"):
    """以 (x, y) 串建門牌點圖層（EPSG:3826，公尺）。"""
    return gpd.GeoDataFrame({"村里": [村里] * len(coords)},
                            geometry=[Point(*c) for c in coords], crs=3826)


# 基地：100m × 100m 方形，左下角在原點
SITE = box(0, 0, 100, 100)


def 排(n, x0, y0, dx=10):
    """在 (x0,y0) 起、間距 dx 的一列 n 個門牌（間距小→必成一群）。"""
    return [(x0 + i * dx, y0) for i in range(n)]


class Test聚類:
    def test_間距超過門檻即分群(self):
        g = addr([(0, 0), (20, 0), (200, 0)])
        lab = sc.cluster(g.geometry.get_coordinates().to_numpy(), 30)
        assert len(set(lab)) == 2

    def test_鏈狀相連視為同一聚落(self):
        # 每兩點相距 25m，首尾相距 100m：單一連結聚類下仍是同一群
        g = addr([(0, 0), (25, 0), (50, 0), (75, 0), (100, 0)])
        lab = sc.cluster(g.geometry.get_coordinates().to_numpy(), 30)
        assert len(set(lab)) == 1


class Test最近聚落:
    def test_不足戶數的群不算聚落(self):
        # 基地旁 5m 有 3 戶（不足 10），300m 外有 12 戶
        g = addr([(105, 50), (110, 50), (115, 50)] + 排(12, 400, 50))
        r = sc.nearest_settlement(SITE, g, eps=30, min_n=10)
        assert r["門牌數"] == 12
        assert r["最短距離_m"] == pytest.approx(300, abs=1)

    def test_全無達標聚落回None(self):
        g = addr([(105, 50), (110, 50)])
        assert sc.nearest_settlement(SITE, g, eps=30, min_n=10)["最短距離_m"] is None

    def test_取最近的達標聚落而非最大者(self):
        g = addr(排(10, 250, 50) + 排(40, 900, 50))
        r = sc.nearest_settlement(SITE, g, eps=30, min_n=10)
        assert r["門牌數"] == 10 and r["最短距離_m"] == pytest.approx(150, abs=1)


class Test判定:
    """★ 核心：安南案型態——基地邊上有零星門牌，真正的聚落在 200m 外 → 未觸及"""

    def test_零星門牌不使其觸及(self):
        g = addr([(103, 50), (150, 50), (160, 20)] + 排(20, 380, 50))
        r = sc.assess(SITE, g, dist=200, min_n=10, eps_list=[30, 50, 100])
        assert r["判定"] == "未觸及"
        # 但仍要如實揭露「最近單一門牌僅 3m」，供人工覆核
        assert r["最近單一門牌_m"] == pytest.approx(3, abs=1)
        assert r["範圍內門牌數_僅供參考"] == 3

    def test_門檻內有達標聚落即觸及(self):
        """西港案型態：157m 處有 40 餘戶聚落 → 觸及"""
        g = addr(排(43, 250, 50))
        r = sc.assess(SITE, g, dist=200, min_n=10, eps_list=[30, 50, 100])
        assert r["判定"] == "觸及"

    def test_錯法一_數範圍內門牌數會判錯(self):
        """30 戶的大聚落只有邊緣 3 戶落在 200m 內：
        若用「範圍內不足十戶就過」會誤判未觸及"""
        # 基地右緣 x=100；門牌自 x=250 起每 25m 一戶（25<30 故同屬一群）
        # → 距基地 150m 起算，200m 內只有 x=250/275/300 三戶
        g = addr(排(30, 250, 50, dx=25))
        r = sc.assess(SITE, g, dist=200, min_n=10, eps_list=[30, 50, 100])
        assert r["範圍內門牌數_僅供參考"] == 3   # 錯法會據此放行
        assert r["判定"] == "觸及"               # 正解：該聚落達 30 戶且在 200m 內

    def test_錯法二_以最近單一門牌判會判錯(self):
        g = addr([(101, 50)] + 排(20, 500, 50))
        r = sc.assess(SITE, g, dist=200, min_n=10, eps_list=[30, 50, 100])
        assert r["最近單一門牌_m"] < 200          # 錯法會據此認定觸及
        assert r["判定"] == "未觸及"              # 正解：那 1 戶構不成十戶聚落

    def test_門檻敏感時不給結論(self):
        """兩簇各 5 戶、相距 60m：50m 門檻下不成聚落、100m 門檻下合成 10 戶聚落"""
        g = addr(排(5, 150, 50) + 排(5, 250, 50))
        r = sc.assess(SITE, g, dist=200, min_n=10, eps_list=[30, 50, 100])
        assert r["判定"] == "門檻敏感須人工判斷"
        assert r["聚落"]["群聚門檻50m"]["判定"] != r["聚落"]["群聚門檻100m"]["判定"]
