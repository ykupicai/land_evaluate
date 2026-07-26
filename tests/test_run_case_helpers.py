# -*- coding: utf-8 -*-
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from run_case import execute_steps, find_from_index, from_prereq_errors, plan_steps


def test_shp_first_run_stops_at_overview():
    # trust-shp 首跑：不比對(match)、不抽圖層(extract)，直接 meta→fishpv→總區位圖後停
    steps = plan_steps(mode_shp=True, resume=False, chosen_mode=None,
                       artifacts={"01": False, "02": False, "03_nonempty": False,
                                  "roadaccess": False, "overview": False})
    assert steps == ["meta", "roadaccess", "digest", "overview", "STOP_MODE"]
    assert "match" not in steps and "extract" not in steps


def test_shp_second_run_persite_renders_via_trust_extract():
    steps = plan_steps(True, False, "persite",
                       {"01": True, "02": False, "03_nonempty": False,
                        "roadaccess": True, "overview": True})
    assert steps == ["trust_extract_persite", "maps"]
    assert "match" not in steps


def test_shp_second_run_single_renders_via_trust_extract():
    steps = plan_steps(True, False, "single",
                       {"01": True, "02": False, "03_nonempty": False,
                        "roadaccess": True, "overview": True})
    assert steps == ["trust_extract_single", "maps"]


def test_shp_mode_persite_without_fishpv_autochains():
    steps = plan_steps(True, False, "persite",
                       {"01": True, "02": False, "03_nonempty": False,
                        "roadaccess": False, "overview": True})
    assert steps == ["roadaccess", "digest", "trust_extract_persite", "maps"]


def test_shp_mode_single_without_fishpv():
    steps = plan_steps(True, False, "single",
                       {"01": True, "02": False, "03_nonempty": False,
                        "roadaccess": False, "overview": True})
    assert steps == ["roadaccess", "digest", "trust_extract_single", "maps"]


def test_list_first_run_stops_at_check():
    steps = plan_steps(False, False, None,
                       {"01": False, "02": False, "03_nonempty": False,
                        "roadaccess": False, "overview": False})
    assert steps == ["parse", "STOP_CHECK"]


def test_list_resume_runs_match_and_fallback():
    steps = plan_steps(False, True, None,
                       {"01": True, "02": False, "03_nonempty": True,
                        "roadaccess": False, "overview": False})
    assert steps[:4] == ["match", "landno", "apply", "match2"]
    assert steps[-1] == "STOP_MODE"


def test_find_from_index_exact_match():
    assert find_from_index(["meta", "extract", "maps"], "extract") == 1


def test_find_from_index_match_aliases_match2():
    assert find_from_index(["extract", "roadaccess", "digest", "match2"], "match") == 3


def test_find_from_index_prefers_earlier_match_over_match2():
    assert find_from_index(["match", "extract", "match2"], "match") == 0


def test_find_from_index_not_found():
    assert find_from_index(["meta", "extract", "maps"], "overview") is None


def test_find_from_index_extract_aliases_trust_extract():
    # trust-shp 模式已無獨立 extract 步驟；--from extract 需能命中 trust_extract_*
    assert find_from_index(["roadaccess", "digest", "trust_extract_persite", "maps"],
                           "extract") == 2
    assert find_from_index(["roadaccess", "digest", "trust_extract_single", "maps"],
                           "extract") == 2


def test_dynamic_fallback_inserts_after_match():
    """critical 修正：match 後發現 03 非空時，fallback 補鏈要插在「當前位置之後」，
    不能 append 到 list 尾端——否則在含 STOP_MODE 的路徑會被 return 擋住而永不執行。"""
    steps = ["match", "extract", "roadaccess", "digest", "overview", "STOP_MODE"]
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
                        "extract", "roadaccess", "digest", "overview", "STOP_MODE"]
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
                        "roadaccess": False, "overview": False})
    assert steps == ["match", "extract", "roadaccess", "digest", "overview", "STOP_MODE"]
    assert "landno" not in steps


def test_list_mode_chosen_with_fishpv_renders_only():
    # resume 後選 mode、fishpv 已有：直接抽圖層→出圖（不重跑臨路）
    steps = plan_steps(False, True, "persite",
                       {"01": True, "02": True, "03_nonempty": False,
                        "roadaccess": True, "overview": True})
    assert steps == ["extract", "maps"]


