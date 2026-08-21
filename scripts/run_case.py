# -*- coding: utf-8 -*-
"""整合總控：shp/土地清冊 → 基地 → 六張基本圖 + qgz（spec 2026-07-11）。

用法見 spec §3。停點：
- 清冊模式首跑：解析出 01 後停（人工核對；--resume 續跑）。
- 兩模式：00_總區位圖 產出後停，等 --mode single|persite。

★ 2026-08-04 接上 ⓪a 免登入主路徑（使用者 2026-08-02 定案「⓪a 主路徑、①＋② 備選」）：
  `--fetch <清冊.csv|xlsx>`（欄位：縣市/鄉鎮(區)/段[/小段]/地號）會先跑
  cadastre_fetch 產 `outputs/<案>/cadastre/基地.shp`，再自動轉入 --shp 流程。
  ⓪a 失敗＝**硬停通知使用者**——備選路徑需本人登入，不自行改走（skill 規定）。

★ 2026-08-21 接上 ⓪a(反查)路徑：`--latlon <Google Maps 網址/短網址/'lat,lon'>`
  ＋`--case <案名>`（網址無法當資料夾名，故 --case 必填）會先跑 latlon_to_parcel
  （點→現行地段地號）產 `cadastre/基地.shp`，再自動轉入 --shp 流程。**單點單筆**；
  反查失敗（跨母號分割/重測改號需線上空間反查、或段名/縣市定位問題）＝硬停。
  詳見 skill `landmark-to-parcel`。
"""
import subprocess
import sys
import time
from pathlib import Path

from config_loader import load_paths, luimap_colortable_path, qgis_python_bat

REPO = Path(__file__).resolve().parent.parent


_P = load_paths(REPO)
# QGIS 執行檔由 qgis_prefix(...\apps\qgis-ltr) 推導出 ...\bin\python-qgis-ltr.bat
QGIS_BAT = qgis_python_bat(_P)
# 臨路評估 2026-07-26 起改為產線內建(scripts/road_access.py)，不再呼叫外部 fishpv。
# LUIMAP 色碼表已 vendor 進 config/；paths.yaml 的 luimap_colortable 仍可覆寫。
COLORTABLE = luimap_colortable_path(REPO, _P)
FROM_STEPS = ("meta", "match", "roadaccess", "overview", "extract", "maps")
# 舊步驟名別名(--from fishpv 仍可用)
FROM_ALIASES = {"fishpv": "roadaccess"}


def norm_section(seg, subseg="") -> str:
    """段＋小段 → cadastre_fetch 的 --section 字串（純函式，供單測）。
    '考試潭'/'鹿寮' → '考試潭段鹿寮小段'；'番社段'/'' → '番社段'。"""
    seg = str(seg or "").strip()
    sub = str(subseg or "").strip()
    if seg.lower() in ("nan", "none"):
        seg = ""
    if sub.lower() in ("nan", "none"):
        sub = ""
    if seg and not seg.endswith("段"):
        seg += "段"
    if sub:
        if not sub.endswith("小段"):
            sub += "小段"
        seg += sub
    return seg


def roster_groups(df) -> list[tuple]:
    """清冊 DataFrame → [(縣市, 鄉鎮, 段名, [地號…]), …]（純函式，供單測）。

    欄名容錯（⓪a 清冊與各式公文表格）：縣市/縣市名稱、鄉鎮區/鄉鎮市區/鄉鎮、
    段/段別/地段/地段名稱、小段（選填）、地號。縣市正規化 台→臺（對齊圖資鍵）。
    缺必要欄 → ValueError 點名缺哪些，不靜默猜。
    """
    cols = {c: str(c).strip() for c in df.columns}

    def pick(*names):
        for n in names:
            for c, cn in cols.items():
                if cn == n:
                    return c
        return None

    c_cty = pick("縣市", "縣市名稱")
    c_town = pick("鄉鎮區", "鄉鎮市區", "鄉鎮")
    c_seg = pick("段", "段別", "地段", "地段名稱")
    c_sub = pick("小段")
    c_no = pick("地號")
    missing = [n for n, c in (("縣市", c_cty), ("鄉鎮", c_town),
                              ("段", c_seg), ("地號", c_no)) if c is None]
    if missing:
        raise ValueError(f"清冊缺欄位：{missing}（需 縣市/鄉鎮(區)/段/地號，小段選填）")
    groups: dict = {}
    for _, r in df.iterrows():
        no = str(r[c_no]).strip()
        if not no or no.lower() == "nan":
            continue
        key = (str(r[c_cty]).strip().replace("台", "臺"), str(r[c_town]).strip(),
               norm_section(r[c_seg], r[c_sub] if c_sub is not None else ""))
        lst = groups.setdefault(key, [])
        if no not in lst:                      # 清冊重複列只取一次
            lst.append(no)
    return [(k[0], k[1], k[2], v) for k, v in groups.items()]


