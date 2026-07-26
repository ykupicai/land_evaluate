# -*- coding: utf-8 -*-
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from shp_to_meta import load_sitemap


def test_load_sitemap(tmp_path):
    p = tmp_path / "sitemap.csv"
    p.write_text("地號,案場ID,案場名稱\n122,S001,S001宗基地\n390,S003,S003宗基地\n",
                 encoding="utf-8-sig")
    m = load_sitemap(str(p))
    assert m["122"] == (("S001", "S001宗基地"),)
    assert m["390"] == (("S003", "S003宗基地"),)
    assert "999" not in m
