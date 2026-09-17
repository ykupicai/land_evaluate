# 土地評估工作流（land_evaluate）

土地清冊／基地 shp／座標 → 地籍與圖資檢核 → 主題圖與 QGIS 專案 → 案件評估簡報。
一般出七張基本圖；全都市計畫案自動略過使用地編定圖。法規判讀、正式查復與交付 QA
仍需承辦者確認，程式產出不等於案件結案。

## 從這裡開始

- 換機器：[環境建置與驗證](docs/環境建置與驗證.md)、[新同事上手指南](docs/新同事上手指南.md)
- 跑案件：[免 AI 批次操作手冊](docs/免AI批次流程操作手冊.md)
- 找程式：[腳本索引](scripts/README.md)
- 看進度：[案件狀態索引](docs/案件狀態索引.md)
- 維護交接：[CLAUDE.md](CLAUDE.md)、[本次整理與驗證紀錄](docs/2026-09-16_專案整理與驗證.md)

## 最小健檢（唯讀）

```powershell
python -X utf8 scripts/doctor.py --qgis
python -X utf8 -m pytest tests/ -q
python -X utf8 scripts/case_inventory.py
```

程式碼、設定與測試進 Git；大型圖資、案件成果、外部引擎與機器專屬設定不在本庫。
只 clone 程式碼不足以執行整條產線。GitHub 可用於程式備份／協作，不是日常出圖的必要服務。
