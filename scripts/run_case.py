# -*- coding: utf-8 -*-
"""整合總控：shp/土地清冊 → 基地 → 六張基本圖 + qgz（spec 2026-07-11）。

用法見 spec §3。停點：
- 清冊模式首跑：解析出 01 後停（人工核對；--resume 續跑）。
- 兩模式：00_總區位圖 產出後停，等 --mode single|persite。
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
QGIS_BAT = Path(r"C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat")
FISHPV_ROOT = Path(r"C:\Users\user\Documents\Claude\Projects\漁電共生評估")
COLORTABLE = (FISHPV_ROOT / "qgis_plugin" / "fishpv_aquavoltaic"
              / "resources" / "luimap_colortable.csv")


def plan_steps(mode_shp: bool, resume: bool, chosen_mode, artifacts: dict) -> list:
    """決定本次要執行的步驟序列（純函式，依 spec §4）。"""
    if mode_shp:
        if chosen_mode:
            steps = []
            if not artifacts.get("01"):
                steps.append("meta")
            if chosen_mode == "persite":
                steps.append("meta_sitemap")   # 重建 01 依 sitemap 分組
            steps += ["extract", "maps"]
            return steps
        return ["meta", "extract", "fishpv", "digest", "overview", "STOP_MODE"]
    # 清冊模式
    if not resume and not chosen_mode:
        return ["parse", "STOP_CHECK"]
    if chosen_mode:
        return ["extract", "maps"] if artifacts.get("fishpv") else \
               ["match", "extract", "fishpv", "digest", "maps"]
    steps = ["match"]
    if artifacts.get("03_nonempty"):
        steps += ["landno", "apply", "match2"]
    steps += ["extract", "fishpv", "digest", "overview", "STOP_MODE"]
    return steps


def digest_fishpv(case_dir: Path) -> None:
    """成果.xlsx → sites_status.csv（給 make_maps）與 sitemap.csv（給 persite）。"""
    import pandas as pd
    df = pd.read_excel(case_dir / "臨路評估" / "成果.xlsx", dtype=str)
    df[["site_id", "臨路狀態", "臨路距離_m", "臨路註記"]].to_csv(
        case_dir / "臨路評估" / "sites_status.csv", index=False, encoding="utf-8-sig")
    rows = []
    for _, r in df.iterrows():
        for no in str(r["地號清單"]).split(";"):
            no = no.strip()
            if no:
                rows.append({"地號": no, "案場ID": r["site_id"],
                             "案場名稱": f"{r['site_id']}宗基地"})
    pd.DataFrame(rows).to_csv(case_dir / "臨路評估" / "sitemap.csv",
                              index=False, encoding="utf-8-sig")
    print(f"digest：sites_status.csv + sitemap.csv（{len(rows)} 地號）")


def _run(cmd, cwd=None, label=""):
    print(f"\n=== {label or ' '.join(map(str, cmd))} ===", flush=True)
    r = subprocess.run([str(c) for c in cmd], cwd=cwd)
    if r.returncode not in (0, 5):  # 5=QGIS 慣有結束碼
        sys.exit(f"[run_case] 步驟失敗（{label}），returncode={r.returncode}")


def main():
    args = sys.argv[1:]
    shp = lst = case = chosen = None
    resume = False
    passthru = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--shp":
            shp = args[i + 1]; i += 2
        elif a == "--list":
            lst = args[i + 1]; i += 2
        elif a == "--case":
            case = args[i + 1]; i += 2
        elif a == "--mode":
            chosen = args[i + 1]; i += 2
        elif a == "--resume":
            resume = True; i += 1
        elif a in ("--no-qgz", "--mem", "--basic"):
            passthru.append(a); i += 1
        else:
            sys.exit(f"未知參數 {a}")
    if not (shp or lst):
        sys.exit("需 --shp <基地.shp> 或 --list <清冊>")
    if chosen not in (None, "single", "persite"):
        sys.exit("--mode 需為 single|persite")
    mode_shp = shp is not None
    if not case:
        case = Path(shp or lst).stem
    case_dir = REPO / "outputs" / case
    case_dir.mkdir(parents=True, exist_ok=True)
    fp = case_dir / "臨路評估"

    def art():
        n03 = case_dir / "03_無法對應清單.xlsx"
        nonempty = False
        if n03.exists():
            import pandas as pd
            nonempty = len(pd.read_excel(n03)) > 0
        return {"01": (case_dir / "01_中繼檔.xlsx").exists(),
                "02": (case_dir / "02_比對結果.xlsx").exists(),
                "03_nonempty": nonempty,
                "fishpv": (fp / "成果.xlsx").exists(),
                "overview": (case_dir / "00_總區位圖.png").exists()}

    dbf = str(Path(shp).with_suffix(".dbf")) if shp else None
    steps = plan_steps(mode_shp, resume, chosen, art())
    print(f"[run_case] 案子={case} 步驟={steps}")
    for s in steps:
        if s == "meta":
            _run(["python", "-X", "utf8", REPO / "scripts" / "shp_to_meta.py",
                  dbf, case, case], label="shp_to_meta")
        elif s == "meta_sitemap":
            _run(["python", "-X", "utf8", REPO / "scripts" / "shp_to_meta.py",
                  dbf, case, case, "--sitemap", fp / "sitemap.csv"],
                 label="shp_to_meta --sitemap")
        elif s == "parse":
            sys.exit("[run_case] 清冊解析請先以既有流程產 01_中繼檔（換版式需 AI 寫解析），"
                     "完成人工核對後加 --resume 續跑")
        elif s in ("match", "match2"):
            _run(["python", "-X", "utf8", REPO / "scripts" / "match_parcels.py", case],
                 label=s)
            if s == "match" and "landno" not in steps and art()["03_nonempty"]:
                # 首次比對後才知道 03 是否非空 → 動態補跑 fallback
                # （已驗證：對 list 迭代中 append 的新元素會被本 for 迴圈繼續走到）
                for extra in ("landno", "apply", "match2"):
                    if extra not in steps:
                        steps.append(extra)
        elif s == "landno":
            _run(["python", "-X", "utf8", REPO / "scripts" / "query_landno.py", case],
                 label="query_landno")
        elif s == "apply":
            _run(["python", "-X", "utf8", REPO / "scripts" / "apply_landno.py", case],
                 label="apply_landno")
        elif s == "extract":
            _run([QGIS_BAT, REPO / "scripts" / "make_maps.py", case, "--extract-only"],
                 label="extract gpkg")
        elif s == "fishpv":
            cad = shp if mode_shp else str(case_dir / "圖層.gpkg")
            _run([FISHPV_ROOT / "env.bat", "python", "-m", "fishpv.cli",
                  "--cadastre", cad, "--colortable", COLORTABLE,
                  "--out", fp, "--no-ai", "--landno-field", "地號"],
                 cwd=str(FISHPV_ROOT), label="fishpv 臨路評估")
        elif s == "digest":
            digest_fishpv(case_dir)
        elif s == "overview":
            _run([QGIS_BAT, REPO / "scripts" / "make_maps.py", case, "--overview-only"],
                 label="00_總區位圖")
        elif s == "maps":
            _run([QGIS_BAT, REPO / "scripts" / "make_maps.py", case, *passthru],
                 label="六張圖+qgz")
        elif s == "STOP_MODE":
            print(f"\n請看 {case_dir / '00_總區位圖.png'} 後選出圖單位，"
                  f"再跑同指令加 --mode single|persite")
            return
        elif s == "STOP_CHECK":
            return
    print("\n[run_case] 完成")


if __name__ == "__main__":
    main()
