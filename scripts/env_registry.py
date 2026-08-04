# -*- coding: utf-8 -*-
"""環境敏感圖層登錄表與解析（純資料＋純函式，**不 import QGIS**，可單元測試）。

自 make_env_map.py 抽出（2026-08-04）：出圖端的 QGIS 相依留在 make_env_map，
「平台圖層名 → 本機 shp／案件層覆蓋檔」的解析邏輯放這裡，
讓 NR23 一課（自然保留區必須解析到 1152 新版）能寫成測試斷言鎖住。

REGISTRY 欄位：
  match = 平台「環境敏感地區套疊分析」結果表的圖層名稱（regex）
  shp   = 相對 環境敏感相關/ 的路徑清單，**依序取第一個存在者**（新版在前、舊版留 fallback）；
          {county} 代入縣市名（臺/台 兩種都試）
  field = 分類欄（None＝不分類、單色）；crs＝該 shp 無 .prj 時的假定 CRS
  rgb   = 本層語意色（保護類偏綠／災害類偏橙紅）；未指定者由出圖端取 GENERIC_PALETTE 色環
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_ROOT = REPO_ROOT / "環境敏感相關"

FLOOD_RGB = {
    "0-0.3": (255, 255, 190), "0.3-0.5": (190, 232, 255), "0.5-1": (115, 178, 255),
    "1-2": (0, 112, 255), "2-3": (0, 38, 194), ">3": (10, 0, 110),
}
FLOOD_LABELS = {
    "0-0.3": "0～0.3 公尺", "0.3-0.5": "0.3～0.5 公尺", "0.5-1": "0.5～1 公尺",
    "1-2": "1～2 公尺", "2-3": "2～3 公尺", ">3": "3 公尺以上",
}

REGISTRY = [
    # 淹水潛勢（各降雨組體，臺南市檔名為 24h500 等；嘉義縣為 q_24hr500）
    {"match": r"淹水潛勢圖?_?定量降雨(\d+)小時(\d+)mm",
     "shp": ["第二級/淹水潛勢/{county}/SHP/{h}h{mm}.shp",
             "第二級/淹水潛勢/{county}/q_{h:0>2}hr{mm}.shp"],
     "field": "type", "colors": FLOOD_RGB, "labels": FLOOD_LABELS,
     "crs": 3826, "title": "淹水潛勢 {h}hr{mm}mm"},
    {"match": r"公路兩側禁建限建", "shp": ["第二級/2-30 公路兩側禁建限建地區/高速公路兩側禁建限建地區.shp"],
     "field": None, "crs": 3826, "title": "公路兩側禁建限建地區"},
    {"match": r"地質敏感區.*活動斷層|活動斷層",
     "shp": ["第二級/2-01 地質敏感區(活動斷層、山崩與地滑、土石流)(1051221update)/全國活動斷層帶.shp"],
     "field": None, "crs": 3826, "title": "地質敏感區（活動斷層）"},
    {"match": r"山崩與地滑|山崩地滑",
     "shp": ["第二級/2-01 地質敏感區(活動斷層、山崩與地滑、土石流)(1051221update)"
             "/地質敏感區山崩與地滑.shp"],
     "field": None, "crs": 3826, "rgb": (255, 127, 14),
     "title": "地質敏感區（山崩與地滑）"},
    # 自然保留區：**一律用 1152（115年第2版，TGOS 下載）**，106 年版只留作 fallback。
    # 2026-08-04 龍崎案實踩：106 年版只有 23 筆、**不含 110.07.30 公告的 NR23 龍崎牛埔
    # 惡地自然保留區**，逐筆距離算出「最近 13,089 公尺、未涉及」，而 TCDGIS 平台正確
    # 回報套疊 8.88 ㎡——**是「有／沒有」的差異，不是面積門檻或邊界誤差**。
    # 換上 1152 後本機重算 8.88 ㎡，與平台一字不差。舊版另漏 NR22 北投石、
    # 且 NR10 台灣一葉蘭為 114 年前的舊範圍。★tests/test_env_registry.py 鎖住此順序。
    {"match": r"自然保留區",
     "shp": ["第一級/1-07 自然保留區 (1152_115年第2版)/自然保留區1152.shp",
             "第一級/1-07 自然保留區 (1060523update)/自然保留區1061.shp"],
     "field": None, "crs": 3826, "rgb": (44, 160, 44),
     "title": "自然保留區"},
    {"match": r"嚴重地層下陷", "shp": ["第二級/2-03 嚴重地層下陷地區(941215update)/941215{county}all_1.shp"],
     "field": None, "crs": 3826, "title": "嚴重地層下陷地區"},
    {"match": r"山坡地", "shp": ["第二級/2-06 山坡地 (1060531update)/BOUNDARY.shp"],
     "field": None, "crs": 3826, "rgb": (148, 103, 189), "title": "山坡地"},
    {"match": r"自來水水質水量保護區",
     "shp": ["第二級/2-20 自來水水質水量保護區(1060629update)/WaterQualityAndQuantityProtectionArea_Twd97_V3.0.shp"],
     "field": None, "crs": 3826, "title": "自來水水質水量保護區"},
    {"match": r"中央管河川區域", "shp": ["第一級/1-03 中央管河川區域 (1060629update)/河川區域線.shp"],
     "field": None, "crs": 3826, "title": "中央管河川區域"},
    {"match": r"中央管區域排水", "shp": ["第一級/1-05 中央管區域排水設施範圍 (1060629update)/排水設施範圍線.shp"],
     "field": None, "crs": 3826, "title": "中央管區域排水設施範圍"},
    {"match": r"海岸管制區", "shp": ["其他/海岸管制區/海岸管制區.shp"],
     "field": None, "crs": 3826, "title": "海岸管制區"},
    # 地下水管制區：平台清單只有「第一級」，第二級須靠本機圖資（2026-07-29 安南案實需）
    # gwconare.shp=第一級(1筆)、gwconare2.shp=gwconare2_all=第二級(11筆，全台)
    {"match": r"地下水管制區第一級|地下水第一級管制區",
     "shp": ["其他/地下水管制區/gwconare.shp"],
     "field": None, "crs": 3826, "title": "地下水管制區第一級", "encoding": "big5"},
    {"match": r"地下水管制區第二級|地下水第二級管制區",
     "shp": ["其他/地下水管制區/gwconare2.shp"],
     "field": None, "crs": 3826, "title": "地下水管制區第二級", "encoding": "big5"},
]
GENERIC_RGB = (255, 80, 80)
# 多個敏感圖層同時套疊時，**每層必須是可區辨的顏色**——否則圖例好幾格同一個紅、
# 看不出哪一塊屬哪一項，等於白畫（2026-08-04 龍崎案實錯：自然保留區與山崩地滑同色）。
# 規則層各自在 REGISTRY 用 "rgb" 指定語意色（保護類偏綠／災害類偏橙紅），
# 未指定者依出現順序取下列色環。色相間距 ≥60°，40% 不透明度疊在正射上仍分得出。
GENERIC_PALETTE = [
    (214, 39, 40),    # 紅
    (255, 127, 14),   # 橙
    (148, 103, 189),  # 紫
    (23, 190, 207),   # 青
    (188, 189, 34),   # 黃綠
    (227, 119, 194),  # 粉
    (44, 160, 44),    # 綠
    (31, 119, 180),   # 藍
]
DEFAULT_ALPHA = 0.40   # 使用者定案 2026-07-29：套疊圖層 40% 不透明度


def _norm_county(name: str) -> list[str]:
    """縣市名的檔名變體（臺/台）。"""
    out = [name]
    if "臺" in name:
        out.append(name.replace("臺", "台"))
    if "台" in name:
        out.append(name.replace("台", "臺"))
    return out


def resolve_case_override(layer_name: str, case_dir: Path):
    """案件層覆蓋：`<案>/環敏/<平台圖層名>.{geojson,shp}` 存在就優先用它。

    用途＝**本機圖資比平台舊**時，把平台的權威幾何拉進來出圖。本機環敏圖資多為
    105~106 年版快照，其後新增／修正的範圍本機一律查不到（2026-08-04 龍崎案：
    NR23 龍崎牛埔惡地自然保留區 110.07.30 新增，本機 106 年版沒有這一筆，
    逐筆距離算出「13 公里外」，而平台正確回報套疊 8.88 ㎡）。

    ⚠ 這是「有／沒有」的差異，不是面積門檻差異——**不可當成邊界誤差忽略**。
    平台幾何取法見 docs 與 skill：MapServer `/identify` 帶 returnGeometry=true。
    """
    d = case_dir / "環敏"
    if not d.is_dir():
        return None
    for p in sorted(d.iterdir()):
        if p.suffix.lower() not in (".geojson", ".json", ".shp"):
            continue
        # 檔名含平台圖層名即視為該項的覆蓋檔（允許加 NR23_ 這類前綴備註）
        if layer_name in p.stem:
            return p
    return None


def resolve_layer(layer_name: str, county: str, case_dir: Path | None = None,
                  env_root: Path = ENV_ROOT):
    """把平台圖層名稱解析成 (路徑, 規則)；找不到回 (None, 規則或 None)。

    案件層 `<案>/環敏/` 的覆蓋檔優先於本機 `環境敏感相關/`（見 resolve_case_override）。
    env_root 供測試注入假圖資樹；正式呼叫一律用預設值。
    """
    ov = resolve_case_override(layer_name, case_dir) if case_dir else None
    for rule in REGISTRY:
        m = re.search(rule["match"], layer_name)
        if not m:
            continue
        subs = {"county": county}
        if m.lastindex:
            groups = m.groups()
            if len(groups) >= 2:
                subs["h"], subs["mm"] = groups[0], groups[1]
        if ov is not None:
            print(f"    （{layer_name}：採案件層覆蓋檔 {ov.name}，不用本機 環境敏感相關/）")
            return ov, dict(rule, _subs=subs, _override=True)
        for tmpl in rule["shp"]:
            for c in _norm_county(county):
                s = dict(subs, county=c)
                try:
                    rel = tmpl.format(**s)
                except (KeyError, IndexError):
                    continue
                p = env_root / rel
                if p.exists():
                    return p, dict(rule, _subs=s)
        return None, rule
    # REGISTRY 沒有這一項，但案件層有覆蓋檔 → 仍然出得了圖（單色、無分類）
    if ov is not None:
        print(f"    （{layer_name}：REGISTRY 未登錄，採案件層覆蓋檔 {ov.name}）")
        return ov, {"match": "", "shp": [], "field": None, "crs": 3826,
                    "title": layer_name, "_subs": {}, "_override": True}
    return None, None
