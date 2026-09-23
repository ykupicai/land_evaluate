# -*- coding: utf-8 -*-
"""doctor 的純函式測試：判定 numpy 是否被使用者 site-packages 蓋掉。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from doctor import numpy_from_user_site  # noqa: E402

USER = r"C:\Users\user\AppData\Roaming\Python\Python312\site-packages"


def test_numpy來自user_site判為污染():
    assert numpy_from_user_site(USER + r"\numpy\__init__.py", USER)


def test_numpy為QGIS自帶不判污染():
    qgis = r"C:\PROGRA~1\QGIS34~1.12\apps\Python312\Lib\site-packages\numpy\__init__.py"
    assert not numpy_from_user_site(qgis, USER)


def test_大小寫與斜線方向不影響判定():
    """Windows 路徑大小寫、正反斜線都可能不一致，不可因此漏判。"""
    mixed = USER.lower().replace("\\", "/") + "/numpy/__init__.py"
    assert numpy_from_user_site(mixed, USER)


def test_前綴相同但不是子資料夾不誤判():
    """site-packages2 不是 site-packages 的子資料夾。"""
    assert not numpy_from_user_site(USER + r"2\numpy\__init__.py", USER)


def test_空值不誤判():
    assert not numpy_from_user_site("", USER)
    assert not numpy_from_user_site(USER + r"\numpy\__init__.py", "")
