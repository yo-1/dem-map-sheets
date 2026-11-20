# QGISテストの実行環境・所有権

Issue #31。製品コード・NoData/mask処理は変更しない。PR #30とは独立。

| 対象 | 環境 | 前提・skip・終了処理 |
|---|---|---|
| tests/test_algorithm_failure_record.py (3件) | 両対応（Windows独立検証待ち） | importで初期化しない。setUpClassで共通acquire、class cleanupでclose。QGIS未導入ならskip |
| tests/qgis_runtime.py | 共通ライフサイクル実装 | Desktop判別はiface/mainWindowとinstance。provider/Processing availabilityも確認。既存アプリを初期化/終了しない |
| tests/test_qgis_runtime.py | QGIS不要 | fakeで所有権・拒否・skip・ASTの正常/禁止例を検証。実Desktop合格の代替ではない |
| 他のdem_map_sheets/tests/test_*.py | QGIS不要（GDAL/PDAL等を要求するものあり） | QGISアプリ生成なし。個別skip/dependency条件を維持 |
| validation/qgis_processing_check.py | Desktop内専用 | 導入済みprovider/Processingを前提。過去rc4版のチェックあり、現行版へ無条件流用不可 |
| validation/qgis_settings_check.py | Desktop内専用 | 既存registry/providerが必要。アプリを初期化/終了しない |
| 他のvalidation/*.py | QGIS不要 | GDAL等の個別依存を確認。wrapper/動的importは別途レビュー |

standalone（QGIS付属Python、リポジトリルート）:

```bash
QT_QPA_PLATFORM=offscreen /usr/bin/python3 -m unittest dem_map_sheets.tests.test_algorithm_failure_record -v
python3 validation/check_qgis_initialization.py
python3 -m unittest dem_map_sheets.tests.test_qgis_runtime -v
```

WindowsではOSGeo4W/QGIS Pythonの環境を使い、QT_QPA_PLATFORMは起動前に設定する。テスト内でDesktopの環境変数を変更しない。

Desktop/Dev Probe（リポジトリルートをsys.path先頭へ追加、Processingを有効化済み）:

```python
import unittest
suite = unittest.defaultTestLoader.loadTestsFromName(
    'dem_map_sheets.tests.test_algorithm_failure_record')
result = unittest.TextTestRunner(verbosity=2).run(suite)
print('tests', result.testsRun, 'failures', len(result.failures), 'errors', len(result.errors))
```

Desktop内ではsys.exit/exitQgisを呼ばず、結果をログへ記録する。新規QgsApplicationを作らない。QGIS/Python版、実行場所、コミット、手順、skip理由を報告する。

standaloneの自前アプリはinitQgis後にProcessingを明示的に準備する。未知の既存Qt/QGISインスタンスは初期化済みと推定せずRuntimeErrorで停止し、新しいプロセスでの実行を案内する。ヘルパーはimportだけではQGISをimport・生成しない。closeはleaseを解放する。所有アプリのexitQgisはプロセス終了時に1回だけ実行する（途中終了すると後続GDAL試験のdriverまで解除されるため）。所有sessionは明示的に再利用し、未知instanceとは区別する。プロセス内で繰り返し独立アプリを生成しない。

## 全件棚卸し（main e791d47基準）

全PythonファイルのQgsApplication/initQgis/exitQgis/start_app参照を確認。
- dem_map_sheets/tests/test_algorithm_failure_record.py:27–28: import時生成・初期化 → 共通fixtureへ移行。
- dem_map_sheets/plugin.py:6,28,49,60: registry登録/参照/解除。製品のライフサイクル初期化ではないため対象外。
- validation/qgis_settings_check.py:13,47: registry参照のみ。Desktop専用で変更不要。
- 他のPythonファイルに直接初期化参照なし。動的生成/外部依存内部まで無事故を保証するものではない。

## CIと限界

AST gateはtestsとvalidationの全Pythonを検査。直接生成・initQgis・exitQgis呼出しを禁止し、唯一tests/qgis_runtime.pyを許可する。別名importも検出。コメント/文字列/registry参照は許可。動的importや任意ラッパーの完全解析はしない。検出結果を製品の欠陥認定と混同しない。
新qgis-lifecycleジョブはgate/自己テスト/実QGIS standalone 3件を実行。他の既存検証ステップは変更しない。
Windows11/QGIS3.44 Desktop内とstandaloneはClaude.aiによる独立検証待ち。既存Windows全体試験の別件3失敗は本Issueの修正範囲外。レビュー前マージ禁止。
