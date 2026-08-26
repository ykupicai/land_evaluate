# -*- coding: utf-8 -*-
"""出圖成果自我檢查——防「回報成功、圖卻是空的」。

這條產線最貴的失敗不是跑掛掉，是**靜默出錯**：`exportToImage` 只要把檔案寫出來就記
「成功」，至於圖裡該有的東西是不是一筆都沒有，沒有人看。已經實際發生過三次
（見 CLAUDE.md「踩過的坑」）：

  ‧ 2026-08-04 龍崎案：`county` 欄空白 → 周邊地籍 0 筆、功能分區 0 筆，主題3 出成
    只有紅框的空白圖，05 清單卻寫「成功」。錯了整整一輪才被發現。
  ‧ 2026-08-04 `--basemap-dir`：路徑被當成案名，12 張圖寫進岔路資料夾，全程回報「成功」。
  ‧ 桌面 QGIS 開著鎖住 gpkg／底圖 tif：六張都回報成功，主題色或底圖整片空白。

三次的共同點都不是程式碼寫錯，是**出圖後沒有任何人檢查內容對不對**。本模組補上這一步：

  A. `check_theme_layers`  出圖「前」：主題賴以成立的圖層筆數為 0 → 直接記失敗，
     連圖都不用出。原因字串會指名是哪個圖層空的，好回頭查上游（通常是縣市欄空白
     或 sitemap 沒對上）。這是**決定性**檢查，不會誤判。
  B. `check_blank_image`   出圖「後」：成品 PNG 幾乎單一顏色 → 記失敗。這是 A 抓不到
     的那類（底圖被鎖、tif 壞成無 geotransform）的最後防線。門檻刻意訂得極保守，
     **寧可漏抓，不可把正常的圖誤判成失敗擋住產線**。

兩者都回「原因字串（＝有問題）或 None（＝通過）」。純函式、不依賴 QGIS，可單測。
"""
from __future__ import annotations

# 各主題賴以成立的圖層：這些圖層空了，圖就沒有意義（只剩紅框與圖例外框）。
# 只列「本模組負責把關」的三個主題——正射套疊／國土利用調查／臨路判定在 make_maps
# 主題迴圈裡本來就各有前置判斷（case_wmts_ok／luimap_rl／sites_lyr），不重複攔。
THEME_CRITICAL_LAYERS: dict[str, tuple[str, ...]] = {
    "使用分區": ("宗地-使用分區",),
    "使用地編定": ("宗地-使用地編定",),
    "國土功能分區": ("國土功能分區",),
}

# 不分主題都必須有東西的圖層：基地紅框是每張圖的主角，它空了代表這個案場整個沒抓到。
BASE_LAYER = "基地地籍"


def check_theme_layers(theme: str, counts: dict[str, int]) -> str | None:
    """出圖前檢查。`counts`＝{圖層名: 筆數}；回 None 表示通過，回字串表示失敗原因。

    未知主題、或 `counts` 裡沒有該圖層的資訊時一律回 None（通過）——本檢查只在
    「明確知道是 0 筆」時才擋，資訊不足不擋，避免因取不到筆數而誤殺。
    """
    if counts.get(BASE_LAYER) == 0:
        return f"{BASE_LAYER} 0 筆（此案場未抓到基地，請查 sitemap 與地號比對）"
    for name in THEME_CRITICAL_LAYERS.get(theme, ()):
        if counts.get(name) == 0:
            return (f"{name} 0 筆（圖會是空的，未出圖）"
                    f"——常見原因：縣市欄空白或無該縣市地籍圖資")
    return None


def check_blank_image(path, dominant_max: float = 0.995,
                      sample: int = 200) -> str | None:
    """出圖後檢查成品 PNG 是不是「整片空白」。回 None 表示通過，回字串表示失敗原因。

    做法：縮到 `sample`×`sample` 後數最常見顏色的佔比，超過 `dominant_max` 即判定空白。

    門檻為何訂在 99.5%：正常的成果圖除了主題色，還一定有圖例方框、比例尺、指北針、
    位置／涵蓋地號註記與基地紅框，最常見顏色（白）的佔比到不了 99.5%；反之底圖整片
    沒鋪上時會非常接近純白。**這個門檻是刻意訂得極保守的**——它的任務是抓「完全空
    白」，不是抓「內容稀疏」。寧可漏抓幾張，也不能把正常的圖誤判成失敗把產線擋掉。

    PIL 不可用或檔案讀不開時一律回 None（跳過檢查）——檢查本身絕不可成為新的失敗源。
    """
    try:
        from PIL import Image  # noqa: PLC0415 — 延遲載入，PIL 缺席時本檢查靜默跳過
    except Exception:  # noqa: BLE001
        return None
    try:
        with Image.open(path) as im:
            im = im.convert("RGB").resize((sample, sample))
            colors = im.getcolors(sample * sample)   # [(count, rgb), ...]
    except Exception:  # noqa: BLE001 — 讀不開不代表圖是壞的，交給既有的檔案存在性檢查
        return None
    if not colors:
        return None
    top_count, _ = max(colors, key=lambda kv: kv[0])
    frac = top_count / float(sample * sample)
    if frac > dominant_max:
        return f"成品圖幾乎全空白（單一顏色佔 {frac:.1%}）——底圖或主題圖層可能沒畫上"
    return None
