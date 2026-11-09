"""M-2 regressions: text_grid() pixel limit and parameter validation.

The limit must be enforced from the data extent BEFORE the float32
memmap backing file is allocated, so a huge or outlier-stretched grid
is rejected without touching disk or memory.
"""
import tempfile
import unittest
from pathlib import Path

from osgeo import gdal, osr

from dem_map_sheets.engine.input_sources import DEFAULTS, text_grid


class TextGridLimitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write(self, name, rows):
        path = self.dir / name
        path.write_text('X,Y,Z\n' + ''.join(r + '\n' for r in rows), encoding='utf-8')
        return str(path)

    def _cfg(self, **kw):
        c = dict(DEFAULTS)
        c.update(input_crs='EPSG:6677', source_nodata=None,
                 text_grid_cell=10.0, max_input_pixels=4)
        c.update(kw)
        return c

    def test_grid_at_limit_is_accepted(self):
        src = self._write('ok.csv', ['0,0,100', '10,0,101', '0,10,102', '10,10,103'])
        dest = self.dir / 'ok.tif'
        count = text_grid(src, dest, self._cfg(), gdal, osr, None)
        self.assertEqual(count, 4)
        self.assertTrue(dest.is_file())
        self.assertFalse(Path(str(dest) + '.grid.bin').exists())

    def test_grid_over_limit_is_rejected_before_allocation(self):
        src = self._write('over.csv', ['0,0,100', '10,0,101', '0,10,102', '10,10,103'])
        dest = self.dir / 'over.tif'
        with self.assertRaises(ValueError):
            text_grid(src, dest, self._cfg(max_input_pixels=3), gdal, osr, None)
        self.assertFalse(dest.exists())
        self.assertFalse(Path(str(dest) + '.grid.bin').exists())

    def test_huge_extent_from_outlier_is_rejected(self):
        src = self._write('outlier.csv', ['0,0,100', '10,0,101', '1000000,1000000,102'])
        dest = self.dir / 'outlier.tif'
        with self.assertRaises(ValueError):
            text_grid(src, dest, self._cfg(max_input_pixels=25000000), gdal, osr, None)
        self.assertFalse(Path(str(dest) + '.grid.bin').exists())

    def test_overflowing_dimensions_raise_value_error(self):
        src = self._write('tiny.csv', ['0,0,100', '1,0,101'])
        with self.assertRaises(ValueError):
            text_grid(src, self.dir / 'tiny.tif',
                      self._cfg(text_grid_cell=1e-320), gdal, osr, None)

    def test_invalid_step_is_rejected(self):
        src = self._write('step.csv', ['0,0,100', '10,0,101'])
        for step in (0.0, -10.0, float('nan'), float('inf')):
            with self.subTest(step=step), self.assertRaises(ValueError):
                text_grid(src, self.dir / 'step.tif',
                          self._cfg(text_grid_cell=step), gdal, osr, None)


if __name__ == '__main__':
    unittest.main()
