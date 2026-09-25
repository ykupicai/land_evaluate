# -*- coding: utf-8 -*-
"""把「qgz 重開後圖例與交付 PNG 不一致」那兩個錯法寫成斷言（2026-09-24 西湖北安段案）。

這兩個缺陷都**不會讓出圖失敗**——PNG 照出、05 清單照記「成功」，只有使用者把 .qgz
在 QGIS 重開時才會發現圖例不是當初那張。沒有自動化驗收抓得到，故在原始碼層面鎖住：

  1. 自訂圖例樹必須改在圖例**自己的**那棵樹上（`leg.model().rootGroup()`），
     不可另建 `QgsLayerTree()` 再 `setRootGroup()` 換進去（換進去的那棵不會被序列化）。
  2. 只為出圖例而造的 `memory` 圖層必須落地成實體檔（`persist_legend_layer`），
     否則 qgz 只記下 memory uri、重開是空的。

需要 QGIS 才能跑的端到端驗證見 docs/2026-09-24_qgz圖例修復.md。
"""
import re
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def src(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def code_lines(text: str) -> list[str]:
    """去掉整行註解與行內註解，只留真正會執行的程式碼（註解裡寫「不可這樣做」不算犯規）。"""
    out = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0]
        if stripped.strip():
            out.append(stripped)
    return out


@pytest.mark.parametrize("name", ["make_maps.py", "build_layout.py"])
def test_legend_uses_its_own_root_group(name):
    """圖例樹＝leg.model().rootGroup()；出現 setRootGroup 就是回到存不進 qgz 的寫法。"""
    lines = code_lines(src(name))
    assert not [ln for ln in lines if "setRootGroup(" in ln], (
        f"{name}：圖例不可用 setRootGroup 換一棵自建的 QgsLayerTree，"
        "那棵樹不會被 project.write() 序列化，qgz 重開後圖例會退回整個專案圖層樹")
    assert any("leg.model().rootGroup()" in ln for ln in lines), (
        f"{name}：找不到 leg.model().rootGroup()，自訂圖例樹應建在圖例自己的那棵樹上")
    assert any("removeAllChildren()" in ln for ln in lines), (
        f"{name}：改用圖例自己的樹之後必須先 removeAllChildren()，"
        "否則會把整個專案圖層樹的複本一起留在圖例裡")


def test_legend_proxy_layers_are_persisted():
    """三個圖例代理層（都市計畫分區×2、國土利用類別）都要走 persist_legend_layer。"""
    text = src("make_maps.py")
    lines = code_lines(text)
    assert sum("persist_legend_layer(" in ln for ln in lines) >= 4, (
        "make_maps.py：圖例代理層應全部改走 persist_legend_layer 落地成實體 gpkg")
    # 記憶體層直接被 return 當圖例用＝舊錯法
    assert not re.search(r"keep_alive\.append\((lyr|plyr)\)\s*\n\s*return \1", text), (
        "make_maps.py：圖例代理的記憶體層不可直接回傳，memory provider 存不進 qgz")


def test_persist_helper_falls_back_instead_of_breaking_rendering():
    """落地失敗只能降級（回傳原記憶體層），不可讓整批出圖掛掉。"""
    text = src("make_maps.py")
    body = text.split("def persist_legend_layer(", 1)[1].split("\ndef ", 1)[0]
    assert "except Exception" in body and "return mem" in body, (
        "persist_legend_layer 必須在落地失敗時退回原記憶體層，不可往外拋")
    assert "layerName" in body and "GPKG" in body


def test_every_rendered_layer_is_registered_in_project():
    """進圖面／圖例的圖層都要 addMapLayer，否則 project.write() 不會寫進 qgz。

    2026-09-24 實見：權屬層 `l_own` 只 keep_alive、沒進 project，重開 qgz 後權屬版面
    只剩基地紅框，公私別分色整層不見（8~9 成畫面不同），出圖當下卻完全正常。
    """
    text = src("make_maps.py")
    assert "project.addMapLayer(l_own" in text, (
        "make_maps.py：權屬層 l_own 必須 project.addMapLayer，否則 qgz 存不住它")


def test_district_labels_are_persisted():
    """行政區界的區名標註層同病：memory 點層要落地，否則 qgz 重開後區名整批消失。"""
    text = src("district_line.py")
    assert "_persist_labels(" in text, "district_line.py：區名標註層應落地成實體 gpkg"
    body = text.split("def _persist_labels(", 1)[1]
    assert "except Exception" in body and "return pts" in body, (
        "_persist_labels 落地失敗要退回原記憶體層，不可阻斷出圖")
    assert "tag" in text.split("def district_layers(", 1)[1].split(")", 1)[0], (
        "district_layers 要能吃 tag（案場 ID），否則逐案場出圖會互相覆寫同一個標註檔")
