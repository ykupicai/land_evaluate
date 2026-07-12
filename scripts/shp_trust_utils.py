# -*- coding: utf-8 -*-
"""trust-shp 模式的純資料輔助（不依賴 QGIS，故可獨立單元測試）。

供 make_maps.py 於 trust-shp 出圖時：讀 shp 屬性(DBF，依 .cpg 決定編碼)、
地號正規化比對、sitemap 分派案場。幾何相關（QgsVectorLayer/extract）留在 make_maps。
"""
from __future__ import annotations

import codecs
import csv
import re
import struct
from collections import Counter
from pathlib import Path

from parse_helpers import normalize_county

# sitemap 查無涵蓋的地號 → 歸此 id（刻意不與真案場 001/S001.. 撞號，讓問題自我暴露）
UNMAPPED_ID = "_UNMAPPED"
UNMAPPED_NAME = "未對應宗基地"

# 地號正規化用：全形連字號類與「之」→ 半形「-」
_HYPHEN_LIKE = "－—–ー﹣‐‑之"
_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize_landno(s) -> str:
    """地號正規化供比對：去空白、全形數字→半形、全形「-」/「之」→半形「-」、
    去掉母/子號各自前導零；子號為 0 視為無子號。
    例：'0331-0001'→'331-1'、'331之1'→'331-1'、' 122 '→'122'、'331-0000'→'331'。"""
    s = str(s or "").strip()
    if not s:
        return ""
    s = s.translate(_FW_DIGITS)
    for ch in _HYPHEN_LIKE:
        s = s.replace(ch, "-")
    s = re.sub(r"\s+", "", s)
    parts = s.split("-")

    def strip0(x: str) -> str:
        x = x.lstrip("0")
        return x if x else "0"

    mother = strip0(parts[0]) if parts[0] else ""
    if len(parts) >= 2 and parts[1] != "":
        child = strip0(parts[1])
        if child != "0":
            return f"{mother}-{child}"
    return mother


def dbf_encoding(dbf_path: str) -> str:
    """依同名 .cpg 檔決定 DBF 編碼（UTF-8 / Big5 / cp950 / 950…），無 .cpg 預設 utf-8。"""
    cpg = Path(dbf_path).with_suffix(".cpg")
    if not cpg.exists():
        return "utf-8"
    try:
        raw = cpg.read_text(errors="replace").strip()
    except Exception:  # noqa: BLE001
        return "utf-8"
    u = raw.upper()
    if not u:
        return "utf-8"
    if "UTF" in u and "8" in u:
        return "utf-8"
    if "950" in u or "BIG5" in u or "BIG-5" in u or "CP950" in u:
        return "cp950"
    try:  # 其餘直接嘗試當 codec 名（如 "ISO-8859-1"）
        codecs.lookup(raw)
        return raw
    except Exception:  # noqa: BLE001
        return "utf-8"


def read_dbf(dbf_path: str) -> list[dict]:
    """極簡 DBF 讀取（純標準庫，QGIS 內建 Python 無 dbfread 亦可用）。
    編碼依 .cpg 決定（見 dbf_encoding），值以 errors=replace 解為字串並去空白——
    本工具只需 C 型字串欄（段號/地號/段別/縣市/鄉鎮/使分/使類），數值欄以字串帶過不使用。
    欄名截斷（>10 bytes 的中文欄名）容忍；跳過標記刪除的記錄，與 QGIS OGR 讀取順序/
    筆數一致，供幾何-屬性按位置配對。"""
    enc = dbf_encoding(dbf_path)
    with open(dbf_path, "rb") as f:
        data = f.read()
    n_rec = struct.unpack_from("<I", data, 4)[0]
    header_size = struct.unpack_from("<H", data, 8)[0]
    rec_size = struct.unpack_from("<H", data, 10)[0]
    fields = []  # (name, length)
    off = 32
    while data[off] != 0x0D:
        name = data[off:off + 11].split(b"\x00", 1)[0].decode(enc, "replace")
        length = data[off + 16]
        fields.append((name, length))
        off += 32
    recs = []
    for i in range(n_rec):
        start = header_size + i * rec_size
        if start >= len(data):
            break
        if data[start] == 0x2A:  # 標記刪除
            continue
        pos = start + 1
        rec = {}
        for name, length in fields:
            raw = data[pos:pos + length]
            rec[name] = raw.decode(enc, "replace").strip()
            pos += length
        recs.append(rec)
    return recs


