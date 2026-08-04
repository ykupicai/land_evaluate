# -*- coding: utf-8 -*-
"""近期改過判定語意、卻一直沒有測試的四組純函式。

每一組都直接對應一次實錯：
  classify_ownership — 2026-08-04 龍崎案：⓪a 寫「私(推論)」、精確比對讓公私筆數同時歸 0
  F_* 欄名別名表    — 同日：讀取端只認 TCDGIS 欄名，⓪a 路徑三層靜默降級
  is_unregistered   — 2026-08-02：9xxx 未登記地誤當一般宗地會把 0 面積計入登記面積
  norm_no           — MOI_API_039 的 8 碼地號若不正規化，全段公有地 join 不到任何一筆
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cadastre_fetch import is_unregistered                     # noqa: E402
from persite_stats import classify_ownership                   # noqa: E402
from public_land import norm_no                                # noqa: E402
from shp_trust_utils import (                                  # noqa: E402
    F_COUNTY, F_LANDNO, F_LTYPE, F_SEG, F_TOWN, F_ZONE, shp_get,
)

# ── 公私別 ────────────────────────────────────────────────────────────────

def test_ownership_exact_and_inferred():
    assert classify_ownership("公") == "公"
    assert classify_ownership("私") == "私"
    assert classify_ownership("私(推論)") == "私"     # ⓪a cadastre_fetch 的寫法
    assert classify_ownership("公/國有") == "公"      # TCDGIS 匯出的寫法


def test_ownership_blank_is_not_private():
    """空白≠私有（CLAUDE.md：本機快照所有權欄空白率 82.5%，缺的正是國有水利地）。"""
    for v in ("", None, "  ", "未登記", float("nan")):
        assert classify_ownership(v) is None, v


# ── trust-shp 欄名別名表 ─────────────────────────────────────────────────

REC_0A = {"縣市": "臺南市", "鄉鎮": "龍崎區", "段": "番社段",
          "地號": "56-4", "使分": "工業區", "使類": "特定目的事業用地"}
REC_TCD = {"20251201_1": "臺南市", "20251201_2": "龍崎區", "20251201_3": "番社段",
           "20251201_4": "56-4", "20251201_6": "工業區", "20251201_7": "特定目的事業用地"}


def test_field_aliases_accept_both_sources():
    """兩種基地 shp 來源（⓪a／② area_join）逐欄都取得到值——
    這張表漏一個欄名，周邊地籍、功能分區、使分/使類會**一起**靜默降級而回報成功。"""
    for rec in (REC_0A, REC_TCD):
        assert shp_get(rec, F_COUNTY) == "臺南市"
        assert shp_get(rec, F_TOWN) == "龍崎區"
        assert shp_get(rec, F_SEG) == "番社段"
        assert shp_get(rec, F_LANDNO) == "56-4"
        assert shp_get(rec, F_ZONE) == "工業區"
        assert shp_get(rec, F_LTYPE) == "特定目的事業用地"


def test_field_aliases_missing_returns_empty():
    assert shp_get({}, F_COUNTY) == ""      # 呼叫端要能辨識「取不到值」


# ── 9xxx 未登記土地 ───────────────────────────────────────────────────────

def test_unregistered_by_mother_number():
    assert is_unregistered("9006-100")
    assert is_unregistered("90060100")      # 8 碼形式
    assert not is_unregistered("8999")
    assert not is_unregistered("56-4")
    assert not is_unregistered("112-19")


# ── MOI_API_039 地號正規化 ───────────────────────────────────────────────

def test_norm_no_eight_digit():
    assert norm_no("02780000") == "278"
    assert norm_no("03060001") == "306-1"
    assert norm_no("0278-0000") == "278"


def test_norm_no_passthrough():
    assert norm_no("56-4") == "56-4"
    assert norm_no(" 278 ") == "278"