def test_list_mode_chosen_without_fishpv_autochains():
    # resume 後選 mode、fishpv 缺：出圖前自動補跑 match/extract/fishpv/digest
    steps = plan_steps(False, True, "single",
                       {"01": True, "02": True, "03_nonempty": False,
                        "roadaccess": False, "overview": True})
    assert steps == ["match", "extract", "roadaccess", "digest", "maps"]


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

    def fake_run(cmd, cwd=None, label="", allowed=(0,), crash_artifact=None,
                 fatal=True):
        calls.append(label)
        if label in ("match", "match2"):
            # 模擬 match_parcels 首跑後：有比不到的地號 → 產生非空 03
            pd.DataFrame({"地號": ["2535-1"]}).to_excel(
                d / "03_無法對應清單.xlsx", index=False)
        if label == "臨路評估(內建)":
            # 模擬 fishpv 成功產出（無 sites.shp 時 overview 會依 #1 降級短路）
            fp = d / "臨路評估"
            fp.mkdir(exist_ok=True)
            (fp / "sites.shp").write_bytes(b"x")
        return True

    monkeypatch.setattr(run_case, "_run", fake_run)
    monkeypatch.setattr(run_case, "digest_fishpv", lambda cd: calls.append("digest_fn"))
    monkeypatch.setattr(sys, "argv",
                        ["run_case.py", "--list", "x.xlsx", "--case", case, "--resume"])

    run_case.main()

    # 規劃時 03 為空 → 靜態步驟無 landno；match 執行後 03 轉非空 → 動態補鏈
    assert calls[:4] == ["match", "query_landno", "apply_landno", "match2"]
    # fallback 補鏈整段排在 STOP_MODE 之前（STOP_MODE 不經 _run，故不在 calls 中）
    assert "00_總區位圖" in calls and calls.index("match2") < calls.index("00_總區位圖")


# --- #1 CRITICAL：fishpv 失敗降級不中斷（spec §6：圖5/6缺、1-4照出） -------

def test_main_shp_fishpv_failure_degrades_and_flow_continues(tmp_path, monkeypatch,
                                                             capsys):
    """trust-shp 首跑：fishpv 子進程失敗（_run 回 False）時流程不可中斷——
    印降級警告、跳過 digest（無成果.xlsx）、跳過總區位圖（無 sites.shp），
    仍走到 STOP_MODE 停點並提示可直接選 --mode。"""
    import run_case

    monkeypatch.setattr(run_case, "REPO", tmp_path)
    calls = []

    def fake_run(cmd, cwd=None, label="", allowed=(0,), crash_artifact=None,
                 fatal=True):
        calls.append(label)
        if label == "臨路評估(內建)":
            assert fatal is False  # 臨路評估必須以非致命方式呼叫
            return False           # 模擬 road_access.py 非零 returncode
        return True

    monkeypatch.setattr(run_case, "_run", fake_run)
    monkeypatch.setattr(run_case, "digest_fishpv",
                        lambda cd: calls.append("digest_fn"))
    monkeypatch.setattr(sys, "argv",
                        ["run_case.py", "--shp", str(tmp_path / "基地.shp"),
                         "--case", "ftest"])

    run_case.main()  # 不可 SystemExit

    out = capsys.readouterr().out
    assert "shp_to_meta" in calls and "臨路評估(內建)" in calls
    assert "digest_fn" not in calls            # 無成果.xlsx → digest 跳過
    assert "00_總區位圖" not in calls           # 無 sites.shp → overview 短路不派 QGIS
    assert "臨路評估失敗" in out                # 明確降級警告
    assert "--mode single|persite" in out       # STOP_MODE 停點訊息照印


def test_main_shp_fishpv_ok_still_runs_digest_and_overview(tmp_path, monkeypatch):
    """對照組：fishpv 成功（產出成果.xlsx/sites.shp）時 digest/overview 照跑。"""
    import run_case

    monkeypatch.setattr(run_case, "REPO", tmp_path)
    case_dir = tmp_path / "outputs" / "ftest2"
    calls = []

    def fake_run(cmd, cwd=None, label="", allowed=(0,), crash_artifact=None,
                 fatal=True):
        calls.append(label)
        if label == "臨路評估(內建)":
            fp = case_dir / "臨路評估"
            fp.mkdir(parents=True, exist_ok=True)
            (fp / "成果.xlsx").write_bytes(b"x")
            (fp / "sites.shp").write_bytes(b"x")
        return True

    monkeypatch.setattr(run_case, "_run", fake_run)
    monkeypatch.setattr(run_case, "digest_fishpv",
                        lambda cd: calls.append("digest_fn"))
    monkeypatch.setattr(sys, "argv",
                        ["run_case.py", "--shp", str(tmp_path / "基地.shp"),
                         "--case", "ftest2"])
    run_case.main()
    assert "digest_fn" in calls and "00_總區位圖" in calls


# --- #3：--from 截斷起點的前置產物檢查 -------------------------------------

def test_from_prereq_maps_missing_gpkg_and_02(tmp_path):
    errs = from_prereq_errors("maps", False, tmp_path, None, None)
    joined = "\n".join(errs)
    assert "圖層.gpkg" in joined and "02_比對結果.xlsx" in joined
    assert "--from extract" in joined and "--from match" in joined


def test_from_prereq_maps_trust_persite_needs_sitemap_or_result(tmp_path):
    shp = tmp_path / "基地.shp"
    shp.write_bytes(b"x")
    (tmp_path / "圖層.gpkg").write_bytes(b"x")
    errs = from_prereq_errors("maps", True, tmp_path, str(shp), "persite")
    assert any("成果.xlsx" in e for e in errs)
    # 有 sitemap.csv 即補齊
    fp = tmp_path / "臨路評估"
    fp.mkdir()
    (fp / "sitemap.csv").write_text("地號,案場ID,案場名稱\n", encoding="utf-8-sig")
    assert from_prereq_errors("maps", True, tmp_path, str(shp), "persite") == []


