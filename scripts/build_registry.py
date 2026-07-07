# -*- coding: utf-8 -*-
"""
掃描「地籍圖資/」與「國土功能分區/」，產出 config/county_registry.yaml。

用途：把兩批高度異質的圖資（尤其國土功能分區各縣市資料夾/檔名/欄位都不同）
一次性掃描登錄成統一結構，後續 match_parcels.py / make_maps.py 直接讀
registry，不用每次重新猜欄位。圖資更新後可重跑本腳本重新產出 yaml。

執行：python -X utf8 scripts/build_registry.py
需求：系統 Python（本專案用 3.14）+ geopandas/pyogrio/pandas/pyyaml。

設計文件：docs/superpowers/specs/2026-07-07-大量圖資轉換工具-design.md §4.2, §6
"""

from __future__ import annotations

import re
from pathlib import Path

import pyogrio
import yaml

# ---------------------------------------------------------------------------
# 路徑設定
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
CADASTRAL_DIR = REPO_ROOT / "地籍圖資"
ZONES_DIR = REPO_ROOT / "國土功能分區"
OUTPUT_YAML = REPO_ROOT / "config" / "county_registry.yaml"

# 20 個地籍縣市（檔名用「台」），對照到標準「臺」寫法的縣市 key。
# 台/臺 正規化：只有 台北市/台中市/台南市 三個會混用「台」，其餘縣市名稱一致。
TAI_TO_TAI_MAP = {
    "台北市": "臺北市",
    "台中市": "臺中市",
    "台南市": "臺南市",
}

# 全台 22 縣市標準名單（用來算 missing 清單）
ALL_COUNTIES = [
    "臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市",
    "基隆市", "新竹市", "嘉義市",
    "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣",
    "屏東縣", "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣",
    "金門縣", "連江縣",
]

# 國土功能分區資料夾名 → 縣市 key 的對照（資料夾名不規則，用關鍵字比對縣市名）
# 部分資料夾省略「市/縣」字尾（如「苗栗國土功能分區1091215」），故比對名單
# 同時納入縣市全名與去掉市/縣字尾的簡稱，簡稱較長者優先比對避免誤判。
_COUNTY_ALIASES: dict[str, str] = {}
for _c in ALL_COUNTIES:
    _COUNTY_ALIASES[_c] = _c
    if _c[-1] in ("市", "縣"):
        _COUNTY_ALIASES.setdefault(_c[:-1], _c)
COUNTY_NAME_PATTERN = re.compile(
    "(" + "|".join(sorted(_COUNTY_ALIASES.keys(), key=len, reverse=True)) + ")"
)

# 用來判斷某欄位是否「像」國土功能分區分類值的關鍵字
# 標準全名（如 城鄉發展地區第一類）與部分縣市省略「地區」二字的變體（如 城鄉發展第一類）皆納入。
ZONE_VALUE_KEYWORDS = [
    "國土保育地區", "農業發展地區", "城鄉發展地區", "海洋資源地區",
    "國土保育第", "農業發展第", "城鄉發展第", "海洋資源第",
]

# 民國年日期樣式，如 1091215、109.11、1090709、20200909 等，用來從檔名/資料夾名推版本
ROC_DATE_PATTERNS = [
    re.compile(r"(?<!\d)(\d{7})(?!\d)"),        # 1091215 (7位: 民國年3碼+月2碼+日2碼)
    re.compile(r"(?<!\d)(\d{6})(?!\d)"),        # 109.11 拿掉點後 -> 10911 之類 (6位)
    re.compile(r"(\d{3})\.(\d{1,2})"),           # 109.11
    re.compile(r"(?<!\d)(\d{8})(?!\d)"),        # 20200909 西元日期
]


def normalize_county_from_filename(stem: str) -> str | None:
    """把地籍 shp 檔名(不含副檔名)正規化成臺/縣市標準 key。"""
    if stem in TAI_TO_TAI_MAP:
        return TAI_TO_TAI_MAP[stem]
    if stem in ALL_COUNTIES:
        return stem
    return None


