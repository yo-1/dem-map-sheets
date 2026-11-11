"""Forestry grid coordinate reference (coordinator spec 2026-11-10).

Independent expected values: a 2x2 grid, base X=1000 m, Y=2000 m,
elevations [[101,102],[103,104]], cell sizes 0.5/1/5 m. Input points
are X={1000,1000+s}, Y={2000,2000-s}. The produced grid GeoTIFF is
2x2 with GeoTransform (x0, s, 0, y0, 0, -s), where center gives
(x0,y0)=(1000-s/2, 2000+s/2) and upper_left gives (1000, 2000).
Tolerance 1e-7 m; elevations, NoData and CRS are checked too."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

from dem_map_sheets.engine.input_sources import DEFAULTS, prepare_inputs, validate_input

CASES = [(0.5, (999.75, 2000.25)), (1.0, (999.5, 2000.5)), (5.0, (997.5, 2002.5))]


def _csv(path, s):
    rows = ['X,Y,Z',
            '1000,2000,101',
            f'{1000 + s},2000,102',
            f'1000,{2000 - s},103',
            f'{1000 + s},{2000 - s},104']
    path.write_text('\n'.join(rows) + '\n', encoding='utf-8')
    return path


class ForestGridReferenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._n = 0

    def _prepare(self, s, **kw):
        self._n += 1
        csv = _csv(self.root / f'g{self._n}.csv', s)
        c = dict(DEFAULTS)
        c.update(input_type='forest', inputs=[str(csv)], input_crs='EPSG:6677',
                 target_crs='EPSG:6677', cell_size=s, forest_axis_order='east_north',
                 forest_grid_cell=str(s), max_pixels=10 ** 9)
        c.update(kw)
        work = self.root / f'work{self._n}'
        prepared, report = prepare_inputs(c, work, gdal, osr)
        raws = sorted(work.glob('forest_grid_*.tif'))
        self.assertEqual(len(raws), 1)
        ds = gdal.Open(str(raws[0]))
        array = ds.ReadAsArray()
        gt = ds.GetGeoTransform()
        nodata = ds.GetRasterBand(1).GetNoDataValue()
        authid = ds.GetSpatialRef().GetAuthorityCode(None)
        size = (ds.RasterXSize, ds.RasterYSize)
        ds = None
        return array, gt, nodata, authid, size, report

    def _check(self, s, mode, origin):
        array, gt, nodata, authid, size, report = self._prepare(
            s, forest_grid_coordinate_reference=mode)
        expected_gt = (origin[0], s, 0.0, origin[1], 0.0, -s)
        for got, want in zip(gt, expected_gt):
            self.assertAlmostEqual(got, want, delta=1e-7)
        self.assertEqual(size, (2, 2))
        np.testing.assert_array_equal(array, np.array([[101, 102], [103, 104]]))
        self.assertEqual(nodata, -999999.0)  # the raw forest_grid intermediate keeps the canonical sentinel
        self.assertEqual(authid, '6677')
        detected = report['detected'][0]
        self.assertEqual(detected['grid_cell'], s)
        self.assertEqual(detected['coordinate_reference'], mode)
        self.assertEqual(detected['coordinate_reference_source'], 'explicit')

    def test_center_origins(self):
        for s, origin in CASES:
            with self.subTest(s=s):
                self._check(s, 'center', origin)

    def test_upper_left_origins(self):
        for s, _origin in CASES:
            with self.subTest(s=s):
                self._check(s, 'upper_left', (1000.0, 2000.0))

    def test_default_is_center_and_recorded_as_default(self):
        s, origin = CASES[1]
        array, gt, nodata, authid, size, report = self._prepare(s)
        self.assertAlmostEqual(gt[0], origin[0], delta=1e-7)
        self.assertAlmostEqual(gt[3], origin[1], delta=1e-7)
        detected = report['detected'][0]
        self.assertEqual(detected['coordinate_reference'], 'center')
        self.assertEqual(detected['coordinate_reference_source'], 'default')

    def test_invalid_reference_rejected_in_japanese(self):
        c = dict(DEFAULTS)
        c.update(forest_grid_coordinate_reference='middle')
        with self.assertRaises(ValueError) as ctx:
            validate_input(c)
        self.assertIn('upper_left', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
