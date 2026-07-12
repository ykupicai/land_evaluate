# -*- coding: utf-8 -*-
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from run_case import execute_steps, find_from_index, plan_steps


def test_shp_first_run_stops_at_overview():
    # trust-shp 首跑：不比對(match)、不抽圖層(extract)，直接 meta→fishpv→總區位圖後停
    steps = plan_steps(mode_shp=True, resume=False, chosen_mode=None,
                       artifacts={"01": False, "02": False, "03_nonempty": False,
                                  "fishpv": False, "overview": False})
    assert steps == ["meta", "fishpv", "digest", "overview", "STOP_MODE"]
    assert "match" not in steps and "extract" not in steps


def test_shp_second_run_persite_renders_via_trust_extract():
    steps = plan_steps(True, False, "persite",
                       {"01": True, "02": False, "03_nonempty": False,
                        "fishpv": True, "overview": True})
    assert steps == ["trust_extract_persite", "maps"]
    assert "match" not in steps


def test_shp_second_run_single_renders_via_trust_extract():
    steps = plan_steps(True, False, "single",
                       {"01": True, "02": False, "03_nonempty": False,
                        "fishpv": True, "overview": True})
    assert steps == ["trust_extract_single", "maps"]


def test_shp_mode_persite_without_fishpv_autochains():
    steps = plan_steps(True, False, "persite",
                       {"01": True, "02": False, "03_nonempty": False,
                        "fishpv": False, "overview": True})
    assert steps == ["fishpv", "digest", "trust_extract_persite", "maps"]


def test_shp_mode_single_without_fishpv():
    steps = plan_steps(True, False, "single",
                       {"01": True, "02": False, "03_nonempty": False,
                        "fishpv": False, "overview": True})
    assert steps == ["fishpv", "digest", "trust_extract_single", "maps"]


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


def test_find_from_index_exact_match():
    assert find_from_index(["meta", "extract", "maps"], "extract") == 1


def test_find_from_index_match_aliases_match2():
    assert find_from_index(["extract", "fishpv", "digest", "match2"], "match") == 3


def test_find_from_index_prefers_earlier_match_over_match2():
    assert find_from_index(["match", "extract", "match2"], "match") == 0


def test_find_from_index_not_found():
    assert find_from_index(["meta", "extract", "maps"], "overview") is None


def test_find_from_index_extract_aliases_trust_extract():
    # trust-shp 模式已無獨立 extract 步驟；--from extract 需能命中 trust_extract_*
    assert find_from_index(["fishpv", "digest", "trust_extract_persite", "maps"],
                           "extract") == 2
    assert find_from_index(["fishpv", "digest", "trust_extract_single", "maps"],
                           "extract") == 2


def test_dynamic_fallback_inserts_after_match():
    """critical 修正：match 後發現 03 非空時，fallback 補鏈要插在「當前位置之後」，
    不能 append 到 list 尾端——否則在含 STOP_MODE 的路徑會被 return 擋住而永不執行。"""
    steps = ["match", "extract", "fishpv", "digest", "overview", "STOP_MODE"]
    state = {"03_nonempty": False}
    order = []

    def fake_runner(s):
        order.append(s)
        if s == "match":
            # 首次比對後才知道 03 非空（模擬 match_parcels.py 執行完的結果）
            state["03_nonempty"] = True
        return s == "STOP_MODE"

    def fake_art():
        return dict(state)

    executed = execute_steps(steps, fake_runner, fake_art)

    assert executed[:4] == ["match", "landno", "apply", "match2"]
    assert executed == ["match", "landno", "apply", "match2",
                        "extract", "fishpv", "digest", "overview", "STOP_MODE"]
    # STOP_MODE 前補鏈（landno/apply/match2）已完成，而不是被 append 到尾端沒跑到
    assert order == executed
    assert order.index("STOP_MODE") == len(order) - 1


def test_execute_steps_skips_unknown_step_with_warning(capsys):
    executed = execute_steps(["meta", "no_such_step", "extract"],
                             runner=lambda s: False, art=lambda: {})
    assert executed == ["meta", "extract"]
    assert "no_such_step" in capsys.readouterr().out