def guess_version_from_name(name: str) -> str | None:
    """從檔名/資料夾名猜資料版本（民國年或西元年日期字串），猜不到回 None。"""
    m = re.search(r"(\d{3})\.(\d{1,2})(?:\.(\d{1,2}))?", name)
    if m:
        return m.group(0) + "（民國年.月，待確認）"
    m = re.search(r"(?<!\d)(\d{7})(?!\d)", name)
    if m:
        return m.group(1) + "（民國年月日 YYYMMDD，待確認）"
    m = re.search(r"(?<!\d)(\d{8})(?!\d)", name)
    if m:
        return m.group(1) + "（西元年月日 YYYYMMDD，待確認）"
    m = re.search(r"(?<!\d)(\d{6})(?!\d)", name)
    if m:
        return m.group(1) + "（日期字串，待確認格式）"
    return None


def find_zone_field(shp_path: Path) -> tuple[str | None, str]:
    """
    抽樣讀檔案，找出「國土功能分區」值所在欄位。
    回傳 (欄位名或 None, 備註)。
    策略：
      1. 逐欄檢查是否有值包含 ZONE_VALUE_KEYWORDS 任一關鍵字 -> 命中即為分區欄。
      2. 若無命中，但欄位值本身看起來像簡碼（如 城鄉1/國保2/農發3/海洋2-1），
         也視為分區欄（此類檔案本身多半已用檔名/資料夾名分類，欄位僅是簡碼輔助）。
      3. 若檔名/資料夾名本身就是分區類別（如 城1.shp、未來發展地區.shp），
         判定為「檔名即分類」，欄位留 None，備註說明依檔名判斷。
      4. 檔案只有單一去重值、且該值本身像分區簡碼，視為單一分類欄位。
      5. 都找不到則回傳 None，並在備註寫下抽樣到的欄位/值供人工確認。
    """
    try:
        df = pyogrio.read_dataframe(shp_path, max_features=200, read_geometry=False)
    except Exception as exc:  # noqa: BLE001
        return None, f"讀取失敗：{exc}"

    if df.empty:
        return None, "抽樣 200 筆為空（檔案可能無資料或需加大抽樣數）"

    # 欄位名可能重複（shapefile 10 字元截斷造成），逐一用位置(iloc)取值避免
    # df[col] 在重複欄名時回傳 DataFrame 而非 Series。
    def col_values(idx: int):
        series = df.iloc[:, idx]
        dtype_str = str(series.dtype)
        is_textlike = (
            series.dtype == object
            or dtype_str.startswith("string")
            or dtype_str == "str"
        )
        if not is_textlike:
            return None
        try:
            return series.dropna().astype(str).unique()
        except Exception:  # noqa: BLE001
            return None

    # 策略 1：找含完整分類關鍵字的欄位
    for idx, col in enumerate(df.columns):
        values = col_values(idx)
        if values is None:
            continue
        if any(any(kw in v for kw in ZONE_VALUE_KEYWORDS) for v in values):
            return col, "偵測到完整分類名稱（如國土保育地區/農業發展地區等）"

    # 策略 2：找看起來像簡碼的欄位（中文短碼 + 數字，例：城鄉1、國保2、農發3、海洋2-1、國1、農1）
    short_code_re = re.compile(
        r"^(國保|農發|城鄉|海洋|國土保育|農業|城鄉發展|海洋資源|國|農|城|海)[0-9一二三四五\-之]+$"
    )
    best_short_code: tuple[str, float] | None = None
    for idx, col in enumerate(df.columns):
        values = col_values(idx)
        if values is None or len(values) == 0:
            continue
        hit_ratio = sum(1 for v in values if short_code_re.match(v)) / len(values)
        if hit_ratio > 0.5 and (best_short_code is None or hit_ratio > best_short_code[1]):
            best_short_code = (col, hit_ratio)
    if best_short_code:
        return (
            best_short_code[0],
            "偵測到簡碼分類值（如城鄉1/國保2/農發3/國1等），非完整分類名稱，出圖時需搭配檔名對照完整分類",
        )

    # 策略 3：完全沒有明顯分類欄位，但檔名/上層資料夾名本身就是分區類別
    # （如 城1.shp、農1.shp、未來發展地區.shp、國土計畫範圍.shp）—
    # 這類檔案的「分類」100% 靠檔名/資料夾名判斷，非欄位。優先判斷，避免誤把
    # 無關的單一值欄位（如縣市名、河堤名）誤認成分區欄位。
    name_context = f"{shp_path.parent.name}/{shp_path.stem}"
    if any(kw.replace("地區", "").replace("第", "") in name_context for kw in
           ["國土保育", "農業發展", "城鄉發展", "海洋資源", "未來發展"]) or \
       re.match(r"^(國保|農發|城鄉|海洋|國|農|城|海)[0-9\-之]+$", shp_path.stem):
        return (
            None,
            "檔案無分類欄位，分類須 100% 依檔名/資料夾名判斷（此類圖層本身已用檔名分類，非欄位缺失）",
        )

    # 策略 4：此檔案本身可能是「單一分類」檔，欄位裡只有單一去重值，且該值本身
    # 看起來像分區簡碼（如「國1」「城」）才採用，避免誤抓縣市名/河堤名等無關欄位。
    single_value_short_code_re = re.compile(
        r"^(國保|農發|城鄉|海洋|國土保育|農業|城鄉發展|海洋資源|國|農|城|海|陸域|海域)"
        r"[0-9一二三四五\-之]*$"
    )
    for idx, col in enumerate(df.columns):
        values = col_values(idx)
        if values is None or len(values) != 1:
            continue
        v = values[0].strip()
        if v and single_value_short_code_re.match(v):
            return (
                col,
                f"檔案為單一分類（欄位 {col} 全部同值「{v}」），"
                "分類名稱需搭配檔名/資料夾名判斷（此類檔案本身已按分區類別拆檔）",
            )

    sample_cols = {}
    for idx, col in enumerate(df.columns):
        series = df.iloc[:, idx]
        try:
            sample_cols[col] = list(series.dropna().astype(str).unique()[:5])
        except Exception:  # noqa: BLE001
            sample_cols[col] = ["<unreadable>"]
    return None, f"抽樣未命中分區關鍵字/簡碼樣式，欄位樣本值：{sample_cols}"