def fetch_roster(list_path: Path, case_dir: Path) -> Path:
    """⓪a 免登入取地籍（主路徑）：清冊 → `cadastre/基地.shp`。

    逐（縣市/鄉鎮/段）組呼叫 cadastre_fetch（界址環＋getLandInfo＋MOI_API_039 權屬），
    多組時合併。已有 基地.shp 就跳過（要重抓先刪 cadastre/）。
    🚧 閘門：shp 筆數＝清冊筆數；任一組失敗＝硬停——**備選的平台路徑需使用者本人
    登入，不可自行改走**（skill「⓪a 走不通時停下通知使用者」）。
    """
    import pandas as pd
    out_dir = case_dir / "cadastre"
    final = out_dir / "基地.shp"
    if final.exists():
        print(f"[run_case] ⓪a 已有 {final}，跳過重抓（要重抓請先刪 cadastre/）")
        return final
    lp = str(list_path).lower()
    df = (pd.read_excel(list_path) if lp.endswith((".xlsx", ".xls"))
          else pd.read_csv(list_path, encoding="utf-8-sig"))
    groups = roster_groups(df)
    total = sum(len(g[3]) for g in groups)
    print(f"[run_case] ⓪a 免登入取地籍：{len(groups)} 組（縣市/鄉鎮/段）、共 {total} 筆")
    parts = []
    for i, (cty, town, seg, nos) in enumerate(groups, 1):
        name = "基地" if len(groups) == 1 else f"基地_g{i:02d}"
        print(f"[run_case] ⓪a {i}/{len(groups)}：{cty}{town}{seg}　{len(nos)} 筆")
        r = subprocess.run([sys.executable, "-X", "utf8",
                            str(REPO / "scripts" / "cadastre_fetch.py"), "fetch",
                            "--county", cty, "--town", town, "--section", seg,
                            "--landnos", *nos, "--out", str(out_dir), "--name", name],
                           cwd=REPO)
        if r.returncode != 0:
            sys.exit(f"[run_case] ⓪a 取地籍失敗（{cty}{town}{seg}）——主路徑走不通，"
                     "停下通知使用者。備選＝平台路徑（①＋②）需本人登入，不自行改走。")
        parts.append(out_dir / f"{name}.shp")
    import geopandas as gpd
    if len(parts) > 1:
        merged = pd.concat([gpd.read_file(p) for p in parts], ignore_index=True)
        gpd.GeoDataFrame(merged, crs="EPSG:3826").to_file(final, encoding="utf-8")
    n = len(gpd.read_file(final))
    if n != total:
        sys.exit(f"[run_case] 🚧 ⓪a 閘門：shp {n} 筆 ≠ 清冊 {total} 筆——"
                 "查無／失敗的地號請先釐清（新舊地號？未登記 9xxx？）再續跑")
    print(f"[run_case] ⓪a 完成：{final}（{n} 筆，含使分/使類/登記面積/權屬推論）")

    return _neighbors_and_road(final, case_dir)


def _neighbors_and_road(final: Path, case_dir: Path) -> Path:
    """基地.shp 產出後的共用附帶步驟（fetch_roster／fetch_latlon 共用）：
    ①周邊地籍自動發現＋即時屬性回填 ②區位圖道路外擴基準。皆**非硬性閘門**，
    失敗只警告不中止（基地本身已成功取得，這兩者是加值）。"""
    out_dir = final.parent
    # ① 周邊地籍：評估範圍外擴 50m 內與地籍有交集者，作為後續分區/使用地編定的
    #    基準（取代舊快照/WMTS）。
    neigh = out_dir / "鄰地.shp"
    if not neigh.exists():
        r = subprocess.run([sys.executable, "-X", "utf8",
                            str(REPO / "scripts" / "cadastre_neighbors.py"),
                            "--joined", str(final), "--out", str(out_dir),
                            "--buffer", "50"], cwd=REPO)
        if r.returncode != 0:
            print("[run_case] ⚠ 周邊地籍自動發現失敗（不影響基地本身），"
                 "可事後手動補跑 scripts/cadastre_neighbors.py")
    else:
        print(f"[run_case] 周邊地籍已有 {neigh}，跳過重抓")

    # ② 區位圖道路外擴基準——供 make_locality_map.py 讀取。
    road_json = case_dir / "區位圖_道路基準.json"
    if not road_json.exists():
        r = subprocess.run([sys.executable, "-X", "utf8",
                            str(REPO / "scripts" / "road_network.py"),
                            "--joined", str(final), "--out", str(case_dir)],
                           cwd=REPO)
        if r.returncode != 0:
            print("[run_case] ⚠ 區位圖道路基準查詢失敗（不影響基地本身），"
                 "make_locality_map.py 會退回固定 1500m 舊制")
    else:
        print(f"[run_case] 區位圖道路基準已有 {road_json}，跳過重抓")
    return final


