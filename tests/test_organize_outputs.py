"""權屬加入基本圖後，成果整理不可漏掉它；全部寫入暫存資料夾。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import organize_outputs as organize


def test_copies_all_seven_themes_without_touching_originals(tmp_path, monkeypatch):
    source, dest = tmp_path / 'source', tmp_path / 'dest'
    source.mkdir()
    themes = ['使用分區', '使用地編定', '權屬', '國土功能分區', '正射套疊', '國土利用調查', '臨路判定']
    for theme in themes:
        (source / f'S001_基地一_{theme}.png').write_bytes(theme.encode('utf-8'))
    (source / '00_總區位圖.png').write_bytes(b'overview')
    monkeypatch.setattr(organize, 'SRC', source)
    monkeypatch.setattr(organize, 'DST', dest)
    organize.main()
    assert len(list(dest.rglob('*.png'))) == 7
    assert len(list(source.glob('*.png'))) == 8
    for theme in themes:
        assert (dest / 'S001_基地一' / f'{theme}.png').read_bytes() == theme.encode('utf-8')