def scan_cadastral() -> dict:
    """掃描地籍圖資/*.shp，回傳 {縣市key: shp路徑}（相對於 repo root，用 / 分隔）。"""
    result: dict[str, str] = {}
    for shp in sorted(CADASTRAL_DIR.glob("*.shp")):
        county = normalize_county_from_filename(shp.stem)
        if county is None:
            continue  # 段藉.shp 等非縣市檔案，另外處理
        rel_path = shp.relative_to(REPO_ROOT).as_posix()
        result[county] = rel_path
    return result


def scan_functional_zones() -> dict[str, list[dict]]:
    """
    遞迴掃描 國土功能分區/ 下所有 .shp（排除圖例.xml 等非 shp 檔），
    依縣市名分組，每個 shp 檔逐一讀 CRS/欄位/抽樣找分區欄位。
    回傳 {縣市key: [ {path, zone_field, crs, notes}, ... ]}
    """
    result: dict[str, list[dict]] = {}

    for shp in sorted(ZONES_DIR.rglob("*.shp")):
        rel_path = shp.relative_to(REPO_ROOT).as_posix()

        # 用資料夾路徑（含頂層資料夾名）判斷縣市
        search_text = str(shp.relative_to(ZONES_DIR))
        m = COUNTY_NAME_PATTERN.search(search_text)
        if not m:
            county = "未知（無法從路徑判斷縣市，請人工確認）"
        else:
            county = _COUNTY_ALIASES[m.group(1)]

        # CRS
        try:
            info = pyogrio.read_info(shp)
            crs = info.get("crs")
        except Exception as exc:  # noqa: BLE001
            crs = None
            info = None

        notes_parts = []

        if crs is None:
            notes_parts.append("缺 CRS（無 .prj 或讀取失敗），推測應為 EPSG:3826，待確認")

        # 分區欄位
        zone_field, field_notes = find_zone_field(shp)
        if zone_field is None:
            notes_parts.append(f"無法自動判斷分區欄位：{field_notes}")
        else:
            notes_parts.append(field_notes)

        # 版本推斷：先看檔名，再看直屬資料夾名，再看上一層資料夾名
        version = (
            guess_version_from_name(shp.stem)
            or guess_version_from_name(shp.parent.name)
            or guess_version_from_name(shp.parent.parent.name if shp.parent != ZONES_DIR else "")
        )
        if version:
            notes_parts.append(f"版本推斷：{version}")
        else:
            notes_parts.append("版本推斷：檔名/資料夾名未找到日期，待人工確認")

        entry = {
            "path": rel_path,
            "zone_field": zone_field,
            "crs": crs,
            "notes": "；".join(notes_parts),
        }
        result.setdefault(county, []).append(entry)

    return result