def fetch_latlon(spec: str, case_dir: Path) -> Path:
    """⓪a(反查)取地籍：Google Maps 網址／短網址／'lat,lon' → cadastre/基地.shp。

    走 latlon_to_parcel（點→現行地段地號：展開網址抓地標點→定縣市→本機候選→
    段名先驗→農業部即時確認→分割時收斂探測），再與 fetch_roster 同尾段接
    周邊地籍/道路基準。
    ⚠ 反查是**單點單筆**；多筆基地請走 --fetch 清冊或 --shp。
    🚧 反查失敗（跨母號分割/重測改號需線上空間反查、或段名/縣市定位問題）＝硬停。
    """
    out_dir = case_dir / "cadastre"
    final = out_dir / "基地.shp"
    if final.exists():
        print(f"[run_case] ⓪a(反查) 已有 {final}，跳過反查（要重抓請先刪 cadastre/）")
        return _neighbors_and_road(final, case_dir)
    print(f"[run_case] ⓪a(反查)取地籍：{spec}")
    r = subprocess.run([sys.executable, "-X", "utf8",
                        str(REPO / "scripts" / "latlon_to_parcel.py"),
                        spec, "--out", str(out_dir), "--name", "基地"], cwd=REPO)
    if r.returncode != 0:
        sys.exit("[run_case] ⓪a(反查)取地籍失敗——見上方訊息。跨母號分割／重測改號"
                 "需線上空間反查（未接掛）；或段名／縣市定位問題，請人工釐清後再續跑。")
    print(f"[run_case] ⓪a(反查) 完成：{final}")
    return _neighbors_and_road(final, case_dir)


