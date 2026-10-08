# Windows / QGIS 3.44 rc4 再試験

この手順とスクリプトは準備済み。今回のAI環境ではWindows/QGISで未実行。
実機確認が終わるまでmainへマージしない。

1. `DEM_Map_Sheets_QGIS_v0.2.1-rc4.zip`をQGISの「ZIPからインストール」で導入し、QGISを再起動する。
2. DEM Map Sheetsを有効にする。ツールバーまたはラスタメニューから画面を開く。
   タイトルの`v0.2.1-rc4`、標高基準の選択・追加入力、設定保存・読込ボタンを確認する。
   設定保存・読込ボタンはプラグインのメニューから開いた画面に付く。
3. 本ソース一式を展開する。QGIS Pythonコンソールで次を実行する。
   `validation`のパスだけ実際の場所に変更する。

```python
from pathlib import Path
v = Path(r'C:\DEM_Map_Sheets_rc4\GitHub_source\validation')
def run_check(name):
    p = v / name
    exec(compile(p.read_text(encoding='utf-8'), str(p), 'exec'),
         {'__name__': '__main__', '__file__': str(p)})
run_check('qgis_settings_check.py')
run_check('qgis_processing_check.py')
```

設定試験は実Processing画面を使い、ファイル選択と通知だけを自動応答する。
画面・ボタンのバージョン表示、CRSの保存、静的値、動的式の拒否、標高基準「その他」、
実ウィジェットでのJSON保存・読込、欠落パス通知、不正JSONで画面が変更されないことを確認する。
各項目の成否と環境情報を`validation/qgis_results/settings_*/qgis_settings_results.json`へ記録する。

Processing試験は以前Windowsで合格した9件をrc4で再実行し、採用元の記録とPNG復号も検査する。
全件合格すると`validation/qgis_results/*/qgis_validation_results.json`を生成する。
途中で例外が出た場合は全件合格ではない。コンソールの例外全文も返す。
いずれも合成データを使い、実業務データには書き込まない。

精密なTerrainRGB試験はGDAL・NumPy・PillowがあるPythonで次を実行する。

```text
python verify_terrainrgb.py <dem_map_sheetsを含む親フォルダー> <新しい結果フォルダー>
```

QGISコンソールから実行する場合：

```python
import sys, uuid
import dem_map_sheets
saved_argv = sys.argv[:]
try:
    sys.argv = ['verify_terrainrgb.py', str(Path(dem_map_sheets.__file__).parent.parent),
                str(v / ('terrainrgb_' + uuid.uuid4().hex[:8]))]
    run_check('verify_terrainrgb.py')
finally:
    sys.argv = saved_argv
```

PillowのImportErrorが出た場合、精密試験は未実施として記録する。
Processing試験のPNG検査はGDALで読むためPillowに依存しない。

## 実データ確認と返却

LEM、LAS/LAZ/COPC、XYZ/CSV/TXT、GSI DEM ZIP/XML、TIFF+TFW、GeoTIFF、森林成果ZIPを
実際のデータで確認する。LAS系は利用可能なPDALが必要。
年度・精度・点密度は根拠のある台帳を使い、CRS・標高基準・NoData・入力量を記録する。
標高基準の選択は記録だけで、高さ変換は行わない。
県全域規模の処理時間・メモリ・タイル枚数と中止動作も記録する。

結果JSON、例外全文、使用データの形式と根拠、GUIでの確認結果をWorkへ返す。
Workから元Chatへ実装・検証結果を返し、main反映の判断はその後に行う。
