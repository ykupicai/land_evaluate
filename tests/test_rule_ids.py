"""檢核清單「規則編號」欄（2026-11-12 建）：格式與正規化。"""
import glob
import os
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from check_zones import normalize_rule_ids  # noqa: E402


def test_normalize_variants():
    assert normalize_rule_ids(None) == []
    assert normalize_rule_ids("") == []
    assert normalize_rule_ids("AGR-005") == ["AGR-005"]
    assert normalize_rule_ids("AGR-002、AGR-003,AGR-002") == ["AGR-002", "AGR-003"]
    assert normalize_rule_ids(["SP01-01B", "LUR-052-1", "LOCAL-TNN-001"]) == [
        "SP01-01B", "LUR-052-1", "LOCAL-TNN-001"]


@pytest.mark.parametrize("bad", ["agr-005", "AGR005", "總編§14", "DTM 20"])
def test_reject_bad_format(bad):
    with pytest.raises(ValueError):
        normalize_rule_ids(bad)


def test_all_checklists_rule_ids_valid():
    """所有清單的規則編號都要能過格式檢查（否則 check_zones 跑到該項才爆）。"""
    files = glob.glob(os.path.join(ROOT, "config", "檢核清單", "*.yaml"))
    assert files
    n = 0
    for f in files:
        d = yaml.safe_load(open(f, encoding="utf-8"))
        for it in d.get("檢核項目", []):
            n += len(normalize_rule_ids(it.get("規則編號")))
    assert n >= 27  # 2026-11-12 首批：共通兩份＋寵物／養生村／社福共 27 個編號
