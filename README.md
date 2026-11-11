# DEM Map Sheets

QGIS 3.44〜3.99向けのDEM作成・統合・TerrainRGB XYZプラグイン。GPL-3.0-only。

現在版：0.2.1。配布ZIPは GitHub Release v0.2.1 に添付されています。

操作説明は dem_map_sheets/README.md、検証状況は validation/TEST_RECORD.md を参照してください。

## NoData仕様（次期版で変更）

出力DEMのNoData値は設定可能になりました（パラメータ `OUTPUT_NODATA`／設定キー `output_nodata`、既定 -9999、Float32で正確に表せる有限値のみ）。入力側の欠測判定は入力自身の定義（バンドNoData、source_nodata、形式定義）のみで行い、値の推測はしません。NaN・±inf セルは欠測として扱われ、出力NoData値に変換されます。出力NoData値と一致する有効セルが入力にある場合は、件数を数えて警告し run.json に記録します。詳細は dem_map_sheets/README_ja.md の「重要な注意」を参照してください。

合成データ検証は GitHub Actions（Synthetic DEM validation）で push／PR のたびに実行されており、GDAL 回転ラスタ等の回帰テスト（validation/test_gdal_edges.py）も含みます。Windows 実機での検証結果も validation/TEST_RECORD.md に記録しています。