def plan_steps(mode_shp: bool, resume: bool, chosen_mode, artifacts: dict) -> list:
    """決定本次要執行的步驟序列（純函式，依 spec §4/§5.1）。

    trust-shp 模式（spec §5.1）：基地層幾何與屬性直接取自 shp、**不比對官方 FID**，
    故無 match 步驟；出圖以 make_maps --trust-shp 直讀 shp（trust_extract_* + maps）。
    """
    if mode_shp:
        if chosen_mode:
            steps = []
            if not artifacts.get("roadaccess"):
                steps += ["roadaccess", "digest"]
            # trust 出圖：single 全歸 001；persite 依 sitemap 每宗基地一組
            steps.append("trust_extract_persite" if chosen_mode == "persite"
                         else "trust_extract_single")
            steps.append("maps")
            return steps
        # 首跑：解析留痕(meta)→臨路(fishpv/digest)→總區位圖，停等選模式；不比對、不抽圖層
        return ["meta", "roadaccess", "digest", "overview", "STOP_MODE"]
    # 清冊模式
    if not resume and not chosen_mode:
        return ["parse", "STOP_CHECK"]
    if chosen_mode:
        return ["extract", "maps"] if artifacts.get("roadaccess") else \
               ["match", "extract", "roadaccess", "digest", "maps"]
    steps = ["match"]
    if artifacts.get("03_nonempty"):
        steps += ["landno", "apply", "match2"]
    steps += ["extract", "roadaccess", "digest", "overview", "STOP_MODE"]
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
    "roadaccess", "digest", "overview", "maps", "STOP_MODE", "STOP_CHECK",
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
    """成果.xlsx → sites_status.csv（給 make_maps）；sitemap.csv 以 案場定義/ 為權威。

    2026-07-16 縮編定案：案場定義（聚類＋sitemap）由產線的 build_sites_sitemap.py
    先做，fishpv 只當臨路計算器。故 sitemap.csv 優先直接複製 案場定義/sitemap.csv
    （帶段欄）。舊攤平路徑（由成果.xlsx 地號清單反推、無段欄）遇跨案場重複地號
    會讓 load_sitemap 分派失敗、單筆案場無聲消失（東石 S012 實錯），一律硬停。"""
    import shutil

    import pandas as pd
    xlsx = case_dir / "臨路評估" / "成果.xlsx"
    df = pd.read_excel(xlsx, dtype=str)
    required = ["site_id", "臨路狀態", "臨路距離_m", "臨路註記", "地號清單"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SystemExit(f"[run_case] 成果.xlsx 缺欄位：{missing}")
    df[["site_id", "臨路狀態", "臨路距離_m", "臨路註記"]].to_csv(
        case_dir / "臨路評估" / "sites_status.csv", index=False, encoding="utf-8-sig")

    auth = case_dir / "案場定義" / "sitemap.csv"
    if auth.exists():
        shutil.copy2(auth, case_dir / "臨路評估" / "sitemap.csv")
        print("digest：sites_status.csv；sitemap.csv ← 案場定義/（權威，帶段欄）")
        return
    rows = []
    for _, r in df.iterrows():
        for no in str(r["地號清單"]).split(";"):
            no = no.strip()
            if not no or no.lower() == "nan":
                continue
            rows.append({"地號": no, "案場ID": r["site_id"],
                         "案場名稱": f"{r['site_id']}宗基地"})
    dup = {no for no in {r["地號"] for r in rows}
           if sum(1 for r in rows if r["地號"] == no) > 1}
    if dup:
        raise SystemExit(
            f"[run_case] 無 案場定義/sitemap.csv 且成果.xlsx 反推的 sitemap 有跨案場"
            f"重複地號 {sorted(dup)[:6]}…（無段欄無法分派）。"
            f"請先跑 scripts/build_sites_sitemap.py --joined <面積join.shp> "
            f"--out-dir {case_dir / '案場定義'} 再重來")
    pd.DataFrame(rows).to_csv(case_dir / "臨路評估" / "sitemap.csv",
                              index=False, encoding="utf-8-sig")
    print(f"digest：sites_status.csv + sitemap.csv（{len(rows)} 地號，無段欄舊路徑）")


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
    elif from_step == "roadaccess":
        if mode_shp:
            need(shp, "請確認 --shp 路徑")
        else:
            need(case_dir / "圖層.gpkg",
                 "清冊模式臨路評估吃 圖層.gpkg 基地層，請先 --from extract（或完整 --resume）")
    elif from_step == "overview":
        need(fp / "sites.shp", "總區位圖需宗基地聚合結果，請先 --from roadaccess")
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
                     "trust persite 出圖需 臨路評估/sitemap.csv 或 成果.xlsx，請先 --from roadaccess")
        else:
            need(case_dir / "02_比對結果.xlsx",
                 "maps 需讀 02 建案場清單，請先 --from match 完成比對")
    return errs


def assessment_followups(case_dir: Path) -> list[str]:
    """依案件既有產物列出基礎製圖後仍待完成的評估階段。

    這裡只檢查跨案共通成果，不替代各案型 YAML、人工查證或簡報 QA。接受歷史案件
    曾使用的替代檔名，避免把已完成案件誤報為缺件。純檔案存在性檢查，不寫入案件。
    """
    checks = [
        ("環境敏感套疊（ngis_sensitive.py）",
         ("環敏NGIS.json", "環敏NGIS.csv", "環境敏感套疊.csv")),
        ("國土功能分區逐筆核對（nluz_landzone.py）",
         ("國土功能分區_逐筆核對.csv", "國土功能分區.csv")),
        ("禁止事由檢核（check_zones.py）",
         ("禁止事由檢核.json", "禁止事由檢核.txt")),
        ("逐案場統計（persite_stats.py）", ("逐案場統計.json",)),
        ("案件狀態紀錄（STATE.md）", ("STATE.md",)),
    ]
    pending = [label for label, names in checks
               if not any((case_dir / name).exists() for name in names)]
    if not any(p.suffix.lower() == ".pptx" for p in case_dir.glob("*.pptx")):
        pending.append("評估簡報與人工 QA")
    return pending


