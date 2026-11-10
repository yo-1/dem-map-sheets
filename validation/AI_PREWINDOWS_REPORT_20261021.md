# DEM Map Sheets v0.2.1-rc4 — AI事前監査完了時点の記録
更新日: 2026-10-21

対象: yo-1/dem-map-sheets / Draft PR #1 / codex/dem-v0.2.1-rc4

## 実装・テスト
- TerrainRGB: 有効標高の表現範囲外を明示的に拒否し、誤った値への無言クリップを防止。
- 設定JSON: 同じディレクトリに一時保存してos.replaceで原子的に置換。失敗時は既存設定を保持。
- DEMマージ: GDALのVRT/Translate/Warp失敗時も一時VRTをfinallyで削除。
- 回転GeoTransform: DEM範囲とTerrainRGB投影範囲の算出に四隅すべてを使用。
- Python単体テスト: 既存18件から24件へ増加。ローカル環境の全24件が成功。
- Windows/QGIS・実GDALでの回転ラスタ・大量DEM動作はまだ確認していない。CI結果も今回の追加コミットについては未確認。

## 最終段階に回す実機検証
1. Windows/QGIS 3.44でrc4導入・GUI表示・設定保存読込。
2. validation/WINDOWS_RC4.md のProcessing9件、TerrainRGB実出力。
3. 回転GeoTransformを含むテストDEM、異常な標高値、設定JSON保存の上書き失敗を確認。
4. LEM、LAS/LAZ/COPC、XYZ/CSV/TXT、GSI DEM ZIP/XML、TIFF+TFW、GeoTIFF、森林成果ZIP。
5. 県全域規模の処理時間・メモリ・タイル枚数・中止時の状態。

## 保留条件
- 追加コミット後のGitHub Actionsが成功したことを確認する。
- 実機試験と実業務データ試験を終えるまでPR #1はDraftを維持し、mainにマージしない。
- 旧validation/TEST_RECORD.mdのLinux試験結果は変更しない。
