# -*- coding: utf-8 -*-
r"""重開 <案>_maxmap.qgz、把每個版面重新匯出，逐像素比對 `圖檔_maxmap/` 的交付 PNG。

這是「qgz 圖例與交付 PNG 不一致」（2026-09-24 西湖北安段案）唯一抓得到的驗收方式：
出圖當下一切正常，只有**重開 qgz 再匯出**才會看見圖例退回整個專案圖層樹、或圖例
代理層變空。屬 manual 測試（需 QGIS 與既有案件產物），不進 pytest。

用法（一律經 qgis_python.ps1，見 CLAUDE.md）：
    .\scripts\qgis_python.ps1 tests\manual\qgz_legend_check.py <案名> [<案名> ...]

匯出檔寫到 `outputs/_maintenance/qgz_recheck/<案名>/`，不動原案件成果。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

EXPORT_DPI = 300


def check_case(case: str) -> int:
    from qgis.core import QgsLayoutExporter, QgsProject

    case_dir = REPO / "outputs" / case
    # make_maps 寫的是 f"{案名}{sfx}.qgz"；案件夾裡可能還有更名前的舊 qgz，不可誤抓
    qgz = case_dir / f"{case}_maxmap.qgz"
    if not qgz.exists():
        qgz = next(iter(sorted(case_dir.glob("*_maxmap.qgz"))), None)
    if qgz is None or not qgz.exists():
        print(f"[{case}] 找不到 *_maxmap.qgz")
        return 1
    img_dir = case_dir / "圖檔_maxmap"
    out_dir = REPO / "outputs" / "_maintenance" / "qgz_recheck" / case
    out_dir.mkdir(parents=True, exist_ok=True)

    project = QgsProject.instance()
    project.clear()
    if not project.read(str(qgz)):
        print(f"[{case}] qgz 讀取失敗：{qgz}")
        return 1
    layouts = project.layoutManager().printLayouts()
    print(f"[{case}] {qgz.name}：{len(layouts)} 個版面", flush=True)

    # 版面名 f"{案場ID}_{主題}"；成品檔名 f"{案場ID}_{案場名}_{主題}.png"
    by_theme = {}
    for png in img_dir.glob("*.png"):
        parts = png.stem.split("_")
        if len(parts) >= 3:
            by_theme[(parts[0], parts[-1])] = png

    bad = 0
    for layout in layouts:
        name = layout.name()
        cid, _, theme = name.partition("_")
        png = by_theme.get((cid, theme))
        exporter = QgsLayoutExporter(layout)
        settings = QgsLayoutExporter.ImageExportSettings()
        settings.dpi = EXPORT_DPI
        dst = out_dir / f"{name}.png"
        code = exporter.exportToImage(str(dst), settings)
        if code != QgsLayoutExporter.Success:
            print(f"  ✖ {name}：重新匯出失敗（{code}）")
            bad += 1
            continue
        if png is None:
            print(f"  ? {name}：找不到對應的交付 PNG，僅匯出未比對")
            continue
        verdict = compare(dst, png)
        print(f"  {'✔' if verdict is None else '✖'} {name}：{verdict or '與交付 PNG 逐像素相同'}")
        bad += verdict is not None
    project.clear()
    return bad


def compare(a: Path, b: Path):
    """相同回 None，不同回一句說明。"""
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return "PIL 未安裝，無法比對"
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return f"尺寸不同 {ia.size} vs {ib.size}"
    diff = ImageChops.difference(ia, ib)
    if diff.getbbox() is None:
        return None
    n = sum(1 for p in diff.getdata() if p != (0, 0, 0))
    return f"{n} 像素不同（{n / (ia.size[0] * ia.size[1]):.3%}），bbox={diff.getbbox()}"


def main() -> int:
    from qgis.core import QgsApplication, QgsProject

    import qgis_session
    from config_loader import load_paths

    cases = sys.argv[1:]
    if not cases:
        print(__doc__)
        return 2
    prefix = load_paths(REPO)["qgis_prefix"]

    def create_application():
        QgsApplication.setPrefixPath(prefix, True)
        return QgsApplication([], False)

    def worker(start):
        start()
        return sum(check_case(c) for c in cases)

    bad = qgis_session.run_qgis_job(worker, create_application,
                                    lambda: QgsProject.instance().clear())
    print("全部相同" if not bad else f"{bad} 個版面與交付 PNG 不一致")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
