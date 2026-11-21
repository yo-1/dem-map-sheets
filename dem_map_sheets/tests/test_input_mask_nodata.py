"""Input-mask NoData integrity: valid cells NEAR the input's own declared
NoData (PR #26 follow-up, ChatGPT assignment 2026-11-19).

Measured by Claude.ai on Windows 11 / QGIS 3.44.15 / GDAL 3.13.3 with the
merged build (4479364 lineage): an input GeoTIFF declaring NoData -9999
whose valid cells -9999.0005 / -9999.002 / -9999.004 / -9998.996 are read by
GDAL itself as mask 0 (GDAL's NoData comparison carries a tolerance of
about 4 float32 steps), so normalize's Warp VRT rewrites them to the
output NoData (-32768 in the report) and the run completed with no
warning, no collision record and four elevations silently missing.

The output-side window check cannot see this case: the values are near
the INPUT declaration, not the output value. The fix reads the input's
own GDAL mask before warping - but only when that mask is derived
solely from the declared NoData (GMF_NODATA). An explicit mask band, an
alpha band or an external .msk also produces mask-0 cells for reasons
that are the file's own explicit declaration, so those inputs are
outside the check by design and must NOT be stopped (documented
limitation). NaN cells are the non-finite path, never this check.
Cells exactly equal to the declared NoData are the file's own holes.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from input_fixtures import have_gdal, make_geotiff  # noqa: E402

HAVE_GDAL = have_gdal()
if HAVE_GDAL:
    from osgeo import gdal, osr
    from dem_map_sheets.engine.dem_pipeline import run
    from dem_map_sheets.engine.input_sources import DEFAULTS, NoDataCollisionError

# the seven values of the Windows reports; the first four sit inside
# GDAL's tolerance of -9999, the last three outside it
LOST = [-9999.0005, -9999.002, -9999.004, -9998.996]
KEPT = [-9999.008, -9999.016, -9998.99]
SEVEN = LOST + KEPT


def raster(values, fill=100.0):
    a = np.full((20, 20), fill, dtype='float32')
    a[5, 2:2 + len(values)] = np.array(values, dtype='float32')
    return a


@unittest.skipUnless(HAVE_GDAL, 'GDAL (osgeo) is not available')
class InputMaskNoDataCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gdal.UseExceptions()
        osr.UseExceptions()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._n = 0

    def tif(self, values, nodata=-9999.0, name=None, fill=100.0):
        self._n += 1
        return make_geotiff(self.root / (name or f'in{self._n}.tif'), raster(values, fill),
                            gt=(1000.0, 1.0, 0.0, 2020.0, 0.0, -1.0), nodata=nodata)

    def config(self, inputs, **kw):
        c = {**DEFAULTS, 'input_type': 'raster', 'inputs': [str(p) for p in inputs], 'input_crs': '',
             'target_crs': 'EPSG:6677', 'cell_size': 1.0, 'source_nodata': None, 'max_pixels': 25_000_000,
             'normalize_rasters': True}
        c.update(kw)
        return c

    def run_dem(self, inputs, **kw):
        self._n += 1
        out = self.root / f'out{self._n}'
        manifest = run(self.config(inputs, **kw), out, gdal, osr, None)
        ds = gdal.Open(manifest['merged_dem'])
        band = ds.GetRasterBand(1)
        array, nodata = band.ReadAsArray(), band.GetNoDataValue()
        ds = None
        return manifest, array, nodata, out

    def expect_stop(self, inputs, **kw):
        self._n += 1
        out = self.root / f'stop{self._n}'
        with self.assertRaises(NoDataCollisionError) as ctx:
            run(self.config(inputs, **kw), out, gdal, osr, None)
        record = json.loads((out / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(record['status'], 'failed')
        self.assertFalse(list(out.glob('02_merged*')), 'a collision stop must not publish a DEM')
        return ctx.exception, record


class ReproductionTests(InputMaskNoDataCase):
    def test_near_declared_values_stop_the_run_when_output_nodata_differs(self):
        """The reported case: declared -9999, output -32768, 4 of 7 values lost silently."""
        path = self.tif(SEVEN, nodata=-9999.0)
        error, record = self.expect_stop([path], output_nodata=-32768.0)
        detail = record['failure_detail']
        self.assertEqual(len(detail), 1)
        entry = detail[0]
        self.assertEqual(Path(entry['source']).name, path.name)
        self.assertEqual(entry['stage'], 'normalize')
        self.assertEqual(entry['role'], 'input_declared')
        self.assertEqual(entry['declared_nodata'], -9999.0)
        self.assertEqual(entry['count'], 4)
        self.assertEqual([(p['row'], p['col']) for p in entry['positions']],
                         [(5, 2), (5, 3), (5, 4), (5, 5)])
        np.testing.assert_allclose(sorted(entry['values']), sorted(
            float(np.float32(v)) for v in LOST))
        self.assertIn('宣言NoData', record['error'])
        self.assertIn(path.name, record['error'])

    def test_the_same_input_completes_when_the_values_are_outside_the_tolerance(self):
        path = self.tif(KEPT, nodata=-9999.0)
        manifest, array, nodata, _out = self.run_dem([path], output_nodata=-32768.0)
        self.assertEqual(manifest['status'], 'completed')
        self.assertEqual(nodata, -32768.0)
        np.testing.assert_array_equal(array[5, 2:5], np.array(KEPT, dtype='float32'))

    def test_exact_declared_cells_are_the_files_own_holes(self):
        path = self.tif([-9999.0, 50.0, 60.0], nodata=-9999.0)
        manifest, array, nodata, _out = self.run_dem([path], output_nodata=-32768.0)
        self.assertEqual(manifest['status'], 'completed')
        self.assertEqual(array[5, 2], -32768.0)  # missing, converted to the output NoData
        np.testing.assert_array_equal(array[5, 3:5], np.array([50.0, 60.0], dtype='float32'))

    def test_declared_equals_output_still_stops_once_via_the_output_record(self):
        """Disjointness: with declared == output the near cell is reported
        once, by the pre-existing output-collision record - not twice."""
        path = self.tif([-9999.002], nodata=-9999.0)
        _e, record = self.expect_stop([path])  # default output -9999
        detail = record['failure_detail']
        self.assertEqual(len(detail), 1)
        self.assertEqual(detail[0]['role'], 'output')
        self.assertEqual(detail[0]['count'], 1)


class FalsePositiveGuardTests(InputMaskNoDataCase):
    def test_explicit_mask_band_is_not_flagged(self):
        """An internal mask band masking a plain valid value is the file's
        own explicit declaration: outside this check, the run completes."""
        path = self.tif([100.0, 200.0], nodata=-9999.0)
        ds = gdal.Open(str(path), gdal.GA_Update)
        band = ds.GetRasterBand(1)
        band.CreateMaskBand(0)
        mask = np.full((20, 20), 255, dtype='uint8')
        mask[5, 2] = 0  # mask out the 100.0 cell explicitly
        band.GetMaskBand().WriteArray(mask)
        ds.FlushCache()
        ds = None
        manifest, array, nodata, _out = self.run_dem([path], output_nodata=-32768.0)
        self.assertEqual(manifest['status'], 'completed')

    def test_internal_explicit_mask_near_declared_values_fail_fast(self):
        path = self.tif(SEVEN, nodata=-9999.0)
        ds = gdal.Open(str(path), gdal.GA_Update)
        band = ds.GetRasterBand(1)
        with gdal.config_option('GDAL_TIFF_INTERNAL_MASK', 'YES'):
            band.CreateMaskBand(gdal.GMF_PER_DATASET)
            band.GetMaskBand().WriteArray(np.full((20, 20), 255, dtype='uint8'))
        ds = None
        ds = gdal.Open(str(path))
        self.assertTrue(ds.GetRasterBand(1).GetMaskFlags() & gdal.GMF_PER_DATASET)
        ds = None
        _error, record = self.expect_stop([path], output_nodata=-32768.0)
        self.assertEqual(record['failure_detail'][0]['role'], 'input_declared')
        self.assertEqual(record['failure_detail'][0]['count'], 4)

    def test_external_explicit_mask_near_declared_values_fail_fast(self):
        path = self.tif(SEVEN, nodata=-9999.0)
        with gdal.config_option('GDAL_TIFF_INTERNAL_MASK', 'NO'):
            ds = gdal.Open(str(path), gdal.GA_Update)
            band = ds.GetRasterBand(1)
            band.CreateMaskBand(gdal.GMF_PER_DATASET)
            band.GetMaskBand().WriteArray(np.full((20, 20), 255, dtype='uint8'))
            ds = None
        self.assertTrue(Path(str(path) + '.msk').exists())
        _error, record = self.expect_stop([path], output_nodata=-32768.0)
        self.assertEqual(record['failure_detail'][0]['role'], 'input_declared')
        self.assertEqual(record['failure_detail'][0]['count'], 4)

    def test_explicit_mask_exact_declared_hole_does_not_fail(self):
        path = self.tif([-9999.0, 42.0], nodata=-9999.0)
        ds = gdal.Open(str(path), gdal.GA_Update)
        band = ds.GetRasterBand(1)
        band.CreateMaskBand(gdal.GMF_PER_DATASET)
        band.GetMaskBand().WriteArray(np.full((20, 20), 255, dtype='uint8'))
        ds = None
        manifest, _array, _nodata, _out = self.run_dem([path], output_nodata=-32768.0)
        self.assertEqual(manifest['status'], 'completed')

    def test_nan_cells_are_not_flagged(self):
        a = raster([np.nan, np.nan, 42.0])
        self._n += 1
        path = make_geotiff(self.root / f'in{self._n}.tif', a,
                            gt=(1000.0, 1.0, 0.0, 2020.0, 0.0, -1.0), nodata=-9999.0)
        manifest, array, nodata, _out = self.run_dem([path], output_nodata=-32768.0)
        self.assertEqual(manifest['status'], 'completed')
        self.assertEqual(array[5, 2], -32768.0)  # NaN materialized as missing
        self.assertEqual(array[5, 4], 42.0)

    def test_integer_raster_near_values_are_preserved(self):
        """Integer bands compare exactly in GDAL: -9998 next to a declared
        -9999 is ordinary valid data and its mask stays valid."""
        a = np.full((20, 20), 100, dtype='int16')
        a[5, 2:4] = [-9998, -9997]
        self._n += 1
        path = self.root / f'in{self._n}.tif'
        ds = gdal.GetDriverByName('GTiff').Create(str(path), 20, 20, 1, gdal.GDT_Int16)
        ds.SetGeoTransform((1000.0, 1.0, 0.0, 2020.0, 0.0, -1.0))
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(6677)
        ds.SetProjection(srs.ExportToWkt())
        band = ds.GetRasterBand(1)
        band.SetNoDataValue(-9999)
        band.WriteArray(a)
        ds.FlushCache()
        ds = None
        manifest, array, nodata, _out = self.run_dem([path], output_nodata=-32768.0)
        self.assertEqual(manifest['status'], 'completed')
        np.testing.assert_array_equal(array[5, 2:4], np.array([-9998.0, -9997.0], dtype='float32'))

    def test_a_differing_source_nodata_override_disarms_the_check(self):
        """With source_nodata=-999999 the warp's missing definition is the
        override, not the band declaration: the -9999-near values are far
        from it and are preserved as the valid data they are."""
        path = self.tif(SEVEN, nodata=-9999.0)
        manifest, array, nodata, _out = self.run_dem(
            [path], output_nodata=-32768.0, source_nodata=-999999.0)
        self.assertEqual(manifest['status'], 'completed')
        np.testing.assert_array_equal(array[5, 2:9], np.array(SEVEN, dtype='float32'))


class SourceNoDataOverrideRegressionTests(InputMaskNoDataCase):
    """Claude Windows findings F2-F5: explicit srcNodata may drop valid near values."""

    def _override_case(self, band_nodata, mask_mode):
        path = self.tif(SEVEN, nodata=band_nodata)
        if mask_mode != 'none':
            ds = gdal.Open(str(path), gdal.GA_Update)
            with gdal.config_option('GDAL_TIFF_INTERNAL_MASK',
                                    'YES' if mask_mode == 'internal' else 'NO'):
                ds.GetRasterBand(1).CreateMaskBand(gdal.GMF_PER_DATASET)
                ds.GetRasterBand(1).GetMaskBand().WriteArray(
                    np.full((20, 20), 255, dtype='uint8'))
            ds = None
        _error, record = self.expect_stop(
            [path], output_nodata=-32768.0, source_nodata=-9999.0)
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(len(record['failure_detail']), 1)
        entry = record['failure_detail'][0]
        self.assertEqual(entry['role'], 'input_declared')
        self.assertEqual(entry['declared_nodata'], -9999.0)
        self.assertEqual(entry['count'], 4)
        self.assertEqual([(p['row'], p['col']) for p in entry['positions']],
                         [(5, 2), (5, 3), (5, 4), (5, 5)])

    def test_F2_no_band_nodata_internal_mask(self):
        self._override_case(None, 'internal')

    def test_F3_no_band_nodata_no_mask(self):
        self._override_case(None, 'none')

    def test_F4_different_band_nodata_internal_mask(self):
        self._override_case(-32768.0, 'internal')

    def test_F5_different_band_nodata_external_mask(self):
        self._override_case(-32768.0, 'external')


class MixedRoleTests(InputMaskNoDataCase):
    """Both collision kinds in one run (ChatGPT review of PR #27):
    the headline error describes the first entry's kind, so its cell
    count must be that kind's subtotal - never the sum of both kinds -
    and the other kind must be named with its own count. The per-entry
    failure_detail records stay exact either way."""

    def _mixed(self, order):
        # A: declared -999999, a value near the OUTPUT -9999 -> role 'output', 1 cell
        a = self.tif([-9999.002], nodata=-999999.0, name='a.tif')
        # B: declared -32768, values near its OWN declaration, far from the
        # output -9999 -> role 'input_declared', 2 cells
        b = self.tif([-32768.002, -32767.996], nodata=-32768.0, name='b.tif')
        return [a, b] if order == 'ab' else [b, a]

    def test_output_first_reports_each_kind_with_its_own_count(self):
        _e, record = self.expect_stop(self._mixed('ab'))  # default output -9999
        detail = record['failure_detail']
        self.assertEqual(len(detail), 2)
        by_role = {e['role']: e for e in detail}
        self.assertEqual(by_role['output']['count'], 1)
        self.assertEqual(by_role['input_declared']['count'], 2)
        self.assertEqual(by_role['input_declared']['declared_nodata'], -32768.0)
        # headline: 1 cell charged to the output kind, not 3
        self.assertIn('に 1 件あります', record['error'])
        self.assertNotIn('に 3 件あります', record['error'])
        # the other kind is named with its own subtotal
        self.assertIn('宣言NoData近傍の衝突も 2 件', record['error'])

    def test_same_kind_across_files_headline_is_first_file_only(self):
        # The verification case behind the wording fix: first file B holds
        # 2 cells, a second file C holds 4 more of the SAME kind. The
        # headline must read as B's own 2, with 6 stated only as the
        # cross-file subtotal - never as 'Bに6件'.
        b = self.tif([-32768.002, -32767.996], nodata=-32768.0, name='b.tif')
        c = self.tif([-32768.004, -32768.008, -32767.996, -32767.992],
                     nodata=-32768.0, name='c.tif')
        _e, record = self.expect_stop([b, c])
        detail = record['failure_detail']
        self.assertEqual([e['count'] for e in detail], [2, 4])
        self.assertIn('標高セルが 2 件あります', record['error'])
        self.assertNotIn('標高セルが 6 件あります', record['error'])
        self.assertIn('小計は全入力で 6 件', record['error'])

    def test_same_kind_across_files_reversed_order(self):
        b = self.tif([-32768.002, -32767.996], nodata=-32768.0, name='b.tif')
        c = self.tif([-32768.004, -32768.008, -32767.996, -32767.992],
                     nodata=-32768.0, name='c.tif')
        _e, record = self.expect_stop([c, b])
        detail = record['failure_detail']
        self.assertEqual([e['count'] for e in detail], [4, 2])
        self.assertIn('標高セルが 4 件あります', record['error'])
        self.assertNotIn('標高セルが 6 件あります', record['error'])
        self.assertIn('小計は全入力で 6 件', record['error'])

    def test_input_declared_first_reports_each_kind_with_its_own_count(self):
        _e, record = self.expect_stop(self._mixed('ba'))
        detail = record['failure_detail']
        self.assertEqual(len(detail), 2)
        by_role = {e['role']: e for e in detail}
        self.assertEqual(by_role['output']['count'], 1)
        self.assertEqual(by_role['input_declared']['count'], 2)
        # headline: 2 cells charged to the input-declared kind, not 3
        self.assertIn('標高セルが 2 件あります', record['error'])
        self.assertNotIn('標高セルが 3 件あります', record['error'])
        self.assertIn('出力NoData値との衝突も 1 件', record['error'])


if __name__ == '__main__':
    unittest.main()
