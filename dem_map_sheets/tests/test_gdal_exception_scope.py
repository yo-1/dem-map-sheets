"""W-13: GDAL exception state is process-global. The plugin must not
flip it for the whole QGIS session (the old algorithm.py called
gdal.UseExceptions() and never restored it). run() scopes the
setting with ExceptionMgr and restores the caller's state exactly,
on success and on failure."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from osgeo import gdal, osr

from dem_map_sheets.engine.dem_pipeline import run
from dem_map_sheets.engine.input_sources import DEFAULTS


def _tiny_tif(path):
    ds = gdal.GetDriverByName('GTiff').Create(str(path), 5, 5, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((0.0, 1.0, 0.0, 5.0, 0.0, -1.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(6677)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(-9999.0)
    band.WriteArray(np.full((5, 5), 100.0, dtype='float32'))
    ds = None
    return path


def _cfg(src):
    c = dict(DEFAULTS)
    c.update(input_type='raster', inputs=[str(src)], input_crs='EPSG:6677',
             target_crs='EPSG:6677', cell_size=2.0, source_nodata=-9999.0,
             max_pixels=10 ** 9)
    return c


@unittest.skipUnless(hasattr(gdal, 'ExceptionMgr'), 'ExceptionMgr unavailable')
class ExceptionScopeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.src = _tiny_tif(self.root / 'tiny.tif')
        self._before = gdal.GetUseExceptions()
        self.addCleanup(self._restore)

    def _restore(self):
        if gdal.GetUseExceptions() != self._before:
            (gdal.UseExceptions if self._before else gdal.DontUseExceptions)()

    def test_state_off_is_restored_after_success(self):
        gdal.DontUseExceptions()
        manifest = run(_cfg(self.src), self.root / 'out', gdal, osr, None)
        self.assertEqual(manifest['status'], 'completed')
        self.assertEqual(gdal.GetUseExceptions(), 0)

    def test_state_on_is_kept_after_success(self):
        gdal.UseExceptions()
        run(_cfg(self.src), self.root / 'out', gdal, osr, None)
        self.assertEqual(gdal.GetUseExceptions(), 1)

    def test_state_is_restored_after_failure(self):
        gdal.DontUseExceptions()
        bad = _cfg(self.src)
        bad['target_crs'] = 'EPSG:99999'
        with self.assertRaises(ValueError):
            run(bad, self.root / 'out', gdal, osr, None)
        self.assertEqual(gdal.GetUseExceptions(), 0)

    def test_algorithm_does_not_set_global_exceptions(self):
        source = (Path(__file__).resolve().parent.parent / 'algorithm.py').read_text(encoding='utf-8')
        self.assertNotIn('gdal.UseExceptions()', source)


if __name__ == '__main__':
    unittest.main()
