# -*- coding: utf-8 -*-
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from shp_trust_utils import (  # noqa: E402
    UNMAPPED_ID,
    assign_site,
    dbf_encoding,
    load_sitemap,
    normalize_landno,
    read_dbf,
    read_shp_trust_meta,
)


# ---------------------------------------------------------------------------
# normalize_landno
# ---------------------------------------------------------------------------

def test_normalize_landno_strips_leading_zeros_mother_and_child():
    assert normalize_landno("0331-0001") == "331-1"


def test_normalize_landno_zhi_and_fullwidth_hyphen():
    assert normalize_landno("331之1") == "331-1"
    assert normalize_landno("331－1") == "331-1"


def test_normalize_landno_fullwidth_digits_and_spaces():
    assert normalize_landno(" １２２ ") == "122"


def test_normalize_landno_child_zero_treated_as_no_child():
    assert normalize_landno("331-0000") == "331"
    assert normalize_landno("122") == "122"


def test_normalize_landno_empty():
    assert normalize_landno("") == ""
    assert normalize_landno(None) == ""


def test_normalize_landno_matches_across_formats():
    # 基地原文 vs 官方 landno_text 還原，兩邊正規化後應相等
    assert normalize_landno("122-3") == normalize_landno("0122-0003")


# ---------------------------------------------------------------------------
# 建構最小 DBF 供 read_dbf / dbf_encoding 測試
# ---------------------------------------------------------------------------

def _build_dbf(fields, records, encoding):
    """fields=[(name,length)], records=[{name:str}]，回傳 DBF bytes。"""
    header_size = 32 + 32 * len(fields) + 1
    rec_size = 1 + sum(l for _, l in fields)
    out = bytearray()
    out += bytes([0x03, 125, 1, 1])            # version + date(YY MM DD)
    out += struct.pack("<I", len(records))     # 記錄數
    out += struct.pack("<H", header_size)
    out += struct.pack("<H", rec_size)
    out += bytes(20)                           # 補滿 32 byte header
    for name, length in fields:
        nb = name.encode(encoding)[:11]
        desc = bytearray(32)
        desc[0:len(nb)] = nb
        desc[11] = ord("C")                    # 一律 C 型字串欄
        desc[16] = length
        out += desc
    out += bytes([0x0D])                        # field 描述符終止
    for rec in records:
        out += bytes([0x20])                    # 非刪除旗標
        for name, length in fields:
            vb = rec.get(name, "").encode(encoding)[:length]
            out += vb + b" " * (length - len(vb))
    return bytes(out)


def _write_shp_dbf(tmp_path, fields, records, encoding, cpg=None):
    dbf = tmp_path / "t.dbf"
    dbf.write_bytes(_build_dbf(fields, records, encoding))
    if cpg is not None:
        (tmp_path / "t.cpg").write_text(cpg)
    return str(dbf)


def test_read_dbf_utf8(tmp_path):
    fields = [("段別", 30), ("地號", 10), ("20251201_6", 30)]
    recs = [{"段別": "番子渡頭段", "地號": "122", "20251201_6": "一般農業區"}]
    dbf = _write_shp_dbf(tmp_path, fields, recs, "utf-8")  # 無 .cpg → 預設 utf-8
    got = read_dbf(dbf)
    assert len(got) == 1
    assert got[0]["段別"] == "番子渡頭段"
    assert got[0]["地號"] == "122"
    assert got[0]["20251201_6"] == "一般農業區"


def test_read_dbf_big5_via_cpg(tmp_path):
    fields = [("段別", 30), ("20251201_1", 20), ("20251201_6", 30)]
    recs = [{"段別": "番子渡頭段", "20251201_1": "臺南市", "20251201_6": "一般農業區"}]
    dbf = _write_shp_dbf(tmp_path, fields, recs, "cp950", cpg="Big5")
    got = read_dbf(dbf)
    assert got[0]["段別"] == "番子渡頭段"       # Big5 值不再靜默變亂碼
    assert got[0]["20251201_1"] == "臺南市"
    assert got[0]["20251201_6"] == "一般農業區"


