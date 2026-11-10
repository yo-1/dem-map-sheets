# Windows / QGIS 3.44 実機検証チェックリスト

Linux/CI では検証できない項目の実機手順。各項目に「手順・確認方法・記録するもの」を置く。
対象: Windows 10/11、QGIS 3.44（同梱 GDAL/PROJ）、プラグインは対象コミットの ZIP を
クリーンプロファイルへインストールして実施する。

## 事前記録（全項目の前提）

- QGIS のバージョン情報画面（ヘルプ > QGIS について）: QGIS / GDAL / PROJ の版を保存。
- Python コンソールで `validation/epsg_probe.py` を実行し、出力（EPSG:6668 の名称と
  datum）を保存する。QGIS 同梱 EPSG データベースが JGD2011 世代か JGD2024 世代かを確定する。
- KD-5 相当: 5x5 px・1 m の微小 GeoTIFF をセル 10 m で取り込み、ハングしないことを確認。

## W-1 子プロセス（PDAL）への環境変数継承【高】

コード側の現状: `run_process` は PATH 先頭に PDAL の bin を追加し、PROJ_DATA /
GDAL_DATA を PDAL 側インストールの share へ上書き、PROJ_LIB=PROJ_DATA とし、
GDAL_DRIVER_PATH を除去する。PYTHONHOME / PYTHONPATH は除去していない。
手順:
1. QGIS を通常起動し、Processing から点群 DEM 処理（実 LAS）を実行する。
2. 実行前に QGIS の Python コンソールで `os.environ` の PROJ_LIB / GDAL_DATA /
   PYTHONPATH を記録し、処理後 work/ の PDAL ログに再投影エラーが無いことを確認。
3. 可能なら座標既知の LAS で、出力 DEM の位置が既知座標と一致することを確認。
記録: ログファイル名一覧、エラー有無、座標照合結果。

## W-2 キャンセル時のファイルロック【高】

手順: 大きめの LAS で処理を開始し、PDAL 実行中にキャンセルする。
確認: 例外メッセージがキャンセルであること（PermissionError に化けていないこと）、
結果フォルダにステージ済みコピー（pdal_ascii_inputs 等）が残っていないこと、
run.json の status が cancelled で work/ が診断ファイルのみに刈られていること。

## W-3 PDAL ログの実名と prune 保持【高】

手順: 意図的に失敗する入力（壊れた LAS 等）で実行する。
確認: work/ に残る PDAL ログの実ファイル名一覧を記録し、許可リスト
（.log/.stderr/.stdout/.json/.txt）で保持されること。それ以外の拡張子の診断
ファイルがあれば、その名前を報告する（許可リスト見直しの材料）。

## W-4 日本語パス 4 パターン【高】

入力ファイル名・入力フォルダ・出力先・PDAL 実行ファイルパスの各々に
日本語と空白を含む組み合わせで実行し、成功すること。あわせて cp932 環境の
LEM メタデータ CSV と CSV 点群が文字化けなく読めること。

## W-5 QGIS 同梱 GDAL での基本動作【高】

事前記録の版で、合成データ一式（validation の自己検証相当）を QGIS 内
Processing から実行し、成功・失敗の表示が正しいこと。

## W-6 画面表示【中】

- 入口検証エラー（例: cell_size の不正は画面からは到達しないが、CRS 不正等）で
  メッセージが利用者に意味の伝わる文であること。
- 無効入力 25 件のフォルダで、警告が 20 件＋サマリ 1 件と表示されること（F-3）。
- キャンセルが「失敗」ではなくキャンセルとして表示されること。

## W-7 UNC・長いパス【中】

UNC パス（\\server\share\...）上の入力と、260 文字超の出力パスで実行し、
失敗する場合はエラーメッセージが明確であること。

## W-8 ディスクフル【中】

小容量ドライブ（または容量制限フォルダ）を出力先にし、書込み失敗時に
元例外が表示されること、run.json が無くても処理が二次例外で落ちないこと。

## W-9 ZIP インストール【中】

配布 ZIP をクリーンプロファイルへインストールし、ロード／アンロード／
リロードでメニュー・ツールバーの重複登録が無いこと。

## W-10 実 LAS/LAZ・keep_work【中】

日本語名を含む実 LAS/LAZ で PDAL >= 2.9 の実行、keep_work ON/OFF の
work/ 残存差、成功時に work/ が削除されること。

## W-11 conda 環境作成【中】

`environment-pointcloud.yml`（環境名 dem-map-sheets-pointcloud）から
Windows の conda で新規作成でき、gdal>=3.10 / pdal>=2.9 が解決されること。
作成した pdal のパスをプラグイン設定に指定して W-1 を実施する。

## W-12 大きめ入力【低】

大型 GSI XML・大量 LEM・大規模点群でのメモリ使用量と UI 応答
（処理中も QGIS が応答すること、進捗が進むこと）。
