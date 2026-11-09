"""Run inside the QGIS Python console, after enabling rc4 and restarting QGIS.

Uses real Processing widgets; intercepts only file pickers and message boxes.
Does not start DEM processing or change the installed plugin.
"""
from pathlib import Path
from unittest.mock import patch
import json
import platform
import traceback
import uuid

from qgis.core import Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsProperty
from qgis.gui import QgsProcessingParametersGenerator
from qgis.PyQt.QtWidgets import QApplication, QFileDialog, QMessageBox
from osgeo import gdal
from dem_map_sheets.version import VERSION
from dem_map_sheets.settings_dialog import SettingsDialog, portable
from dem_map_sheets.profiles import resolve_vertical


def check_settings():
    root = Path(__file__).resolve().parent / 'qgis_results' / ('settings_' + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    checks, messages = [], []
    dialog = None

    def check(name, callback):
        try:
            callback()
            checks.append(dict(name=name, status='passed'))
        except Exception:
            checks.append(dict(name=name, status='failed', traceback=traceback.format_exc()))

    def require(value, detail):
        if not value:
            raise AssertionError(detail)

    def message(kind):
        def collect(parent, title, text, *args, **kwargs):
            messages.append(dict(kind=kind, title=str(title), text=str(text)))
            return QMessageBox.Ok
        return collect

    try:
        require(VERSION == '0.2.1-rc4', 'rc4をインストールしてQGISを再起動してください: ' + VERSION)
        algorithm = QgsApplication.processingRegistry().createAlgorithmById('demmapsheets:create')
        require(algorithm is not None, 'DEM Map Sheetsを有効にしてください。')
        dialog = SettingsDialog(algorithm)
        dialog.show()
        QApplication.processEvents()
        check('dialog_title_and_buttons', lambda: require(
            VERSION in dialog.windowTitle() and [b.text() for b in dialog.profile_buttons] ==
            ['設定を保存', '設定を読み込む'], dialog.windowTitle()))
        check('crs_portable', lambda: require(portable(QgsCoordinateReferenceSystem('EPSG:6677')) == 'EPSG:6677', 'CRS'))
        check('static_property', lambda: require(portable(QgsProperty.fromValue(2.5)) == 2.5, 'Static property'))

        def dynamic():
            try:
                portable(QgsProperty.fromExpression('1+1'))
            except ValueError:
                return
            raise AssertionError('Dynamic expression accepted')
        check('dynamic_property_rejected', dynamic)
        check('vertical_other', lambda: require(resolve_vertical(4, '試験基準') == '試験基準', '標高基準'))
        source = root / 'dummy.asc'
        source.write_text('UI test placeholder; do not process this file.\n', encoding='utf-8')
        params = dict(INPUT_TYPE=0, FILES=[str(source)], INPUT_CRS='EPSG:6677', CRS='EPSG:6677',
                      CELL=2.5, VERTICAL_MODE=4, VERTICAL='試験基準', MERGE_PRIORITY=2,
                      TERRAINRGB=False, OUTPUT=str(root / 'output'))
        profile = root / 'settings.json'

        with patch.object(QMessageBox, 'information', side_effect=message('information')), \
             patch.object(QMessageBox, 'warning', side_effect=message('warning')):
            def roundtrip():
                dialog.setParameters(params)
                with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(profile), 'JSON')):
                    dialog.profile_buttons[0].click()
                require(profile.is_file(), messages)
                saved = json.loads(profile.read_text(encoding='utf-8'))
                require(saved['plugin_version'] == VERSION, saved)
                for key, expected in params.items():
                    require(saved['parameters'].get(key) == expected, (key, saved['parameters'].get(key)))
                dialog.setParameters({**params, 'CELL':5.0, 'VERTICAL_MODE':0, 'VERTICAL':''})
                with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(profile), 'JSON')):
                    dialog.profile_buttons[1].click()
                current = portable(dialog.mainWidget().createProcessingParameters(
                    QgsProcessingParametersGenerator.Flag.SkipValidation))
                for key, expected in params.items():
                    require(current.get(key) == expected, (key, current.get(key)))
                require(not any(m['kind'] == 'warning' for m in messages), messages)
            check('save_load_real_widgets', roundtrip)

            def unset_crs_roundtrip():
                # A blank required CRS is a valid saved dialog state. It must
                # round-trip through JSON, while the algorithm still rejects
                # execution until the user selects a CRS.
                blank_profile = root / 'settings_unset_crs.json'
                blank_params = {**params, 'CRS': ''}
                dialog.setParameters(blank_params)
                with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(blank_profile), 'JSON')):
                    dialog.profile_buttons[0].click()
                require(blank_profile.is_file(), 'Blank-CRS profile was not saved')
                saved = json.loads(blank_profile.read_text(encoding='utf-8'))
                require(saved['parameters'].get('CRS') == '', saved)
                dialog.setParameters(params)
                before = len(messages)
                with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(blank_profile), 'JSON')):
                    dialog.profile_buttons[1].click()
                current = portable(dialog.mainWidget().createProcessingParameters(
                    QgsProcessingParametersGenerator.Flag.SkipValidation))
                require(current.get('CRS') == '', current)
                require(not any(m['kind'] == 'warning' for m in messages[before:]), messages[before:])
                require(not algorithm.parameterDefinition('CRS').checkValueIsAcceptable('', dialog.mainWidget().processing_context),
                        'Unset CRS must remain invalid for processing')
            check('unset_crs_save_load_roundtrip', unset_crs_roundtrip)

            def invalid_crs_preserves_settings():
                valid_profile = root / 'settings_invalid_crs.json'
                baseline = {**params, 'CRS': 'EPSG:6677', 'INPUT_CRS': 'EPSG:6677'}
                for key in ('CRS', 'INPUT_CRS'):
                    for bad_value in ('not-a-crs', 'EPSG:99999', 'PROJCS["x"]'):
                        dialog.setParameters(baseline)
                        before = portable(dialog.mainWidget().createProcessingParameters(
                            QgsProcessingParametersGenerator.Flag.SkipValidation))
                        invalid_values = {**before, key: bad_value}
                        from dem_map_sheets.profiles import save_profile
                        save_profile(valid_profile, invalid_values)
                        msg_start = len(messages)
                        with patch.object(QFileDialog, 'getOpenFileName',
                                          return_value=(str(valid_profile), 'JSON')):
                            dialog.profile_buttons[1].click()
                        after = portable(dialog.mainWidget().createProcessingParameters(
                            QgsProcessingParametersGenerator.Flag.SkipValidation))
                        require(before == after, (key, bad_value, before, after))
                        require(any(m['kind'] == 'warning' and key in m['text']
                                    for m in messages[msg_start:]), (key, bad_value, messages[msg_start:]))
            check('invalid_crs_preserves_widgets', invalid_crs_preserves_settings)

            def missing():
                require(profile.exists(), 'Roundtrip must pass first')
                source.unlink(missing_ok=True)
                before = len(messages)
                with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(profile), 'JSON')):
                    dialog.load_settings()
                require(any('見つからない入力' in m['text'] for m in messages[before:]), messages[before:])
            check('missing_input_notification', missing)

            def invalid():
                before_params = portable(dialog.mainWidget().createProcessingParameters(
                    QgsProcessingParametersGenerator.Flag.SkipValidation))
                bad = root / 'bad.json'
                bad.write_text('{"format":"wrong"}', encoding='utf-8')
                before = len(messages)
                with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(bad), 'JSON')):
                    dialog.load_settings()
                after_params = portable(dialog.mainWidget().createProcessingParameters(
                    QgsProcessingParametersGenerator.Flag.SkipValidation))
                require(any(m['kind'] == 'warning' for m in messages[before:]), 'No rejection')
                require(before_params == after_params, 'Invalid profile changed widgets')
            check('invalid_profile_rejected_without_change', invalid)
    except Exception:
        checks.append(dict(name='setup', status='failed', traceback=traceback.format_exc()))
    finally:
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        result = dict(plugin_version=VERSION, qgis=Qgis.QGIS_VERSION, python=platform.python_version(),
                      platform=platform.platform(), gdal=gdal.VersionInfo('RELEASE_NAME'),
                      checks=checks, messages=messages)
        report = root / 'qgis_settings_results.json'
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Settings:', sum(c['status']=='passed' for c in checks), '/', len(checks), '; report:', report)
    if any(c['status'] != 'passed' for c in checks):
        raise AssertionError('Settings check failed; see ' + str(report))
    return result


if __name__ == '__main__':
    check_settings()
