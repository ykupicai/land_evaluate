# -*- coding: utf-8 -*-
"""整合總控：shp/土地清冊 → 基地 → 六張基本圖 + qgz（spec 2026-07-11）。

用法見 spec §3。停點：
- 清冊模式首跑：解析出 01 後停（人工核對；--resume 續跑）。
- 兩模式：00_總區位圖 產出後停，等 --mode single|persite。
"""
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
QGIS_BAT = Path(r"C:\Program Files\QGIS 3.40.12\bin\python-qgis-ltr.bat")
FISHPV_ROOT = Path(r"C:\Users\user\Documents\Claude\Projects\漁電共生評估")
COLORTABLE = (FISHPV_ROOT / "qgis_plugin" / "fishpv_aquavoltaic"
              / "resources" / "luimap_colortable.csv")
FROM_STEPS = ("meta", "match", "fishpv", "overview", "extract", "maps")


def plan_steps(mode_shp: bool, resume: bool, chosen_mode, artifacts: dict) -> list:
    """決定本次要執行的步驟序列（純函式，依 spec §4/§5.1）。

    trust-shp 模式（spec §5.1）：基地層幾何與屬性直接取自 shp、**不比對官方 FID**，
    故無 match 步驟；出圖以 make_maps --trust-shp 直讀 shp（trust_extract_* + maps）。
    """
    if mode_shp:
        if chosen_mode:
            steps = []
            if not artifacts.get("fishpv"):
                steps += ["fishpv", "digest"]
            # trust 出圖：single 全歸 001；persite 依 sitemap 每宗基地一組
            steps.append("trust_extract_persite" if chosen_mode == "persite"
                         else "trust_extract_single")
            steps.append("maps")
            return steps
        # 首跑：解析留痕(meta)→臨路(fishpv/digest)→總區位圖，停等選模式；不比對、不抽圖層
        return ["meta", "fishpv", "digest", "overview", "STOP_MODE"]
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


def find_from_index(steps: list, target: str) -> int | None:
    """在 steps 中找 --from 目標的第一個相符索引：
    - target=='match' 同時匹配 match/match2（清冊 fallback 補鏈）；
    - target=='extract' 同時匹配 extract/trust_extract_single/trust_extract_persite
      （trust-shp 模式已無獨立 extract，抽圖層改由 trust_extract_* 承擔）。"""
    aliases = {
        "match": {"match", "match2"},
        "extract": {"extract", "trust_extract_single", "trust_extract_persite"},
    }
    want = aliases.get(target, {target})
    for i, s in enumerate(steps):
        if s in want:
            return i
    return None


KNOWN_STEPS = {
    "meta", "parse", "match", "match2", "landno", "apply", "extract",
    "trust_extract_single", "trust_extract_persite",
    "fishpv", "digest", "overview", "maps", "STOP_MODE", "STOP_CHECK",
}


def execute_steps(steps: list, runner, art) -> list[str]:
    """執行 steps（含 match 後 fallback 動態插入），回傳實際執行序（純函式邏輯，
    副作用全部委派給 runner/art，方便單測不碰檔案系統）。

    runner(step_name) -> bool：執行單一步驟的副作用；回傳 True 表示應立即停止
    （對應舊版 main() 內遇到 parse/STOP_MODE/STOP_CHECK 時的 return）。
    art() -> dict：artifacts 快照（同 main() 內 art()），用來判斷 match 後 03 是否
    非空，才知道要不要動態補 landno/apply/match2。

    steps 這個 list 物件會被就地修改（在「當前位置之後」插入補鏈步驟），需要保留
    原始序列的呼叫端請自行先複製一份再傳入。
    """
    executed: list = []
    i = 0
    while i < len(steps):
        s = steps[i]
        if s not in KNOWN_STEPS:
            print(f"[run_case] 警告：未知步驟 {s!r}，略過")
            i += 1
            continue
        executed.append(s)
        stop = runner(s)
        if s == "match" and "landno" not in steps and art().get("03_nonempty"):
            # 首次比對後才知道 03 是否非空 → 在「當前位置之後」插入 fallback，
            # 確保執行序為 match→landno→apply→match2→(原後續)：
            # 不會被後面的 STOP_MODE 擋住（append 到尾端會），也不會排到
            # maps 之後（出圖才不會用到未修正的 02）。
            steps[i + 1:i + 1] = ["landno", "apply", "match2"]
        if stop:
            break
        i += 1
    return executed


