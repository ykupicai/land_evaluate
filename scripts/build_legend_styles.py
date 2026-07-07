# -*- coding: utf-8 -*-
"""
從使用者 QGIS 樣式庫（symbology-style.db）撈符號 XML，
組成 categorized renderer 的 QML，覆寫：
  styles/使用分區.qml（欄位：使分）
  styles/使用地編定.qml（欄位：使類）

重點：
- 符號 XML 整段嵌入（LinePatternFill 斜線紋理才能保留），不是只抄色碼。
- db 的 <prop v="..." k="color"/> 屬性順序 v 在前，QGIS 3.40 仍可讀 legacy prop 格式。
- 找不到符號名的值 → 近似色 SimpleFill（執行時列出）。

執行（系統 Python 即可，不需 QGIS）：
  python -X utf8 scripts/build_legend_styles.py
"""

from __future__ import annotations

import copy
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STYLE_DB = Path.home() / "AppData/Roaming/QGIS/QGIS3/profiles/default/symbology-style.db"
STYLES_DIR = REPO_ROOT / "styles"

URBAN_LABEL = "都市土地（使用分區另查）"
GRAY_URBAN = (200, 200, 200)   # 都市土地/缺值 → 中性淺灰
GRAY_OTHER = (225, 225, 225)   # 未預期值（catch-all）

# 使分值 → (樣式庫符號名 或 None, 找不到時的近似色RGB)
ZONING_MAP: list[tuple[str, str | None, tuple[int, int, int] | None]] = [
    ("特定農業區", "特定農業區", None),
    ("一般農業區", "一般農業區", None),
    ("山坡地保育區", "山坡地保育區", None),
    ("森林區", "森林區", None),
    ("河川區", "河川區", None),
    ("鄉村區", "鄉村區", None),
    ("工業區", "工業區", None),
    ("特定專用區", "特定專用區", None),
    ("風景區", "風景區", None),
    ("國家公園區", "國家公園區", None),
    ("海域區", "海域區", (100, 180, 255)),  # 樣式庫預期無此符號
    (URBAN_LABEL, None, GRAY_URBAN),
]

# 使類值 → 符號名（甲乙丙共用、丁建/礦業為簡名、墳墓用地沿用殯葬符號）
TYPE_MAP: list[tuple[str, str | None, tuple[int, int, int] | None]] = [
    ("甲種建築用地", "甲乙丙建築用地", None),
    ("乙種建築用地", "甲乙丙建築用地", None),
    ("丙種建築用地", "甲乙丙建築用地", None),
    ("丁種建築用地", "丁建用地", None),
    ("農牧用地", "農牧用地", None),
    ("林業用地", "林業用地", None),
    ("養殖用地", "養殖用地", None),
    ("鹽業用地", "鹽業用地", None),
    ("礦業用地", "礦業", None),
    ("窯業用地", "窯業用地", None),
    ("交通用地", "交通用地", None),
    ("水利用地", "水利用地", None),
    ("遊憩用地", "遊憩用地", None),
    ("古蹟保存用地", "古蹟保存用地", None),
    ("生態保護用地", "生態保護用地", None),
    ("國土保安用地", "國土保安用地", None),
    ("殯葬用地", "殯葬用地", None),
    ("墳墓用地", "殯葬用地", None),  # 舊名，沿用殯葬符號
    ("特定目的事業用地", "特定目的事業用地", None),
    ("暫未編定", "暫未編定", None),
    (URBAN_LABEL, None, GRAY_URBAN),
]


def simple_fill_symbol(rgb: tuple[int, int, int]) -> ET.Element:
    r, g, b = rgb
    sym = ET.fromstring(
        '<symbol type="fill" alpha="1" force_rhr="0" clip_to_extent="1">'
        '<layer class="SimpleFill" enabled="1" locked="0" pass="0">'
        f'<prop v="{r},{g},{b},255" k="color"/>'
        '<prop v="solid" k="style"/>'
        '<prop v="35,35,35,0" k="outline_color"/>'
        '<prop v="solid" k="outline_style"/>'
        '<prop v="0.2" k="outline_width"/>'
        '<prop v="MM" k="outline_width_unit"/>'
        '<prop v="bevel" k="joinstyle"/>'
        '<prop v="0,0" k="offset"/>'
        '<prop v="MM" k="offset_unit"/>'
        "</layer></symbol>")
    return sym


def build_qml(field: str, mapping: list[tuple[str, str | None, tuple[int, int, int] | None]],
              db_symbols: dict[str, ET.Element], missing: list[str]) -> str:
    qgis = ET.Element("qgis", {"styleCategories": "Symbology", "version": "3.40.12-Bratislava"})
    rend = ET.SubElement(qgis, "renderer-v2", {
        "type": "categorizedSymbol", "attr": field,
        "forceraster": "0", "symbollevels": "0", "enableorderby": "0", "referencescale": "-1"})
    cats = ET.SubElement(rend, "categories")
    syms = ET.SubElement(rend, "symbols")

    def add(value: str, label: str, sym: ET.Element) -> None:
        i = str(len(cats))
        ET.SubElement(cats, "category", {
            "symbol": i, "value": value, "label": label, "render": "true", "type": "string"})
        s = copy.deepcopy(sym)
        s.set("name", i)
        syms.append(s)

    for value, sym_name, fallback in mapping:
        if sym_name and sym_name in db_symbols:
            add(value, value, db_symbols[sym_name])
        else:
            if sym_name:  # 想用符號但庫裡沒有
                missing.append(f"{field}={value}（找不到符號「{sym_name}」，近似色）")
            add(value, value, simple_fill_symbol(fallback or GRAY_OTHER))

    # catch-all（空值/未預期值）
    add("", "其他", simple_fill_symbol(GRAY_OTHER))

    ET.SubElement(rend, "rotation")
    ET.SubElement(rend, "sizescale")
    ET.SubElement(qgis, "blendMode").text = "0"
    ET.SubElement(qgis, "featureBlendMode").text = "0"
    ET.SubElement(qgis, "layerGeometryType").text = "2"
    ET.indent(qgis)
    return '<!DOCTYPE qgis PUBLIC \'http://mrcc.com/qgis.dtd\' \'SYSTEM\'>\n' + \
        ET.tostring(qgis, encoding="unicode")


def main() -> None:
    if not STYLE_DB.exists():
        sys.exit(f"找不到樣式庫：{STYLE_DB}")
    con = sqlite3.connect(STYLE_DB)
    wanted = {m for _, m, _ in ZONING_MAP + TYPE_MAP if m}
    db_symbols: dict[str, ET.Element] = {}
    for name in sorted(wanted):
        row = con.execute("SELECT xml FROM symbol WHERE name=?", (name,)).fetchone()
        if row:
            db_symbols[name] = ET.fromstring(row[0])
    con.close()

    missing: list[str] = []
    STYLES_DIR.mkdir(exist_ok=True)
    for fname, field, mapping in (("使用分區.qml", "使分", ZONING_MAP),
                                  ("使用地編定.qml", "使類", TYPE_MAP)):
        qml = build_qml(field, mapping, db_symbols, missing)
        (STYLES_DIR / fname).write_text(qml, encoding="utf-8")
        print(f"已寫入 styles/{fname}（{len(mapping) + 1} 類）")

    if missing:
        print("找不到對應符號、以近似色代替的值：")
        for m in missing:
            print(f"  - {m}")
    else:
        print("全部值均對到樣式庫符號（都市土地/其他為設計上的中性灰）。")


if __name__ == "__main__":
    main()
