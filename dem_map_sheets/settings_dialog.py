"""Processing dialog with reusable JSON settings. GPL-3.0-only."""
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QDialogButtonBox, QFileDialog, QMessageBox
from qgis.core import QgsCoordinateReferenceSystem, QgsProcessingParameterCrs, QgsProperty
from qgis.gui import QgsProcessingParametersGenerator
from processing.gui.AlgorithmDialog import AlgorithmDialog
from .profiles import save_profile, load_profile, missing_inputs

def portable(value):
    if isinstance(value, QVariant):
        return None if value.isNull() else portable(value.value())
    if isinstance(value, QgsCoordinateReferenceSystem):
        return (value.authid() or value.toWkt()) if value.isValid() else ''
    if isinstance(value, QgsProperty):
        if value.propertyType() == QgsProperty.StaticProperty:
            return portable(value.staticValue())
        raise ValueError('動的な式を含む設定は保存できません。固定値を指定してください。')
    if isinstance(value, dict):
        return {k: portable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [portable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError('保存できない設定値: ' + type(value).__name__)

class SettingsDialog(AlgorithmDialog):
    def __init__(self, alg, parent=None):
        super().__init__(alg, parent=parent)
        self.profile_buttons = []
        for label, callback in [('設定を保存', self.save_settings), ('設定を読み込む', self.load_settings)]:
            button = self.buttonBox().addButton(label, QDialogButtonBox.ActionRole)
            button.clicked.connect(callback)
            self.profile_buttons.append(button)

    def save_settings(self):
        path, _ = QFileDialog.getSaveFileName(self, '設定を保存', 'dem_settings.json', 'JSON (*.json)')
        if not path:
            return
        if not path.lower().endswith('.json'):
            path += '.json'
        try:
            values = self.mainWidget().createProcessingParameters(QgsProcessingParametersGenerator.Flag.SkipValidation)
            save_profile(path, portable(values))
            QMessageBox.information(self, '設定を保存', '設定を保存しました。')
        except Exception as exc:
            QMessageBox.warning(self, '設定を保存できません', str(exc))

    def load_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, '設定を読み込む', '', 'JSON (*.json)')
        if not path:
            return
        try:
            allowed = [p.name() for p in self.algorithm().parameterDefinitions()]
            values = load_profile(path, allowed)
            context = self.mainWidget().processing_context
            for key, value in values.items():
                if key in ('FILES', 'INPUT_FOLDER', 'PRIORITY_METADATA', 'PDAL', 'OUTPUT'):
                    continue
                # An unset output CRS is a valid saved dialog state even
                # though CRS is mandatory when processing starts. Preserve
                # the blank widget state here; processAlgorithm() remains
                # responsible for rejecting execution without a valid CRS.
                definition = self.algorithm().parameterDefinition(key)
                if isinstance(definition, QgsProcessingParameterCrs):
                    # A blank CRS is a valid *saved* dialog state. Nonblank
                    # CRS values must resolve before any widgets are changed.
                    if value is None or value == '':
                        continue
                    if not isinstance(value, str):
                        raise ValueError('設定値が不正です: ' + key)
                    crs = QgsCoordinateReferenceSystem.fromUserInput(value)
                    if not crs.isValid():
                        raise ValueError(
                            f'設定ファイルの {key} をこのPCで解釈できません: {value!r}。'
                            '読み込みを中止しました。現在の設定は変更されていません。'
                            '有効なEPSGコードまたはWKTを指定してください。')
                if not definition.checkValueIsAcceptable(value, context):
                    raise ValueError('設定値が不正です: ' + key)
            self.setParameters(values)
            missing = missing_inputs(values)
            message = '設定を読み込みました。画面で確認してから実行してください。'
            if missing:
                message += '\n\n見つからない入力（修正してください）:\n' + '\n'.join(missing)
            QMessageBox.information(self, '設定を読み込む', message)
        except Exception as exc:
            QMessageBox.warning(self, '設定を読み込めません', str(exc))

    def blockAdditionalControlsWhileRunning(self):
        super().blockAdditionalControlsWhileRunning()
        for button in self.profile_buttons:
            button.setEnabled(False)

    def resetAdditionalGui(self):
        super().resetAdditionalGui()
        for button in getattr(self, 'profile_buttons', []):
            button.setEnabled(True)