def test_from_prereq_fishpv_list_mode_needs_gpkg(tmp_path):
    errs = from_prereq_errors("roadaccess", False, tmp_path, None, None)
    assert any("圖層.gpkg" in e for e in errs)
    (tmp_path / "圖層.gpkg").write_bytes(b"x")
    assert from_prereq_errors("roadaccess", False, tmp_path, None, None) == []


def test_from_prereq_overview_needs_sites_shp(tmp_path):
    errs = from_prereq_errors("overview", True, tmp_path, "x.shp", None)
    assert any("sites.shp" in e and "--from roadaccess" in e for e in errs)


def test_from_prereq_match_needs_01(tmp_path):
    errs = from_prereq_errors("match", False, tmp_path, None, None)
    assert any("01_中繼檔.xlsx" in e for e in errs)


def test_main_from_maps_without_gpkg_exits_with_message(tmp_path, monkeypatch):
    """端到端：--from maps 缺 圖層.gpkg → 開跑前就以中文訊息中止，
    不再等 QGIS 子進程丟 traceback 才知道。"""
    import run_case

    monkeypatch.setattr(run_case, "REPO", tmp_path)
    shp = tmp_path / "基地.shp"
    shp.write_bytes(b"x")
    monkeypatch.setattr(run_case, "_run",
                        lambda *a, **k: pytest.fail("不應執行任何步驟"))
    monkeypatch.setattr(sys, "argv",
                        ["run_case.py", "--shp", str(shp), "--case", "c1",
                         "--mode", "single", "--from", "maps"])
    with pytest.raises(SystemExit) as ei:
        run_case.main()
    msg = str(ei.value)
    assert "前置產物不足" in msg and "圖層.gpkg" in msg


# --- #4：QGIS returncode 豁免收斂（_run 由呼叫端明示） ----------------------

class _FakeProc:
    def __init__(self, rc):
        self.returncode = rc


def _patch_subprocess(monkeypatch, rc, side_effect=None):
    import run_case

    def fake(cmd, cwd=None):
        if side_effect:
            side_effect()
        return _FakeProc(rc)

    monkeypatch.setattr(run_case.subprocess, "run", fake)


def test_run_default_nonzero_exits(monkeypatch):
    import run_case
    _patch_subprocess(monkeypatch, 1)
    with pytest.raises(SystemExit):
        run_case._run(["python", "x.py"], label="t")


def test_run_qgis_crash_code_no_longer_blanket_exempt(monkeypatch):
    """extract/overview 類步驟：0xC0000005 不再無條件放行（無產物 → 失敗）。"""
    import run_case
    _patch_subprocess(monkeypatch, 3221225477)
    with pytest.raises(SystemExit):
        run_case._run([r"C:\...\python-qgis-ltr.bat", "make_maps.py"], label="extract")


def test_run_crash_code_with_fresh_artifact_passes(tmp_path, monkeypatch, capsys):
    """崩潰碼 + 對應產物「本次剛寫出」→ 判為收尾崩潰，警告後放行。"""
    import run_case
    art = tmp_path / "圖層.gpkg"
    _patch_subprocess(monkeypatch, 3221225477,
                      side_effect=lambda: art.write_bytes(b"g"))
    assert run_case._run(["bat", "s"], label="extract gpkg",
                         crash_artifact=art) is True
    assert "收尾崩潰" in capsys.readouterr().out


def test_run_crash_code_with_stale_artifact_exits(tmp_path, monkeypatch):
    """產物是舊檔（本次沒寫出）→ 不放行，視為真失敗。"""
    import os
    import time as _t
    import run_case
    art = tmp_path / "圖層.gpkg"
    art.write_bytes(b"old")
    old = _t.time() - 3600
    os.utime(art, (old, old))
    _patch_subprocess(monkeypatch, 3221225477)
    with pytest.raises(SystemExit):
        run_case._run(["bat", "s"], label="extract gpkg", crash_artifact=art)


def test_run_maps_explicit_allowed_codes_pass(monkeypatch):
    """maps 步驟由呼叫端明示 allowed=(0,5,3221225477) → 照舊豁免。"""
    import run_case
    for rc in (0, 5, 3221225477):
        _patch_subprocess(monkeypatch, rc)
        assert run_case._run(["bat", "make_maps.py"], label="六張圖+qgz",
                             allowed=(0,) + run_case.QGIS_CRASH_CODES) is True


def test_run_fatal_false_returns_false_without_exit(monkeypatch, capsys):
    import run_case
    _patch_subprocess(monkeypatch, 2)
    assert run_case._run(["env.bat", "roadaccess"], label="臨路評估(內建)",
                         fatal=False) is False
    assert "不中斷" in capsys.readouterr().out
