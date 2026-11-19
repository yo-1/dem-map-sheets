# run.json 仕様（schema_version 1）

機械可読な定義は `schema/run.schema.json`（JSON Schema 2020-12）。

## 共通キー（全 run.json に必須）
| キー | 型 | 説明 |
|---|---|---|
| `schema_version` | integer | 現在 `1`。キーが無い run.json は旧形式（0 相当） |
| `qa_status` | string | 常に `"not_evaluated"`。納品合否の自動判定は行わない |
| `status` | string | `completed` / `failed` / `cancelled`（従来どおり） |
| `merge_priority` | string | `last` / `first` / `auto` |

`status` は処理の終了状態、`qa_status` は品質評価の状態で、互いに独立。
全NoData出力（`warnings` に `all_nodata_output`）でも `status=completed`・`qa_status=not_evaluated` のまま。

## completed のとき必須
`input_type, source_count, warnings, source_dems, merged_dem, target_crs, cell_size,
vertical_reference, vertical_conversion, output_nodata, work_retained`。
任意: `priority_selection, input_report, nodata_value_collisions, source_datum_label,
proj_epsg_version, terrainrgb_xyz`。

## failed / cancelled のとき
必須: `error`。任意: `failure_detail, work_dir, work_retained, work_removed_bytes,
work_retained_bytes, outputs_present`。
（algorithm 層の補正経路では最小形 `status, error, merge_priority` のみとなる場合がある。）

## 互換規則
- 既存キー・値は変更しない。追加のみ。読み手は未知キーを無視すること。
- `schema_version` を上げるのは既存キーの意味・型を変える場合のみ。

## 明示mask安全停止（Issue #29 A案）

`MaskSemanticsConflictError`は`status=failed`、`qa_status=not_evaluated`、`schema_version=1`を維持し、
`mask_safety_stop=true`と`failure_detail[]`を追加する。既存schemaの`failure_detail`は拡張可能なため、schema版・旧roleの意味は変更しない。

| キー | 内容 |
|---|---|
| `role` | `input_mask`（既存の`output`/`input_declared`と区別） |
| `reason` | `valid_near_effective_nodata`、`explicit_invalid_may_become_valid`、または比較不能・読取異常の理由 |
| `stage` | `normalize_preflight` / `passthrough_preflight` / `materialize_preflight` |
| `band, mask_flags` | 検査したバンド番号とGDAL mask flag。取得不能時のflagはnull |
| `effective_source_nodata, effective_source_nodata_origin` | 実際にその経路で適用する欠測値と由来（band/override/forest_default） |
| `declared_nodata` | 元バンドのNoData。非有限値はnull、別の`*_repr`文字列で表す |
| `count, count_kind` | 消失候補は`detected_candidates`、復活リスク候補は`potential`。未評価は0/`not_evaluated` |
| `values, positions` | 値のサンプル最大16種、位置サンプル最大5件（0始まりrow/colとセル中心座標）。countはサンプル数ではなく総数 |
| `gdal_version` | 検査時のGDAL版 |

検査は512×512以下の窓で行う。数値専用VRTの生値と原バンドが一致することも検証する。
出力側衝突として扱うセルを重複して`input_mask`に数えない。
新maskエラーは最初の危険入力で即時停止するため、全入力の走査完了や全種別の集約を保証しない。
`SKIP_INVALID=true`でも握り潰さない。原入力は変更しない。
この安全停止に限りKEEP_WORKにかかわらず作業領域を診断情報へ整理し、生成済みの入力別DEM等も削除する。
削除失敗時はfailedのままログを出し、残った出力は`outputs_present`へ記録する。
実測の記録例は`validation/issue29_failure_example.json`（一時パスだけを置換）を参照。

## provenance（来歴。Issue #16 PR-3）
`provenance` は completed では必須、failed/cancelled では任意（最小形の補正経路では無い場合がある）。

| キー | 説明 |
|---|---|
| `status` | `complete`（全ハッシュ取得）/ `incomplete`（一部取得不能）/ `partial`（失敗・中断のため未計算）/ `skipped`（`record_hashes=False`） |
| `hash_algorithm` | `sha256` |
| `inputs[]` | `index`, `name`（basename のみ）, `bytes`, `sha256`, `sha256_status`（`ok`/`error`/`not_computed`）, `error`（例外種別のみ） |
| `outputs[]` | `path`（run ディレクトリからの相対）, `bytes`, `sha256`, `sha256_status`, `error` |
| `environment` | 取得できた版のみ: `python, plugin, gdal, proj, numpy, qgis, pdal, commit`。取得不能な項目はキーごと省略 |

### 挙動
- **成功時のみ**ハッシュを計算。入力は処理完了後（成功確定時）に計算するため、処理中に入力が差し替えられた場合は処理時の内容と一致しない可能性がある。
- **ハッシュ失敗**（読取エラー・ファイル消失等）は run を失敗にしない。該当ファイルは `sha256=null`・`sha256_status=error`・`error=<例外種別>`、`provenance.status=incomplete`、`warnings` に `provenance_incomplete`。
- **失敗・中断時**はハッシュを計算しない（`partial`）。入力は名前・サイズのみ。生成済み出力は従来の `outputs_present`（パスとサイズ）で確認する。
- **キャンセル**はハッシュ計算中も尊重し、`status=cancelled` で記録する。
- **`record_hashes=false`**（設定キー）で省略可能（`skipped`）。サイズは記録する。
- **漏えい対策**: 入力は basename のみ（同名は `index` で区別）、出力は相対パス。環境変数値・ホスト名・ユーザー名・実行ファイルのパスは記録しない。PDAL は `pdal --version` の出力から `pdal X.Y.Z` の行だけを抽出して記録する（Windows の先頭罫線等は無視。特定できなければ `pdal` キーを省略）。

### `complete` の意味（重要）
`provenance.status=complete` は、**記録対象の範囲内で**全ハッシュが取得できたことだけを意味する。LEM/TFW 等の同梱ファイル、ZIP 展開物、TerrainRGB タイル本体は対象外のため、**納品物一式の完全照合ではない**。納品判定（`qa_status`）とも無関係である。
`run()` 後に追加される成果物（TerrainRGB の `xyz.json`）のハッシュに失敗した場合も、`provenance.status=incomplete` と `warnings` の `provenance_incomplete` を記録する。

### 初版の制約
- 対象は discover 後の主入力ファイルのみ。LEM と TFW 等の同梱ファイル、ZIP の展開物は対象外（ZIP は ZIP 自体）。
- TerrainRGB はタイル本体をハッシュせず `xyz.json` のみ。
- `run.json` 自身のハッシュは含めない。`commit` は `build_info.json` がある場合のみ。
