# NoData修正（Issue #9 / PR #10）の検証手順 ― Windows 11 / QGIS 3.44 実機向け

- 対象：作業ブランチ `claude/nodata-fix-issue9`（基点 `2849ef4`＝`muse/nodata-spec`）。バージョン表示は 0.2.1 のまま（版番号は未決定）。
- 実施主体：claude.ai（実機）。**このファイルは自動テストの実施結果ではなく、実機で行う手順です。未実施の項目を「合格」としないでください。**
- 先に必ず行うこと：受領したZIPのサイズとSHA-256（全64桁）を、PC側で独立に再計算して報告書記載の値と照合する。不一致なら導入しない。

## 0. 修正の要点（期待される挙動）

| 区分 | 修正前（2849ef4） | 修正後 |
|---|---|---|
| 点群 Z=-9999（出力NoData -9999）。IDW・平均 | 正常終了し周辺セルを汚染（実測 min -4615／-3007） | 補間前にエラー停止、`run.json` status=failed |
| 点群 Z=-9999。最低値 | 停止 | 停止（変わらず） |
| LAS Z=-999999 | **無通知で欠測化**（テキストは停止＝不一致） | テキストと同じく停止（種別 internal） |
| `-9999.002` など許容幅内の近傍値 | 周辺セルが消える（無通知） | 停止（GDALがNoDataと読む範囲） |
| 入力NoData指定（NODATA）＝その値 | LASでは無視 | その値の点を欠測として補間から除外し、正常終了 |

停止時の `run.json` には `status`、`error`（日本語・英語併記、入力名・値・点数・対処）、`failure_detail`（`source`／`value`／`role`／`count`）が入ります。

## 1. 試験データの作成

PDAL（conda環境の `pdal.exe`。`pdal_wrench.exe` や `python.exe` ではない）が使えるPCで：

```powershell
# PowerShell。リポジトリ（または展開したZIP）の validation フォルダーから
python validation\make_nodata_fixtures.py --pdal "C:\path\to\pdal.exe" --out "$env:TEMP\nodata_fixtures"
```

`--out` は存在しないフォルダーを指定（既存は拒否します）。作成物は `nodata_<csv|las|laz>_<normal|m9999|m999999|near9999|clean>`（15点）と `日本語フォルダ\点群_normal.las`。
CSV（テキスト点）はPDALが無くても作れます（`--pdal` を省略）。座標は EPSG:6677、10 m 間隔の地表面16点＋(5,5)の追加1点。

## 2. QGIS Processing の設定（共通）

入力形式：テキスト点は「標高テキスト」＋「不規則点（PDAL補間）」、点群は「レーザ点群」。入力CRS `EPSG:6677`、出力CRS `EPSG:6677`、出力セルサイズ `10`、点群DEM補間半径 `15`、TerrainRGB はオフ、PDAL 実行ファイルを指定。点群補間は IDW／平均／最低値の3通り。

## 3. 試験表（Processing GUI。結果を run.json とログで記録）

| ID | 入力 | 方式 | 入力NoData | 出力NoData | 期待 |
|---|---|---|---|---|---|
| V-1 | csv_m9999 | IDW・平均・最低値 | 空欄 | -9999 | 3方式とも停止。値 -9999・種別 output・点数 1 |
| V-2 | las_m9999 / laz_m9999 | IDW・平均・最低値 | 空欄 | -9999 | 6通りとも停止（修正前は IDW／平均が完了した） |
| V-3 | csv_m999999 / las_m999999 / laz_m999999 | 3方式 | 空欄 | -9999 | 全て停止。種別 internal。**LASがテキストと一致すること** |
| V-4 | las_near9999 | 3方式 | 空欄 | -9999 | 停止（-9999.002 は許容幅内） |
| V-5 | *_normal（csv/las/laz） | 3方式 | 空欄 | -9999 | 全て完了。有効セルの最小値が約100（汚染なし）。GeoTIFFのNoDataは -9999 |
| V-6 | las_m9999, las_m999999, csv_m9999, csv_m999999 | IDW | **その値**（-9999／-999999） | -9999 | 完了。DEMが `*_clean` の結果と一致（点は欠測として除外） |
| V-7 | las_m9999 | IDW | -999999（もう一方の値） | -9999 | 停止（-9999 の点は宣言されていない） |
| V-8 | las_m9999 | IDW | 空欄 | -32768 | **完了**（-9999 は有効標高）。GeoTIFF NoData=-32768、深い負値セルが出る |
| V-9 | las_m999999 | IDW | 空欄 | -999999 | 停止（種別 internal の1件のみ） |
| V-10 | `日本語フォルダ\点群_normal.las`、出力先も日本語フォルダー | IDW | 空欄 | -9999 | 完了。実行後に `%TEMP%`（または DEM_MAP_SHEETS_TEMP）に `dem_map_sheets_pdal_*` が残らない |
| V-11 | V-2 の失敗後 | – | – | – | 出力フォルダーの `work` に `.tif`/`.laz` が残らず、`*.log`/`*.json`（`info_*.json`、`nodatascan_*`）は残る（run.json の `work_retained`=diagnostics_only） |
| V-12 | 修正前の回帰確認：D-1（出力先パス長のエラー文言）、D-2（SKIP_INVALID）、E-1（EPSG:6677→6678、EPSG:4326 の軸順） | – | – | – | 従来どおり。これらは自動テスト（実PDAL含む）でも確認済みだが、実機での確認は未実施 |

注意：止まるのが正しいケースで GeoTIFF が出力されていたら不合格です（`02_merged_dem.tif` が存在しないこと）。

## 4. 到達しにくい経路の試験方法（個別番号N-xの対応は未確認）

Issue #9 の N-1〜N-12 の個別定義は Drive の検証計画にあり、この作業環境からは読めないため、**番号ごとの対応付けは未実施**です。代わりに、GUIから到達できない／しにくい経路の実行手順を示します。

- **パススルー経路**（`normalize_rasters=False` かつ `input_crs` 空）：`algorithm.py` は常に `normalize_rasters=True` のためGUIからは到達できません。QGISのPythonコンソールで：

```python
from osgeo import gdal, osr
from dem_map_sheets.engine.dem_pipeline import run
gdal.UseExceptions()
cfg = dict(inputs=[r"C:\data\dem.tif"], input_type="raster", normalize_rasters=False, input_crs="",
           target_crs="EPSG:6677", cell_size=10.0, source_nodata=None, output_nodata=-9999.0,
           max_pixels=25000000)
print(run(cfg, r"C:\data\out_passthrough", gdal, osr)["output_nodata"])
```

- **API直呼びでの点群**：同じ `run()` に `input_type="lidar"`、`pdal_path`、`point_method`、`source_nodata` を渡せば、GUIと同じ検査を通ります（V-1〜V-9 の再現・自動化に使えます）。
- **ディスク満杯（W-8）**は実施しない方針。

## 5. 記録すること

各試験：試験ID、入力、設定値、結果（完了／停止）、run.json の `status`・`failure_detail`、エラー文言、GeoTIFFのNoData値、`dem_map_sheets_pdal_*` の残存有無、実行していない項目とその理由。
