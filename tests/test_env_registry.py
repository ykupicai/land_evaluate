# -*- coding: utf-8 -*-
"""env_registry（環敏圖層登錄表與解析）——把 2026-08-04 NR23 事故的教訓寫成斷言。

事故：本機自然保留區 106 年版不含 110.07.30 公告的 NR23 龍崎牛埔惡地自然保留區，
逐筆距離算出「13 公里外、未涉及」，與平台「套疊 8.88 ㎡」**方向相反**。
修法＝TGOS 補 115年第2版（1152）放清單第一位、106 年版留 fallback，
另加案件層 `<案>/環敏/` 覆蓋機制。這裡鎖住三件事：
  1. 自然保留區的 shp 清單 1152 在 1061 之前（誰把順序調回去，測試就掛）
  2. resolve_layer 依序取第一個**存在**的檔（新版缺檔時退回舊版，不是壞掉）
  3. 案件層覆蓋檔優先於本機圖資，且 REGISTRY 未登錄的圖層也吃得到覆蓋
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from env_registry import REGISTRY, resolve_case_override, resolve_layer  # noqa: E402


def _rule(pattern_sub: str) -> dict:
    hits = [r for r in REGISTRY if pattern_sub in r["match"]]
    assert hits, f"REGISTRY 找不到 match 含 {pattern_sub!r} 的規則"
    return hits[0]


# ── 1. 登錄表本身的不變量 ────────────────────────────────────────────────

def test_nature_reserve_new_version_first():
    """自然保留區：1152（115年第2版）必須排在 1061（106 年版）之前。"""
    shps = _rule("自然保留區")["shp"]
    i_new = next(i for i, p in enumerate(shps) if "1152" in p)
    i_old = next(i for i, p in enumerate(shps) if "1061" in p)
    assert i_new < i_old, "新版圖資必須在 fallback 舊版之前（NR23 事故的修法）"


def test_landslide_registered():
    """山崩與地滑要有登錄（曾缺這條而印「無法解析為本機圖資」）。"""
    r = _rule("山崩與地滑")
    assert any("山崩與地滑" in p for p in r["shp"])


def test_semantic_colors_distinct():
    """指定了語意色的規則，彼此顏色不得相同（同色圖例等於白畫）。"""
    colored = [tuple(r["rgb"]) for r in REGISTRY if r.get("rgb")]
    assert len(colored) == len(set(colored))


# ── 2. resolve_layer：依序取第一個存在的檔 ──────────────────────────────

def _fake_env(tmp_path: Path, rels: list[str]) -> Path:
    for rel in rels:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    return tmp_path


def test_resolve_prefers_first_existing(tmp_path):
    rule = _rule("自然保留區")
    root = _fake_env(tmp_path, rule["shp"])          # 兩版都在 → 應取 1152
    p, r = resolve_layer("自然保留區", "臺南市", env_root=root)
    assert p is not None and "1152" in str(p)
    assert r["title"] == "自然保留區"


def test_resolve_falls_back_to_old_version(tmp_path):
    rule = _rule("自然保留區")
    root = _fake_env(tmp_path, [s for s in rule["shp"] if "1061" in s])   # 只有舊版
    p, _ = resolve_layer("自然保留區", "臺南市", env_root=root)
    assert p is not None and "1061" in str(p)


def test_resolve_missing_returns_rule_not_none(tmp_path):
    """圖資全缺：回 (None, 規則)——呼叫端據此印「無法解析」，不是丟例外。"""
    p, r = resolve_layer("自然保留區", "臺南市", env_root=tmp_path)
    assert p is None and r is not None and r["title"] == "自然保留區"


def test_resolve_county_variant(tmp_path):
    """縣市名臺/台變體：登錄表寫 {county}，圖資夾用「台南市」也要找得到。"""
    root = _fake_env(tmp_path, ["第二級/淹水潛勢/台南市/SHP/24h500.shp"])
    p, r = resolve_layer("淹水潛勢圖_定量降雨24小時500mm", "臺南市", env_root=root)
    assert p is not None and "台南市" in str(p)
    assert r["_subs"]["h"] == "24" and r["_subs"]["mm"] == "500"


# ── 3. 案件層覆蓋 ────────────────────────────────────────────────────────

def _fake_case(tmp_path: Path, fname: str) -> Path:
    d = tmp_path / "case" / "環敏"
    d.mkdir(parents=True, exist_ok=True)
    (d / fname).write_text("{}", encoding="utf-8")
    return tmp_path / "case"


def test_case_override_beats_local(tmp_path):
    """覆蓋檔存在時優先於本機圖資（即使本機兩版都在）。"""
    rule = _rule("自然保留區")
    root = _fake_env(tmp_path, rule["shp"])
    case = _fake_case(tmp_path, "NR23_自然保留區.geojson")   # 允許前綴備註
    p, r = resolve_layer("自然保留區", "臺南市", case_dir=case, env_root=root)
    assert p is not None and p.suffix == ".geojson"
    assert r.get("_override") is True


def test_case_override_for_unregistered_layer(tmp_path):
    """REGISTRY 沒登錄的圖層，有覆蓋檔仍出得了圖（單色、無分類）。"""
    case = _fake_case(tmp_path, "某新種敏感區.geojson")
    p, r = resolve_layer("某新種敏感區", "臺南市", case_dir=case, env_root=tmp_path)
    assert p is not None and r["_override"] and r["title"] == "某新種敏感區"


def test_case_override_ignores_other_files(tmp_path):
    case = _fake_case(tmp_path, "毫無關係的檔.geojson")
    assert resolve_case_override("自然保留區", case) is None
