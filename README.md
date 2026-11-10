# DEM Map Sheets

QGIS 3.44向けDEM作成・統合・TerrainRGB XYZプラグイン。GPL-3.0-only。

現在版：0.2.1-rc4（試験版）。操作説明はdem_map_sheets/README.md、検証状況はvalidation/TEST_RECORD.md。

Windows実機確認前にmainへマージしない。

再試験手順：validation/WINDOWS_RC4.md。合成データ検証は GitHub Actions（Synthetic DEM validation）で push／PR のたびに実行されており、2026-11-09 から GDAL 回転ラスタ等の回帰テスト（validation/test_gdal_edges.py）も含む。
