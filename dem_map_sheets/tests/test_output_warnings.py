"""All-NoData output detection: a run whose merged DEM contains no
valid elevation cells must warn instead of looking successful
(Windows field test, round 1: a 5x5 m input at a 10 m cell produced
a 1x1 all-NoData DEM with status 'completed' and no warning)."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

from dem_map_sheets.engine.dem_pipeline import run
from dem_map_sheets.engine.input_sources import DEFAULTS


class _Feedback:
    def __init__(self):
        self.warnings = []

    def isCanceled(self):
        return False

    def setProgress(self, _value):
        pass

    def pushWarning(self, message):
        self.warnings.append(message)

    def pushInfo(self, _message):
        pass


def _small_tif(path, fill=100.0):
    ds = gdal.GetDriverByName('GTiff').Create(str(path), 5, 5, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((0.0, 1.0, 0.0, 5.0, 0.0, -1.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(6677)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(-9999.0)
    band.WriteArray(np.full((5, 5), fill, dtype='float32'))
    ds = None
    return path


def _cfg(src, cell):
    c = dict(DEFAULTS)
    c.update(input_type='raster', inputs=[str(src)], input_crs='EPSG:6677',
             target_crs='EPSG:6677', cell_size=cell, source_nodata=-9999.0,
             max_pixels=10 ** 9)
    return c


class AllNoDataWarningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.src = _small_tif(self.root / 'small.tif')

    def test_tiny_input_at_coarse_cell_warns_but_completes(self):
        feedback = _Feedback()
        manifest = run(_cfg(self.src, 10.0), self.root / 'out10',
                       gdal, osr, feedback)
        self.assertEqual(manifest['status'], 'completed')
        self.assertEqual(manifest['warnings'], ['all_nodata_output'])
        self.assertEqual(len(feedback.warnings), 1)
        self.assertIn('NoData', feedback.warnings[0])

    def test_same_input_at_fine_cell_does_not_warn(self):
        feedback = _Feedback()
        manifest = run(_cfg(self.src, 2.0), self.root / 'out2',
                       gdal, osr, feedback)
        self.assertEqual(manifest['warnings'], [])
        self.assertEqual(feedback.warnings, [])

    def test_genuinely_empty_source_also_warns(self):
        empty = _small_tif(self.root / 'empty.tif', fill=-9999.0)
        feedback = _Feedback()
        manifest = run(_cfg(empty, 2.0), self.root / 'out_empty',
                       gdal, osr, feedback)
        self.assertEqual(manifest['warnings'], ['all_nodata_output'])


if __name__ == '__main__':
    unittest.main()
