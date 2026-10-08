import json
import tempfile
import unittest
from pathlib import Path
from dem_map_sheets.profiles import load_profile, save_profile, resolve_vertical, missing_inputs

class ProfileTests(unittest.TestCase):
    def test_round_trip_and_missing_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'settings.json'
            values = {'CELL': 100, 'CRS': 'EPSG:6677', 'FILES': [str(Path(d)/'missing.asc')],
                      'VERTICAL': '任意の標高基準', 'TERRAINRGB': False, 'NODATA': None}
            save_profile(p, values)
            self.assertEqual(load_profile(p, values), values)
            self.assertEqual(missing_inputs(values), values['FILES'])

    def test_reject_foreign_future_and_unknown_parameters(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'settings.json'
            for payload in [{'format': 'other'}, {'format': 'dem-map-sheets-settings', 'schema_version': 2},
                            {'format': 'dem-map-sheets-settings', 'schema_version': 1, 'parameters': {'UNSUPPORTED': 1}}]:
                p.write_text(json.dumps(payload))
                with self.assertRaises(ValueError):
                    load_profile(p, ['CELL'])

    def test_vertical_default_selection_and_legacy_override(self):
        self.assertEqual(resolve_vertical(0, ''), '不明・未確認')
        self.assertEqual(resolve_vertical(2, ''), '測地成果2024（標高）')
        self.assertEqual(resolve_vertical(0, ' 旧設定の基準 '), '旧設定の基準')
        with self.assertRaises(ValueError):
            resolve_vertical(4, '')
