# 入力形式別テスト対応表（E：入力形式別テスト）

- 作成日：2026-11-09／担当：Claude Code（E）
- 基準：`codex/dem-v0.2.1-rc4`（6eed194、PR #1 Draft）。互換確認：`muse/pre-release-fixes`（570ed21）
- 検証環境：Ubuntu 24.04 / Python 3.12.3 / **GDAL 3.8.4（実GDAL）** / numpy 1.26.4 （CIの `ubuntu-24.04` + `python3-gdal` と同一構成）
- **実PDAL：未使用**（PDALは模擬のみ）。**Windows／QGIS 3.44：未実施**（完了扱いにしない）

## 1. 追加テストファイル（すべて新規。既存ファイルは変更していない）

| ファイル | 件数 | 実GDAL | 内容 |
|---|---|---|---|
| `dem_map_sheets/tests/input_fixtures.py` | – | – | 合成入力ビルダー（LEM/GSI XML/ZIP/GeoTIFF/TFW/模擬PDAL） |
| `dem_map_sheets/tests/test_input_options.py` | 50 | 不要 | `validate_input`、`discover`、テキスト行解析、XYZ自動判定、格子間隔推定 |
| `dem_map_sheets/tests/test_input_gsi_xml.py` | 33 | 不要 | 国土地理院DEM XMLパーサ契約（R-8/R-9の後退検知を含む） |
| `dem_map_sheets/tests/test_input_forest_lem.py` | 42 | 不要 | LEM読込、メタ判定、分類、ZIP安全展開 |
| `dem_map_sheets/tests/test_input_gdal_formats.py` | 72 | **必要** | GeoTIFF／TIFF+TFW／格子CSV・XYZ・TXT／LEM／GSI XML・ZIP／森林ZIP のE2E、CRS、NoData、画素数上限、`run()` |
| `dem_map_sheets/tests/test_input_lidar_mocked.py` | 34 | 必要（OSR） | LAS/LAZ：模擬PDALによるサマリ検証、CRS、限界値、パイプライン構成 |
| `validation/known_defects/test_known_defects.py` | 6（9サブ） | 必要 | **未修正不具合の再現（通常CIから分離）** |

CIの既存ステップ `unittest discover -s dem_map_sheets/tests` が追加分（231件）を自動実行する。workflow は変更していない。
GDAL無し環境では GDAL／PDAL模擬スイートを自動スキップ（231件中106件スキップ）。

## 2. 入力形式 × テスト種別

凡例：◎＝実GDALで検証／○＝純Python（GDAL不要）で検証／△＝模擬（PDAL）で検証／×＝未実施／－＝該当なし

