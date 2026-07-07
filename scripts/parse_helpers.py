# -*- coding: utf-8 -*-
"""
parse_helpers.py — 純函式模組，供階段1解析與階段2比對共用。

涵蓋：
- 縣市名正規化（台/臺）
- 地號字串解析（含地號→宗地母/宗地子）
- 地號清單展開（頓號/逗號分隔、"(部分)" 註記）
- 段藉.shp 讀取與快取
- 段小段碼查詢（縣市/鄉鎮/段/小段 → 段小段碼）
- 宗地母/宗地子 反向組成地號顯示字串

不依賴 QGIS；只用 pandas / pyogrio。可在系統 Python 3.14 下直接執行單元測試。
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

# ---------------------------------------------------------------------------
# 1. 縣市名正規化
# ---------------------------------------------------------------------------

# 地籍圖資 shp 檔名慣用「台」，本工具統一正規化為「臺」開頭標準名。
_TAI_COUNTIES = [
    "臺北市",
    "臺中市",
    "臺南市",
    "臺東縣",
]

# 「台」→「臺」的簡單字元替換即可涵蓋所有已知縣市名
# （台北市/台中市/台南市/台東縣 → 臺北市/臺中市/臺南市/臺東縣）。
# 其餘縣市（新北市、桃園市、高雄市…）本身不含「台/臺」字，不受影響。


def normalize_county(name: Optional[str]) -> Optional[str]:
    """將縣市名中的「台」正規化為「臺」，並去除頭尾/中間空白。

    範例：
        '台北市'  -> '臺北市'
        ' 臺中市 ' -> '臺中市'
        '新北市'  -> '新北市'（不變）
        None      -> None
    """
    if name is None:
        return None
    if not isinstance(name, str):
        return name

    s = name.strip()
    # 去除字串中任何空白（含全形空白）
    s = re.sub(r"[\s　]+", "", s)
    if s == "":
        return None

    s = s.replace("台", "臺")
    return s


# ---------------------------------------------------------------------------
# 2. 地號解析：字串 -> (宗地母, 宗地子)
# ---------------------------------------------------------------------------

# 全形數字 -> 半形數字
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
# 全形連字號/破折號類符號 -> 半形 '-'
_HYPHEN_CHARS = "－—–ー﹣‐‑"


class LandNoParseError(ValueError):
    """地號字串無法解析時的例外，攜帶原因字串。"""

    def __init__(self, reason: str, raw: str):
        super().__init__(reason)
        self.reason = reason
        self.raw = raw


def _clean_landno_str(s: str) -> str:
    """地號字串前處理：去空白、去「地號」字尾、全形數字/連字號轉半形。"""
    s = unicodedata.normalize("NFKC", s)  # 順便處理大部分全形字元
    s = s.strip()
    s = re.sub(r"[\s　]+", "", s)
    # 去掉尾端「地號」「號」等字樣
    s = re.sub(r"地號$", "", s)
    s = s.translate(_FULLWIDTH_DIGITS)
    for ch in _HYPHEN_CHARS:
        s = s.replace(ch, "-")
    return s


def parse_landno(s: Optional[str]) -> Optional[Tuple[str, str]]:
    """將地號字串轉為 (宗地母, 宗地子) 4 位數字字串 tuple。

    範例：
        '331-1'  -> ('0331', '0001')
        '331'    -> ('0331', '0000')
        '1053'   -> ('1053', '0000')
        '331之1' -> ('0331', '0001')
        '331-1地號' -> ('0331', '0001')

    無法解析時回傳 None。若要取得原因，改用 `parse_landno_verbose`。
    """
    result = parse_landno_verbose(s)
    if result is None:
        return None
    if isinstance(result, LandNoParseError):
        return None
    return result


def parse_landno_verbose(s):
    """同 parse_landno，但解析失敗時回傳 LandNoParseError（可讀 .reason / .raw）
    而非單純 None，方便呼叫端記錄失敗原因。解析成功回傳 (母, 子) tuple。
    """
    if s is None:
        return LandNoParseError("空值", "")
    if isinstance(s, float) and pd.isna(s):
        return LandNoParseError("空值", "")
    if not isinstance(s, str):
        s = str(s)

    raw = s
    cleaned = _clean_landno_str(s)
    if cleaned == "":
        return LandNoParseError("空字串", raw)

    # 支援「331之1」「331-1」兩種子號分隔寫法
    cleaned = cleaned.replace("之", "-")

    m = re.fullmatch(r"(\d{1,4})(?:-(\d{1,4}))?", cleaned)
    if not m:
        return LandNoParseError(f"格式不符：'{cleaned}'", raw)

    mother_raw, child_raw = m.group(1), m.group(2)

    if len(mother_raw) > 4 or (child_raw and len(child_raw) > 4):
        return LandNoParseError(f"數字位數超過4位：'{cleaned}'", raw)

    mother = mother_raw.zfill(4)
    child = (child_raw or "0").zfill(4) if child_raw is not None else "0000"

    return (mother, child)


# ---------------------------------------------------------------------------
# 3. 地號清單展開
# ---------------------------------------------------------------------------

# 頓號、全形逗號、半形逗號、分號 皆視為清單分隔符
_LIST_SEP_PATTERN = re.compile(r"[、,，;；]+")
_PARTIAL_PATTERN = re.compile(r"[（(]\s*部分\s*[）)]")


def expand_landno_list(s: Optional[str]) -> List[Tuple[str, bool]]:
    """展開以頓號/逗號等分隔的地號清單字串。

    回傳 [(地號原文, 部分註記bool), ...]，其中「地號原文」為去除
    "(部分)" 註記與前後空白後的個別地號片段（尚未做 parse_landno 轉換）。

    範例：
        '3、3-2、4'     -> [('3', False), ('3-2', False), ('4', False)]
        '343(部分)'      -> [('343', True)]
        '331,331-1'      -> [('331', False), ('331-1', False)]
        ''  / None        -> []
    """
    if s is None:
        return []
    if isinstance(s, float) and pd.isna(s):
        return []
    if not isinstance(s, str):
        s = str(s)

    s = unicodedata.normalize("NFKC", s)
    s = s.strip()
    if s == "":
        return []

    parts = _LIST_SEP_PATTERN.split(s)
    result: List[Tuple[str, bool]] = []
    for part in parts:
        part = part.strip()
        if part == "":
            continue
        is_partial = bool(_PARTIAL_PATTERN.search(part))
        part_clean = _PARTIAL_PATTERN.sub("", part).strip()
        if part_clean == "":
            continue
        result.append((part_clean, is_partial))
    return result


# ---------------------------------------------------------------------------
# 4. 段藉.shp 讀取與快取
# ---------------------------------------------------------------------------

_DEFAULT_SECTION_INDEX_PATH = "地籍圖資/段藉.shp"


@lru_cache(maxsize=8)
def _load_section_index_cached(path_str: str) -> pd.DataFrame:
    import warnings

    import pyogrio

    with warnings.catch_warnings():
        # 段藉.shp 部分中文字元在 Big5->UTF-8 轉換時會有警告，不影響本工具使用的欄位
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        df = pyogrio.read_dataframe(path_str)

    # 只保留本工具需要且可信的欄位（避免誤用重複後綴欄位 縣市_1 / 段_12 等）
    keep_cols = [c for c in ["縣市", "鄉鎮", "段", "小段", "段小段碼"] if c in df.columns]
    df = df[keep_cols].copy()

    df["縣市_norm"] = df["縣市"].map(normalize_county)
    df["鄉鎮_norm"] = df["鄉鎮"].map(lambda x: x.strip() if isinstance(x, str) else x)
    df["段_norm"] = df["段"].map(lambda x: x.strip() if isinstance(x, str) else x)
    df["小段_norm"] = df["小段"].map(lambda x: x.strip() if isinstance(x, str) else x)

    return df


def load_section_index(path: str = _DEFAULT_SECTION_INDEX_PATH) -> pd.DataFrame:
    """讀取全台段/小段索引（段藉.shp），回傳 DataFrame（依路徑快取）。

    欄位：縣市、鄉鎮、段、小段、段小段碼，以及正規化輔助欄位
    縣市_norm、鄉鎮_norm、段_norm、小段_norm。
    """
    resolved = str(Path(path))
    return _load_section_index_cached(resolved)


# ---------------------------------------------------------------------------
# 5. 段小段碼查詢
# ---------------------------------------------------------------------------


def resolve_section(
    df: pd.DataFrame,
    county: str,
    town: str,
    section: str,
    subsection: Optional[str] = None,
) -> List[str]:
    """依 縣市/鄉鎮/段/小段 查詢段小段碼清單。

    - 查詢前會對 county 做 normalize_county（因索引可能用「台」，輸入可能用「臺」或反之）。
    - subsection 為 None 時，只比對到「段」層級（可能對應多筆小段的段小段碼）。
    - 回傳清單長度：
        0 -> 查無
        1 -> 唯一解
        >1 -> 有多筆，需人工確認（例如段名重複於不同小段、或大小段資料本身有多筆記錄）
    """
    county_n = normalize_county(county)
    town_n = town.strip() if isinstance(town, str) else town
    section_n = section.strip() if isinstance(section, str) else section

    mask = (
        (df["縣市_norm"] == county_n)
        & (df["鄉鎮_norm"] == town_n)
        & (df["段_norm"] == section_n)
    )

    if subsection is not None:
        subsection_n = subsection.strip() if isinstance(subsection, str) else subsection
        mask = mask & (df["小段_norm"] == subsection_n)

    matched = df.loc[mask, "段小段碼"]
    codes = [c for c in matched.tolist() if c is not None]
    # 去重但保留穩定順序
    seen = set()
    unique_codes = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            unique_codes.append(c)
    return unique_codes


# ---------------------------------------------------------------------------
# 6. 宗地母/宗地子 -> 顯示字串
# ---------------------------------------------------------------------------


def landno_display(mother: str, child: str) -> str:
    """將 4 位數字字串的 宗地母/宗地子 轉回顯示用地號字串。

    範例：
        ('0331', '0001') -> '331-1'
        ('0331', '0000') -> '331'
        ('1053', '0000') -> '1053'
    """
    mother_disp = str(int(mother))
    child_str = str(child)
    if child_str in ("0000", "0", ""):
        return mother_disp
    child_disp = str(int(child_str))
    return f"{mother_disp}-{child_disp}"
