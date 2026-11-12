# -*- coding: utf-8 -*-
"""測試用的假 osgeo:讓系統 Python(沒有 GDAL)也能 import nlsc_wmts / road_access,
並讓圖磚拼接的失敗路徑可以不碰真 GDAL、不連網地單測。

用法:
    import fake_osgeo
    fake_osgeo.install_stub()          # 真 osgeo 不存在時才塞入空殼,讓 import 通過
    import nlsc_wmts
    g, o = fake_osgeo.FakeGdal(), fake_osgeo.FakeOsr()
    monkeypatch.setattr(nlsc_wmts, "gdal", g); monkeypatch.setattr(nlsc_wmts, "osr", o)
    ...  g.created[-1]["bands"] 是寫出的各波段陣列
"""
import io
import sys
import types

import numpy as np


def install_stub():
    try:
        import osgeo  # noqa: F401
        return
    except ImportError:
        pass
    pkg = types.ModuleType("osgeo")
    for name in ("gdal", "ogr", "osr"):
        m = types.ModuleType(f"osgeo.{name}")
        m.UseExceptions = lambda *a, **k: None
        setattr(pkg, name, m)
        sys.modules[f"osgeo.{name}"] = m
    sys.modules["osgeo"] = pkg


class _Band:
    def __init__(self, store, idx):
        self.store, self.idx = store, idx

    def WriteArray(self, arr):
        self.store["bands"][self.idx] = np.array(arr)

    def SetColorInterpretation(self, v):
        self.store["alpha_band"] = self.idx


class _DS:
    def __init__(self, store):
        self.store = store

    def SetGeoTransform(self, gt):
        self.store["gt"] = gt

    def SetProjection(self, w):
        pass

    def GetRasterBand(self, i):
        return _Band(self.store, i - 1)

    def FlushCache(self):
        pass


class _Driver:
    def __init__(self, g):
        self.g = g

    def Create(self, path, W, H, nb, dt, options=None):
        store = {"path": path, "W": W, "H": H, "nb": nb, "bands": [None] * nb}
        self.g.created.append(store)
        return _DS(store)


class _Opened:
    def __init__(self, arr):
        self.arr = arr

    def ReadAsArray(self):
        return self.arr


class FakeGdal:
    GCI_AlphaBand = 6
    GDT_Byte = 1

    def __init__(self):
        self.mem = {}
        self.created = []

    def UseExceptions(self):
        pass

    def FileFromMemBuffer(self, name, blob):
        self.mem[name] = blob

    def Open(self, name):
        from PIL import Image
        img = Image.open(io.BytesIO(self.mem[name]))   # 壞資料 → 例外(同真 GDAL)
        if img.mode in ("L", "P"):
            return _Opened(np.asarray(img.convert("L")))
        mode = "RGBA" if img.mode == "RGBA" else "RGB"
        return _Opened(np.transpose(np.asarray(img.convert(mode)), (2, 0, 1)))

    def Unlink(self, name):
        self.mem.pop(name, None)

    def GetDriverByName(self, name):
        return _Driver(self)


class _SRS:
    def __init__(self):
        self.code = None

    def ImportFromEPSG(self, c):
        self.code = c

    def SetAxisMappingStrategy(self, s):
        pass

    def ExportToWkt(self):
        return f"EPSG:{self.code}"


class _CT:
    def __init__(self, s, d):
        from pyproj import Transformer
        self.tf = Transformer.from_crs(f"EPSG:{s.code}", f"EPSG:{d.code}", always_xy=True)

    def TransformPoint(self, x, y):
        a, b = self.tf.transform(x, y)
        return (a, b, 0.0)


class FakeOsr:
    OAMS_TRADITIONAL_GIS_ORDER = 0
    SpatialReference = _SRS
    CoordinateTransformation = _CT
