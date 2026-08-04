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


# ── trust-shp 的欄名別名表（★集中在此，勿在呼叫處各自寫死）─────────────────
# 基地 shp 有兩種來源，欄名完全不同：
#   ② shp-xls-area-join --trust-fields → `20251201_*` 系列（TCDGIS 匯出 Excel 的欄序）
#   ⓪a cadastre_fetch（免登入）        → 中文短欄名 縣市/鄉鎮/段/使分/使類
# ⚠ 2026-08-04 龍崎案實錯：這些清單原本只寫 `20251201_*`，⓪a 路徑逐欄取到空字串，
#   於是 county="" → registry 查不到 → 印出「(空白)無地籍圖資（周邊地籍略過）」
#   並 continue，**連功能分區一起被跳過**；gpkg 的 使分/使類 也全被填成
#   「都市土地（使用分區另查）」。因主題1/2 已改走 WMTS，錯誤被遮住整整一輪才發現。
F_LANDNO = ["地號", "20251201_4"]
F_COUNTY = ["縣市", "縣市名稱", "20251201_1"]
F_TOWN = ["鄉鎮", "鄉鎮區", "鄉鎮市區", "20251201_2"]
F_SEG = ["段", "段別", "地段名稱", "20251201_3"]
F_ZONE = ["使分", "使用分區", "20251201_6"]
F_LTYPE = ["使類", "使用地類別", "20251201_7"]


def shp_norm_city(s: str) -> str:
    """縣市名正規化：巿→市、台→臺、去空白（對齊 registry 鍵與 shp_to_meta）。"""
    return normalize_county((s or "").replace("巿", "市")) or ""


def norm_seg(s: str) -> str:
    """段名正規化供 sitemap 複合鍵:去 [代碼] 前綴、去「段/小段」字樣與空白。
    例:'[4344]番子渡頭段'→'番子渡頭';'更寮段更寮小段'→'更寮更寮';
    清冊側 段='更寮'+小段='更寮' 亦得 '更寮更寮',兩側可對上。"""
    s = re.sub(r"^\[\w+\]", "", str(s or "").strip())
    return s.replace("小段", "").replace("段", "").replace(" ", "")


def load_sitemap(path: str) -> dict:
    """sitemap.csv(地號,案場ID,案場名稱[,段,小段]) → 查找 dict。值為
    ((案場ID,案場名稱), ...) 元組——**同段同地號列在多個案場時全部保留**
    (清冊允許一宗地同屬多案場,各案場都要出圖)。
    有段欄時建複合鍵 '正規化段|正規化地號'(跨縣市清冊必備——同地號可能出現在
    不同段/不同案場);同時保留「無碰撞的」純地號鍵向下相容。同地號跨案場
    (不同段)者從純地號鍵移除,強迫走複合鍵,避免張冠李戴。"""
    out: dict[str, list] = {}
    plain: dict[str, list] = {}
    collide: set = set()
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            no = normalize_landno(row.get("地號", ""))
            if not no:
                continue
            val = (str(row.get("案場ID", "")).strip(),
                   str(row.get("案場名稱", "")).strip())
            seg = norm_seg(str(row.get("段", "") or "") + str(row.get("小段", "") or ""))
            if seg:
                lst = out.setdefault(f"{seg}|{no}", [])
                if val not in lst:
                    lst.append(val)
            if no in plain and val not in plain[no]:
                collide.add(no)
            plain.setdefault(no, []).append(val)
    result = {k: tuple(v) for k, v in out.items()}
    for no, vals in plain.items():
        if no not in collide:
            result.setdefault(no, tuple(dict.fromkeys(vals)))
    if collide:
        print(f"[提示] sitemap:{len(collide)} 個地號跨案場重複,僅能以 段+地號 比對:"
              f"{'、'.join(sorted(collide))}", flush=True)
    return result


def gpkg_reuse_ok(gpkg_ids: set, case_ids: set) -> bool:
    """--skip-extract 重用既有 圖層.gpkg 的前提（純規則，兩模式共用）：
    本次全部案場ID都已存在於 gpkg 基地層即可重用（gpkg 較多屬正常——--cases= 過濾
    出圖子集）。任一集合為空或有本次案場不在 gpkg（如換 --mode 造成分組不同：
    single 的 001 vs persite 的 S001..）→ 不可重用，需完整重抽。"""
    return bool(case_ids) and bool(gpkg_ids) and case_ids <= gpkg_ids


