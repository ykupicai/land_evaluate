# -*- coding: utf-8 -*-
"""權屬（公私別）分色的**唯一**實作 —— make_maps 與 make_extra_maps 共用。

2026-08-26 使用者定案：權屬圖納入基本圖組（`make_maps` 六主題之一），不再只是
`make_extra_maps --theme 權屬` 的加值品項。為此把原本巢狀在 make_extra_maps 主流程
裡的 `_style_by_owner` 抽到這裡——**兩支出圖腳本一律 import 同一份，不可各自複製**。

★ 分色的 key 是 `persite_stats.classify_ownership()` **正規化後**的 '公'／'私'，
  不是 shp 裡的原始字串。原始值兩條取得路徑用詞不同：
    ⓪a `cadastre_fetch`（現行主路徑）→ 「國有」「直轄市有」「縣市有」「私(推論)」
    ② TCDGIS 匯出                    → 「公」「私」
  直接拿原始值當 key，會讓全部落到預設灰色、公私分不出來（2026-08-07 安南案實錯）。
  判定本身集中在 `persite_stats.classify_ownership`，那是全產線唯一判準，此處只負責上色。

配色與線寬的既定值（2026-08-07 使用者定案，改動前請先確認）：
  ‧ 公有偏暖（橙）／私有偏冷（藍），圖例一眼可分；無法判定為灰。
  ‧ 基地 outline 0.4／標註 11pt；周邊 outline 0.3／標註 9pt——靠粗細與字級分主從。
  ‧ 周邊 alpha 只比基地低一級（`max(0.30, alpha-0.10)`），**不可再疊 setOpacity()**：
    符號已帶 alpha，再乘圖層透明度會變 0.55×0.55≈0.30，周邊在正射底圖上幾乎看不見。
  ‧ 周邊也要畫外框並標地號——權屬圖要能逐筆對照哪一號是公有／私有，只有色塊不夠用。
"""
from __future__ import annotations

OWNER_STYLE = {
    "公": ((255, 140, 0), "公有"),
    "私": ((80, 140, 255), "私有"),
}
OWNER_UNKNOWN = ((165, 165, 165), "無法判定（空白／未登記）")

PUB_FIELD = "公私別"
TMP_FIELD = "_權屬"          # 暫存欄：渲染與圖例都吃它（原始欄留著供查）


def style_by_owner(lyr, name, label_fn=None, label_size=0,
                   outline="60,60,60,255", outline_w="0.4", alpha=0.55) -> list:
    """就地把面圖層依權屬分色；回傳出現過的類別（保序：公→私→未判定）。

    `label_fn(lyr, field, size)` 由呼叫端注入（兩支腳本各有自己的標註設定），
    給 None 或 label_size=0 即不標註。

    圖層缺「公私別」欄時丟 `KeyError`——呼叫端自行決定要硬停還是記成該張圖失敗，
    本模組不替呼叫端做流程決策。
    """
    from qgis.core import (QgsCategorizedSymbolRenderer, QgsField,  # noqa: PLC0415
                           QgsFillSymbol, QgsRendererCategory)
    from qgis.PyQt.QtCore import QVariant  # noqa: PLC0415

    from persite_stats import classify_ownership  # noqa: PLC0415 — 全產線唯一判準

    i_pub = lyr.fields().indexFromName(PUB_FIELD)
    if i_pub < 0:
        raise KeyError(f"{name} 無「{PUB_FIELD}」欄，無法做權屬分色")

    lyr.startEditing()
    if lyr.fields().indexFromName(TMP_FIELD) < 0:
        lyr.addAttribute(QgsField(TMP_FIELD, QVariant.String))
        lyr.updateFields()
    i_new = lyr.fields().indexFromName(TMP_FIELD)
    seen = []
    for f in lyr.getFeatures():
        k = classify_ownership(f[i_pub]) or "未判定"
        lyr.changeAttributeValue(f.id(), i_new, k)
        if k not in seen:
            seen.append(k)
    lyr.commitChanges()

    order = [k for k in ("公", "私", "未判定") if k in seen]
    cats = []
    for k in order:
        rgb, lab = (OWNER_STYLE.get(k) if k != "未判定" else OWNER_UNKNOWN)
        cats.append(QgsRendererCategory(k, QgsFillSymbol.createSimple({
            "color": "%d,%d,%d,%d" % (rgb + (int(alpha * 255),)),
            "outline_color": outline, "outline_width": outline_w}), lab))
    lyr.setRenderer(QgsCategorizedSymbolRenderer(TMP_FIELD, cats))
    if label_size and label_fn:
        label_fn(lyr, "地號", label_size)
    return order


def neighbour_alpha(base_alpha: float) -> float:
    """周邊地籍的 alpha：只比基地低一級。見模組 docstring 的「不可再疊 setOpacity」。"""
    return max(0.30, base_alpha - 0.10)
