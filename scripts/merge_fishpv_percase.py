# -*- coding: utf-8 -*-
"""合併逐案場 fishpv 產物 → outputs/20260716清潔隊/臨路評估/
sites.shp(site_id 重編 <cid>-<n>) + 成果.xlsx + sites_status.csv + features.geojson"""
import re
from pathlib import Path
import pandas as pd
import geopandas as gpd

BASE = Path(r'C:\Users\user\Documents\Claude\Projects\大量圖資轉換工具\outputs\20260716清潔隊')
PER = BASE / '臨路評估_percase'
OUT = BASE / '臨路評估'
OUT.mkdir(exist_ok=True)

sites_all, res_all, feat_all = [], [], []
fails = []
for d in sorted(PER.glob('out_*'), key=lambda p: int(p.name.split('_')[1])):
    cid = d.name.split('_')[1]
    if not (d / '成果.xlsx').exists():
        fails.append(cid)
        continue
    res = pd.read_excel(d / '成果.xlsx', dtype=str)
    g = gpd.read_file(d / 'sites.shp')
    # site_id 重編:S001→<cid>-1(圖上 label 短、跨案場唯一)
    ids = sorted(set(res['site_id']))
    remap = {sid: f'{cid}-{i+1}' for i, sid in enumerate(ids)}
    res['site_id'] = res['site_id'].map(remap)
    g['site_id'] = g['site_id'].astype(str).map(remap)
    res.insert(0, '案場ID', cid)
    sites_all.append(g)
    res_all.append(res)
    fj = d / 'features.geojson'
    if fj.exists():
        try:
            feat_all.append(gpd.read_file(fj))
        except Exception as e:
            print(f'[warn] {cid} features.geojson 讀取失敗: {e}')

res = pd.concat(res_all, ignore_index=True)
sites = pd.concat(sites_all, ignore_index=True)
sites = gpd.GeoDataFrame(sites, crs=sites_all[0].crs)
sites.to_file(OUT / 'sites.shp', encoding='utf-8')
res.to_excel(OUT / '成果.xlsx', index=False)
res[['site_id', '臨路狀態', '臨路距離_m', '臨路註記']].to_csv(
    OUT / 'sites_status.csv', index=False, encoding='utf-8-sig')
if feat_all:
    feats = pd.concat(feat_all, ignore_index=True)
    feats = gpd.GeoDataFrame(feats, crs=feat_all[0].crs)
    feats.to_file(OUT / 'features.geojson', driver='GeoJSON')
print(f'合併完成: {len(res)} 宗基地 / {len(sites)} 幾何 / '
      f'{len(feats) if feat_all else 0} 地物 | 失敗案場: {fails or "無"}')
