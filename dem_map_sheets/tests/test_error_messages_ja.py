"""Field test (6d1809c differential): two error paths were still
English-only. Entry errors keep the English technical message and
carry a Japanese cause/remediation note."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

from dem_map_sheets.engine.dem_pipeline import _merge_auto
from dem_map_sheets.engine.input_sources import DEFAULTS, discover, validate_input


def _cfg(**kw):
    c = dict(DEFAULTS)
    c.update(input_type='raster', recursive=False, max_input_files=100)
    c.update(kw)
    return c


class DiscoverMessageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_missing_input_file_message_is_bilingual(self):
        with self.assertRaises(ValueError) as ctx:
            discover([str(self.root / 'missing.tif')], '', _cfg())
        self.assertIn('No input matched', str(ctx.exception))
        self.assertIn('入力ファイルが見つかりません', str(ctx.exception))

    def test_folder_without_supported_files_message_is_bilingual(self):
        folder = self.root / 'empty_folder'
        folder.mkdir()
        (folder / 'note.txt').write_text('x', encoding='utf-8')
        with self.assertRaises(ValueError) as ctx:
            discover([str(folder)], '', _cfg())
        self.assertIn('No supported files', str(ctx.exception))
        self.assertIn('対応形式のファイルがありません', str(ctx.exception))


class AutoPriorityMessageTests(unittest.TestCase):
    def test_no_nonempty_sheets_message_is_bilingual(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / 'empty.tif'
            ds = gdal.GetDriverByName('GTiff').Create(str(src), 5, 5, 1, gdal.GDT_Float32)
            ds.SetGeoTransform((-1000.0, 10.0, 0.0, 1000.0, 0.0, -10.0))
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(6677)
            ds.SetProjection(srs.ExportToWkt())
            band = ds.GetRasterBand(1)
            band.SetNoDataValue(-999999.0)
            band.WriteArray(np.full((5, 5), -999999.0, dtype='float32'))
            ds = None
            c = dict(DEFAULTS)
            c.update(target_crs='EPSG:6677', cell_size=10.0)
            groups = [dict(vrt=str(src), metadata={}, input_report={}, source=str(src))]
            out = root / 'out'
            out.mkdir()
            with self.assertRaises(ValueError) as ctx:
                _merge_auto(c, groups, out, gdal, osr, None)
            self.assertIn('no nonempty map sheets', str(ctx.exception))
            self.assertIn('図郭が1件もありませんでした', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()


class InputCrsMessageTests(unittest.TestCase):
    def test_text_without_input_crs_is_bilingual(self):
        c = _cfg(input_type='text', input_crs='')
        with self.assertRaises(ValueError) as ctx:
            validate_input(c)
        self.assertIn('requires input_crs', str(ctx.exception))
        self.assertIn('入力CRS（元の座標系）が必須です', str(ctx.exception))
        self.assertIn('指定してください', str(ctx.exception))
