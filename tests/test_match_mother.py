# -*- coding: utf-8 -*-
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from match_parcels import pick_fids


K2F = {("DF4344", "0122", "0000"): [10], ("DF4344", "0122", "0001"): [11],
       ("DF4344", "0122", "0003"): [12], ("DF4344", "0999", "0000"): [13]}
KM = {}
for (code, m, c), fids in K2F.items():
    KM.setdefault((code, m), []).extend(fids)


def test_exact_mode():
    assert pick_fids(K2F, KM, "DF4344", "0122", "0001", "") == [11]
    assert pick_fids(K2F, KM, "DF4344", "0122", "9999", "") == []


def test_mother_mode_returns_all_children():
    assert sorted(pick_fids(K2F, KM, "DF4344", "0122", "", "母號範圍")) == [10, 11, 12]


def test_mother_mode_missing():
    assert pick_fids(K2F, KM, "DF4344", "0777", "", "母號範圍") == []
