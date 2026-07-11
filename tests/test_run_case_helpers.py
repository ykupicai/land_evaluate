# -*- coding: utf-8 -*-
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from run_case import plan_steps


def test_shp_first_run_stops_at_overview():
    steps = plan_steps(mode_shp=True, resume=False, chosen_mode=None,
                       artifacts={"01": False, "02": False, "03_nonempty": False,
                                  "fishpv": False, "overview": False})
    assert steps == ["meta", "extract", "fishpv", "digest", "overview", "STOP_MODE"]


def test_shp_second_run_with_mode_renders():
    steps = plan_steps(True, False, "persite",
                       {"01": True, "02": False, "03_nonempty": False,
                        "fishpv": True, "overview": True})
    assert steps[-1] == "maps" and "meta_sitemap" in steps


def test_list_first_run_stops_at_check():
    steps = plan_steps(False, False, None,
                       {"01": False, "02": False, "03_nonempty": False,
                        "fishpv": False, "overview": False})
    assert steps == ["parse", "STOP_CHECK"]


def test_list_resume_runs_match_and_fallback():
    steps = plan_steps(False, True, None,
                       {"01": True, "02": False, "03_nonempty": True,
                        "fishpv": False, "overview": False})
    assert steps[:4] == ["match", "landno", "apply", "match2"]
    assert steps[-1] == "STOP_MODE"
