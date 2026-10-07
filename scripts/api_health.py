# -*- coding: utf-8 -*-
"""外部服務每週健檢：這條產線相依的政府 API／圖台／圖磚今天還能不能用。

**只讀。**不下載圖資、不寫任何案件檔、不改設定；只打最小的探測請求並寫報告。

為什麼不是「ping 一下有回 200 就算好」——本專案踩過的三種假綠燈：
  ① NLSC 對**不存在的圖層**回 HTTP 200 + **0 bytes 空 gif**（見 CLAUDE.md）。
     → image 型探測一律驗位元組數與 content-type。
  ② 政府站台前面的代理／錯誤頁會回 200 + HTML，內容根本不是該服務。
     → json 型探測要求「解得出 JSON 且頂層有指定的鍵」。
  ③ 服務活著但**欄位改名／圖層改編號**，程式照樣壞。
     → 都市計畫 9 縣市圖台直接讀 `urbanplan_fetch.COUNTY` 註冊表，逐一驗
       `?f=json` 的欄位清單「有沒有涵蓋註冊表對應到的欄位」。這是最會靜默壞掉的一類。

另有**漂移檢查**：掃 scripts/ 與 config/ 實際出現的外部主機，和登錄表比對。
程式新接了一個服務卻沒登錄 → 報 WARN，避免健檢覆蓋率隨時間腐化。

用法（系統 Python 即可，不需要 QGIS）：
    python -X utf8 scripts/api_health.py            # 跑完整健檢、寫報告
    python -X utf8 scripts/api_health.py --json     # 只印 JSON（給自動化接）
    python -X utf8 scripts/api_health.py --only moa-cadastre,ngis-token
    python -X utf8 scripts/api_health.py --no-report # 不寫檔（除錯用）

離開碼：0＝沒有 FAIL；1＝有 FAIL。**與上週相比有無變化**不影響離開碼，
看報告開頭那段「本次變化」或桌面的警示檔。
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import datetime as dt
import json

from pathlib import Path
import re
import sys
import time

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

REPORT_DIR = REPO / "outputs" / "_maintenance" / "api_health"
REGISTRY = REPO / "config" / "api_health.yaml"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) api_health/1.0"}
DEFAULT_TIMEOUT = 30
SLOW_SECONDS = 10.0          # 通得過但很慢 → WARN，通常是對方站快撐不住的前兆
STATUS_ORDER = {"FAIL": 0, "WARN": 1, "SKIP": 2, "OK": 3}


# ── HTTP 底層（政府憑證缺 SKI 的相容處理）──────────────────────────────────
def _session():
    """回 requests.Session，對**所有 https** 掛上 govtls 放寬 strict 的 adapter。

    ⚠ 為什麼要放寬、放寬到什麼程度（2026-09-22 首跑實測）：
    本機 Python 3.14 的 `ssl.create_default_context()` 帶 `VERIFY_X509_STRICT`，
    而**大量政府站台的憑證缺 Subject Key Identifier 擴充欄位**——實測至少
    openapi.moi.gov.tw／api.nlsc.gov.tw／wmts.nlsc.gov.tw／lisp.land.moi.gov.tw
    ＋6 個縣市都計圖台全中。不放寬的話健檢會一次噴 15 個紅燈，**但那些服務其實是好的**
    （產線多半走 urllib＋系統憑證存放區，不受此旗標影響）——這種假紅燈比沒有健檢更糟，
    會訓練人忽略警報。
    govtls **只清掉 strict 旗標**，CA 憑證鏈驗證與主機名檢查全部保留，
    **不是 verify=False**；真正的憑證問題（過期、簽發鏈錯、換成別人的站）照樣會 FAIL。

    govtls 只對它自己登錄的幾個主機掛 adapter，故這裡改掛在 "https://" 前綴，
    涵蓋本健檢會打的全部政府主機。載不到 govtls 就照預設跑（受影響主機會顯示 SSL 錯誤）。
    """
    import requests
    session = requests.Session()
    session.headers.update(UA)
    try:
        from config_loader import skill_path
        govtls_dir = skill_path("env-sensitivity-check", "scripts")
        if govtls_dir.is_dir():
            sys.path.insert(0, str(govtls_dir))
            import govtls  # noqa: PLC0415
            session.mount("https://", govtls.RelaxedStrictAdapter())
    except Exception:  # noqa: BLE001 - 載不到就用預設 session，不阻斷健檢
        pass
    return session


def _fetch(session, url, timeout, referer=None, method="GET", **kwargs):
    headers = {"Referer": referer} if referer else None
    started = time.perf_counter()
    resp = session.request(method, url, timeout=timeout, headers=headers, **kwargs)
    return resp, time.perf_counter() - started


def _loads(text):
    """容忍 UTF-8 BOM 的 json.loads（臺北市圖台 ?f=json 就帶 BOM）。"""
    return json.loads(text.lstrip("\ufeff"))


# ── 四種探測 ───────────────────────────────────────────────────────────────
def probe_json(session, spec):
    timeout = spec.get("逾時", DEFAULT_TIMEOUT)
    resp, secs = _fetch(session, spec["url"], timeout, spec.get("referer"))
    if resp.status_code != 200:
        return "FAIL", f"HTTP {resp.status_code}", secs
    try:
        data = _loads(resp.text)
    except ValueError:
        head = resp.text.strip()[:60].replace("\n", " ")
        return "FAIL", f"回應不是 JSON（可能是錯誤頁／代理攔截）：{head}", secs
    missing = [k for k in spec.get("必含鍵") or [] if k not in data]
    if missing:
        return "FAIL", f"JSON 缺少預期鍵 {missing}；服務可能已改版", secs
    for want in spec.get("必含文字") or []:
        if want not in resp.text:
            return "FAIL", f"回應未含「{want}」；服務能力或圖層可能已變更", secs
    return "OK", f"JSON 正常（{len(resp.content)} bytes）", secs


def probe_image(session, spec):
    timeout = spec.get("逾時", DEFAULT_TIMEOUT)
    resp, secs = _fetch(session, spec["url"], timeout, spec.get("referer"))
    if resp.status_code != 200:
        return "FAIL", f"HTTP {resp.status_code}", secs
    size = len(resp.content)
    floor = spec.get("最小位元組", 500)
    ctype = resp.headers.get("Content-Type", "")
    if size < floor:
        # NLSC 對不存在的圖層就是回這個：200 + 極小的空 gif
        return "FAIL", f"只回 {size} bytes（門檻 {floor}）＝空圖磚，該圖層可能已下架或改名", secs
    if not ctype.startswith("image/"):
        return "FAIL", f"Content-Type 是 {ctype or '(空)'}，不是影像", secs
    return "OK", f"{ctype} {size} bytes", secs


def probe_text(session, spec):
    timeout = spec.get("逾時", DEFAULT_TIMEOUT)
    resp, secs = _fetch(session, spec["url"], timeout, spec.get("referer"))
    if resp.status_code >= 500:
        return "FAIL", f"HTTP {resp.status_code}（伺服器端錯誤）", secs
    if resp.status_code >= 400:
        # 405/404 代表站在、只是這個 method/路徑不吃；對「站是否活著」而言算通
        return "WARN", f"HTTP {resp.status_code}（站台有回應，但此路徑非預期用法）", secs
    for want in spec.get("必含文字") or []:
        if want not in resp.text:
            return "FAIL", f"頁面未含「{want}」；可能被導到錯誤頁", secs
    return "OK", f"HTTP 200（{len(resp.content)} bytes）", secs


def probe_python(session, spec):
    func = PY_PROBES.get(spec.get("函式"))
    if func is None:
        return "FAIL", f"登錄表指定的函式 {spec.get('函式')!r} 不存在", 0.0
    started = time.perf_counter()
    status, detail = func(session, spec)
    return status, detail, time.perf_counter() - started


PROBES = {"json": probe_json, "image": probe_image, "text": probe_text, "python": probe_python}


# ── python 型探測：直接呼叫產線實際使用的那支函式 ─────────────────────────
# 地籍探測的測試框：高雄彌陀安港段587（2026-10 已知有宗地）。框內 0 筆＝Query 壞了。
MOA_TEST_BOX = (171730.0, 2521695.0, 171790.0, 2521755.0)


def py_moa_newest_version(session, spec):
    """農業部地籍：版次清單＋**實際打 Tiled 框查服務**（⓪a′ 主路徑）。

    2026-10-07 code review 修：原本只驗 newest_version() 拿得到版次，漏掉 CLAUDE.md 點名
    最可能的情境——版次清單已出現新版、但 CadastralMap_Tiled_<版次> 還沒建好或改名，
    此時健檢綠燈、框查全停。改為直接呼叫產線 moa_parcel_query 的 polygon_layers 與
    count_envelope（同一段 HTTP 程式、同一份 token）。
    """
    import moa_parcel_query as mpq  # noqa: PLC0415
    ver = mpq.newest_version()
    if not ver:
        return "FAIL", "取不到現行版次（newest_version 回空值）"
    try:
        layers = mpq.polygon_layers(ver)
        n = mpq.count_envelope(*MOA_TEST_BOX, version=ver)
    except Exception as exc:  # noqa: BLE001
        return "FAIL", f"版次 {ver} 的 Tiled 框查服務不可用：{str(exc)[:150]}"
    if n <= 0:
        return "FAIL", f"版次 {ver}：測試框回 0 筆（已知有宗地），Query 可能已壞"
    return "OK", f"現行版次 {ver}；Tiled 服務 {len(layers)} 層、測試框回 {n} 筆"


def py_ngis_token(session, spec):
    from config_loader import skill_path
    sys.path.insert(0, str(skill_path("ngis-sensitive", "scripts")))
    import ngis  # noqa: PLC0415
    token = ngis.fetch_token()
    if not token:
        return "FAIL", "取不到 token"
    layers = ngis.list_layers(token, level="二級")
    return "OK", f"token 正常，二級圖層 {len(layers)} 層"


# 布袋鎮 1410 段 2026-08 實測：國有 90＋縣市有 43＝133 筆。低於此門檻即視為資料或結構異常。
PUBLIC_LAND_MIN_TOTAL = 120


def py_moi_public_land(session, spec):
    """權屬：直接呼叫產線 public_land.fetch_section_raw（同一段 urllib 程式）。

    2026-10-07 code review 修：原本只數 NATIONAL.PUBLIC（90 筆），產線 query_section 卻
    會走遍每個分支（含 COUNTY 縣市有 43 筆）；COUNTY 分支消失時健檢仍綠燈、權屬圖卻漏掉
    縣市有公有地。另原本沒驗 STATUS、門檻只有 >0。
    """
    import public_land  # noqa: PLC0415
    try:
        j = public_land.fetch_section_raw("Q", "03", "1410", timeout=spec.get("逾時", 45))
    except Exception as exc:  # noqa: BLE001
        return "FAIL", f"查詢失敗：{str(exc)[:150]}"
    counts = public_land.branch_counts(j)
    total = sum(counts.values())
    missing = [k for k in ("NATIONAL", "COUNTY") if not counts.get(k)]
    if missing:
        return "FAIL", f"分支 {missing} 回 0 筆（現有 {counts}）；產線權屬會靜默漏掉該類公有地"
    if total < PUBLIC_LAND_MIN_TOTAL:
        return "FAIL", f"只回 {total} 筆（門檻 {PUBLIC_LAND_MIN_TOTAL}，已知 133）：{counts}"
    return "OK", f"嘉義縣布袋鎮 1410 段回 {total} 筆公有地（{counts}）"


def py_moi_newold(session, spec):
    """新舊地號（MOI_API_011）：直接呼叫產線 latlon_to_parcel.newold_query，以已知樣本驗結構。

    樣本＝臺南麻豆 港子尾段11（事務所 DF、段碼 4311）→ 重測後客子寮段314（段碼 4438）。
    2026-10-07 code review 修：原本設成 text 型送 GET，對方回「參數格式錯誤」照樣判 OK；
    路由改名回 404 也只判 WARN、不留紙條。
    """
    import latlon_to_parcel  # noqa: PLC0415
    got = latlon_to_parcel.newold_query("DF", "4311", "00110000", "N",
                                        timeout=spec.get("逾時", 45))
    if got is None:
        return "FAIL", "已知樣本（港子尾段11）查無新地號：API 下架、改路由或結構變更"
    if got != ("4438", "03140000"):
        return "FAIL", f"已知樣本回 {got}，預期 ('4438', '03140000')；結構可能變更"
    return "OK", "已知樣本 港子尾段11 → 客子寮段314 正確"


def py_nlsc_buffer(session, spec):
    """環域查詢：/com 端點已知回空字串（不是空陣列），故用 med 驗。"""
    url = "https://api.nlsc.gov.tw/other/MarkBufferAnlys/med/121.5654/25.0330/500"
    resp, _ = _fetch(session, url, spec.get("逾時", DEFAULT_TIMEOUT))
    if resp.status_code != 200:
        return "FAIL", f"HTTP {resp.status_code}"
    if not resp.text.strip():
        return "FAIL", "回空字串（非空陣列）＝端點異常，不可視為查無"
    try:
        data = _loads(resp.text)
    except ValueError:
        return "FAIL", "回應不是 JSON"
    return "OK", f"醫療環域回 {len(data)} 筆"


PY_PROBES = {"moa_newest_version": py_moa_newest_version, "ngis_token": py_ngis_token,
             "moi_public_land": py_moi_public_land, "moi_newold": py_moi_newold,
             "nlsc_buffer": py_nlsc_buffer}


# ── 都市計畫縣市圖台：直接驗註冊表對應的欄位還在不在 ───────────────────────
def urbanplan_specs():
    """由 urbanplan_fetch 的註冊表產生探測項；不另抄一份 URL，才不會與產線分岔。"""
    try:
        import urbanplan_fetch as up  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return [{"id": "urbanplan-registry", "名稱": "都市計畫縣市圖台註冊表",
                 "影響": "無法讀取 urbanplan_fetch 註冊表", "檢查": "_error",
                 "_error": f"import 失敗：{exc}"}]
    specs = []
    for county, cfg in up.COUNTY.items():
        query = cfg.get("query") or ""
        base = query.rsplit("/query", 1)[0]
        # 有代理的縣市（高雄、花蓮）直連會被要求 token；產線本來就走代理，健檢比照，
        # 否則會一直報「Token Required」這種與實際可用性無關的假紅燈。
        target = base + "?f=json"
        if cfg.get("proxy"):
            target = cfg["proxy"] + target
        specs.append({
            "id": f"urbanplan-{county}",
            "名稱": f"都市計畫圖台 {county}",
            "影響": f"Ⓤ {county} 退回本機 2019 粗版 shp，道路截角與分區精度下降",
            "檢查": "_urbanplan",
            "url": target,
            "referer": cfg.get("referer"),
            # 須 token 的縣市（新北）：token 是從一支靜態 JS 用 regex 抓的，對方改版就會
            # 靜默失效——這正是每週最值得驗的一條，故健檢比照產線先抓 token 再打服務。
            "token_url": cfg.get("token_url"),
            "token_regex": cfg.get("token_regex"),
            "unstable": bool(cfg.get("unstable")),
            "fields": [v for v in (cfg.get("fields") or {}).values() if v],
            "逾時": 45,
        })
    for county in getattr(up, "LANDNUM_COUNTY", {}):
        specs.append({
            "id": f"urbanplan-{county}",
            "名稱": f"都市計畫圖台 {county}（地號反查型）",
            "影響": f"Ⓤ {county} 退回本機 2019 粗版 shp",
            "檢查": "text",
            # 這兩個縣市是自訂 API（POST、要 token／參數），健檢只驗「站台活著」；
            # referer 指的是圖台頁面，比裸網域根目錄更能代表服務是否還在。
            "url": getattr(up, "LANDNUM_COUNTY")[county].get("referer")
                   or getattr(up, "LANDNUM_COUNTY")[county].get("base") or "",
            "必含文字": [],
            "逾時": 45,
        })
    return [s for s in specs if s.get("url")]


def probe_urbanplan(session, spec):
    """ArcGIS 圖層 `?f=json`：先確認圖層在，再確認註冊表對應的欄位都還在。"""
    if spec.get("unstable"):
        return "SKIP", "註冊表標 unstable（已知不穩定，產線本來就退回本機 shp）", 0.0
    timeout = spec.get("逾時", 45)
    url = spec["url"]
    if spec.get("token_url") and spec.get("token_regex"):
        page, _ = _fetch(session, spec["token_url"], timeout, spec.get("referer"))
        found = re.search(spec["token_regex"], page.text)
        if not found:
            return ("FAIL", "取不到 token：token_regex 在該頁抓不到值（對方改版了，"
                            "產線會靜默退回本機 shp）", 0.0)
        url += "&token=" + found.group(1)
    resp, secs = _fetch(session, url, timeout, spec.get("referer"))
    if resp.status_code != 200:
        return "FAIL", f"HTTP {resp.status_code}", secs
    try:
        data = _loads(resp.text)
    except ValueError:
        head = resp.text.strip()[:60].replace("\n", " ")
        return "FAIL", f"回應不是 JSON（可能是登入頁／代理攔截）：{head}", secs
    if "error" in data:
        return "FAIL", f"服務回錯誤：{data['error']}", secs
    if "fields" not in data:
        return "FAIL", "回應沒有 fields（圖層可能已下架或改編號）", secs
    have = {f.get("name") for f in data["fields"]} | {f.get("alias") for f in data["fields"]}
    missing = [f for f in spec.get("fields") or [] if f not in have]
    if missing:
        return "FAIL", f"圖層在，但註冊表對應的欄位不見了：{missing}（欄位改名會靜默出錯）", secs
    name = data.get("name", "?")
    return "OK", f"圖層「{name}」，{len(spec.get('fields') or [])} 個對應欄位齊全", secs


# ── 漂移檢查：程式碼實際用到的主機 vs 登錄表 ──────────────────────────────
def code_hosts(repo=REPO):
    pattern = re.compile(r"https?://([A-Za-z0-9._-]+)")
    hosts = set()
    for folder in ("scripts", "config"):
        for path in (repo / folder).rglob("*"):
            if path.suffix.lower() not in (".py", ".yaml", ".yml") or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            hosts |= {h.rstrip(".") for h in pattern.findall(text)}
    return hosts


def drift_row(registry, specs, repo=REPO):
    listed = {h for h in registry.get("不檢查主機") or []}
    for spec in specs:
        url = spec.get("url") or ""
        found = re.match(r"https?://([A-Za-z0-9._-]+)", url)
        if found:
            listed.add(found.group(1))
    # python 型探測打的主機寫在程式裡，補進來
    listed |= {"coagis.colife.org.tw", "giss.nlma.gov.tw", "openapi.moi.gov.tw", "api.nlsc.gov.tw"}
    # 縣市圖台的 referer／proxy／base 主機隨註冊表走，一起視為已涵蓋——
    # 它們與該縣市的 query 是同一組服務，單獨列出只會讓漂移檢查每週喊同一批。
    try:
        import urbanplan_fetch as up  # noqa: PLC0415
        for cfg in list(up.COUNTY.values()) + list(getattr(up, "LANDNUM_COUNTY", {}).values()):
            for value in cfg.values():
                if isinstance(value, str):
                    found = re.match(r"https?://([A-Za-z0-9._-]+)", value)
                    if found:
                        listed.add(found.group(1))
    except Exception:  # noqa: BLE001 - 讀不到就退回較吵的行為，不阻斷
        pass
    extra = sorted(h for h in code_hosts(repo) - listed if "." in h)
    if not extra:
        return {"id": "_drift", "名稱": "健檢覆蓋率（漂移檢查）", "狀態": "OK",
                "影響": "—", "說明": "程式碼用到的外部主機都已登錄或列為不檢查", "秒": 0.0}
    return {"id": "_drift", "名稱": "健檢覆蓋率（漂移檢查）", "狀態": "WARN",
            "影響": "這些服務壞了不會被本健檢發現",
            "說明": f"{len(extra)} 個主機未登錄：{', '.join(extra)}"
                    "　→ 補進 config/api_health.yaml 的「服務」或「不檢查主機」",
            "秒": 0.0}


# ── 執行 ───────────────────────────────────────────────────────────────────
def load_registry(path=REGISTRY):
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def explain(exc):
    """把 requests 的長串例外壓成一句能行動的話。

    原始 SSLError 訊息長達五行、重點（憑證主機名對不上）埋在最後，
    週報上會被當成雜訊略過——而「對方裝錯憑證」恰恰是要立刻處理的那種。
    """
    text = str(exc)
    if "Hostname mismatch" in text:
        want = re.search(r"not valid for '([^']+)'", text)
        return (f"憑證主機名不符：對方裝的憑證不是發給 {want.group(1) if want else '本主機'} 的。"
                "服務本身可能還活著，但任何正常驗憑證的程式都連不上"
                "（⚠ 不要改成關閉驗證來繞過，先確認官方是否換了網域）")
    if "certificate has expired" in text:
        return "憑證已過期，需等對方更新"
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return f"憑證驗證失敗：{text.split('certificate verify failed:')[-1].strip()[:120]}"
    if "Max retries exceeded" in text or "ConnectionError" in type(exc).__name__:
        return "連不上（逾時或拒絕連線）"
    return f"{type(exc).__name__}: {text[:200]}"


def run_one(session, spec):
    kind = spec.get("檢查")
    probe = probe_urbanplan if kind == "_urbanplan" else PROBES.get(kind)
    row = {"id": spec["id"], "名稱": spec.get("名稱", spec["id"]),
           "影響": spec.get("影響", "")}
    if kind == "_error":
        row.update(狀態="FAIL", 說明=spec.get("_error", ""), 秒=0.0)
        return row
    if probe is None:
        row.update(狀態="FAIL", 說明=f"未知的檢查型別 {kind!r}", 秒=0.0)
        return row
    try:
        status, detail, secs = probe(session, spec)
    except Exception as exc:  # noqa: BLE001 - 單項失敗不可中斷整份健檢
        status, detail, secs = "FAIL", explain(exc), 0.0
    if status == "OK" and secs > SLOW_SECONDS:
        status, detail = "WARN", f"{detail}（但耗時 {secs:.1f} 秒，偏慢）"
    row.update(狀態=status, 說明=detail, 秒=round(secs, 2))
    return row


def run(only=None, repo=REPO, workers=8):
    registry = load_registry()
    specs = list(registry.get("服務") or []) + urbanplan_specs()
    if only:
        wanted = {s.strip() for s in only}
        specs = [s for s in specs if s["id"] in wanted]
    session = _session()
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(lambda s: run_one(session, s), specs))
    if not only:
        rows.append(drift_row(registry, specs, repo))
    rows.sort(key=lambda r: (STATUS_ORDER.get(r["狀態"], 9), r["id"]))
    return rows


# ── 與上次比較 ─────────────────────────────────────────────────────────────
def previous(report_dir=REPORT_DIR):
    files = sorted(report_dir.glob("*.json")) if report_dir.is_dir() else []
    if not files:
        return None, {}
    latest = files[-1]
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, {}
    return latest.name, {r["id"]: r["狀態"] for r in data.get("項目", [])}


def diff(rows, before):
    """回 (壞掉的, 復原的, 新項目)；只有這三類值得打斷使用者。"""
    broke, fixed, added = [], [], []
    for row in rows:
        was, now = before.get(row["id"]), row["狀態"]
        if was is None:
            if now == "FAIL":
                added.append(row)
            continue
        if was != "FAIL" and now == "FAIL":
            broke.append(row)
        elif was == "FAIL" and now != "FAIL":
            fixed.append(row)
    return broke, fixed, added


# ── 報告 ───────────────────────────────────────────────────────────────────
def markdown(rows, broke, fixed, added, prev_name, stamp):
    counts = {s: sum(1 for r in rows if r["狀態"] == s) for s in ("FAIL", "WARN", "SKIP", "OK")}
    out = [f"# 外部服務健檢　{stamp}", ""]
    out.append(f"**{counts['FAIL']} 項不可用**／{counts['WARN']} 項需注意／"
               f"{counts['SKIP']} 項略過／{counts['OK']} 項正常"
               f"（共 {len(rows)} 項）")
    out.append("")
    out.append("## 本次變化" + (f"（對照 {prev_name}）" if prev_name else "（首次執行，無對照）"))
    out.append("")
    if not prev_name:
        out.append("首次建立基準，下次執行才會有變化比對。")
    elif not (broke or fixed or added):
        out.append("與上次相同，沒有新壞掉也沒有復原的項目。")
    else:
        for row in broke:
            out.append(f"- 🔴 **新壞掉**：{row['名稱']} — {row['說明']}")
            out.append(f"  - 影響：{row['影響']}")
        for row in added:
            out.append(f"- 🔴 **新增項目且不可用**：{row['名稱']} — {row['說明']}")
        for row in fixed:
            out.append(f"- 🟢 **已復原**：{row['名稱']}")
    out += ["", "## 逐項結果", "",
            "| 狀態 | 服務 | 說明 | 壞掉會影響什麼 | 秒 |", "|---|---|---|---|---|"]
    icon = {"FAIL": "🔴 FAIL", "WARN": "🟡 WARN", "SKIP": "⚪ SKIP", "OK": "🟢 OK"}
    for row in rows:
        detail = str(row["說明"]).replace("|", "\\|").replace("\n", " ")
        out.append(f"| {icon.get(row['狀態'], row['狀態'])} | {row['名稱']} | {detail} | "
                   f"{row['影響']} | {row['秒']} |")
    out += ["", "---", "",
            "口徑：本健檢只證明「服務今天打得通、回的東西結構正確」，"
            "**不證明資料內容正確或為最新版**。圖資版本、法定認定仍照各該流程查核。",
            "", "重跑：`python -X utf8 scripts/api_health.py`"]
    return "\n".join(out) + "\n"


def write_report(rows, broke, fixed, added, prev_name, report_dir=REPORT_DIR):
    report_dir.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    payload = {"執行時間": now.isoformat(timespec="seconds"), "對照": prev_name, "項目": rows}
    json_path = report_dir / f"{now:%Y-%m-%d_%H%M}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    text = markdown(rows, broke, fixed, added, prev_name, stamp)
    (report_dir / "最新報告.md").write_text(text, encoding="utf-8")
    return json_path, text


def alert(broke, fixed, added, text):
    """只有狀態變了才打擾使用者：桌面丟一張紙條，再盡力跳一則通知。"""
    if not (broke or fixed or added):
        return None
    desktop = Path.home() / "Desktop"
    if not desktop.is_dir():
        return None
    name = ("外部服務異常" if (broke or added) else "外部服務已復原")
    path = desktop / f"⚠ {name}_{dt.datetime.now():%Y%m%d}.txt"
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="外部 API／圖台每週健檢（只讀）")
    ap.add_argument("--only", help="只跑指定 id，逗號分隔")
    ap.add_argument("--json", action="store_true", help="只輸出 JSON")
    ap.add_argument("--no-report", action="store_true", help="不寫報告檔")
    args = ap.parse_args(argv)

    rows = run(only=args.only.split(",") if args.only else None)
    prev_name, before = previous()
    broke, fixed, added = diff(rows, before)

    if args.json:
        print(json.dumps({"項目": rows, "新壞掉": [r["id"] for r in broke],
                          "已復原": [r["id"] for r in fixed]}, ensure_ascii=False, indent=2))
    else:
        _, text = (None, markdown(rows, broke, fixed, added, prev_name,
                                  dt.datetime.now().strftime("%Y-%m-%d %H:%M")))
        print(text)
    if not args.no_report:
        json_path, text = write_report(rows, broke, fixed, added, prev_name)
        note = alert(broke, fixed, added, text)
        if not args.json:
            print(f"報告：{json_path}")
            if note:
                print(f"狀態有變化，已於桌面留紙條：{note}")
    return 1 if any(r["狀態"] == "FAIL" for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
