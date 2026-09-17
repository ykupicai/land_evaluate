"""生命週期測試不載入 QGIS：包含例外 traceback 及循環參照保活。"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from qgis_session import run_qgis_job


@pytest.mark.parametrize('failure', [None, RuntimeError('render failed'), SystemExit(2)])
@pytest.mark.parametrize('cycle', [False, True])
def test_resources_released_before_exit_even_on_error(failure, cycle):
    events = []

    class App:
        def initQgis(self):
            events.append('init')

        def exitQgis(self):
            events.append('exit')

    class Layer:
        def __del__(self):
            events.append('layer freed')

    def worker(start):
        start()
        start()  # 同一工作中只初始化一次
        layer = Layer()
        if cycle:
            layer.self_ref = layer
        if failure:
            raise failure
        return 'done'  # 同樣涵蓋 --extract-only / --overview-only 的提早 return

    if failure:
        with pytest.raises(type(failure)) as caught:
            run_qgis_job(worker, App, lambda: events.append('clear project'))
        assert caught.value is failure  # 不吞例外、不改成成功碼
    else:
        assert run_qgis_job(worker, App, lambda: events.append('clear project')) == 'done'
    assert events.count('init') == events.count('exit') == 1
    assert events.index('layer freed') < events.index('exit')
    assert events.index('clear project') < events.index('exit')


def test_validation_failure_does_not_start_qgis():
    def never_called():
        pytest.fail('參數檢查失敗時不應啟動或清理 QGIS')

    def worker(start):
        raise ValueError('invalid argument')

    with pytest.raises(ValueError, match='invalid argument'):
        run_qgis_job(worker, never_called, never_called)


def test_failed_initialization_still_shuts_down():
    events = []

    class App:
        def initQgis(self):
            raise RuntimeError('init failed')

        def exitQgis(self):
            events.append('exit')

    with pytest.raises(RuntimeError, match='init failed'):
        run_qgis_job(lambda start: start(), App, lambda: events.append('clear'))
    assert events == ['clear', 'exit']
