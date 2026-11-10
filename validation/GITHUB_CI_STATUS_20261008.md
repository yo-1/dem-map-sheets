# DEM Map Sheets rc4 — GitHub登録・CI状況追記

確認日: 2026-10-08（JST）
対象: yo-1/dem-map-sheets / Draft PR #1 / codex/dem-v0.2.1-rc4

## GitHub登録
- 登録用ソース・CI定義・検証資料を開発ブランチにpush済み。
- PR #1: https://github.com/yo-1/dem-map-sheets/pull/1 （Draft、base: main）
- PRの変更ファイル一覧は77件をGitHub連携で確認。
- 登録時のコミット表示: a2ec9c3（短縮SHA）。

## CI
- ユーザーが2026-10-08に提示したGitHub Actions画面では、Synthetic DEM validation の2実行（push、pull_request）がいずれも緑の成功表示。表示所要時間は31秒と36秒。
- .github/workflows/validation.ymlには単体テスト、DEM/NoData検証、TerrainRGB検証、成果物アップロードが定義されていることをGitHub連携で確認。
- GitHub連携のcommit workflow runs / combined statuses APIは空配列を返したため、各ジョブのログ・artifactの内容までは独立に確認できていない。
- 画面の成功表示と詳細ログ未照合は区別して扱う。

## Windows実機・未完了
- Windows/QGIS 3.44でのrc4 GUI・設定保存/読込・Processing9件は未確認。
- 実業務データ形式別試験、県全域の性能・中止動作試験は未実施。
- 手順: validation/WINDOWS_RC4.md
- 実機結果・必要な追加是正が確定するまで、Draft維持・mainマージ禁止。

※このファイルは過去の validation/TEST_RECORD.md の「GitHub未登録」記述を時点更新する追記であり、過去のLinux検証記録を書き換えない。
