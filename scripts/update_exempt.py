# -*- coding: utf-8 -*-
"""環敏『應免查範圍』每月更新（2026-08-05 建）。

初篩來源是官方 PDF 的離線全表（見 exempt_source.py / parse_exempt_pdf.py）。
官方會更新，故**每月**重下並重解析一次。

每月步驟（使用者定案 2026-08-05）：
  1. 到 環境敏感地區單一窗口查詢平台 →『應免查範圍查詢』頁
     （https://eland.nlma.gov.tw/seportal/ ，首頁 →『應免查範圍查詢』）
  2. 按右上『匯出全部項目』下載 PDF。
  3. 存成 config/環敏應免查範圍_<版本>.pdf（版本＝PDF 首頁『資料更新時間』，如 115-0731）。
  4. 跑本腳本重解析並就地更新 config/環敏應免查範圍.json：
        python -X utf8 scripts/update_exempt.py --pdf config/環敏應免查範圍_115-0731.pdf
     不給 --pdf 時，自動抓 config/ 下最新（檔名日期最大）的一份。

⚠ PDF（14.6MB）不進版控（.gitignore 已排除），解析後的 .json 才進版控。
⚠ 解析／判定的踩坑（長名換行漏列、免查列取相反、粒度三種）已寫成
   tests/test_exempt_prescreen.py，改動後務必 pytest。
"""
from __future__ import annotations

from config_loader import skill_path

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "config"
sys.path.insert(0, str(REPO / "scripts"))
import parse_exempt_pdf as PP  # noqa: E402

# eland『應免查範圍查詢』頁與『匯出全部項目』端點（2026-08-05 由平台實查）：
# GET  頁面取 __RequestVerificationToken（ASP.NET 防偽 token，免登入）
# POST ExportPdfAll 帶該 token → 回 PDF（全部項目）。
_PAGE = "https://eland.nlma.gov.tw/seportal/Web_SEData"
_EXPORT = "https://eland.nlma.gov.tw/seportal/Web_SEData/ExportPdfAll"


def download_pdf(out_dir: Path) -> Path:
    """線上下載最新『應免查範圍』全表 PDF，存 config/環敏應免查範圍_<版本>.pdf。"""
    # gov_session＝已掛政府 TLS 相容 adapter（eland 憑證缺 SKI，見 skill govtls.py）
    sys.path.insert(0, str(skill_path("env-sensitivity-check", "scripts")))
    from govtls import gov_session  # noqa: PLC0415
    s = gov_session()
    ua = {"User-Agent": "Mozilla/5.0 env-sensitivity-check/1.0"}
    page = s.get(_PAGE, timeout=30, headers=ua)
    page.raise_for_status()
    m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page.text)
    if not m:
        sys.exit("取不到 __RequestVerificationToken（平台版面可能改版）→ 改用手動下載，見檔頭步驟。")
    r = s.post(_EXPORT, data={"__RequestVerificationToken": m.group(1)},
               timeout=120, headers=ua)
    r.raise_for_status()
    if r.content[:4] != b"%PDF":
        ct = r.headers.get("Content-Type", "?")
        sys.exit(f"回應不是 PDF（Content-Type={ct}、{len(r.content)} bytes）→ 改用手動下載。")
    # 版本＝PDF 首頁『資料更新時間：NNN年MM月DD日』→ 檔名日期
    tmp = out_dir / "_下載中.pdf"
    tmp.write_bytes(r.content)
    ver = PP.parse(tmp).get("version") or "unknown"
    mv = re.search(r"(\d+)年(\d+)月(\d+)日", ver)
    tag = f"{mv.group(1)}-{int(mv.group(2)):02d}{int(mv.group(3)):02d}" if mv else "latest"
    final = out_dir / f"環敏應免查範圍_{tag}.pdf"
    tmp.replace(final)
    print(f"已下載：{final.name}（版本 {ver}、{len(r.content):,} bytes）")
    return final


def _latest_pdf() -> Path | None:
    cands = list(CONFIG.glob("環敏應免查範圍_*.pdf"))
    if not cands:
        return None

    def keyf(p):
        m = re.findall(r"\d+", p.stem)
        return tuple(int(x) for x in m) if m else (0,)
    return sorted(cands, key=keyf)[-1]


def main():
    ap = argparse.ArgumentParser(description="重解析『應免查範圍』PDF → config/環敏應免查範圍.json")
    ap.add_argument("--pdf", default=None, help="PDF 路徑（預設抓 config/ 下最新一份）")
    ap.add_argument("--download", action="store_true",
                    help="先線上下載最新 PDF（POST ExportPdfAll，免登入）再解析")
    ap.add_argument("--out", default=str(CONFIG / "環敏應免查範圍.json"))
    a = ap.parse_args()

    if a.download:
        pdf = download_pdf(CONFIG)
    else:
        pdf = Path(a.pdf) if a.pdf else _latest_pdf()
    if not pdf or not pdf.exists():
        sys.exit("找不到 PDF。請先到平台『應免查範圍查詢』按『匯出全部項目』下載，"
                 "存成 config/環敏應免查範圍_<版本>.pdf（見本檔檔頭步驟）。")

    import json  # noqa: PLC0415
    result = PP.parse(pdf)
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    empty = [it for it in result["items"] if not it["regions"]]
    print(f"來源 PDF：{pdf.name}")
    print(f"版本：{result['version']}　項目數：{result['n_items']}")
    if empty:
        print(f"⚠ {len(empty)} 項清單為空，解析可能有問題：",
              [f'{it["level"]}#{it["item_no"]}{it["name"]}' for it in empty[:8]])
    else:
        print("所有項目皆有行政區清單（無空列）。")
    print(f"→ {a.out}")
    print("提醒：改動後跑 `pytest tests/test_exempt_prescreen.py`。")


if __name__ == "__main__":
    main()
