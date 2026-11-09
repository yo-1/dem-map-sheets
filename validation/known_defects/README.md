# 既知不具合の再現テスト（通常CIから分離）

このフォルダのテストは、**発見当時の未修正不具合を再現する**ために作られました（下記のとおり全項目解消済み）。
通常CI（`dem_map_sheets/tests`）には含めません。本体修正後に合格することを確認し、
合格した項目は `dem_map_sheets/tests/` へ移して回帰テストにします。

```bash
# リポジトリ直下で実行（GDAL(osgeo)が使えるPythonで）
PYTHONPATH=. python3 -m unittest discover -s validation/known_defects -v
```

- ハングや異常終了を起こす項目は**別プロセス＋タイムアウト**で実行します（スイート自体は止まりません）。
- 環境にGDALが無い場合はスキップします。
- 各テストIDと概要・影響は `validation/INPUT_FORMAT_TEST_MATRIX.md` の「未修正不具合」表を参照してください。

| ID | 内容 | 状態（2026-11-09、`muse/pre-release-fixes`） |
|----|------|------|
| KD-1 | エンジンAPIが不正な `cell_size`（負値・NaN）を拒否せず、GDALがハング／プロセスabort | **解消**（run() 入口検証。回帰は dem_map_sheets/tests/test_run_validation.py） |
| KD-2 | `validate_input()` が `run()` から呼ばれず、不正オプションが素通り | **解消**（run() 入口で接続。同上） |
| KD-3 | `text_grid` に画素数上限が無い（M-2） | **解消**（test_text_grid_limits.py） |
| KD-4 | LEMの `dx`/`dy` が 0 以下でも検証されない（座標スケール明示時） | **解消**（test_input_forest_lem.py の LemSpacingValidationTests） |
| KD-5 | 出力セルより小さい極小ラスタで `gdal.Warp` がハング（GDAL 3.8.4で確認） | **GDAL 3.8.4 のみの環境不具合**。GDAL 3.10.3 / 3.13.3 では再現せず（CI の gdal310 ジョブで継続確認） |
| KD-6 | GSI XMLの数値不正メッセージにタプル位置が無い（品質） | **解消**（test_gsi_safety.py の TupleErrorDetailTests） |

本スイートは全項目が合格する状態が正常になった（KD-5 は GDAL 3.8.4 環境でのみ
失敗しうる）。回帰の正本は `dem_map_sheets/tests/` 側のテストであり、本フォルダは
不具合の再現記録として残す。
