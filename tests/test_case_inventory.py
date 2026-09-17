import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from case_inventory import declared_status, inventory


def test_status_does_not_confuse_regulatory_followups_with_delivery():
    assert declared_status('> 已結案\n\n## 待確認（未結案的 N 項）') == '已結案（依紀錄）'
    assert declared_status('| **狀態** | **進行中（測試案）** |') == '進行中（測試案）'
    assert declared_status('# 案件\n簡報已產出\nQA 通過') == '待確認'
    assert declared_status('# 案件\n\n## 第二次結案（2026-09-16）') == '已結案（依紀錄）'


def test_pptx_presence_never_closes_a_case(tmp_path):
    case = tmp_path / '20260916_測試'
    case.mkdir()
    (case / 'final.pptx').touch()
    (tmp_path / '_maintenance_20260916_0').mkdir()
    rows = inventory(tmp_path)
    assert len(rows) == 1
    assert rows[0]['status'] == '待確認' and not rows[0]['state_exists']