def test_read_dbf_skips_deleted(tmp_path):
    fields = [("地號", 10)]
    dbf = tmp_path / "t.dbf"
    raw = bytearray(_build_dbf(fields, [{"地號": "1"}, {"地號": "2"}], "utf-8"))
    # 把第 2 筆記錄旗標改為刪除(0x2A)
    header_size = struct.unpack_from("<H", raw, 8)[0]
    rec_size = struct.unpack_from("<H", raw, 10)[0]
    raw[header_size + rec_size] = 0x2A
    dbf.write_bytes(bytes(raw))
    got = read_dbf(str(dbf))
    assert [r["地號"] for r in got] == ["1"]


def test_dbf_encoding(tmp_path):
    dbf = tmp_path / "t.dbf"
    dbf.write_bytes(b"")
    assert dbf_encoding(str(dbf)) == "utf-8"            # 無 .cpg
    (tmp_path / "t.cpg").write_text("UTF-8")
    assert dbf_encoding(str(dbf)) == "utf-8"
    (tmp_path / "t.cpg").write_text("Big5")
    assert dbf_encoding(str(dbf)) == "cp950"
    (tmp_path / "t.cpg").write_text("950")
    assert dbf_encoding(str(dbf)) == "cp950"


# ---------------------------------------------------------------------------
# assign_site / load_sitemap / read_shp_trust_meta
# ---------------------------------------------------------------------------

def test_assign_site_single_all_001():
    assert assign_site("122", {}, "案子") == ("001", "案子")


def test_assign_site_persite_normalized_hit_and_miss():
    sm = {"122": ("S001", "S001宗基地")}
    assert assign_site("0122", sm, "案子") == ("S001", "S001宗基地")  # 正規化後命中
    assert assign_site("999", sm, "案子") is None                    # 查無→None


def test_load_sitemap_normalizes_keys(tmp_path):
    csvf = tmp_path / "sm.csv"
    csvf.write_text("地號,案場ID,案場名稱\n0122-0001,S001,S001宗基地\n",
                    encoding="utf-8-sig")
    sm = load_sitemap(str(csvf))
    assert sm["122-1"] == ("S001", "S001宗基地")


def test_read_shp_trust_meta_unmapped_goes_to_unmapped_id(tmp_path):
    fields = [("段號", 14), ("地號", 10), ("段別", 30),
              ("20251201_1", 20), ("20251201_2", 20),
              ("20251201_6", 30), ("20251201_7", 30)]
    recs = [
        {"段號": "DF434401220000", "地號": "122", "段別": "番子渡頭段",
         "20251201_1": "臺南市", "20251201_2": "官田區",
         "20251201_6": "一般農業區", "20251201_7": "農牧用地"},
        {"段號": "", "地號": "999", "段別": "番子渡頭段",
         "20251201_1": "臺南市", "20251201_2": "官田區",
         "20251201_6": "一般農業區", "20251201_7": "農牧用地"},
    ]
    dbf = _write_shp_dbf(tmp_path, fields, recs, "utf-8")
    shp = str(Path(dbf).with_suffix(".shp"))
    sm = {"122": ("S001", "S001宗基地")}
    cases = read_shp_trust_meta(shp, sm, "案子", None, set())
    assert "S001" in cases
    assert UNMAPPED_ID in cases                       # 查無→獨立 id，不併入真案場
    assert cases["S001"]["rows"][0]["地號"] == "122"
    assert cases[UNMAPPED_ID]["rows"][0]["地號"] == "999"
    # 無 14 碼段號者仍照收，宗地母/子留空
    assert cases[UNMAPPED_ID]["rows"][0]["宗地母"] == ""


def test_read_shp_trust_meta_single_mode_all_001(tmp_path):
    fields = [("段號", 14), ("地號", 10), ("20251201_1", 20)]
    recs = [{"段號": "DF434401220000", "地號": "122", "20251201_1": "臺南市"},
            {"段號": "", "地號": "999", "20251201_1": "臺南市"}]
    dbf = _write_shp_dbf(tmp_path, fields, recs, "utf-8")
    shp = str(Path(dbf).with_suffix(".shp"))
    cases = read_shp_trust_meta(shp, {}, "案子", None, set())
    assert list(cases) == ["001"]
    assert len(cases["001"]["rows"]) == 2             # 含無段號者
