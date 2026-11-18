"""Raster-path NoData integrity: valid cells NEAR the output NoData (Issue #9 / PR #10).

Reproduces the Windows 11 / QGIS 3.44 / GDAL 3.13.3 finding on 2849ef4: with
output NoData -9999, valid input cells -9999.0005 / -9999.002 / -9999.004 /
-9998.996 silently became NoData while -9999.008 / -9999.016 / -9998.99 were
kept, and run.json said "completed".  GDAL (BuildVRT srcNodata) compares floats
with a tolerance, so a cell merely *near* the NoData value is read back as
missing; the old checks only counted exact equality.

Where it was lost (measured, GDAL 3.8.4): normalize and materialize keep the
values (they only tag the file NoData=-9999); merge_geotiffs (BuildVRT with
srcNodata=output NoData) drops them; a pass-through file keeps them but any
consumer reads them as masked.  Hence the check must run BEFORE the NoData tag
or merge is applied: normalize scans the input, materialize scans the
output.  A cell that cannot be preserved stops the run (no value is
replaced, interpolated or invented).

Also pins that the collision window really covers what the installed GDAL
treats as NoData, for several values (cross-version safety: this file runs
in the GDAL 3.8.4, 3.10 and 3.13 CI jobs).
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
nodata_window = None
if HAVE_GDAL:
    from osgeo import gdal, osr
    from dem_map_sheets.engine.dem_pipeline import materialize_geotiff, run
    from dem_map_sheets.engine.input_sources import DEFAULTS, NoDataCollisionError
    try:
        from dem_map_sheets.engine._constants import nodata_window
    except ImportError:  # pre-fix code (33e370a) has no window: the reproduction tests still run there
        nodata_window = None

# the seven values of the Windows report; the first four were lost
LOST = [-9999.0005, -9999.002, -9999.004, -9998.996]
KEPT = [-9999.008, -9999.016, -9998.99]
SEVEN = LOST + KEPT


def raster(values, fill=100.0):
    a = np.full((20, 20), fill, dtype='float32')
    a[5, 2:2 + len(values)] = np.array(values, dtype='float32')
    return a


@unittest.skipUnless(HAVE_GDAL, 'GDAL (osgeo) is not available')
class RasterNearNoDataCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gdal.UseExceptions()
        osr.UseExceptions()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._n = 0

    def tif(self, values, nodata=-999999.0, name=None, fill=100.0):
        self._n += 1
        return make_geotiff(self.root / (name or f'in{self._n}.tif'), raster(values, fill),
                            gt=(1000.0, 1.0, 0.0, 2020.0, 0.0, -1.0), nodata=nodata)

    def config(self, inputs, **kw):
        c = {**DEFAULTS, 'input_type': 'raster', 'inputs': [str(p) for p in inputs], 'input_crs': '',
             'target_crs': 'EPSG:6677', 'cell_size': 1.0, 'source_nodata': None, 'max_pixels': 25_000_000,
             # the QGIS algorithm always sets normalize_rasters=True; pass-through is the API-only path
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


class WindowsReportReproductionTests(RasterNearNoDataCase):
    def test_the_four_lost_values_stop_the_run_for_every_input_nodata_declaration(self):
        """Pre-fix: completed with 4 cells silently missing (all declarations, all priorities)."""
        for declared in (-999999.0, -32768.0, -9999.0):
            for priority in (0, 1, 2):
                priority_mode = ['last', 'first', 'auto'][priority]
                with self.subTest(declared=declared, merge_priority=priority_mode):
                    path = self.tif(SEVEN, nodata=declared)
                    error, record = self.expect_stop([path], merge_priority=priority_mode)
                    detail = record['failure_detail']
                    self.assertEqual(len(detail), 1)
                    entry = detail[0]
                    self.assertEqual(Path(entry['source']).name, path.name)
                    self.assertEqual(entry['stage'], 'normalize')
                    self.assertEqual(entry['output_nodata'], -9999.0)
                    # exactly the four cells GDAL would have dropped, located in the input grid
                    self.assertEqual(entry['count'], 4)
                    self.assertEqual([(p['row'], p['col']) for p in entry['positions']],
                                     [(5, 2), (5, 3), (5, 4), (5, 5)])
                    np.testing.assert_allclose(sorted(entry['values']), sorted(
                        float(np.float32(v)) for v in LOST))
                    self.assertAlmostEqual(entry['positions'][0]['x'], 1002.5)
                    self.assertAlmostEqual(entry['positions'][0]['y'], 2014.5)
                    self.assertIn(path.name, record['error'])
                    self.assertIn('-9999.0020', record['error'])

    def test_values_outside_the_window_are_preserved_exactly(self):
        """-9999.008 / -9999.016 / -9998.99 survived before and must still survive, unchanged."""
        for declared in (-999999.0, -32768.0):
            with self.subTest(declared=declared):
                path = self.tif(KEPT, nodata=declared)
                manifest, array, nodata, _out = self.run_dem([path])
                self.assertEqual(manifest['status'], 'completed')
                self.assertEqual(nodata, -9999.0)
                np.testing.assert_array_equal(array[5, 2:5], np.array(KEPT, dtype='float32'))
                self.assertEqual(int((array == -9999.0).sum()), 0)

    def test_with_output_nodata_32768_all_seven_values_are_preserved(self):
        """The Windows control case: nothing is near -32768, so nothing is lost or blocked."""
        path = self.tif(SEVEN, nodata=-999999.0)
        manifest, array, nodata, _out = self.run_dem([path], output_nodata=-32768.0)
        self.assertEqual(nodata, -32768.0)
        np.testing.assert_array_equal(array[5, 2:9], np.array(SEVEN, dtype='float32'))

    def test_collision_is_relative_to_the_configured_output_nodata(self):
        path = self.tif([-32768.002, -32768.5], nodata=-999999.0)
        self.expect_stop([path], output_nodata=-32768.0)
        manifest, array, nodata, _out = self.run_dem([path])  # default -9999: fine
        self.assertEqual(manifest['status'], 'completed')
        np.testing.assert_array_equal(array[5, 2:4], np.array([-32768.002, -32768.5], dtype='float32'))

    def test_exact_value_is_still_a_collision(self):
        path = self.tif([-9999.0], nodata=-999999.0)
        _e, record = self.expect_stop([path])
        self.assertEqual(record['failure_detail'][0]['count'], 1)

    def test_the_declared_input_nodata_itself_is_missing_not_a_collision(self):
        path = self.tif([-9999.0], nodata=-9999.0)  # declared: these are the file's own holes
        manifest, array, nodata, _out = self.run_dem([path])
        self.assertEqual(manifest['status'], 'completed')
        self.assertEqual(array[5, 2], -9999.0)  # still missing
        self.assertEqual(nodata, -9999.0)

    def test_declared_nodata_does_not_excuse_a_neighbouring_valid_value(self):
        path = self.tif([-9999.0, -9999.002], nodata=-9999.0)
        _e, record = self.expect_stop([path])
        self.assertEqual(record['failure_detail'][0]['count'], 1)
        self.assertEqual(record['failure_detail'][0]['positions'][0]['col'], 3)

    def test_source_nodata_override_is_honoured_the_same_way(self):
        path = self.tif([-9999.0, -9999.002], nodata=None)
        _e, record = self.expect_stop([path], source_nodata=-9999.0)
        self.assertEqual(record['failure_detail'][0]['count'], 1)

    def test_one_bad_input_among_several_is_named(self):
        good = self.tif([], name='good.tif')
        bad = self.tif([-9999.004], name='bad.tif')
        error, record = self.expect_stop([good, bad])
        self.assertEqual([Path(e['source']).name for e in record['failure_detail']], ['bad.tif'])
        self.assertIn('bad.tif', record['error'])

    def test_every_collision_across_inputs_is_recorded(self):
        a = self.tif([-9999.001], name='a.tif')
        b = self.tif([-9999.002, -9998.998], name='b.tif')
        _e, record = self.expect_stop([a, b])
        self.assertEqual({Path(e['source']).name: e['count'] for e in record['failure_detail']},
                         {'a.tif': 1, 'b.tif': 2})
        self.assertIn('ほか 1 件', record['error'])

    def test_negative_positive_zero_and_small_nodata_values(self):
        cases = [(9999.0, [9999.002, 10000.0]), (0.0, [0.0]), (-1.0, [-1.0000004, -0.5]),
                 (1.5, [1.5000003, 1.6])]
        for out_nd, values in cases:
            with self.subTest(output_nodata=out_nd):
                path = self.tif([values[0]], nodata=-999999.0)
                self.expect_stop([path], output_nodata=out_nd)
                ok = self.tif(values[1:], nodata=-999999.0)
                manifest, *_ = self.run_dem([ok], output_nodata=out_nd)
                self.assertEqual(manifest['status'], 'completed')


class PassThroughAndMaterializeTests(RasterNearNoDataCase):
    def test_pass_through_materialize_refuses_to_tag_near_values_as_nodata(self):
        """pass-through (normalize_rasters=False, no input_crs): the file keeps its values and gets
        NoData=-9999 - which would mask the near values.  Stop and publish nothing."""
        path = self.tif(SEVEN, nodata=-9999.0)
        dest = self.root / 'pt.tif'
        notes = []
        with self.assertRaises(NoDataCollisionError) as ctx:
            materialize_geotiff(path, dest, gdal, -9999.0, notes)
        self.assertFalse(dest.exists())
        self.assertFalse(Path(str(dest) + '.part').exists())
        entry = ctx.exception.detail[0]
        self.assertEqual((entry['stage'], entry['count']), ('materialize', 4))
        self.assertEqual([(p['row'], p['col']) for p in entry['positions']], [(5, 2), (5, 3), (5, 4), (5, 5)])
        self.assertEqual(notes[0]['nodata_collision_cells'], 4)

    def test_pass_through_run_stops_with_failure_detail(self):
        path = self.tif(SEVEN, nodata=-9999.0)
        error, record = self.expect_stop([path], normalize_rasters=False)
        self.assertEqual(record['failure_detail'][0]['stage'], 'materialize')
        self.assertEqual(record['failure_detail'][0]['count'], 4)

    def test_pass_through_keeps_values_outside_the_window_and_the_files_own_holes(self):
        path = self.tif([-9999.0] + KEPT, nodata=-9999.0)
        dest = self.root / 'pt_ok.tif'
        materialize_geotiff(path, dest, gdal, -9999.0, [])
        ds = gdal.Open(str(dest))
        array = ds.GetRasterBand(1).ReadAsArray()
        self.assertEqual(ds.GetRasterBand(1).GetNoDataValue(), -9999.0)
        ds = None
        self.assertEqual(array[5, 2], -9999.0)
        np.testing.assert_array_equal(array[5, 3:6], np.array(KEPT, dtype='float32'))

    def test_pass_through_changing_the_output_nodata_keeps_every_value(self):
        path = self.tif(SEVEN + [-999999.0], nodata=-999999.0)
        dest = self.root / 'pt_32768.tif'
        materialize_geotiff(path, dest, gdal, -32768.0, [])
        ds = gdal.Open(str(dest))
        array = ds.GetRasterBand(1).ReadAsArray()
        ds = None
        np.testing.assert_array_equal(array[5, 2:9], np.array(SEVEN, dtype='float32'))
        self.assertEqual(array[5, 9], -32768.0)  # the declared hole is converted, not lost


class NanAndPriorityRegressionTests(RasterNearNoDataCase):
    def test_nan_cells_still_become_nodata_and_lower_priority_data_still_fills(self):
        top = self.tif([float('nan')], nodata=-999999.0, name='top.tif', fill=200.0)
        low = self.tif([], nodata=-999999.0, name='low.tif', fill=300.0)
        manifest, array, nodata, _out = self.run_dem([low, top], merge_priority='last')
        self.assertEqual(manifest['status'], 'completed')
        self.assertEqual(array[5, 2], 300.0)  # NaN in the preferred input is missing -> lower input fills
        self.assertEqual(array[0, 0], 200.0)
        self.assertEqual(nodata, -9999.0)

    def test_values_near_but_not_in_the_window_and_normal_data_complete(self):
        path = self.tif([-9998.5, -9999.5, 0.0, -0.001, 1234.5])
        manifest, array, nodata, _out = self.run_dem([path])
        self.assertEqual(manifest['status'], 'completed')
        np.testing.assert_array_equal(array[5, 2:7], np.array([-9998.5, -9999.5, 0.0, -0.001, 1234.5], 'float32'))


@unittest.skipUnless(HAVE_GDAL and nodata_window is not None, 'GDAL or the NoData window is not available')
class WindowCoversInstalledGdalTests(unittest.TestCase):
    """The collision window must contain every value the INSTALLED GDAL reads as NoData.
    Measured through BuildVRT srcNodata, Warp srcNodata and the GTiff mask band."""

    VALUES = [-9999.0, -32768.0, -999999.0, 9999.0, 32767.5, -1.0, 1.0, -0.001, 0.001, 0.0, 123.456, -88.8]
    STEPS = 14

    @classmethod
    def setUpClass(cls):
        gdal.UseExceptions()

    def neighbours(self, nodata):
        v = np.float32(nodata)
        below, above = [v], [v]
        for _ in range(self.STEPS):
            below.append(np.nextafter(below[-1], np.float32(-np.inf)))
            above.append(np.nextafter(above[-1], np.float32(np.inf)))
        return np.array(below[::-1] + above[1:], dtype='float32')

    def treated_as_nodata(self, nodata, tmp):
        values = self.neighbours(nodata)
        src = Path(tmp) / 'n.tif'
        ds = gdal.GetDriverByName('GTiff').Create(str(src), values.size, 1, 1, gdal.GDT_Float32)
        ds.SetGeoTransform((0, 1, 0, 1, 0, -1))
        ds.GetRasterBand(1).WriteArray(values.reshape(1, -1))
        ds = None
        found = {}
        vrt = Path(tmp) / 'n.vrt'
        gdal.BuildVRT(str(vrt), [str(src)], srcNodata=nodata, VRTNodata=nodata).FlushCache()
        out = gdal.Open(str(vrt)).ReadAsArray()[0]
        found['BuildVRT'] = {i for i in range(values.size) if out[i] == np.float32(nodata)}
        warped = gdal.Warp('', str(src), format='MEM', srcNodata=nodata, dstNodata=nodata).ReadAsArray()[0]
        found['Warp'] = {i for i in range(values.size) if warped[i] == np.float32(nodata)}
        tagged = Path(tmp) / 't.tif'
        gdal.Translate(str(tagged), str(src), noData=nodata)
        tagged_ds = gdal.Open(str(tagged))  # keep the dataset alive while its mask band is read
        mask = tagged_ds.GetRasterBand(1).GetMaskBand().ReadAsArray()[0]
        tagged_ds = None
        found['mask band'] = {i for i in range(values.size) if mask[i] == 0}
        return values, found

    def test_window_contains_everything_gdal_treats_as_nodata(self):
        worst = {}
        with tempfile.TemporaryDirectory() as tmp:
            for nodata in self.VALUES:
                _v, lo, hi = nodata_window(nodata)
                values, found = self.treated_as_nodata(nodata, tmp)
                for mechanism, indices in found.items():
                    for i in indices:
                        with self.subTest(nodata=nodata, mechanism=mechanism, neighbour=int(i)):
                            self.assertTrue(lo <= float(values[i]) <= hi,
                                            f'GDAL {gdal.__version__} reads {float(values[i])!r} as NoData '
                                            f'{nodata} but the window is [{lo!r}, {hi!r}]')
                    worst[(nodata, mechanism)] = len(indices)
        # recorded in the log of every CI job (the measured tolerance per GDAL version)
        print(f'\nGDAL {gdal.__version__} NoData tolerance (neighbours treated as NoData, incl. exact): '
              + '; '.join(f'{k[0]:g}/{k[1]}={v}' for k, v in sorted(worst.items())), file=sys.stderr)

    def test_window_is_not_wildly_wider_than_gdals_own_tolerance(self):
        """Bounds the false-positive risk, in value terms (float32 steps are asymmetric at powers
        of two): the window half-width may exceed what GDAL actually reads as NoData by the 5/4
        safety margin (+ rounding), never by much more."""
        with tempfile.TemporaryDirectory() as tmp:
            for nodata in (-9999.0, -32768.0, -999999.0, -1.0, 9999.0, 123.456):
                v, lo, hi = nodata_window(nodata)
                step = float(np.nextafter(np.float32(v), np.float32(np.inf)) - np.float32(v))
                values, found = self.treated_as_nodata(nodata, tmp)
                reach = max(abs(float(values[i]) - v) for indices in found.values() for i in indices)
                with self.subTest(nodata=nodata):
                    self.assertLessEqual(hi - v, 1.6 * reach + 2 * step)
                    self.assertGreaterEqual(hi - v, reach)  # and it covers it (also asserted above)

    def test_the_window_is_symmetric_and_contains_the_value(self):
        for value in self.VALUES:
            v, lo, hi = nodata_window(value)
            self.assertLessEqual(lo, v)
            self.assertGreaterEqual(hi, v)
            self.assertAlmostEqual(v - lo, hi - v, delta=abs(v) * 1e-12 + 1e-300)


if __name__ == '__main__':
    unittest.main()
