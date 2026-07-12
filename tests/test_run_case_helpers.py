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


# --- 清冊模式 plan_steps 各分支對齊 spec §4 --------------------------------

def test_list_resume_no_mode_empty03_omits_fallback():
    # resume 無 mode、03 為空：不預先排 fallback（動態插入交給 execute_steps）
    steps = plan_steps(False, True, None,
                       {"01": True, "02": False, "03_nonempty": False,
                        "fishpv": False, "overview": False})
    assert steps == ["match", "extract", "fishpv", "digest", "overview", "STOP_MODE"]
    assert "landno" not in steps


def test_list_mode_chosen_with_fishpv_renders_only():
    # resume 後選 mode、fishpv 已有：直接抽圖層→出圖（不重跑臨路）
    steps = plan_steps(False, True, "persite",
                       {"01": True, "02": True, "03_nonempty": False,
                        "fishpv": True, "overview": True})
    assert steps == ["extract", "maps"]


def test_list_mode_chosen_without_fishpv_autochains():
    # resume 後選 mode、fishpv 缺：出圖前自動補跑 match/extract/fishpv/digest
    steps = plan_steps(False, True, "single",
                       {"01": True, "02": True, "03_nonempty": False,
                        "fishpv": False, "overview": True})
    assert steps == ["match", "extract", "fishpv", "digest", "maps"]


def test_main_list_resume_dispatches_fallback_chain(tmp_path, monkeypatch):
    """端到端接線：真的呼叫 run_case.main()（僅把 subprocess 與 fishpv 摘要
    邊界 stub 掉），證明 match 後偵測到非空 03 時，真實的 run_step closure 會被
    execute_steps 動態插入並依序派發 match→query_landno→apply_landno→match2，
    且排在 STOP_MODE 之前。"""
    import pandas as pd
    import run_case

    monkeypatch.setattr(run_case, "REPO", tmp_path)
    case = "itest"
    d = tmp_path / "outputs" / case
    d.mkdir(parents=True)
    # resume 前提：01 已存在、人工核對過；03 首跑前為空
    pd.DataFrame({"a": [1]}).to_excel(d / "01_中繼檔.xlsx", index=False)
    pd.DataFrame(columns=["地號"]).to_excel(d / "03_無法對應清單.xlsx", index=False)

    calls = []

    def fake_run(cmd, cwd=None, label=""):
        calls.append(label)
        if label in ("match", "match2"):
            # 模擬 match_parcels 首跑後：有比不到的地號 → 產生非空 03
            pd.DataFrame({"地號": ["2535-1"]}).to_excel(
                d / "03_無法對應清單.xlsx", index=False)

    monkeypatch.setattr(run_case, "_run", fake_run)
    monkeypatch.setattr(run_case, "digest_fishpv", lambda cd: calls.append("digest_fn"))
    monkeypatch.setattr(sys, "argv",
                        ["run_case.py", "--list", "x.xlsx", "--case", case, "--resume"])

    run_case.main()

    # 規劃時 03 為空 → 靜態步驟無 landno；match 執行後 03 轉非空 → 動態補鏈
    assert calls[:4] == ["match", "query_landno", "apply_landno", "match2"]
    # fallback 補鏈整段排在 STOP_MODE 之前（STOP_MODE 不經 _run，故不在 calls 中）
    assert "00_總區位圖" in calls and calls.index("match2") < calls.index("00_總區位圖")
