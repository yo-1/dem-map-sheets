# 既知不具合の再現テスト（通常CIから分離）

このフォルダのテストは、**未修正の不具合を再現するため、現状のコードでは失敗します**。
通常CI（`dem_map_sheets/tests`）には含めません。本体修正後に合格することを確認し、
合格した項目は `dem_map_sheets/tests/` へ移して回帰テストにします。

```bash
# リポジトリ直下で実行（GDAL(osgeo)が使えるPythonで）
PYTHONPATH=. python3 -m unittest discover -s validation/known_defects -v
```

- ハングや異常終了を起こす項目は**別プロセス＋タイムアウト**で実行します（スイート自体は止まりません）。
- 環境にGDALが無い場合はスキップします。
- 各テストIDと概要・影響は `validation/INPUT_FORMAT_TEST_MATRIX.md` の「未修正不具合」表を参照してください。

| ID | 内容 | 状態（本ブランチ基準 `codex/dem-v0.2.1-rc4`） |
|----|------|------|
| KD-1 | エンジンAPIが不正な `cell_size`（負値・NaN）を拒否せず、GDALがハング／プロセスabort | 失敗（未修正） |
| KD-2 | `validate_input()` が `run()` から呼ばれず、不正オプションが素通り | 失敗（未修正） |
| KD-3 | `text_grid` に画素数上限が無い（M-2） | 失敗（M-2修正で合格見込み） |
| KD-4 | LEMの `dx`/`dy` が 0 以下でも検証されない（座標スケール明示時） | 失敗（未修正） |
| KD-5 | 出力セルより小さい極小ラスタで `gdal.Warp` がハング（GDAL 3.8.4で確認） | 失敗（未修正・GDAL依存の可能性） |
| KD-6 | GSI XMLの数値不正メッセージにタプル位置が無い（品質） | 失敗（未修正・低） |
