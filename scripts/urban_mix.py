# -*- coding: utf-8 -*-
"""基地的「都市／非都市」組成判定 —— 決定要出哪幾張分區圖。

使用者定案（2026-08-26）：
  ① 全部都是都市計畫區 → **只出使用分區圖**（使用地編定不出）
  ② 涉及都市計畫區及非都市土地 → 兩張都出
  ③ 全部都是非都市土地 → 兩張都出

理由：都市土地沒有「使用地類別」（使類欄本來就空），此時使用地編定圖與使用分區圖
會疊同一份都市計畫圖層、產出**兩張一模一樣的圖**（2026-08-26 員林案實見：兩個檔
同為 2,875 KB、內容相同）。多出來的那張沒有資訊、只會讓人以為漏看了什麼。

★ 判定不可只看「使分／使類空白」（CLAUDE.md 明載、2026-08-06 使用者親自更正的原則）：
  母號 <9000 且使分／使類空白者，**可能是都市土地，也可能是未登錄土地**，getLandInfo
  對兩者回傳相同、無法區分。所以本模組要求「空白」**且**「幾何落在都市計畫圖層內」
  才認定為都市計畫區；未登錄地則單獨歸類、不參與投票。

逐筆分類：
  非都市土地 ＝ 使分或使類有值（有值即為非都市土地之編定，這是決定性證據）
  都市計畫區 ＝ 兩欄皆空 **且** 落在都市計畫圖層內
  未登錄地   ＝ 母號 ≥9000 或帶未登記旗標（不參與投票，見下）
  無法判定   ＝ 兩欄皆空、又不在都市計畫圖層內、也不是未登錄地

未登錄地為何不參與投票：它既非都市土地也非已編定之非都市土地，硬歸任一邊都會誤導。
基地若「都市＋未登錄地」混合，仍視為全都市（未登錄地本來就沒有使用地編定可畫）。

⚠ 安全預設：只要有任何一筆「無法判定」，一律回 UNKNOWN → 呼叫端兩張都出。
   寧可多出一張重複的圖，也不可因判定不足而**漏掉**該出的圖。
"""
from __future__ import annotations

ALL_URBAN = "all_urban"          # ① 全都市計畫區 → 只出使用分區
MIXED = "mixed"                  # ② 都市＋非都市 → 兩張都出
ALL_NONURBAN = "all_nonurban"    # ③ 全非都市土地 → 兩張都出
UNKNOWN = "unknown"              # 判定不足 → 兩張都出（安全側）

# 使分/使類 欄可能帶的「空值代名詞」：make_maps 對空白會填 URBAN_LABEL，
# 這些一律視為「沒有編定值」，不可當成有值。
_BLANKISH = ("", "none", "null", "nan", "都市土地（使用分區另查）", "都市土地")


def _has_value(v) -> bool:
    """欄位是否帶「真正的編定值」。"""
    if v is None:
        return False
    s = str(v).strip()
    return bool(s) and s.lower() not in [b.lower() for b in _BLANKISH]


def classify_parcel(zone, ltype, in_urban_plan: bool,
                    unregistered: bool = False) -> str:
    """單筆分類，回 'nonurban' / 'urban' / 'unregistered' / 'unknown'。"""
    if _has_value(zone) or _has_value(ltype):
        return "nonurban"          # 有編定值＝非都市土地（決定性）
    if unregistered:
        return "unregistered"      # 未登錄地：不參與投票
    if in_urban_plan:
        return "urban"             # 空白＋落在都市計畫圖層內＝都市計畫區
    return "unknown"               # 空白但不在都市計畫圖層內：可能是未登錄地，不臆測


def classify(parcels) -> str:
    """基地整體分類。`parcels`＝[(zone, ltype, in_urban_plan, unregistered), ...]。

    回 ALL_URBAN / MIXED / ALL_NONURBAN / UNKNOWN。空清單回 UNKNOWN。
    """
    kinds = [classify_parcel(*p) if len(p) == 4 else classify_parcel(*p, False)
             for p in parcels]
    if not kinds or "unknown" in kinds:
        return UNKNOWN                       # 安全側：判定不足就兩張都出
    urban = "urban" in kinds
    nonurban = "nonurban" in kinds
    if urban and nonurban:
        return MIXED
    if urban:
        return ALL_URBAN                     # 都市（可含未登錄地）
    if nonurban:
        return ALL_NONURBAN
    return UNKNOWN                           # 全是未登錄地：無從判斷，兩張都出


def themes_to_skip(kind: str) -> set:
    """依分類回「該略過的主題」。目前只有全都市時略過使用地編定。"""
    return {"使用地編定"} if kind == ALL_URBAN else set()


def skip_reason(kind: str) -> str:
    return ("全區屬都市計畫區，無使用地類別編定（使類為空），"
            "使用地編定圖與使用分區圖內容相同故不重複出圖")
