# -*- coding: utf-8 -*-
"""寵物生命紀念設施檢核清單的結構檢查（純 yaml，不依賴 QGIS／GIS 套件）。"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
YAML_PATH = ROOT / "config" / "檢核清單" / "寵物生命紀念設施.yaml"
# check_zones.run_item 目前處理的類型（見 scripts/check_zones.py 的 typ == ...）
SUPPORTED = {"note", "inquiry", "attribute", "settlement", "api_distance",
             "osm_distance", "area", "overlay", "distance"}


def _items():
    data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    return data["檢核項目"]


def test_yaml_loads_and_nonempty():
    assert len(_items()) > 0


def test_each_item_has_id_name_type():
    for it in _items():
        assert it.get("id"), it
        assert it.get("名稱"), it.get("id")
        assert it.get("類型"), it.get("id")


def test_ids_unique():
    ids = [it["id"] for it in _items()]
    assert len(ids) == len(set(ids))


def test_types_supported_by_check_zones():
    bad = [(it["id"], it["類型"]) for it in _items() if it["類型"] not in SUPPORTED]
    assert not bad, bad
    src = (ROOT / "scripts" / "check_zones.py").read_text(encoding="utf-8")
    for t in {it["類型"] for it in _items()}:
        assert f'"{t}"' in src, f"check_zones.py 找不到類型 {t}"


def test_note_items_have_level():
    for it in _items():
        if it["類型"] == "note":
            assert str(it.get("級別", "")).strip(), f"{it['id']} 的 note 項缺級別"


def test_hazard_keywords_not_bare_oil_char():
    """名稱篩選不可用單字「油」（會命中油飯、油漆行）；須為加油站／中油／油庫等較精確關鍵字。"""
    it = next(i for i in _items() if i["id"] == "pet_hazard_500m")
    kws = it["名稱含"]
    assert "油" not in kws, kws
    for k in ("加油站", "中油", "油庫"):
        assert k in kws
    for bad in ("油飯", "油漆行"):
        assert not any(k in bad for k in kws), bad
