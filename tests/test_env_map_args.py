import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import env_map_args as ema

HDR_NGIS = "案場,級別,圖層名稱,重疊面積_m2,佔基地比例\n"


def _w(p, text):
    p.write_text(text, encoding="utf-8-sig")


def test_pick_prefers_platform_csv_then_ngis(tmp_path):
    _w(tmp_path / "環敏NGIS.csv", HDR_NGIS)
    p, why = ema.pick_env_csv([tmp_path])
    assert p.name == "環敏NGIS.csv" and "採用" in why
    _w(tmp_path / "環境敏感套疊.csv", "圖層名稱,套疊面積_m2\n")
    assert ema.pick_env_csv([tmp_path])[0].name == "環境敏感套疊.csv"


def test_pick_falls_back_to_second_dir_and_missing(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    _w(b / "環敏NGIS.csv", HDR_NGIS)
    assert ema.pick_env_csv([a, b])[0] == b / "環敏NGIS.csv"
    p, why = ema.pick_env_csv([a])
    assert p is None and "找不到" in why
    assert ema.pick_env_csv([a], explicit=tmp_path / "x.csv")[0] is None


def test_read_hits_filters_by_site(tmp_path):
    p = tmp_path / "環敏NGIS.csv"
    _w(p, HDR_NGIS + "S001,一級,山坡地,100,0.1\nS001,一級,斷層,0,0\n"
                     "S002,二級,山坡地,50,0.5\nS002,二級,水庫,20,0.2\n")
    assert ema.read_hits(p) == ([("山坡地", 100.0), ("山坡地", 50.0), ("水庫", 20.0)], 4)
    assert ema.read_hits(p, "S001") == ([("山坡地", 100.0)], 2)
    assert ema.read_hits(p, "S002")[0] == [("山坡地", 50.0), ("水庫", 20.0)]
    assert ema.has_site_column(p)


def test_read_hits_without_site_column_not_filtered(tmp_path):
    p = tmp_path / "環境敏感套疊.csv"
    _w(p, "圖層名稱,套疊面積_m2\n山坡地,10\n")
    assert ema.read_hits(p, "S001") == ([("山坡地", 10.0)], 1)
    assert not ema.has_site_column(p)


def test_site_keys_need_section_and_landno(tmp_path):
    sm = tmp_path / "sitemap.csv"
    _w(sm, "段,地號,案場ID,案場名稱\n[0283]甲段,12,S001,a\n乙段,12,S002,b\n乙段,13,S002,b\n")
    k1 = ema.load_site_keys(sm, "S001")
    assert ema.in_site(k1, "[0283]甲段", "12")
    assert ema.in_site(k1, "甲段", "12")
    assert not ema.in_site(k1, "乙段", "12")       # 跨段同地號不可誤配
    assert not ema.in_site(k1, "甲段", "13")
    assert ema.load_site_keys(sm, "S999") == set()


def test_site_keys_without_section_column(tmp_path):
    sm = tmp_path / "sitemap.csv"
    _w(sm, "地號,案場ID\n12,S001\n")
    assert ema.in_site(ema.load_site_keys(sm, "S001"), "任一段", "12")


def test_png_names_keep_legacy_without_site():
    assert ema.env_png_name("案") == "001_案_環境敏感.png"
    assert ema.env_png_name("案", "S001") == "001_案_S001_環境敏感.png"
    assert ema.env_stats_name() == "環境敏感套疊_逐類面積.csv"
    assert ema.env_stats_name("S001") != ema.env_stats_name()


def test_multi_site_csv_without_site_dedupes_and_warns(tmp_path):
    p = tmp_path / "環敏NGIS.csv"
    _w(p, HDR_NGIS + "S001,一級,山坡地,100,0.1\nS002,二級,山坡地,150,0.5\nS002,二級,水庫,20,0.2\n")
    hits, n, warns = ema.read_hits_ex(p)
    assert dict(hits) == {"山坡地": 150.0, "水庫": 20.0} and len(hits) == 2   # 不重複計入
    assert n == 2 and any("--site" in w for w in warns)


def test_site_filter_zero_rows_warns_with_actual_values(tmp_path):
    p = tmp_path / "環敏NGIS.csv"
    _w(p, HDR_NGIS + "基地,一級,山坡地,100,0.1\n")
    hits, n, warns = ema.read_hits_ex(p, "S001")
    assert hits == [] and n == 0
    assert warns and "基地" in warns[0] and "S001" in warns[0]


def test_single_site_or_matching_site_no_warning(tmp_path):
    p = tmp_path / "環敏NGIS.csv"
    _w(p, HDR_NGIS + "S001,一級,山坡地,100,0.1\nS001,一級,水庫,5,0.1\n")
    assert ema.read_hits_ex(p)[2] == [] and ema.read_hits_ex(p, "S001")[2] == []
    assert ema.read_hits_ex(p, "S001")[0] == [("山坡地", 100.0), ("水庫", 5.0)]