| 観点 | LAS/LAZ | 森林LEM | 格子CSV/XYZ/TXT | TIFF+TFW / GeoTIFF | GSI XML / ZIP |
|---|---|---|---|---|---|
| 正常入力→DEM生成 | △（パイプライン構成のみ）／×（実読込） | ◎ | ◎ | ◎ | ◎ |
| CRS認識 | △ ヘッダ／`input_crs`／境界再投影 | ◎ `input_crs`必須 | ◎ 投影m系のみ許可 | ◎ 埋込／TFW+指定／再投影位置を独立変換で照合 | ◎ 別名(JGD2000/2011/2024)・上書き・地理座標限定 |
| 不正CRS拒否 | △ 空・不明・複合 | ◎ 空 | ◎ 空・不明・地理・フィート | ◎ 不明・複合・鉛直のみ・3D | ◎ 空・投影CRS上書き |
| NoData | － （PDAL側。nodata=-999999 指定のみ確認） | ◎ -9999/-1111 | ◎ `source_nodata`／欠測点／forest_nodata | ◎ 埋込／未定義時の必須化／NaN・Inf | ◎ -9999・データなし・海水面・NaN |
| 欠損・破損・不正形式 | △ サマリ欠落・0点・非有限・非JSON・PDAL失敗 | ◎ 行数・短行・番号不一致・非数値・メタ欠落 | ◎ 非数値・短行・区切り・文字コード・空 | ◎ 破損TIFF・欠落・多バンド・scale/offset・破損TFW | ◎ 切断XML・非XML・DTD・不正数値・不正タプル |
| 不正な範囲・解像度・座標 | △ 非有限境界 | ○ 整合しない境界、スケール | ◎ 非格子点・重複矛盾・単位 | ◎ 無georef拒否 | ○ 緯度経度逆転・範囲外・開始点・走査順 |
| ZIPの安全な読込 | － | ○ 絶対/`..`/ドライブ/`\`区切り・サイズ上限・破損・巣状 | － | ◎（森林ZIP内） | ◎ 展開せず読込・`../`名でも書込なし・DTD |
| 入力サイズ・画素数上限 | △ モザイク/ブロック数/バッファ上限 | ○◎ 境界値 | KD-3（M-2）参照 | 仕様上上限なし（VRT） | ○◎ 境界値（=上限は通過/+1で拒否） |
| 異常時の明確なエラー | ◎ メッセージ内容を検証（PDAL stderr含む） | ◎ 行番号・列番号 | ◎ ファイル:行 | ◎ | ◎／一部不明確（KD-6） |
| 他担当領域との重複 | PDAL起動/中断/後始末は Muse（test_pdal_process, test_temp_cleanup）→**重複せず** | | text_grid上限は M-2（Codex/Muse）→再現のみ分離 | | DTD/メモリは R-8/R-9→挙動契約のみ |

## 3. 実PDAL／実GDALの区別

**実GDAL 3.8.4で検証済み**：CRS解決、世界ファイル探索（.tfw/.wld/.tifw/大文字）、NoData写像、再投影、warp、LEM/GSI/XYZ→GeoTIFF、`run()` 全体。
**純Pythonで検証**：GSI XMLパース、LEM読込、ZIP展開、discover、テキスト解析。
**模擬PDALで検証**：PDALバージョンゲート(2.9)、`info --summary` の検証、CRS、境界再投影、リーダー選択(las/copc)、限界値、パイプラインJSON。

**実PDAL未検証（要 PDAL≥2.9 環境）**
1. 本物のLAS/LAZ/COPCの読込（ヘッダCRS、点群数、境界）
2. 破損・途中切れLAS/LAZ、LAS1.4/点フォーマット差
3. `filters.expression` が実際に地面点だけを残すこと／SMRF分類／`--stream` 可否
4. `filters.reprojection` に渡すWKTの軸順（EPSG:6677 の N,E 順の扱い）
5. 複合CRS（水平+鉛直）を持つLASの実挙動（本テストは現行制約E-1cを固定しただけ）
6. 日本語パス×出力パスのASCII/非ASCII 4通り

**GDAL 3.10以上（環境定義 `gdal>=3.10`）／QGIS同梱GDAL／Windows：未検証**（検証したのは3.8.4のみ）。

## 4. 検証の妥当性（変異テスト）

本体を意図的に1箇所ずつ壊した別コピーで追加スイートを実行し、検出を確認した（実施10種）。
検出：GSIのNoData(-9999)除去、ZIPパス検査無効化、text_gridのY軸反転、LEMの-1111除去、DTDガード除去、PDALバージョンゲート除去、LEM画素数上限除去、`.wld`除外、GSI画素数上限除去、ZIPメンバー上限除去、重複矛盾検査除去、TFW要求除去、多バンド検査除去、地面クラス絞込除去、COPCリーダー除去、NaN標高の素通り。
**未検出（等価変異）**：複合CRS検査（軸数でも別経路で拒否される）、GSI開始点検査（走査生成側でも拒否される）。
この過程で**テスト自体の欠陥**（ZIP検査が退行すると実ファイルシステムの `/abs/evil.tif` へ書込む設計）を見つけ、tempディレクトリ内に閉じるよう修正済み。

## 5. 未修正不具合（`validation/known_defects/`、通常CIから分離）

| ID | 重大度 | 内容 | 到達経路 | 基準での結果 | Muse修正ブランチでの結果 |
|---|---|---|---|---|---|
| KD-1 | 中（API）／低（GUI） | 不正な `cell_size`（負値）で `gdal.Warp` が**ハング**、NaNで**プロセスabort（SIGABRT）**、0は不明瞭な `RuntimeError`。エンジンが検証しない | エンジン直呼び（FME・スクリプト）。QGIS画面は 0.01〜100 に制限済み | 失敗 | 失敗（未修正） |
| KD-2 | 中 | `validate_input()` は定義のみで**どこからも呼ばれない**（`cell_size`/`target_crs`/`max_pixels` は元々検査項目外）。例：`text_columns=[1,1,3]` が素通り | エンジン直呼び | 失敗 | 失敗（未修正） |
| KD-3 | 高（M-2） | `text_grid` に画素数上限が無い。外れ値1点で巨大memmap | 格子CSV/XYZ/TXT | 失敗 | **合格（M-2修正で解消を確認）** |
| KD-4 | 低 | LEMの `dx`/`dy` ≤ 0 を検証しない。`dx=0` は不明瞭なGDALエラー、`dx<0`（座標スケール明示時）は東西反転して**静かに誤配置** | LEM + `forest_lem_coordinate_scale` 明示 | 失敗 | 失敗（未修正） |
| KD-5 | 低（要追加調査） | 出力セルより小さい極小ラスタ（例：5×5px・1 m を10 mセルへ、境界を跨ぐ）で `gdal.Warp` がハング。通常サイズ（10×10px以上）では再現せず。GDAL 3.8.4のみ確認（3.10以上は未確認） | 全入力形式の `normalize()` | 失敗 | 失敗（未修正） |
| KD-6 | 低（品質） | GSI XMLの数値不正メッセージが `could not convert string to float: 'xyz'` のみで位置情報が無い | GSI | 失敗 | 失敗（未修正） |

本体修正は行っていない。統括判断のうえ、修正後は合格した項目を `dem_map_sheets/tests/` へ移して回帰テスト化する。

## 6. 統括判断が必要な仕様上の観察（不具合と断定しない）

1. **XYZ格子の画素中心／縁の解釈**：`text_grid` は点を画素**中心**として配置する（原点 = 最小座標 − 半セル）。一方、出力グリッドは `targetAlignedPixels` で**セル幅の整数倍に縁を揃える**。点座標が出力セルの整数倍（例：5 m刻みで x=…,100,105）だと半セルずれてバイリニア補間され、標高が標本点で保存されない（例：4点 1/2/3/nodata → 周囲平均値が混入）。点が「セル中心（2.5+5k）」の場合は正確。実データ（森林格子CSV）の座標規約が確認できるまで**未確認**。テストは「中心座標なら値が保存される」ことのみ固定。
2. **M-3の適用範囲（Muse修正ブランチ）**：格子の `ValueError` を一括して「無視＋警告」にしたため、`Regular text grid requires a projected metre CRS` 等の**設定起因エラー**も個別警告化され、最終的に汎用の `No usable DEMs found` になり原因が見えにくい。互換テスト（`test_unequal_xy_spacing_is_refused`）が影響を受けたため、両挙動を許容する形にした。
3. **NaN/Inf画素**：ラスタ入力のNaN/InfはNODATAへ変換されず値のまま通過（下流が非有限を無効扱い）。テストは「数値標高にならない」ことのみ固定。
4. **GDAL例外モードで例外型が変わる**：不明CRSは `UseExceptions` 有効時 `RuntimeError`、無効時 `ValueError`。QGIS側は `UseExceptions` 有効。テストは両者を許容。
5. **`.tiffw`**：`has_world_file` は `.tiffw` を世界ファイルとみなさないが、GDAL 3.8.4 は読む。森林TIFFで誤拒否の可能性（低）。
6. **GSI XML の ParseError** は `SyntaxError` 系で `ValueError` ではない（`algorithm.py` は `Exception` で捕捉するため実害なし）。

## 7. 実行方法

```bash
# 追加テスト＋既存（CIと同じ）。GDAL(osgeo)が使える python3.12 で
PYTHONPATH=. python3 -m unittest discover -s dem_map_sheets/tests -v
# 追加分のみ
PYTHONPATH=. python3 -m unittest discover -s dem_map_sheets/tests -p 'test_input_*.py' -v
# 既知不具合の再現（失敗が正常。約50秒）
PYTHONPATH=. python3 -m unittest discover -s validation/known_defects -v
```

注意：既存の `test_temp_cleanup.py` は模擬PDALの shebang が `#!/usr/bin/env python3` のため、`python3` がGDAL無しのPythonを指す環境では失敗する（環境依存。CIの `/usr/bin/python3`＝3.12では問題なし）。

## 8. 結果（本ブランチ）

| 対象 | 結果 |
|---|---|
| 基準 `6eed194` + 追加：`unittest discover -s dem_map_sheets/tests` | 272件 OK（expected failures=4 は既存のM-1固定分） |
| Muse修正 `570ed21` + 追加 | 295件 OK |
| GDAL無し環境（追加分のみ） | 231件 OK（106件スキップ） |
| `validation/known_defects`（基準） | 6件中全て失敗（未修正不具合の再現）。Muse修正ブランチでは KD-3 のみ合格 |