def build_registry() -> dict:
    cadastral_map = scan_cadastral()
    zones_map = scan_functional_zones()

    counties: dict[str, dict] = {}
    for county in ALL_COUNTIES:
        counties[county] = {
            "cadastral": cadastral_map.get(county),
            "functional_zones": zones_map.get(county, []),
            "notes": "",
        }

    # 未知縣市（路徑判斷失敗）另外掛在 counties 底下，方便人工複查，不計入 22 縣市清單
    unknown_key = "未知（無法從路徑判斷縣市，請人工確認）"
    if unknown_key in zones_map:
        counties[unknown_key] = {
            "cadastral": None,
            "functional_zones": zones_map[unknown_key],
            "notes": "路徑無法判斷縣市，人工複查後歸類",
        }

    missing_cadastral = [c for c in ALL_COUNTIES if not cadastral_map.get(c)]
    missing_zones = [c for c in ALL_COUNTIES if not zones_map.get(c)]

    registry = {
        "_meta": {
            "generated_by": "scripts/build_registry.py",
            "source_cadastral_dir": "地籍圖資/",
            "source_zones_dir": "國土功能分區/",
            "note": (
                "此檔為掃描結果，圖資更新後可重跑 scripts/build_registry.py 重新產出。"
                "台/臺正規化：地籍 shp 檔名用「台」，本檔 counties key 一律用「臺」；"
                "cadastral 欄位值為實際檔案路徑（不改檔名）。"
            ),
        },
        "counties": counties,
        "missing": {
            "cadastral": missing_cadastral,
            "functional_zones": missing_zones,
        },
        "legend_xml": "國土功能分區/圖例.xml",
        "section_index": "地籍圖資/段藉.shp",
    }
    return registry


def main() -> None:
    registry = build_registry()
    OUTPUT_YAML.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_YAML.open("w", encoding="utf-8") as f:
        yaml.dump(
            registry,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=100,
        )
    print(f"已寫入 {OUTPUT_YAML}")

    print("\n--- 摘要 ---")
    print(f"地籍缺：{registry['missing']['cadastral']}")
    print(f"功能分區缺：{registry['missing']['functional_zones']}")

    print("\n--- 各縣市功能分區欄位 ---")
    for county, data in registry["counties"].items():
        for fz in data.get("functional_zones", []):
            print(f"{county} | {fz['path']} | zone_field={fz['zone_field']} | crs={fz['crs']}")


if __name__ == "__main__":
    main()