def theme6_missing_record(fishpv_dir) -> tuple[str, str]:
    """主題6（臨路判定）無可用 sites 圖層時，05 清單該記什麼（狀態, 備註）——
    區分「本來就沒跑 fishpv」與「fishpv 跑過但失敗」（純規則，供 make_maps 與單測）：
    - 臨路評估資料夾不存在 → 照舊手冊直跑 make_maps、未經 run_case →「略過」；
    - 資料夾存在但 sites.shp 缺/壞 → fishpv 執行過但失敗 →「失敗：臨路評估失敗」。"""
    if not Path(fishpv_dir).exists():
        return ("略過", "未跑臨路評估(fishpv)；需圖6請走 run_case（或先跑 fishpv）")
    return ("失敗", "臨路評估失敗或產物不完整（缺/壞 sites.shp）")


def assign_site(landno: str, sitemap: dict, case_name: str, seg: str = ""):
    """(段,)地號 → ((案場ID, 案場名稱), ...)。一宗地可同屬多案場(元組含多對)。
    single(無 sitemap)全歸 001；persite 查 sitemap:先試 '正規化段|地號' 複合鍵
    (跨縣市清冊),再退純地號鍵(單一地區清冊/舊格式)。
    查無回 None（呼叫端歸 UNMAPPED_ID 並記 warning，不靜默併入真案場）。
    相容手建舊式 dict:值若是單一 (id,name) 會包成一層元組。"""
    def _norm(v):
        if v is None:
            return None
        return (v,) if v and isinstance(v[0], str) else tuple(v)
    if sitemap:
        no = normalize_landno(landno)
        if seg:
            hit = sitemap.get(f"{norm_seg(seg)}|{no}")
            if hit is not None:
                return _norm(hit)
        return _norm(sitemap.get(no))
    return (("001", case_name),)


def read_shp_trust_meta(shp_path: str, sitemap: dict, case_name: str,
                        only: set | None, exclude: set) -> dict:
    """只讀 dbf 屬性（免 QGIS）建 cases dict：id/name/county/town/rows。
    幾何在 make_maps.extract_layers_trust 才讀。無段號者照收（rows 之宗地母/子留空）。
    sitemap 查無的地號歸 UNMAPPED_ID 並列名警告。"""
    dbf_path = str(Path(shp_path).with_suffix(".dbf"))
    recs = read_dbf(dbf_path)
    cnt_c = Counter(shp_norm_city(shp_get(r, F_COUNTY)) for r in recs)
    cnt_t = Counter(shp_get(r, F_TOWN) for r in recs)

    def top(cnt):
        vals = [(k, v) for k, v in cnt.items() if k]
        return max(vals, key=lambda x: x[1])[0] if vals else ""

    def_c, def_t = top(cnt_c), top(cnt_t)
    cases: dict = {}
    missed: list[str] = []
    for r in recs:
        landno = shp_get(r, F_LANDNO)
        raw_seg = shp_get(r, F_SEG)
        sites = assign_site(landno, sitemap, case_name, seg=raw_seg)
        if sites is None:
            missed.append(f"{raw_seg}{landno}" if raw_seg else landno)
            sites = ((UNMAPPED_ID, UNMAPPED_NAME),)
        county = shp_norm_city(shp_get(r, F_COUNTY)) or def_c
        town = shp_get(r, F_TOWN) or def_t
        seg = re.sub(r"段$", "", shp_get(r, F_SEG))
        seg_code = shp_get(r, ["段號"])
        mother = child = ""
        if len(seg_code) >= 14:
            mother, child = seg_code[6:10], seg_code[10:14]
        zone = shp_get(r, F_ZONE)
        ltype = shp_get(r, F_LTYPE)
        for cid, cname in sites:      # 一宗地可同屬多案場,各案場都收這列
            if cid in exclude or (only and cid not in only):
                continue
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
