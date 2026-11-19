# Issue #29 A案 実装・受入対応表

2026-11-20 / Codex → ChatGPT統括。基準 main `e791d47fbd99c04f36e1a47fd5c0266e316a3be0`。
承認: https://github.com/yo-1/dem-map-sheets/issues/29#issuecomment-7015377231

## 実装と適用範囲

明示マスクを読み取り専用で検査し、消失候補 L と復活リスク候補 R を別理由で停止する。
数値maskは実行中のGDALのメタデータ専用VRTで評価し、値が原入力と一致することも窓ごとに確認する。
これはB案の変換ではない。原入力・NoDataタグ・外部.mskは変更しない。
Rは潜在的な候補数であり、実出力の復活件数ではない。
normalize/pre-Warp、passthrough、materializeに適用し、専用例外はSKIP_INVALIDを迂回する。
GMF_ALL_VALID/GMF_NODATAの従来経路、出力側衝突と次順位補完を維持する。
入力全件の検査完了を保証する集約エラーではない。出力衝突セルをinput_maskへ二重計上しない。

新規 `engine/mask_guard.py` は512×512窓、値サンプル16種・位置5件を上限とし、候補総数は省略しない。
明示maskで確認するdtypeはByte/Int16/UInt16/Float32。その他、scale/offset、未対応flags、読取・比較不能は件数未評価の専用停止。
失敗時はKEEP_WORKによらず同じ実行の生成DEMを削除する。OSロック等で削除できない場合はfailedを維持し、残存物をoutputs_presentと標準エラーへ記録する。
schema_version=1を維持。既存schemaはfailure_detailを開いたオブジェクトとして許容するため変更不要。

## 受入対応

テスト略記 E = `dem_map_sheets.tests.test_explicit_mask_semantics.ExplicitMaskTests`。

| ID | 証跡・対応 | 確認状態 |
|---|---|---|
| M01 | E.test_internal_and_external_four_loss_candidates_before_warp、内部mask、4候補・Warp未到達・原本SHA256一致 | ローカル合格 |
| M02 | 同上の外部.mskケース、原本と.mskのSHA256一致 | ローカル合格 |
| M03 | E.test_loss_and_revival_are_disjoint_reasons、L=4/R=1 | ローカル合格 |
| M04 | E.test_flags_zero_band_specific_mask_is_not_all_valid | ローカル合格 |
| M05 | E.test_revival_without_near_values_is_intentionally_conservative | ローカル合格、保守的停止 |
| M06 | E.test_source_override_matches_the_effective_definition と override_false、正常対照群 | ローカル合格 |
| M07 | 同上のoverride_near、実効D/originを照合 | ローカル合格 |
| M08 | Eのinteger_exact、nan_all_valid、nan_cleaning、zero_candidates、既存非有限値テスト | ローカル合格 |
| M09 | 既存test_input_mask_nodataとtest_raster_nodata_windowを全体回帰で実施 | ローカル合格 |
| M10 | 既存test_input_gdal_formatsの複数バンド拒否を維持。未対応alpha flagsは専用停止 | 複数バンド合格、独立alpha実機未確認 |
| M11 | E.test_missing_definition_is_not_silently_accepted | ローカル合格 |
| M12 | Eのexisting_output_collisionとsimultaneous_output_and_input、別role・非重複 | ローカル合格 |
| M13 | E.test_all_priorities_skip_and_keep_settings_stop_and_prune_prior_outputs | last/first/auto合格 |
| M14 | 同上、SKIP×KEEPの全組合せ、先行入力の生成DEM削除 | ローカル合格 |
| M15 | Eのforestry_geotiff_and_worldfileとpassthrough_and_direct_materialize | ローカル合格 |
| M16 | E.test_tiled_counts_positions_and_sample_limits、1030×1040・窓端 | 窓境界合格。停止はWarp前、warpMemoryLimitの別実測未実施 |
| M17 | Eのmask_read_failure、unverifiable_numeric_vrt、probe_value_change | 模擬読取異常合格。GDALで開く前に欠落した.mskの存在履歴は復元できず、別途入力台帳照合が必要 |
| M18 | Eのlarge_count、cancel、cleanup_failure。512角窓で総数/標本上限確認 | 合成入力合格。県全域性能・実ディスク満杯・Windowsロックは未確認 |

M01〜M18を全環境で合格したという主張ではない。Windows/QGIS3.44.15の独立検証は後工程。

## ローカル試験・反証

Linux、Python3.13.5、GDAL3.10.3、NumPy2.2.4。

```bash
PATH=/usr/bin:/bin:$PATH QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 \
  /usr/bin/python3 -m unittest discover -s dem_map_sheets/tests
PYTHONPATH=. /usr/bin/python3 -m unittest dem_map_sheets.tests.test_explicit_mask_semantics -v
```

専用26テスト合格。全体602テスト合格（28 skip：主にオプトイン実PDAL等。模擬試験を実PDAL試験とみなさない）。
CIは既存validation.ymlが全テストをdiscoverするため変更せず、PRのActionsを証跡とする。ローカル結果を他GDAL版やWindowsの合格とは扱わない。

`issue29_baseline_counterexample.json` は旧main実行の反証。内部/外部maskともcompleted・有効396/400、mask0ケースでは原無効セルの復活も観測した。
改修側の同等合成入力は4消失候補、または4消失+1復活リスクでfailedになる。
`issue29_failure_example.json` は改修実行の実例（合成入力パスのみ置換）。

意図的な既存期待値変更は `test_input_mask_nodata` の明示mask通常値ケース1件のみ。
PR #27では対象外としてcompletedを期待していたが、Issue #29の承認によりinput_maskの復活リスク停止へ変更。他のPR #27期待値は維持。

## 残作業・判定

Draft PRとして差分レビュー待ち。CIの実バージョンと結果はPR/Issueへ追記する。
Windows/QGIS3.44.15、GUI表示、固定ZIPのSHA256/導入一致、県全域性能とOS実障害は未検証。
起動前に存在しない.mskの検知には元データ台帳等の外部証跡が必要。
独立検証と統括レビューが終わるまでマージ・Release・Issueクローズを行わない。