def digest_fishpv(case_dir: Path) -> None:
    """成果.xlsx → sites_status.csv（給 make_maps）與 sitemap.csv（給 persite）。"""
    import pandas as pd
    xlsx = case_dir / "臨路評估" / "成果.xlsx"
    df = pd.read_excel(xlsx, dtype=str)
    required = ["site_id", "臨路狀態", "臨路距離_m", "臨路註記", "地號清單"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SystemExit(f"[run_case] 成果.xlsx 缺欄位：{missing}")
    df[["site_id", "臨路狀態", "臨路距離_m", "臨路註記"]].to_csv(
        case_dir / "臨路評估" / "sites_status.csv", index=False, encoding="utf-8-sig")
    rows = []
    for _, r in df.iterrows():
        for no in str(r["地號清單"]).split(";"):
            no = no.strip()
            if not no or no.lower() == "nan":
                continue
            rows.append({"地號": no, "案場ID": r["site_id"],
                         "案場名稱": f"{r['site_id']}宗基地"})
    pd.DataFrame(rows).to_csv(case_dir / "臨路評估" / "sitemap.csv",
                              index=False, encoding="utf-8-sig")
    print(f"digest：sites_status.csv + sitemap.csv（{len(rows)} 地號）")


# QGIS 已知收尾崩潰碼：5=QGIS 慣有結束碼；3221225477(0xC0000005 存取違例)=大量 GDAL
# 圖磚處理後 app.exitQgis()/解譯器收尾時 libxml2 全域狀態損毀觸發（CLAUDE.md 已記錄），
# 崩潰發生在產物都已成功寫出「之後」，屬無害的行程收尾雜訊。
QGIS_CRASH_CODES = (5, 3221225477)


def _run(cmd, cwd=None, label="", allowed=(0,), crash_artifact=None, fatal=True) -> bool:
    """執行子進程步驟；回傳是否成功。

    - allowed：視為成功的 returncode（預設僅 0）。**只有 maps 步驟**因已知的
      QGIS 收尾崩潰（見 QGIS_CRASH_CODES 註解）可由呼叫端明示放寬。
    - crash_artifact：extract/overview 等其餘 QGIS 步驟不豁免崩潰碼，但若
      returncode 屬 QGIS_CRASH_CODES 且指定的產物「本次執行期間剛寫出」，
      判定為收尾崩潰（產物已完成）→ 警告後放行；產物沒剛寫出則照樣失敗。
    - fatal：False 時失敗不 sys.exit，改回傳 False（呼叫端自行降級，如 fishpv）。
    """
    print(f"\n=== {label or ' '.join(map(str, cmd))} ===", flush=True)
    t0 = time.time()
    r = subprocess.run([str(c) for c in cmd], cwd=cwd)
    if r.returncode in allowed:
        return True
    if crash_artifact is not None and r.returncode in QGIS_CRASH_CODES:
        p = Path(crash_artifact)
        # FAT/網路磁碟 mtime 粒度留 2 秒餘裕
        if p.exists() and p.stat().st_mtime >= t0 - 2:
            print(f"[run_case] 警告：步驟（{label}）以已知 QGIS 收尾崩潰結束"
                  f"（returncode={r.returncode}），但產物 {p.name} 已於本次執行寫出"
                  "→ 視為成功續跑", flush=True)
            return True
    if fatal:
        sys.exit(f"[run_case] 步驟失敗（{label}），returncode={r.returncode}")
    print(f"[run_case] 警告：步驟失敗（{label}），returncode={r.returncode}（不中斷）",
          flush=True)
    return False


def from_prereq_errors(from_step: str, mode_shp: bool, case_dir: Path,
                       shp, chosen) -> list[str]:
    """--from 截斷後，檢查截斷起點依賴的前置產物是否齊備（純函式，可單測）。

    回傳缺件訊息清單（空=可續跑）；每則點名缺什麼檔、該用哪個 --from 或指令補跑。
    """
    errs: list[str] = []
    fp = case_dir / "臨路評估"

    def need(path, hint):
        if not Path(path).exists():
            errs.append(f"缺 {path} —— {hint}")

    if from_step == "meta":
        if mode_shp:
            need(Path(shp).with_suffix(".dbf"), "請確認 --shp 路徑（需同名 .dbf）")
    elif from_step == "match":
        need(case_dir / "01_中繼檔.xlsx",
             "請先完成清冊解析與人工核對（首跑不加 --from），或以 shp_to_meta 產 01")
    elif from_step == "fishpv":
        if mode_shp:
            need(shp, "請確認 --shp 路徑")
        else:
            need(case_dir / "圖層.gpkg",
                 "清冊模式 fishpv 吃 圖層.gpkg 基地層，請先 --from extract（或完整 --resume）")
    elif from_step == "overview":
        need(fp / "sites.shp", "總區位圖需 fishpv 宗基地聚合結果，請先 --from fishpv")
    elif from_step == "extract":
        if mode_shp:
            need(shp, "請確認 --shp 路徑")
        else:
            need(case_dir / "02_比對結果.xlsx", "請先 --from match 完成比對")
    elif from_step == "maps":
        need(case_dir / "圖層.gpkg", "maps 以 --skip-extract 重用圖層，請先 --from extract")
        if mode_shp:
            need(shp, "請確認 --shp 路徑")
            if chosen == "persite" and not (fp / "sitemap.csv").exists():
                need(fp / "成果.xlsx",
                     "trust persite 出圖需 臨路評估/sitemap.csv 或 成果.xlsx，請先 --from fishpv")
        else:
            need(case_dir / "02_比對結果.xlsx",
                 "maps 需讀 02 建案場清單，請先 --from match 完成比對")
    return errs


def main():
    args = sys.argv[1:]
    shp = lst = case = chosen = from_step = None
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
        elif a == "--from":
            from_step = args[i + 1]; i += 2
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
    if from_step is not None and from_step not in FROM_STEPS:
        sys.exit(f"--from 需為 {'|'.join(FROM_STEPS)} 之一")
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

    artifacts = art()
    if not mode_shp and chosen and not artifacts["01"]:
        sys.exit("[run_case] 清冊模式需先完成解析與人工核對（01_中繼檔），"
                 "再用 --resume；之後才可 --mode 出圖")

    dbf = str(Path(shp).with_suffix(".dbf")) if shp else None
    steps = plan_steps(mode_shp, resume, chosen, artifacts)

    if from_step is not None:
        # --from：以「假裝所有產物都不存在」重算最大步驟序列，藉此跳過
        # artifacts 導向的省略，再從第一個相符步驟截斷（強制重跑該點起）。
        full_steps = plan_steps(mode_shp, resume, chosen,
                                {k: False for k in artifacts})
        idx = find_from_index(full_steps, from_step)
        if idx is None:
            avail = [s for s in FROM_STEPS if find_from_index(full_steps, s) is not None]
            sys.exit(f"[run_case] --from {from_step} 在本次流程中找不到對應步驟；"
                     f"本次可用步驟：{avail}")
        steps = full_steps[idx:]
        errs = from_prereq_errors(from_step, mode_shp, case_dir, shp, chosen)
        if errs:
            sys.exit(f"[run_case] --from {from_step} 前置產物不足，先補齊再跑：\n  - "
                     + "\n  - ".join(errs))

    if not mode_shp and chosen == "single":
        print("[run_case] 說明：清冊模式出圖單位＝清冊的案場ID分組，"
              "--mode single 與 persite 目前等效（不另合併為單一組）")

    print(f"[run_case] 案子={case} 步驟={steps}")

    def run_step(s: str) -> bool:
        """執行單一步驟；回傳 True 表示本次流程應在此停止（等同舊版 main() 的 return）。"""
        if s == "meta":
            _run(["python", "-X", "utf8", REPO / "scripts" / "shp_to_meta.py",
                  dbf, case, case], label="shp_to_meta")
        elif s == "parse":
            print("[run_case] 清冊解析請先以既有流程產 01_中繼檔（換版式需 AI 寫解析），"
                 "完成人工核對後加 --resume 續跑")
            return True
        elif s in ("match", "match2"):
            _run(["python", "-X", "utf8", REPO / "scripts" / "match_parcels.py", case],
                 label=s)
        elif s == "landno":
            _run(["python", "-X", "utf8", REPO / "scripts" / "query_landno.py", case],
                 label="query_landno")
        elif s == "apply":
            _run(["python", "-X", "utf8", REPO / "scripts" / "apply_landno.py", case],
                 label="apply_landno")
        elif s == "extract":
            # extract 不豁免 QGIS 崩潰碼；僅當 圖層.gpkg 確實剛寫出才視為收尾崩潰放行
            _run([QGIS_BAT, REPO / "scripts" / "make_maps.py", case, "--extract-only"],
                 label="extract gpkg", crash_artifact=case_dir / "圖層.gpkg")
        elif s == "trust_extract_single":
            _run([QGIS_BAT, REPO / "scripts" / "make_maps.py", case,
                  "--trust-shp", shp, "--extract-only"],
                 label="trust extract（single，直取 shp 幾何）",
                 crash_artifact=case_dir / "圖層.gpkg")
        elif s == "trust_extract_persite":
            if not (fp / "sitemap.csv").exists():
                if (fp / "成果.xlsx").exists():
                    digest_fishpv(case_dir)
                else:
                    sys.exit("[run_case] trust_extract_persite 需 臨路評估/sitemap.csv "
                             "或 成果.xlsx，兩者皆缺（fishpv 臨路評估未成功？先重跑 "
                             "--from fishpv，或改 --mode single）")
            _run([QGIS_BAT, REPO / "scripts" / "make_maps.py", case,
                  "--trust-shp", shp, "--sitemap", fp / "sitemap.csv", "--extract-only"],
                 label="trust extract（persite，依 sitemap 分宗基地）",
                 crash_artifact=case_dir / "圖層.gpkg")
        elif s == "fishpv":
            cad = shp if mode_shp else str(case_dir / "圖層.gpkg")
            ok = _run([FISHPV_ROOT / "env.bat", "python", "-m", "fishpv.cli",
                       "--cadastre", cad, "--colortable", COLORTABLE,
                       "--out", fp, "--no-ai", "--landno-field", "地號"],
                      cwd=str(FISHPV_ROOT), label="fishpv 臨路評估", fatal=False)
            if not ok:
                # spec §6：fishpv 失敗 → 圖5/6 降級、1-4 照出，不中斷整條流程
                print("[run_case] 警告：臨路評估(fishpv)失敗——後續降級續跑："
                      "圖6(臨路判定)將於 05 清單標記「臨路評估失敗」、圖5 無圖例、"
                      "無 00_總區位圖；其餘圖照出。", flush=True)
        elif s == "digest":
            if not (fp / "成果.xlsx").exists():
                print("[run_case] 警告：無 臨路評估/成果.xlsx（fishpv 未成功），"
                      "跳過 digest", flush=True)
            else:
                digest_fishpv(case_dir)
        elif s == "overview":
            if not (fp / "sites.shp").exists():
                print("[run_case] 警告：無 臨路評估/sites.shp（fishpv 未成功），"
                      "跳過 00_總區位圖", flush=True)
                return False
            cmd = [QGIS_BAT, REPO / "scripts" / "make_maps.py", case, "--overview-only"]
            if mode_shp:
                cmd += ["--trust-shp", shp]  # trust 模式免讀 02
            _run(cmd, label="00_總區位圖",
                 crash_artifact=case_dir / "00_總區位圖.png")
        elif s == "maps":
            cmd = [QGIS_BAT, REPO / "scripts" / "make_maps.py", case, "--skip-extract"]
            if mode_shp:
                cmd += ["--trust-shp", shp]  # maps 需 shp 建 cases（免讀 02）
                if chosen == "persite":
                    cmd += ["--sitemap", fp / "sitemap.csv"]
            cmd += passthru
            # 僅 maps 豁免 QGIS 收尾崩潰碼（大量圖磚後已知崩潰，產物已寫出，見 QGIS_CRASH_CODES）
            _run(cmd, label="六張圖+qgz", allowed=(0,) + QGIS_CRASH_CODES)
        elif s == "STOP_MODE":
            png = case_dir / "00_總區位圖.png"
            if png.exists():
                print(f"\n請看 {png} 後選出圖單位，再跑同指令加 --mode single|persite")
            else:
                print("\n[run_case] 無 00_總區位圖（臨路評估未成功或總區位圖產製失敗）；"
                      "請直接跑同指令加 --mode single|persite"
                      "（persite 需臨路評估成果，fishpv 失敗時請改用 single 或先排除問題）")
            return True
        elif s == "STOP_CHECK":
            return True
        return False

    executed = execute_steps(steps, run_step, art)
    if not (executed and executed[-1] in ("parse", "STOP_MODE", "STOP_CHECK")):
        print("\n[run_case] 完成")


if __name__ == "__main__":
    main()
