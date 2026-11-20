# Issue #31 — QGISテスト初期化監査・作業計画

更新日: 2026-11-21
対象: `yo-1/dem-map-sheets`
参照: https://github.com/yo-1/dem-map-sheets/issues/31

## 確認済み（mainのソースを直接取得）

| ファイル | 観測 | 暫定分類 | 対応 |
|---|---|---|---|
| `dem_map_sheets/tests/test_algorithm_failure_record.py` | モジュールimport時に `if _HAS_QGIS: _APP = QgsApplication([], False); _APP.initQgis()` を実行。3件のテストがQGIS import可否でskip判定される | QGIS依存。Desktop内では現在の初期化方式が不適切。standalone対応可否は要検証 | import時初期化を排除し、所有権を管理する共通fixtureに移す |
| `dem_map_sheets/tests/test_input_options.py` | 冒頭に「Pure Python (numpy only): runs in any environment」と明記。確認した範囲にQGIS初期化なし | QGIS不要 | 原則変更しない |

**注意:** これは全件棚卸しではない。上記2ファイル以外のテストとCI定義は未確認。現時点で「全件問題なし」とは判定しない。

## 独立検証からの実測訂正

- 起動済みQGIS内で `test_algorithm_failure_record` を実行すると `TypeError: _qgis_app_init_qgis() takes 0 positional arguments but 1 was given` を観測。
- 別プロセスのQGIS付属Pythonで同3件は `QGIS is not available` として**すべてskip**。**passではなく、テスト本体は未実行**。
- 起動済みQGIS内で全テスト一式は9秒で完了。その後の別テスト群の同一プロセス再読込時に応答停止を観測。因果関係・原因は未確定。
- これらはClaude.aiの報告を統括が中継したものであり、この作業ブランチで新たに再現した結果ではない。

## 実装・検証の順序

1. `tests/`、`validation/`、CIワークフロー等を全件検索し、`QgsApplication`、`initQgis`、`exitQgis`、`instance` のパス・行・用途を一覧化。
2. テストを **QGIS不要 / standalone専用 / Desktop内専用 / 両対応** に分類し、対応範囲を決定。全件両対応を強制しない。
3. 共通初期化fixtureを実装。Desktop内では既存QGISを生成・再初期化・終了しない。standaloneは所有権とProcessing利用可否を検査する。import時副作用を排除。
4. 対象テストを移行し、READMEに環境別の実行方法とskip条件を記載。
5. ASTベースの静的ガードと許可・禁止パターンの自己テストをCIに組み込む。
6. Linux CIおよびWindows/QGIS 3.44の独立検証を記録。**pass/fail/skipを分離**し、skipのみの実行を合格扱いしない。

## 変更範囲と禁止事項

- 対象: テストコード、共通fixture、CI、テスト手順文書。
- 対象外: 製品DEM処理、mask/NoData数値ロジック、PR #30、Dev Probe本体と他リポジトリ。
- PRはDraftのまま統括レビューを受け、許可なくマージ・Releaseしない。
- Claude.aiに新たな作業依頼はしない。今後の独立検証では実行環境、QGIS/GDAL/Python版、コマンド、pass/fail/skipを記録する。

## 進捗

- [x] 専用ブランチ作成
- [x] 2ファイルの一次監査と独立検証報告の訂正事項を記録
- [ ] 全テスト・CI初期化箇所の棚卸し
- [ ] fixture実装とテスト移行
- [ ] ASTガード・CI
- [ ] Windows独立検証
- [ ] 統括最終受入