def shp_get(rec, keys) -> str:
    """記錄依欄名清單取第一個非空值（同 shp_to_meta.g，容忍截斷欄名）。"""
    for k in keys:
        if k in rec and rec[k] not in (None, ""):
            return str(rec[k]).strip()
    return ""


def shp_norm_city(s: str) -> str:
    """縣市名正規化：巿→市、台→臺、去空白（對齊 registry 鍵與 shp_to_meta）。"""
    return normalize_county((s or "").replace("巿", "市")) or ""


def load_sitemap(path: str) -> dict:
    """sitemap.csv(地號,案場ID,案場名稱) → {正規化地號:(案場ID,案場名稱)}。
    鍵用 normalize_landno，查詢端亦正規化，避免母/子號零填/全形差異靜默漏配。"""
    out: dict[str, tuple] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            no = normalize_landno(row.get("地號", ""))
            if no:
                out[no] = (str(row.get("案場ID", "")).strip(),
                           str(row.get("案場名稱", "")).strip())
    return out


def assign_site(landno: str, sitemap: dict, case_name: str):
    """地號 → (案場ID, 案場名稱)。single(無 sitemap)全歸 001；persite 查 sitemap
    (以正規化地號比對)，查無回 None（呼叫端歸 UNMAPPED_ID 並記 warning，不靜默併入真案場）。"""
    if sitemap:
        return sitemap.get(normalize_landno(landno))
    return ("001", case_name)


def read_shp_trust_meta(shp_path: str, sitemap: dict, case_name: str,
                        only: set | None, exclude: set) -> dict:
    """只讀 dbf 屬性（免 QGIS）建 cases dict：id/name/county/town/rows。
    幾何在 make_maps.extract_layers_trust 才讀。無段號者照收（rows 之宗地母/子留空）。
    sitemap 查無的地號歸 UNMAPPED_ID 並列名警告。"""
    dbf_path = str(Path(shp_path).with_suffix(".dbf"))
    recs = read_dbf(dbf_path)
    cnt_c = Counter(shp_norm_city(shp_get(r, ["20251201_1"])) for r in recs)
    cnt_t = Counter(shp_get(r, ["20251201_2"]) for r in recs)

    def top(cnt):
        vals = [(k, v) for k, v in cnt.items() if k]
        return max(vals, key=lambda x: x[1])[0] if vals else ""

    def_c, def_t = top(cnt_c), top(cnt_t)
    cases: dict = {}
    missed: list[str] = []
    for r in recs:
        landno = shp_get(r, ["地號", "20251201_4"])
        site = assign_site(landno, sitemap, case_name)
        if site is None:
            missed.append(landno)
            site = (UNMAPPED_ID, UNMAPPED_NAME)
        cid, cname = site
        if cid in exclude or (only and cid not in only):
            continue
        county = shp_norm_city(shp_get(r, ["20251201_1"])) or def_c
        town = shp_get(r, ["20251201_2"]) or def_t
        seg = re.sub(r"段$", "", shp_get(r, ["段別", "20251201_3"]))
        seg_code = shp_get(r, ["段號"])
        mother = child = ""
        if len(seg_code) >= 14:
            mother, child = seg_code[6:10], seg_code[10:14]
        zone = shp_get(r, ["20251201_6"])
        ltype = shp_get(r, ["20251201_7"])
        c = cases.setdefault(cid, {"id": cid, "name": cname, "county": county,
                                   "town": town, "rows": [],
                                   "_ccnt": Counter(), "_tcnt": Counter()})
        c["_ccnt"][county] += 1
        c["_tcnt"][town] += 1
        c["rows"].append({"地號": landno, "段": seg, "小段": "",
                          "宗地母": mother, "宗地子": child,
                          "使分": zone or None, "使類": ltype or None, "fids": []})
    for c in cases.values():
        if c["_ccnt"]:
            c["county"] = c["_ccnt"].most_common(1)[0][0]
        if c["_tcnt"]:
            c["town"] = c["_tcnt"].most_common(1)[0][0]
        c.pop("_ccnt", None)
        c.pop("_tcnt", None)
    if sitemap and missed:
        print(f"[警告] trust-shp：sitemap 未涵蓋 {len(missed)} 筆地號 → 歸 {UNMAPPED_ID}"
              f"（不併入真案場）：{'、'.join(missed)}", flush=True)
    return cases
