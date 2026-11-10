# DEM Map Sheets

QGIS 3.44〜3.99向けのDEM作成・統合・TerrainRGB XYZプラグイン。GPL-3.0-only。

現在版：0.2.1。配布ZIPは GitHub Release v0.2.1 に添付されています。

操作説明は dem_map_sheets/README.md、検証状況は validation/TEST_RECORD.md を参照してください。

合成データ検証は GitHub Actions（Synthetic DEM validation）で push／PR のたびに実行されており、GDAL 回転ラスタ等の回帰テスト（validation/test_gdal_edges.py）も含みます。Windows 実機での検証結果も validation/TEST_RECORD.md に記録しています。