def main():
    args = sys.argv[1:]
    shp = lst = fetch = latlon = case = chosen = from_step = None
    resume = False
    passthru = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--shp":
            shp = args[i + 1]; i += 2
        elif a == "--fetch":               # ⓪a 免登入主路徑：清冊 → cadastre/基地.shp → --shp 流程
            fetch = args[i + 1]; i += 2
        elif a == "--latlon":              # ⓪a(反查)：Google Maps 網址／'lat,lon' → cadastre/基地.shp → --shp 流程
            latlon = args[i + 1]; i += 2
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
        elif a in ("--no-qgz", "--mem", "--basic",
                   # 2026-07-29 新增：底圖鎖與圖面通則（見 make_maps 說明）
                   "--reuse-basemap", "--no-loc-parcel"):
            passthru.append(a); i += 1
        elif a == "--layout":                      # 透傳 make_maps(maxmap/compact)
            passthru += [a, args[i + 1]]; i += 2
        elif a.startswith(("--layout=", "--themes=", "--cases=", "--exclude=",
                           "--road-margin=", "--basemap-dir=")):
            passthru.append(a); i += 1             # 透傳 make_maps
        else:
            sys.exit(f"未知參數 {a}")
    if not (shp or lst or fetch or latlon):
        sys.exit("需 --shp <基地.shp>、--fetch <清冊(⓪a 免登入)>、"
                 "--latlon <Google Maps 網址/'lat,lon'(⓪a(反查))> 或 --list <清冊(平台比對)>")
    if sum(bool(x) for x in (shp, fetch, latlon, lst)) > 1:
        sys.exit("--shp／--fetch／--latlon／--list 擇一"
                 "（--fetch、--latlon 抓完會自動轉入 --shp 流程）")
    if chosen not in (None, "single", "persite"):
        sys.exit("--mode 需為 single|persite")
    from_step = FROM_ALIASES.get(from_step, from_step)   # 舊名 fishpv → roadaccess
    if from_step is not None and from_step not in FROM_STEPS:
        sys.exit(f"--from 需為 {'|'.join(FROM_STEPS)} 之一")
    if not case:
        if latlon:
            sys.exit("--latlon 請一併給 --case <案名>（網址無法當資料夾名）")
        case = Path(shp or lst or fetch).stem
    case_dir = REPO / "outputs" / case
    case_dir.mkdir(parents=True, exist_ok=True)
    if fetch:
        # ⓪a 前置：抓完即為 trust-shp 輸入，後續與 --shp 完全同流程
        shp = str(fetch_roster(Path(fetch), case_dir))
    if latlon:
        # ⓪a(反查)前置：座標/網址→基地.shp，後續與 --shp 完全同流程（單點單筆）
        shp = str(fetch_latlon(latlon, case_dir))
    mode_shp = shp is not None
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
                "roadaccess": (fp / "成果.xlsx").exists(),
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
        elif s == "roadaccess":
            # 2026-07-26 內建化：不再呼叫外部 fishpv CLI，改用 scripts/road_access.py
            # (自 fishpv 忠實移植，東石 57 案場逐案比對 0 差異)。案場定義先由
            # build_sites_sitemap.py 產權威 sitemap(帶段欄)，臨路再依該分組計算——
            # 案場幾何與 make_maps 出圖用的完全一致。
            cad = shp if mode_shp else str(case_dir / "圖層.gpkg")
            smap = case_dir / "案場定義" / "sitemap.csv"
            ok = True
            if not smap.exists():
                ok = _run([QGIS_BAT, REPO / "scripts" / "build_sites_sitemap.py",
                           "--joined", cad, "--out-dir", case_dir / "案場定義"],
                          label="案場定義(聚類→權威 sitemap)", fatal=False)
            if ok:
                ok = _run([QGIS_BAT, REPO / "scripts" / "road_access.py",
                           "--joined", cad, "--sitemap", smap,
                           "--out", fp, "--colortable", COLORTABLE],
                          label="臨路評估(內建)", fatal=False)
            if not ok:
                # spec §6：臨路評估失敗 → 圖5/6 降級、1-4 照出，不中斷整條流程
                print("[run_case] 警告：臨路評估失敗——後續降級續跑："
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
        print("\n[run_case] 基礎圖資與製圖階段完成")
        pending = assessment_followups(case_dir)
        if pending:
            print("[run_case] 整案評估尚待：")
            for item in pending:
                print(f"  - {item}")
            print("[run_case] 上述項目及各案型人工查證完成後，才可將案件標記為結案。")
        else:
            print("[run_case] 共通成果均已存在；仍請依案型完成內容核對與人工 QA。")


if __name__ == "__main__":
    main()
