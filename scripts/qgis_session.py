"""在工作函式退場後才關閉 QGIS；本模組本身不匯入 QGIS，方便測生命週期。"""
import gc
import traceback


def run_qgis_job(worker, create_application, clear_project):
    """worker(start) 可延後初始化；先釋放圖層／版面，再關閉資料提供者。

    不能在持有 QgsVectorLayer 的工作函式內呼叫 exitQgis：OGR 圖層稍後解構時
    仍會碰到已銷毀的 provider。例外 traceback 也會保留工作函式的局部變數，
    因此出錯時清空已退場 frame 的 locals，但保留原例外與 traceback 位置。
    worker 不得回傳或把 QGIS 物件存到全域；圖層應由工作區域／project 擁有。
    """
    app = None

    def start():
        nonlocal app
        if app is None:
            app = create_application()
            app.initQgis()

    try:
        return worker(start)
    except BaseException as exc:
        traceback.clear_frames(exc.__traceback__)
        raise
    finally:
        if app is not None:
            clear_project()
            gc.collect()
            app.exitQgis()
