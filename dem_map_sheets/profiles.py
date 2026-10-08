"""Portable settings, independent of QGIS. GPL-3.0-only."""
import json
from pathlib import Path
from .version import VERSION

VERTICAL_NAMES = ['不明・未確認', '測地成果2011（標高）', '測地成果2024（標高）', '検証用仮想標高']

def resolve_vertical(mode, text):
    if text.strip():
        return text.strip()
    if mode == 4:
        raise ValueError('「その他」の標高基準名を追加入力してください。')
    return VERTICAL_NAMES[mode]

def save_profile(path, parameters):
    payload = {'format': 'dem-map-sheets-settings', 'schema_version': 1,
               'plugin_version': VERSION, 'parameters': parameters}
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    Path(path).write_text(text + '\n', encoding='utf-8')

def load_profile(path, allowed):
    payload = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(payload, dict) or payload.get('format') != 'dem-map-sheets-settings' or payload.get('schema_version') != 1:
        raise ValueError('DEM Map Sheets設定JSON（schema_version=1）を選択してください。')
    values = payload.get('parameters')
    if not isinstance(values, dict):
        raise ValueError('parametersが不正です。')
    unknown = set(values) - set(allowed)
    if unknown:
        raise ValueError('未対応の設定項目: ' + ', '.join(sorted(unknown)))
    for key in ('INPUT_FOLDER', 'PRIORITY_METADATA', 'PDAL', 'OUTPUT'):
        if key in values and values[key] is not None and not isinstance(values[key], str):
            raise ValueError('パスの形式が不正です: ' + key)
    files = values.get('FILES')
    if files is not None and (not isinstance(files, list) or not all(isinstance(p, str) for p in files)):
        raise ValueError('FILESはファイルパスの配列で指定してください。')
    return values

def missing_inputs(values):
    paths = list(values.get('FILES') or [])
    paths += [values.get(k) for k in ('INPUT_FOLDER', 'PRIORITY_METADATA', 'PDAL') if values.get(k)]
    return [str(p) for p in paths if not Path(p).exists()]
