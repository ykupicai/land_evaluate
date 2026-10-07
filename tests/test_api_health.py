# -*- coding: utf-8 -*-
"""api_health 的離線測試：不連網，全部用假回應。

這裡鎖住的是「哪些回應必須判成壞掉」——本專案踩過的假綠燈都寫成斷言：
HTTP 200 的空圖磚、HTTP 200 的錯誤頁、圖層還在但欄位改名。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import api_health as ah  # noqa: E402


class FakeResp:
    def __init__(self, status=200, content=b"", text=None, headers=None):
        self.status_code = status
        self.content = content
        self.text = text if text is not None else content.decode("utf-8", "replace")
        self.headers = headers or {}


class FakeSession:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self._resp(url) if callable(self._resp) else self._resp


def _run(spec, resp):
    kind = spec.get("檢查")
    probe = ah.probe_urbanplan if kind == "_urbanplan" else ah.PROBES[kind]
    return probe(FakeSession(resp), spec)


# ── image：NLSC 的空圖磚是 HTTP 200，只看狀態碼會全綠 ──────────────────────
def test_空圖磚雖然回200仍判FAIL():
    spec = {"檢查": "image", "url": "x", "最小位元組": 500}
    status, detail, _ = _run(spec, FakeResp(200, b"", headers={"Content-Type": "image/gif"}))
    assert status == "FAIL"
    assert "空圖磚" in detail


def test_正常圖磚判OK():
    spec = {"檢查": "image", "url": "x", "最小位元組": 500}
    status, _, _ = _run(spec, FakeResp(200, b"\x89PNG" + b"0" * 2000,
                                       headers={"Content-Type": "image/png"}))
    assert status == "OK"


def test_圖磚回HTML判FAIL():
    """代理或錯誤頁會回 200＋HTML，長度也夠，只驗大小會漏掉。"""
    spec = {"檢查": "image", "url": "x", "最小位元組": 500}
    status, detail, _ = _run(spec, FakeResp(200, b"<html>" + b"x" * 2000,
                                            headers={"Content-Type": "text/html"}))
    assert status == "FAIL"
    assert "Content-Type" in detail


# ── json：錯誤頁與改版 ────────────────────────────────────────────────────
def test_json型收到HTML判FAIL():
    spec = {"檢查": "json", "url": "x", "必含鍵": ["currentVersion"]}
    status, detail, _ = _run(spec, FakeResp(200, b"<html>login</html>"))
    assert status == "FAIL"
    assert "不是 JSON" in detail


def test_json缺少預期鍵判FAIL():
    spec = {"檢查": "json", "url": "x", "必含鍵": ["currentVersion"]}
    status, detail, _ = _run(spec, FakeResp(200, json.dumps({"other": 1}).encode()))
    assert status == "FAIL"
    assert "currentVersion" in detail


def test_帶BOM的json仍可解析():
    """臺北市圖台 ?f=json 實際會帶 UTF-8 BOM。"""
    body = "﻿" + json.dumps({"currentVersion": 10.9})
    spec = {"檢查": "json", "url": "x", "必含鍵": ["currentVersion"]}
    status, _, _ = _run(spec, FakeResp(200, body.encode("utf-8")))
    assert status == "OK"


# ── 都市計畫圖台：欄位改名是最會靜默出錯的一種 ────────────────────────────
def _layer(fields):
    return json.dumps({"name": "測試圖層",
                       "fields": [{"name": f, "alias": f} for f in fields]}).encode()


def test_圖層在但註冊表欄位不見判FAIL():
    spec = {"檢查": "_urbanplan", "url": "x", "fields": ["使用分區", "建蔽率"]}
    status, detail, _ = _run(spec, FakeResp(200, _layer(["使用分區", "覆蓋率"])))
    assert status == "FAIL"
    assert "建蔽率" in detail


def test_欄位齊全判OK():
    spec = {"檢查": "_urbanplan", "url": "x", "fields": ["使用分區", "建蔽率"]}
    status, _, _ = _run(spec, FakeResp(200, _layer(["使用分區", "建蔽率", "其他"])))
    assert status == "OK"


def test_arcgis回error物件判FAIL():
    spec = {"檢查": "_urbanplan", "url": "x", "fields": []}
    body = json.dumps({"error": {"code": 499, "message": "Token Required"}}).encode()
    status, detail, _ = _run(spec, FakeResp(200, body))
    assert status == "FAIL"
    assert "499" in detail


def test_unstable縣市判SKIP而非FAIL():
    spec = {"檢查": "_urbanplan", "url": "x", "fields": [], "unstable": True}
    status, _, _ = _run(spec, FakeResp(200, b"{}"))
    assert status == "SKIP"


def test_token抓不到判FAIL():
    spec = {"檢查": "_urbanplan", "url": "x?f=json", "fields": [],
            "token_url": "t", "token_regex": r'Token\s*=\s*"([^"]+)"'}
    status, detail, _ = _run(spec, FakeResp(200, "<html>頁面改版了</html>".encode("utf-8")))
    assert status == "FAIL"
    assert "token" in detail


def test_token抓得到就帶進網址():
    spec = {"檢查": "_urbanplan", "url": "x?f=json", "fields": [],
            "token_url": "t", "token_regex": r'Token\s*=\s*"([^"]+)"'}
    session = FakeSession(lambda url: FakeResp(200, b'Token = "ABC123"')
                          if url == "t" else FakeResp(200, _layer([])))
    status, _, _ = ah.probe_urbanplan(session, spec)
    assert status == "OK"
    assert any("token=ABC123" in call[1] for call in session.calls)


# ── 例外訊息要能行動 ──────────────────────────────────────────────────────
def test_主機名不符的訊息要點出重點且不建議關閉驗證():
    exc = Exception("SSLCertVerificationError ... certificate verify failed: "
                    "Hostname mismatch, certificate is not valid for 'openapi.moi.gov.tw'.")
    text = ah.explain(exc)
    assert "憑證主機名不符" in text
    assert "openapi.moi.gov.tw" in text
    assert "不要改成關閉驗證" in text


# ── 與上週比對 ────────────────────────────────────────────────────────────
def test_diff_分出新壞掉與已復原與新增():
    rows = [{"id": "a", "狀態": "FAIL"}, {"id": "b", "狀態": "OK"},
            {"id": "c", "狀態": "FAIL"}, {"id": "d", "狀態": "OK"}]
    before = {"a": "OK", "b": "FAIL", "c": "FAIL"}
    broke, fixed, added = ah.diff(rows, before)
    assert [r["id"] for r in broke] == ["a"]
    assert [r["id"] for r in fixed] == ["b"]
    assert [r["id"] for r in added] == []      # d 是新項目但沒壞，不打擾
    before2 = {}
    _, _, added2 = ah.diff([{"id": "z", "狀態": "FAIL"}], before2)
    assert [r["id"] for r in added2] == ["z"]


def test_一直壞著不算變化():
    """連續壞掉不該每週重複提醒——只有狀態轉變才值得打斷使用者。"""
    rows = [{"id": "a", "狀態": "FAIL"}]
    broke, fixed, added = ah.diff(rows, {"a": "FAIL"})
    assert not (broke or fixed or added)


# ── 慢速降級 ──────────────────────────────────────────────────────────────
def test_通得過但太慢降成WARN(monkeypatch):
    spec = {"id": "slow", "名稱": "慢", "檢查": "text", "url": "x", "必含文字": []}
    monkeypatch.setattr(ah, "probe_text", lambda s, sp: ("OK", "HTTP 200", 99.0))
    monkeypatch.setitem(ah.PROBES, "text", ah.probe_text)
    row = ah.run_one(FakeSession(FakeResp()), spec)
    assert row["狀態"] == "WARN"
    assert "偏慢" in row["說明"]


# ── 漂移檢查：新接的服務沒登錄要被抓出來 ──────────────────────────────────
def test_漂移檢查抓出未登錄主機(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "scripts" / "new_api.py").write_text(
        'URL = "https://brand-new.example.gov.tw/rest"', encoding="utf-8")
    row = ah.drift_row({"不檢查主機": []}, [], repo=tmp_path)
    assert row["狀態"] == "WARN"
    assert "brand-new.example.gov.tw" in row["說明"]


def test_漂移檢查對已登錄主機不報(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "scripts" / "a.py").write_text('U = "https://known.gov.tw/x"', encoding="utf-8")
    row = ah.drift_row({"不檢查主機": ["known.gov.tw"]}, [], repo=tmp_path)
    assert row["狀態"] == "OK"


# ── 登錄表本身 ────────────────────────────────────────────────────────────
def test_登錄表每列都有必要欄位且id唯一():
    registry = ah.load_registry()
    services = registry["服務"]
    ids = [s["id"] for s in services]
    assert len(ids) == len(set(ids)), "id 重複會讓歷史比對錯亂"
    for spec in services:
        assert spec.get("名稱") and spec.get("影響"), f"{spec['id']} 缺名稱或影響"
        assert spec["檢查"] in ("json", "image", "text", "python"), spec["id"]
        if spec["檢查"] == "python":
            assert spec["函式"] in ah.PY_PROBES, f"{spec['id']} 指向不存在的函式"
        else:
            assert spec.get("url"), f"{spec['id']} 缺 url"


def test_報告內容含變化段與逐項表():
    rows = [{"id": "a", "名稱": "甲服務", "狀態": "FAIL", "影響": "會停", "說明": "連不上", "秒": 1.0}]
    text = ah.markdown(rows, rows, [], [], "上週.json", "2026-09-22 09:00")
    assert "本次變化" in text and "新壞掉" in text and "甲服務" in text
    assert "1 項不可用" in text


def test_首次執行不誤報變化():
    rows = [{"id": "a", "名稱": "甲", "狀態": "FAIL", "影響": "", "說明": "", "秒": 0}]
    text = ah.markdown(rows, [], [], [], None, "2026-09-22 09:00")
    assert "首次建立基準" in text


def test_沒有變化時不寫桌面紙條(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert ah.alert([], [], [], "內容") is None


@pytest.mark.parametrize("status,expect", [("FAIL", 0), ("WARN", 1), ("SKIP", 2), ("OK", 3)])
def test_排序讓壞掉的排最前(status, expect):
    assert ah.STATUS_ORDER[status] == expect


# ── 2026-10-07 code review：python 型探測要驗到產線真正會壞的地方 ──────────────
def _fake_module(monkeypatch, name, **attrs):
    import types
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    monkeypatch.setitem(sys.modules, name, m)


def _pl_raw(national, county):
    return {"STATUS": 1, "RESPONSE": [{
        "NATIONAL": {"PUBLIC": [{}] * national} if national is not None else None,
        "COUNTY": {"PUBLIC": [{}] * county} if county is not None else None}]}


def _with_real_branch_counts(monkeypatch, raw):
    import public_land
    _fake_module(monkeypatch, "public_land",
                 fetch_section_raw=lambda *a, **k: raw,
                 branch_counts=public_land.branch_counts)


def test_權屬COUNTY分支消失判FAIL(monkeypatch):
    """★ 錯法：只數 NATIONAL（90 筆）→ 綠燈。正解：COUNTY 分支 0 筆就是產線會漏縣市有地。"""
    _with_real_branch_counts(monkeypatch, _pl_raw(90, None))
    status, detail = ah.py_moi_public_land(None, {})
    assert status == "FAIL" and "COUNTY" in detail


def test_權屬筆數掉到門檻以下判FAIL(monkeypatch):
    _with_real_branch_counts(monkeypatch, _pl_raw(5, 3))
    assert ah.py_moi_public_land(None, {})[0] == "FAIL"


def test_權屬兩分支齊全且達門檻判OK(monkeypatch):
    _with_real_branch_counts(monkeypatch, _pl_raw(90, 43))
    status, detail = ah.py_moi_public_land(None, {})
    assert status == "OK" and "133" in detail


def test_權屬STATUS非1判FAIL(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("API 回 STATUS=0 MESSAGE=x")
    _fake_module(monkeypatch, "public_land", fetch_section_raw=boom, branch_counts=lambda j: {})
    assert ah.py_moi_public_land(None, {})[0] == "FAIL"


def test_新舊地號查無判FAIL(monkeypatch):
    """★ 錯法：GET 回 200（參數格式錯誤）→ OK。正解：已知樣本查不到新地號就是壞了。"""
    _fake_module(monkeypatch, "latlon_to_parcel", newold_query=lambda *a, **k: None)
    assert ah.py_moi_newold(None, {})[0] == "FAIL"


def test_新舊地號回錯結果判FAIL(monkeypatch):
    _fake_module(monkeypatch, "latlon_to_parcel",
                 newold_query=lambda *a, **k: ("9999", "00010000"))
    assert ah.py_moi_newold(None, {})[0] == "FAIL"


def test_新舊地號已知樣本正確判OK(monkeypatch):
    _fake_module(monkeypatch, "latlon_to_parcel",
                 newold_query=lambda *a, **k: ("4438", "03140000"))
    assert ah.py_moi_newold(None, {})[0] == "OK"


def test_地籍版次有但Tiled服務壞判FAIL(monkeypatch):
    """★ 錯法：只驗版次清單 → 綠燈。正解：Tiled 框查服務不可用就是 ⓪a′ 全停。"""
    def boom(*a, **k):
        raise RuntimeError("版次 115Aug 的地籍服務沒有任何圖層")
    _fake_module(monkeypatch, "moa_parcel_query", newest_version=lambda: "115Aug",
                 polygon_layers=boom, count_envelope=lambda *a, **k: 1)
    status, detail = ah.py_moa_newest_version(None, {})
    assert status == "FAIL" and "115Aug" in detail


def test_地籍測試框回0筆判FAIL(monkeypatch):
    _fake_module(monkeypatch, "moa_parcel_query", newest_version=lambda: "115Jun",
                 polygon_layers=lambda v: [(1, "Taiwan")], count_envelope=lambda *a, **k: 0)
    assert ah.py_moa_newest_version(None, {})[0] == "FAIL"


def test_地籍正常判OK(monkeypatch):
    _fake_module(monkeypatch, "moa_parcel_query", newest_version=lambda: "115Jun",
                 polygon_layers=lambda v: [(0, "Penghu"), (1, "Taiwan")],
                 count_envelope=lambda *a, **k: 3)
    assert ah.py_moa_newest_version(None, {})[0] == "OK"


def test_新舊地號登錄為python型():
    import yaml
    reg = yaml.safe_load((Path(__file__).resolve().parent.parent / "config" / "api_health.yaml")
                         .read_text(encoding="utf-8"))
    rows = [r for r in reg["服務"] if r["id"] == "moi-newold-landno"] if isinstance(reg, dict) and "服務" in reg \
        else [r for r in (reg if isinstance(reg, list) else next(v for v in reg.values() if isinstance(v, list)))
              if r.get("id") == "moi-newold-landno"]
    assert rows and rows[0]["檢查"] == "python" and rows[0]["函式"] in ah.PY_PROBES


# ── 2026-10-07 code review #12～#15 ──────────────────────────────────────────
def _up_spec(**kw):
    spec = {"檢查": "_urbanplan", "url": "x", "_base": "https://h/s/MapServer/0",
            "_proxy": None, "_proxy_encode": False, "fields": ["使用分區"]}
    spec.update(kw)
    return spec


def _up_session(layer_body, count_body):
    return FakeSession(lambda url: FakeResp(200, count_body) if "returnCountOnly" in url
                       else FakeResp(200, layer_body))


def test_欄名改掉但別名留著判FAIL():
    """★ 錯法：name∪alias 比對 → 綠燈。產線 attributes 只用 name 當 key。"""
    body = json.dumps({"name": "L", "fields": [{"name": "NEWNAME", "alias": "使用分區"}]}).encode()
    status, detail, _ = ah.probe_urbanplan(_up_session(body, b'{"count": 5}'), _up_spec())
    assert status == "FAIL" and "使用分區" in detail


def test_圖層不支援Query判FAIL():
    body = json.dumps({"name": "L", "capabilities": "Map",
                       "fields": [{"name": "使用分區"}]}).encode()
    status, detail, _ = ah.probe_urbanplan(_up_session(body, b'{"count": 5}'), _up_spec())
    assert status == "FAIL" and "Query" in detail


def test_圖層筆數查詢回0判FAIL():
    body = json.dumps({"name": "L", "capabilities": "Map,Query",
                       "fields": [{"name": "使用分區"}]}).encode()
    status, _, _ = ah.probe_urbanplan(_up_session(body, b'{"count": 0}'), _up_spec())
    assert status == "FAIL"


def test_圖層與筆數查詢都正常判OK且走代理編碼():
    body = json.dumps({"name": "L", "capabilities": "Map,Query",
                       "fields": [{"name": "使用分區"}]}).encode()
    sess = _up_session(body, b'{"count": 17336}')
    spec = _up_spec(_proxy="https://p/proxy.ashx?", _proxy_encode=True)
    status, detail, _ = ah.probe_urbanplan(sess, spec)
    assert status == "OK" and "17,336" in detail
    # 臺南型代理：目標網址必須 URL 編碼後接在代理後面（與產線 _proxied 一致）
    assert all(c[1].startswith("https://p/proxy.ashx?https%3A%2F%2Fh") for c in sess.calls)


def test_臺南細部計畫層也有健檢項():
    ids = {s["id"] for s in ah.urbanplan_specs()}
    assert "urbanplan-臺南市-細計" in ids


def test_API型端點回4xx判FAIL而非WARN():
    spec = {"檢查": "text", "url": "x", "API": True, "必含文字": []}
    assert _run(spec, FakeResp(404, b"{}"))[0] == "FAIL"
    spec2 = {"檢查": "text", "url": "x", "必含文字": []}
    assert _run(spec2, FakeResp(404, b"{}"))[0] == "WARN"


def test_必含類型不符判FAIL():
    spec = {"檢查": "text", "url": "x", "method": "HEAD", "必含類型": "zip", "必含文字": []}
    assert _run(spec, FakeResp(200, b"", headers={"Content-Type": "text/html"}))[0] == "FAIL"
    assert _run(spec, FakeResp(200, b"", headers={"Content-Type": "application/zip"}))[0] == "OK"


def test_NGIS一級圖層不足判FAIL(monkeypatch):
    _fake_module(monkeypatch, "ngis", fetch_token=lambda: "T",
                 list_layers=lambda tok, level: [1] * (3 if level == "一級" else 29))
    monkeypatch.setattr(ah, "skill_path", lambda *a: Path("."), raising=False)
    import config_loader
    monkeypatch.setattr(config_loader, "skill_path", lambda *a: Path("."))
    status, detail = ah.py_ngis_token(None, {})
    assert status == "FAIL" and "一級 3" in detail


def test_地號反查型查無分區判FAIL(monkeypatch):
    _fake_module(monkeypatch, "urbanplan_fetch",
                 query_by_landno=lambda *a: ([], [], []))
    status, _ = ah.py_urbanplan_landno(None, {"county": "宜蘭縣", "sect": "宜興段", "landno": "1"})
    assert status == "FAIL"


def test_only不寫報告(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(ah, "run", lambda only=None: [{"id": "x", "狀態": "OK", "名稱": "x",
                                                       "說明": "", "影響": "", "秒": 0.0}])
    monkeypatch.setattr(ah, "write_report", lambda *a, **k: called.append(1) or ("p", "t"))
    monkeypatch.setattr(ah, "previous", lambda: (None, {}))
    monkeypatch.setattr(ah, "alert", lambda *a, **k: None)   # 不碰真實桌面
    ah.main(["--only", "x"])
    assert not called
    ah.main(["--only", "x", "--write-report"])
    assert called
