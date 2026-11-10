from datetime import datetime
import json
from pathlib import Path
import sys
import uuid
from .version import VERSION
from qgis.core import (
    Qgis, QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterMultipleLayers, QgsProcessingParameterCrs,
    QgsProcessingParameterNumber, QgsProcessingParameterEnum,
    QgsProcessingParameterBoolean, QgsProcessingParameterFolderDestination,
    QgsProcessingParameterFile, QgsProcessingParameterString, QgsMessageLog,
    QgsProcessingOutputRasterLayer, QgsProcessingOutputFolder,
    QgsProcessingOutputFile, QgsVariantUtils,
)


class DEMMapSheetsAlgorithm(QgsProcessingAlgorithm):
    def name(self): return 'create'
    def displayName(self): return f'DEM作成・マージ・TerrainRGB XYZタイル v{VERSION}'
    def group(self): return 'DEM Map Sheets'
    def groupId(self): return 'demmapsheets'
    def createInstance(self): return DEMMapSheetsAlgorithm()
    def tags(self): return ['DEM','GeoTIFF','TerrainRGB','XYZ','LAS','LAZ','LEM','国土地理院','標高']

    def shortHelpString(self):
        return (
            f'DEM Map Sheets v{VERSION}\n'
            'LEM、LAS/LAZ/COPC、XYZ/CSV/TXT、国土地理院基盤地図情報DEM（ZIP/XML）、'
            'TIFF/GeoTIFFから標高GeoTIFFを作成し、共通CRS・解像度へ整合して1枚のDEMへマージ、'
            'さらにTerrainRGB形式のXYZ PNGタイル（EPSG:3857）を生成します。\n'
            '重複DEMは2500図郭ごとに、計測年度→精度→グラウンド点密度→図郭充足率の順で自動選定できます。\n'
            '鉛直基準の変換は行いません。異なる測地成果・標高基準を混在させる前に統一してください。\n'
            '森林航空レーザ成果ではLEM+メタデータCSV/TXT、規則格子CSV/XYZ、TIFF+TFW、GeoTIFF、ZIPを扱います。\n'
            'LAS/LAZ/COPCのDEM化にはPDALが必要です。分類済み点群は地表面分類コード2を既定とします。\n'
            'XYZは森林情報オープンデータ標準仕様のTerrainRGB式 H=-10000+(R*65536+G*256+B)*0.1 を使用します。\n'
            'Copyright (C) 2026 yo-1. Forked from CS Map Sheets. GNU GPL v3.0 only.'
        )

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterEnum('INPUT_TYPE','入力形式',[
            '標高ラスタ（GeoTIFF・TIFF+TFW・IMG・ASC）',
            '国土地理院DEM（ZIP・XML）',
            '標高テキスト（XYZ・CSV・TXT）',
            'レーザ点群（LAS・LAZ・COPC）',
            '森林航空レーザ成果（LEM・CSV格子・TIFF・GeoTIFF・ZIP）'],defaultValue=0))
        self.addParameter(QgsProcessingParameterMultipleLayers('FILES','入力ファイル（複数選択、フォルダー指定時は省略可）',
            QgsProcessing.TypeFile, optional=True))
        self.addParameter(QgsProcessingParameterFile('INPUT_FOLDER','入力フォルダー（省略可）',
            behavior=QgsProcessingParameterFile.Folder, optional=True))
        self.addParameter(QgsProcessingParameterBoolean('RECURSIVE','サブフォルダーも検索する',True))
        self.addParameter(QgsProcessingParameterCrs('INPUT_CRS','入力CRS（テキスト・LEM・非GeoTIFFでは必須）',optional=True))
        output_crs = QgsProcessingParameterCrs('CRS','出力の平面直角座標系',optional=False)
        output_crs.setHelp('必須です。出力の平面直角座標系（例：EPSG:6677）を選択してください。未設定では処理を開始できません。')
        self.addParameter(output_crs)
        self.addParameter(QgsProcessingParameterNumber('CELL','出力セルサイズ（m）',
            QgsProcessingParameterNumber.Double,1.0,minValue=0.01,maxValue=100.0))
        self.addParameter(QgsProcessingParameterEnum('VERTICAL_MODE','入力標高の基準（記録用・変換は行いません）',
            ['不明・未確認','測地成果2011（標高）','測地成果2024（標高）','検証用仮想標高','その他（下欄に入力）'],defaultValue=0))
        self.addParameter(QgsProcessingParameterString('VERTICAL','標高基準の追加入力（選択より優先）',defaultValue='',optional=True))
        self.addParameter(QgsProcessingParameterNumber('NODATA','入力NoData値（空欄は入力定義を使用）',
            QgsProcessingParameterNumber.Double, optional=True))
        self.addParameter(QgsProcessingParameterEnum('MERGE_PRIORITY','重複時の優先順',
            ['後に並ぶ入力を優先','先に並ぶ入力を優先','自動判定（計測年度→精度→グラウンド点密度→2500図郭充足率）'],defaultValue=2))
        self.addParameter(QgsProcessingParameterFile('PRIORITY_METADATA','自動優先用メタデータCSV/JSON（任意）',
            fileFilter='CSV/JSON (*.csv *.json)', optional=True))
        self.addParameter(QgsProcessingParameterNumber('PRIORITY_MAX_SHEETS','自動優先：処理する2500図郭数の上限',
            QgsProcessingParameterNumber.Integer,100000,minValue=1,maxValue=1000000))
        self.addParameter(QgsProcessingParameterBoolean('TERRAINRGB','TerrainRGB XYZタイルを生成する',True))
        self.addParameter(QgsProcessingParameterNumber('XYZ_MIN','XYZ最小ズーム',QgsProcessingParameterNumber.Integer,2,minValue=0,maxValue=24))
        self.addParameter(QgsProcessingParameterNumber('XYZ_MAX','XYZ最大ズーム',QgsProcessingParameterNumber.Integer,18,minValue=0,maxValue=24))
        self.addParameter(QgsProcessingParameterNumber('XYZ_LIMIT','XYZ候補枚数上限',QgsProcessingParameterNumber.Integer,1000000,minValue=1))
        self.addParameter(QgsProcessingParameterEnum('LIDAR_MODE','LAS/LAZ：地表面の取り出し方',
            ['分類済み（指定分類コードを抽出）','地表面点のみのデータ','自動分類（SMRF）'],defaultValue=0))
        self.addParameter(QgsProcessingParameterString('GROUND_CLASSES','LAS/LAZ：地表面分類コード',defaultValue='2'))
        self.addParameter(QgsProcessingParameterFile('PDAL','PDAL実行ファイル（PATHにあれば省略可）',optional=True))
        self.addParameter(QgsProcessingParameterEnum('POINT_METHOD','点群DEM補間',
            ['IDW','平均','最低値'],defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber('POINT_RADIUS','点群DEM補間半径（m）',
            QgsProcessingParameterNumber.Double,2.0,minValue=0.01,maxValue=1000))
        self.addParameter(QgsProcessingParameterEnum('TEXT_MODE','標高テキストの並び',
            ['規則格子','不規則点（PDAL補間）'],defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum('TEXT_DELIMITER','区切り文字',
            ['カンマ','タブ','空白','セミコロン'],defaultValue=0))
        self.addParameter(QgsProcessingParameterString('TEXT_ENCODING','文字コード',defaultValue='utf-8-sig'))
        self.addParameter(QgsProcessingParameterNumber('TEXT_SKIP','読み飛ばし行数',QgsProcessingParameterNumber.Integer,1,minValue=0))
        self.addParameter(QgsProcessingParameterString('TEXT_COLUMNS','座標1・座標2・標高の列番号',defaultValue='1,2,3'))
        self.addParameter(QgsProcessingParameterEnum('TEXT_AXES','座標列の順序',
            ['東,北（GIS X,Y）','北,東（測量 X,Y）'],defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber('TEXT_CELL','規則格子の入力間隔（m）',
            QgsProcessingParameterNumber.Double,1.0,minValue=0.01))
        self.addParameter(QgsProcessingParameterEnum('FOREST_AXES','森林航空レーザCSVの座標順',
            ['北,東（測量X,Y）','東,北（GIS X,Y）'],defaultValue=0))
        self.addParameter(QgsProcessingParameterString('FOREST_CELL','森林CSV格子間隔（autoまたはm）',defaultValue='auto'))
        self.addParameter(QgsProcessingParameterEnum('FOREST_GRID_REF','森林格子座標の解釈',
            ['指定なし（既定：セル中心）','center（セル中心）','upper_left（セル左上角）'],defaultValue=0))
        self.addParameter(QgsProcessingParameterString('LEM_SCALE','LEM座標倍率（auto推奨）',defaultValue='auto'))
        self.addParameter(QgsProcessingParameterNumber('FOREST_NODATA','森林成果の既定NoData',
            QgsProcessingParameterNumber.Double,-9999.0))
        self.addParameter(QgsProcessingParameterBoolean('SKIP_INVALID','読み込めない入力ラスターを警告してスキップする',False))
        self.addParameter(QgsProcessingParameterBoolean('KEEP_WORK','中間変換ファイルを残す',False))
        self.addParameter(QgsProcessingParameterFolderDestination('OUTPUT','出力先の親フォルダー'))
        self.addOutput(QgsProcessingOutputRasterLayer('MERGED_DEM','結合GeoTIFF DEM'))
        self.addOutput(QgsProcessingOutputFolder('SOURCE_DEMS','入力別GeoTIFF DEM'))
        self.addOutput(QgsProcessingOutputFolder('XYZ_FOLDER','TerrainRGB XYZフォルダー'))
        self.addOutput(QgsProcessingOutputFile('MANIFEST','処理記録 run.json'))

    def processAlgorithm(self, parameters, context, feedback):
        from osgeo import gdal, osr
        from .engine.input_sources import explicit_file_parameter
        from .engine.dem_pipeline import run
        from .engine.terrainrgb import write_terrainrgb_xyz
        # GDAL exceptions are scoped inside engine run() (W-13); the
        # plugin must not flip the process-global setting for QGIS.
        mode=['raster','gsi','text','lidar','forest'][self.parameterAsEnum(parameters,'INPUT_TYPE',context)]
        raw=parameters.get('FILES')
        paths=explicit_file_parameter(raw) if not QgsVariantUtils.isNull(raw) and raw not in ('',[]) else []
        folder=self.parameterAsFile(parameters,'INPUT_FOLDER',context)
        if not paths and folder: paths=[folder]
        if not paths: raise QgsProcessingException('入力ファイルまたは入力フォルダーを指定してください。')
        source_crs=self.parameterAsCrs(parameters,'INPUT_CRS',context)
        source_raw=parameters.get('INPUT_CRS')
        source_set=not QgsVariantUtils.isNull(source_raw) and str(source_raw).strip()!=''
        target=self.parameterAsCrs(parameters,'CRS',context)
        if not target.isValid(): raise QgsProcessingException('出力CRSを指定してください。')
        try: columns=[int(v.strip()) for v in self.parameterAsString(parameters,'TEXT_COLUMNS',context).split(',')]
        except ValueError as exc: raise QgsProcessingException('TEXT_COLUMNSは整数3個をカンマ区切りで指定してください。') from exc
        try: classes=[int(v.strip()) for v in self.parameterAsString(parameters,'GROUND_CLASSES',context).split(',')]
        except ValueError as exc: raise QgsProcessingException('GROUND_CLASSESは整数をカンマ区切りで指定してください。') from exc
        nd_raw=parameters.get('NODATA')
        nd=None if QgsVariantUtils.isNull(nd_raw) or nd_raw=='' else self.parameterAsDouble(parameters,'NODATA',context)
        parent=Path(self.parameterAsFileOutput(parameters,'OUTPUT',context))
        result_dir=parent/('demmap_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
        from .profiles import resolve_vertical
        try:
            vertical = resolve_vertical(self.parameterAsEnum(parameters,'VERTICAL_MODE',context),
                                        self.parameterAsString(parameters,'VERTICAL',context))
        except ValueError as exc:
            raise QgsProcessingException(str(exc)) from exc
        c=dict(inputs=paths,input_type=mode,recursive=self.parameterAsBool(parameters,'RECURSIVE',context),
            normalize_rasters=True,input_crs=source_crs.toWkt() if source_set else '',target_crs=target.toWkt(),
            vertical_reference=vertical,cell_size=self.parameterAsDouble(parameters,'CELL',context),
            source_nodata=nd,max_pixels=25000000,max_input_pixels=25000000,max_input_files=100000,
            lidar_mode=['classified','ground_only','auto'][self.parameterAsEnum(parameters,'LIDAR_MODE',context)],
            ground_classes=classes,pdal_path=self.parameterAsFile(parameters,'PDAL',context),
            point_method=['idw','mean','min'][self.parameterAsEnum(parameters,'POINT_METHOD',context)],
            point_radius=self.parameterAsDouble(parameters,'POINT_RADIUS',context),point_tile_size=512,max_points_auto=10000000,
            smrf_cell=1.0,smrf_slope=.15,smrf_threshold=.5,smrf_scalar=1.25,smrf_window=18.0,
            text_mode=['grid','points'][self.parameterAsEnum(parameters,'TEXT_MODE',context)],
            text_delimiter=['comma','tab','space','semicolon'][self.parameterAsEnum(parameters,'TEXT_DELIMITER',context)],
            text_encoding=self.parameterAsString(parameters,'TEXT_ENCODING',context),text_skip_rows=self.parameterAsInt(parameters,'TEXT_SKIP',context),
            text_columns=columns,text_axis_order=['east_north','north_east'][self.parameterAsEnum(parameters,'TEXT_AXES',context)],
            text_grid_cell=self.parameterAsDouble(parameters,'TEXT_CELL',context),
            forest_axis_order=['north_east','east_north'][self.parameterAsEnum(parameters,'FOREST_AXES',context)],
            forest_grid_cell=self.parameterAsString(parameters,'FOREST_CELL',context).strip().lower(),
            forest_lem_coordinate_scale=self.parameterAsString(parameters,'LEM_SCALE',context).strip().lower(),
            forest_nodata=self.parameterAsDouble(parameters,'FOREST_NODATA',context),
            merge_priority=['last','first','auto'][self.parameterAsEnum(parameters,'MERGE_PRIORITY',context)],
            priority_metadata=self.parameterAsFile(parameters,'PRIORITY_METADATA',context),
            max_priority_sheets=self.parameterAsInt(parameters,'PRIORITY_MAX_SHEETS',context),
            skip_invalid_rasters=self.parameterAsBool(parameters,'SKIP_INVALID',context),
            keep_work=self.parameterAsBool(parameters,'KEEP_WORK',context))
        grid_ref=self.parameterAsEnum(parameters,'FOREST_GRID_REF',context)
        if grid_ref in (1,2):
            c['forest_grid_coordinate_reference']=['center','upper_left'][grid_ref-1]
        feedback.pushInfo(f'DEM Map Sheets v{VERSION}: 入力変換を開始します。')
        try:
            manifest=run(c,result_dir,gdal,osr,feedback)
            xyz=''
            if self.parameterAsBool(parameters,'TERRAINRGB',context):
                xyz=str(result_dir/'03_terrainrgb_xyz')
                info=write_terrainrgb_xyz(manifest['merged_dem'],xyz,
                    self.parameterAsInt(parameters,'XYZ_MIN',context),self.parameterAsInt(parameters,'XYZ_MAX',context),
                    self.parameterAsInt(parameters,'XYZ_LIMIT',context),gdal,osr,feedback)
                manifest['terrainrgb_xyz']=info
                (result_dir/'run.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
            feedback.setProgress(100)
            return {'MERGED_DEM':manifest['merged_dem'],'SOURCE_DEMS':str(result_dir/'01_source_dem'),
                    'XYZ_FOLDER':xyz,'MANIFEST':str(result_dir/'run.json')}
        except Exception as exc:
            # Recording the failure must never mask the original
            # exception (M-1b): a corrupt run.json or an unwritable
            # result directory falls back silently.
            try:
                result_dir.mkdir(parents=True,exist_ok=True)
                record=result_dir/'run.json'
                try:
                    failed=json.loads(record.read_text(encoding='utf-8')) if record.exists() else {'merge_priority':c['merge_priority']}
                except Exception:
                    failed={'merge_priority':c['merge_priority']}
                failed.update(status='cancelled' if feedback.isCanceled() else 'failed',error=str(exc))
                record.write_text(json.dumps(failed,ensure_ascii=False,indent=2),encoding='utf-8')
            except Exception as record_exc:
                # The original exception still takes precedence, but the
                # recording failure itself is logged, not silent (r4).
                try:
                    QgsMessageLog.logMessage(
                        f'失敗記録を run.json へ保存できませんでした: {record_exc}',
                        'DEM Map Sheets', Qgis.Warning)
                except Exception:
                    # The message log itself failed; stderr is the
                    # last sink so this is never completely silent.
                    print(f'失敗記録を run.json へ保存できませんでした: {record_exc}',
                          file=sys.stderr)
            raise QgsProcessingException(str(exc)) from exc
