# -*- coding: utf-8 -*-
"""
zone_utils.py — 國土功能分區名稱正規化（純函式，可單測）。

各縣市功能分區 shp 的分區值寫法高度異質（2026-07-07 實測 16 縣市）：
- 完整名：城鄉發展地區第二類之三（桃園/新竹縣/苗栗/臺中/南投/嘉義縣/花蓮/新北部分）
- 完整名異體：城鄉發展地區第二之一類（新北）、海洋發展地區第一類之二（花蓮，
  「發展」應為「資源」）、…示意圖尾綴（宜蘭）、國土保育第一類/城鄉發展第二-一類（澎湖，省「地區」）
- 簡碼：國1/城2-3（新北）、國保1/城鄉2-1/農業1（雲林）、國1/城21/海11（臺南）、
  國保1/城鄉2-1/農發1（高雄/屏東）
- 中文數字碼：國保一/城一/城二之一/城鄉三/農發一/海一之一（臺東）、農四（新北）
- 檔名即分類：國保1.shp/城21.shp/農4.shp（彰化）、國土保育地區第三類.shp（新北）

normalize_zone() 把上述全部映射為標準全名「XX地區第N類[之M]」；
無法辨識（如 其他區位、未來發展地區、陸域、行政區界）回傳 None，
由呼叫端決定沿用原字串或排除。
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Optional

# 類別前綴 → 標準類別名（依長度優先比對；「海洋發展」為花蓮異體寫法）
_CATEGORY_PREFIXES = [
    ("國土保育", "國土保育地區"),
    ("農業發展", "農業發展地區"),
    ("城鄉發展", "城鄉發展地區"),
    ("海洋資源", "海洋資源地區"),
    ("海洋發展", "海洋資源地區"),
    ("國保", "國土保育地區"),
    ("農發", "農業發展地區"),
    ("農業", "農業發展地區"),
    ("城鄉", "城鄉發展地區"),
    ("海洋", "海洋資源地區"),
    ("國", "國土保育地區"),
    ("農", "農業發展地區"),
    ("城", "城鄉發展地區"),
    ("海", "海洋資源地區"),
]

_ZH_DIGIT = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
_DIGIT_ZH = {v: k for k, v in _ZH_DIGIT.items()}


def _digits_of(s: str) -> List[int]:
    """依序取出字串中的數字（阿拉伯或中文一~六），逐「字」一位。"""
    out: List[int] = []
    for ch in s:
        if ch.isdigit():
            out.append(int(ch))
        elif ch in _ZH_DIGIT:
            out.append(_ZH_DIGIT[ch])
    return out


def normalize_zone(raw) -> Optional[str]:
    """將功能分區值/檔名正規化為「XX地區第N類[之M]」標準全名。

    範例：
        '城鄉發展地區第二類之三' -> '城鄉發展地區第二類之三'
        '城鄉發展地區第二之一類' -> '城鄉發展地區第二類之一'（新北異體）
        '城鄉發展第二-一類'      -> '城鄉發展地區第二類之一'（澎湖）
        '城2-3' / '城21' / '城二之三' / '城鄉2-3' -> 依樣式解為第二類之三
        '國保1' / '國1' / '國保一' -> '國土保育地區第一類'
        '農業發展地區第2類示意圖' -> '農業發展地區第二類'（宜蘭）
        '海洋發展地區第一類之二' -> '海洋資源地區第一類之二'（花蓮異體）
        '其他區位' / '未來發展地區' / '陸域' -> None（非四大分區）
    """
    if raw is None or not isinstance(raw, str):
        return None
    s = unicodedata.normalize("NFKC", raw).strip()
    s = re.sub(r"[\s　]+", "", s)
    s = re.sub(r"示意圖$", "", s)
    if not s:
        return None

    # 類別前綴
    category = None
    rest = None
    for prefix, cat in _CATEGORY_PREFIXES:
        if s.startswith(prefix):
            category = cat
            rest = s[len(prefix):]
            break
    if category is None:
        return None

    # 去掉「地區」「第」「類」「之」「-」等結構字後取數字序列
    rest = rest.replace("地區", "")
    digits = _digits_of(rest)
    if len(digits) == 0 or len(digits) > 2:
        # 無級別數字（如 未來發展、國土計畫範圍）或數字過多（如檔名帶日期）→ 非分區值
        return None
    if digits[0] > 6 or (len(digits) == 2 and digits[1] > 3):
        return None

    name = f"{category}第{_DIGIT_ZH[digits[0]]}類"
    if len(digits) == 2:
        name += f"之{_DIGIT_ZH[digits[1]]}"
    return name


# 從輸入文件的國土功能分區欄位文字中，抽出全部標準分區名（供交叉檢核）
_TEXT_ZONE_RE = re.compile(
    r"(國土保育|農業發展|城鄉發展|海洋資源|海洋發展)"
    r"(?:地區)?第?([一二三四五六1-6])[類]?(?:[之\-]([一二三1-3]))?[類]?"
)


def extract_zones_from_text(text) -> List[str]:
    """從自由文字（原表國土功能分區欄）抽出標準分區名清單（去重、保序）。

    範例：
        '農業發展地區第二類(3、3-2地號)、城鄉發展地區第一類(4地號)'
        -> ['農業發展地區第二類', '城鄉發展地區第一類']
        '部分國土保育地區第2類,部分農業發展地區第2類'
        -> ['國土保育地區第二類', '農業發展地區第二類']
    """
    if text is None or not isinstance(text, str):
        return []
    s = unicodedata.normalize("NFKC", text)
    out: List[str] = []
    for m in _TEXT_ZONE_RE.finditer(s):
        token = m.group(1) + "第" + m.group(2) + (f"之{m.group(3)}" if m.group(3) else "")
        std = normalize_zone(token)
        if std and std not in out:
            out.append(std)
    return out
