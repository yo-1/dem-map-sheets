"""Regression tests for the configurable output NoData (Issue #8).

Spec under test:
- the pipeline-level ``output_nodata`` config (default -9999.0)
  decides which value marks missing cells in every deliverable;
- inputs keep being interpreted by their own declared definition
  (band NoData, source_nodata, format rules) - never by guessing
  -9999/-999999 - and a raster with no definition and no non-finite
  cells still stops with an error;
- NaN/inf cells are missing: they become output_nodata and never
  hide a valid lower-priority value at merge time;
- passthrough materialization remaps cell values, not just the
  NoData metadata;
- valid cells equal to the chosen output value stop the run with
  NoDataCollisionError (Issue #8 independent audit, 2026-11-11:
  warning-and-continue is not acceptable), on every path - normal
  raster, passthrough, auto priority, and PDAL point inputs, whose
  writer marks empty cells with the internal sentinel so an
  output-valued data cell survives to be detected;
- the TerrainRGB tile encoding keeps its spec-fixed -9999 for
  missing pixels regardless of output_nodata.

Resampling-boundary assertions are value-level on purpose: how far
a NoData area grows or shrinks under bilinear resampling depends on
the GDAL version and grid alignment, so exact NoData cell counts at
resampled boundaries are not a pass condition.
"""
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
    from dem_map_sheets.engine.dem_pipeline import materialize_geotiff, run
    from dem_map_sheets.engine.input_sources import (
        DEFAULTS, NoDataCollisionError, normalize, validate_input)
    from dem_map_sheets.engine.point_inputs import build_pipeline, inspect_sources
    from dem_map_sheets.engine.terrainrgb import decode_elevation, encode_elevation


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


@unittest.skipUnless(HAVE_GDAL, 'GDAL (osgeo) is not available')
class OutputNoDataCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gdal.UseExceptions()
        osr.UseExceptions()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._n = 0

    def config(self, inputs, **kw):
        c = dict(DEFAULTS)
        c.update(input_type='raster', inputs=[str(p) for p in inputs],
                 input_crs='EPSG:6677', target_crs='EPSG:6677', cell_size=10.0,
                 source_nodata=None, max_pixels=10 ** 9, normalize_rasters=True)
        c.update(kw)
        return c

    def run_dem(self, inputs, **kw):
        self._n += 1
        feedback = _Feedback()
        out = self.root / f'out{self._n}'
        manifest = run(self.config(inputs, **kw), out, gdal, osr, feedback)
        return manifest, out, feedback

    def read(self, path):
        ds = gdal.Open(str(path))
        array = ds.GetRasterBand(1).ReadAsArray()
        nodata = ds.GetRasterBand(1).GetNoDataValue()
        gt = ds.GetGeoTransform()
        ds = None
        return array, nodata, gt


class DefaultAndRemapTests(OutputNoDataCase):
    def test_existing_sentinel_input_recognized_default_output(self):
        values = np.array([[100, 101, 102, 103],
                           [104, -999999, 106, 107],
                           [108, 109, 110, 111]], dtype='float32')
        tif = make_geotiff(self.root / 'a.tif', values, nodata=-999999.0)
        manifest, out, _ = self.run_dem([tif])
        array, nodata, _ = self.read(manifest['merged_dem'])
        self.assertEqual(nodata, -9999.0)
        self.assertEqual(array[1, 1], -9999.0)
        self.assertEqual(array[0, 0], 100.0)
        self.assertEqual(manifest['output_nodata'], -9999.0)
        import json
        record = json.loads((out / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(record['output_nodata'], -9999.0)

    def test_metadata_declared_nodata_is_remapped_to_output(self):
        values = np.array([[100, -9999, 102, 103]] * 3, dtype='float32')
        tif = make_geotiff(self.root / 'a.tif', values)  # band NoData -9999
        manifest, _, _ = self.run_dem([tif], output_nodata=-7777.0)
        array, nodata, _ = self.read(manifest['merged_dem'])
        self.assertEqual(nodata, -7777.0)
        self.assertTrue(np.all(array[:, 1] == -7777.0))
        self.assertEqual(array[0, 0], 100.0)

    def test_arbitrary_output_nodata(self):
        values = np.array([[100, -999999, 102, 103]] * 3, dtype='float32')
        tif = make_geotiff(self.root / 'a.tif', values, nodata=-999999.0)
        manifest, _, _ = self.run_dem([tif], output_nodata=-32768.0)
        array, nodata, _ = self.read(manifest['merged_dem'])
        self.assertEqual(nodata, -32768.0)
        self.assertTrue(np.all(array[:, 1] == -32768.0))
        self.assertEqual(manifest['output_nodata'], -32768.0)


class ValidationTests(unittest.TestCase):
    def test_output_nodata_must_be_finite_and_float32_exact(self):
        base = dict(DEFAULTS)
        for bad in (float('nan'), float('inf'), float('-inf'), 0.1, True, 'x'):
            with self.assertRaises(ValueError, msg=repr(bad)):
                validate_input({**base, 'output_nodata': bad})
        for good in (-9999.0, -999999.0, -32768.0, 0.5, 0, 3):
            validate_input({**base, 'output_nodata': good})


class NanHandlingTests(OutputNoDataCase):
    def norm_config(self):
        return {**DEFAULTS, 'input_crs': 'EPSG:6677', 'target_crs': 'EPSG:6677',
                'cell_size': 10.0, 'source_nodata': None, 'max_pixels': 25_000_000}

    def test_nan_with_declared_nodata_becomes_output_nodata(self):
        values = np.array([[1, np.nan, 3, 4]] * 3, dtype='float32')
        tif = make_geotiff(self.root / 'a.tif', values, nodata=-999999.0)
        out = normalize(tif, self.root / 'o.tif', self.norm_config(), gdal, osr)
        array, nodata, _ = self.read(out)
        self.assertEqual(nodata, -9999.0)
        self.assertTrue(np.all(array[:, 1] == -9999.0))
        self.assertTrue(np.all(np.isfinite(array)))

    def test_nan_without_any_definition_is_missing_not_an_error(self):
        values = np.array([[1, np.nan, 3, 4]] * 3, dtype='float32')
        tif = make_geotiff(self.root / 'a.tif', values, nodata=None)
        out = normalize(tif, self.root / 'o.tif', self.norm_config(), gdal, osr)
        array, nodata, _ = self.read(out)
        self.assertEqual(nodata, -9999.0)
        self.assertTrue(np.all(array[:, 1] == -9999.0))

    def test_finite_without_definition_still_stops(self):
        values = np.array([[1, 2, 3, 4]] * 3, dtype='float32')
        tif = make_geotiff(self.root / 'a.tif', values, nodata=None)
        with self.assertRaisesRegex(ValueError, 'no NoData definition'):
            normalize(tif, self.root / 'o.tif', self.norm_config(), gdal, osr)

    def test_nan_does_not_destroy_lower_priority_value(self):
        top = np.full((3, 4), 100.0, dtype='float32')
        top[0, 0] = np.nan
        a = make_geotiff(self.root / 'a.tif', top, nodata=-999999.0)
        b = make_geotiff(self.root / 'b.tif', np.full((3, 4), 50.0, dtype='float32'),
                         nodata=-999999.0)
        manifest, _, _ = self.run_dem([b, a], merge_priority='last')
        array, nodata, _ = self.read(manifest['merged_dem'])
        self.assertEqual(array[0, 0], 50.0)   # filled from the lower priority DEM
        self.assertEqual(array[0, 1], 100.0)  # the higher priority wins where valid
        self.assertTrue(np.all(np.isfinite(array)))


class MergeFillTests(OutputNoDataCase):
    def test_fill_only_from_valid_cells(self):
        hole = -999999.0
        a = make_geotiff(self.root / 'a.tif',
                         np.array([[hole, 2, 3], [4, 5, hole]], dtype='float32'),
                         nodata=hole)
        b = make_geotiff(self.root / 'b.tif',
                         np.array([[7, hole, 9], [hole, 11, hole]], dtype='float32'),
                         nodata=hole)
        manifest, _, _ = self.run_dem([a, b], merge_priority='last')
        array, nodata, _ = self.read(manifest['merged_dem'])
        np.testing.assert_array_equal(
            array, np.array([[7, 2, 9], [4, 11, -9999]], dtype='float32'))

    def test_all_missing_input_warns_and_uses_output_nodata(self):
        tif = make_geotiff(self.root / 'a.tif',
                           np.full((3, 4), -9999.0, dtype='float32'))  # band NoData -9999
        manifest, _, _ = self.run_dem([tif], cell_size=2.0)
        self.assertIn('all_nodata_output', manifest['warnings'])
        array, nodata, _ = self.read(manifest['merged_dem'])
        self.assertEqual(nodata, -9999.0)
        self.assertTrue(np.all(array == -9999.0))


class PassthroughTests(OutputNoDataCase):
    def test_materialize_remaps_values_not_only_metadata(self):
        values = np.array([[10, -9999], [20, 30]], dtype='float32')
        src = make_geotiff(self.root / 'a.tif', values)  # band NoData -9999
        dest = self.root / 'm.tif'
        materialize_geotiff(src, dest, gdal, -32768.0)
        array, nodata, _ = self.read(dest)
        self.assertEqual(nodata, -32768.0)
        np.testing.assert_array_equal(
            array, np.array([[10, -32768], [20, 30]], dtype='float32'))

    def test_materialize_turns_nan_into_output_nodata(self):
        values = np.array([[10, np.nan], [20, 30]], dtype='float32')
        src = make_geotiff(self.root / 'a.tif', values)
        dest = self.root / 'm.tif'
        materialize_geotiff(src, dest, gdal, -32768.0)
        array, nodata, _ = self.read(dest)
        self.assertEqual(array[0, 1], -32768.0)

    def test_passthrough_run_remaps_values(self):
        values = np.array([[100, -9999, 102, 103]] * 3, dtype='float32')
        tif = make_geotiff(self.root / 'a.tif', values)  # band NoData -9999
        manifest, _, _ = self.run_dem([tif], normalize_rasters=False, input_crs='',
                                      output_nodata=-5555.0)
        array, nodata, _ = self.read(manifest['merged_dem'])
        self.assertEqual(nodata, -5555.0)
        self.assertTrue(np.all(array[:, 1] == -5555.0))
        self.assertEqual(array[0, 0], 100.0)


class CollisionGuardTests(OutputNoDataCase):
    def test_collision_stops_the_run(self):
        values = np.array([[100, -9999, 102, 103],
                           [104, 105, -999999, 107],
                           [108, 109, 110, 111]], dtype='float32')
        tif = make_geotiff(self.root / 'a.tif', values, nodata=-999999.0)
        out = self.root / 'collision_out'
        with self.assertRaises(NoDataCollisionError) as ctx:
            run(self.config([tif]), out, gdal, osr, _Feedback())
        message = str(ctx.exception)
        # The error names the input, the colliding value and the
        # detected count, and tells the user the remedy.
        self.assertIn('a.tif', message)
        self.assertIn('-9999.0', message)
        self.assertIn('1 件', message)
        self.assertIn('別の出力NoData値', message)
        # No successful product: the failure is recorded instead.
        import json
        record = json.loads((out / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(record['status'], 'failed')
        self.assertFalse((out / '02_merged_dem.tif').exists())

    def test_collision_with_custom_output_value_stops(self):
        values = np.array([[100, -7777, 102, 103]] * 3, dtype='float32')
        tif = make_geotiff(self.root / 'a.tif', values, nodata=-999999.0)
        with self.assertRaises(NoDataCollisionError) as ctx:
            run(self.config([tif], output_nodata=-7777.0),
                self.root / 'custom_out', gdal, osr, _Feedback())
        self.assertIn('-7777.0', str(ctx.exception))

    def test_declared_nodata_equal_to_output_is_not_a_collision(self):
        # The input's own declared NoData happens to be the output
        # value: those cells are missing by the input's definition,
        # not valid data, so the run must succeed (no false positive).
        values = np.array([[100, -9999, 102, 103]] * 3, dtype='float32')
        tif = make_geotiff(self.root / 'a.tif', values)  # band NoData -9999
        manifest, _, _ = self.run_dem([tif])
        array, nodata, _ = self.read(manifest['merged_dem'])
        self.assertEqual(nodata, -9999.0)
        self.assertTrue(np.all(array[:, 1] == -9999.0))
        self.assertEqual(array[0, 0], 100.0)

    def test_passthrough_collision_stops(self):
        values = np.array([[100, -9999, 102, 103]] * 3, dtype='float32')
        tif = make_geotiff(self.root / 'a.tif', values, nodata=-999999.0)
        with self.assertRaises(NoDataCollisionError):
            run(self.config([tif], normalize_rasters=False, input_crs=''),
                self.root / 'pass_out', gdal, osr, _Feedback())

    def test_auto_priority_collision_stops(self):
        values = np.array([[100, -9999, 102, 103]] * 3, dtype='float32')
        tif = make_geotiff(self.root / 'a.tif', values, nodata=-999999.0)
        with self.assertRaises(NoDataCollisionError):
            run(self.config([tif], merge_priority='auto'),
                self.root / 'auto_out', gdal, osr, _Feedback())

    def test_materialize_collision_raises_and_writes_nothing(self):
        values = np.array([[10, -9999], [20, 30]], dtype='float32')
        src = make_geotiff(self.root / 'a.tif', values, nodata=None)
        notes = []
        dest = self.root / 'm.tif'
        with self.assertRaises(NoDataCollisionError) as ctx:
            materialize_geotiff(src, dest, gdal, -9999.0, notes)
        self.assertIn('1 件', str(ctx.exception))
        self.assertFalse(dest.exists())  # the temp file is removed
        self.assertEqual(len(notes), 1)  # the count is still recorded
        self.assertEqual(notes[0]['nodata_collision_cells'], 1)


class PdalPathTests(OutputNoDataCase):
    def test_writer_marks_empty_cells_with_internal_sentinel(self):
        # The PDAL writer must NOT use the output NoData value for
        # empty cells: a data cell equal to the output value would be
        # indistinguishable from an empty one. With the internal
        # sentinel (-999999) marking empties, an output-valued data
        # cell survives in the block and materialize_geotiff detects
        # it as a collision, like any raster input.
        c = {**DEFAULTS, 'input_type': 'lidar', 'cell_size': 1.0,
             'target_crs': 'EPSG:6677', 'output_nodata': -7777.0}
        records = [dict(read_path='/data/in.las', reader='readers.las', crs='EPSG:6677')]
        pipeline, _halo = build_pipeline(records, (100.0, 200.0, 164.0, 264.0),
                                         c, self.root / 'o.tif')
        writer = [s for s in pipeline['pipeline'] if s['type'] == 'writers.gdal'][0]
        self.assertEqual(writer['nodata'], -999999.0)


class TextPointSentinelTests(OutputNoDataCase):
    def _text_config(self, path, **kw):
        c = {**DEFAULTS, 'input_type': 'text', 'text_mode': 'points',
             'inputs': [str(path)], 'input_crs': 'EPSG:6677',
             'target_crs': 'EPSG:6677', 'source_nodata': None}
        c.update(kw)
        return c

    def _write_points(self, rows):
        path = self.root / 'points.csv'
        path.write_text('X,Y,Z\n' + ''.join(f'{x},{y},{z}\n' for x, y, z in rows),
                        encoding='utf-8')
        return path

    def test_point_at_internal_sentinel_stops_with_guidance(self):
        path = self._write_points([(1000, 2000, 50.0), (1010, 2000, -999999.0)])
        work = self.root / 'work'
        work.mkdir()
        with self.assertRaisesRegex(ValueError, 'source_nodata'):
            inspect_sources(self._text_config(path), work, 'pdal', osr, _Feedback())

    def test_sentinel_point_declared_as_source_nodata_is_skipped(self):
        path = self._write_points([(1000, 2000, 50.0), (1010, 2000, -999999.0)])
        work = self.root / 'work'
        work.mkdir()
        records, _staged = inspect_sources(
            self._text_config(path, source_nodata=-999999.0),
            work, 'pdal', osr, _Feedback())
        self.assertEqual(records[0]['count'], 1)


class GridSizeTests(OutputNoDataCase):
    def test_output_nodata_at_half_one_and_five_metre_grids(self):
        values = (100 + np.arange(16)).astype('float32').reshape(4, 4)
        values[3, 3] = -999999.0
        tif = make_geotiff(self.root / 'a.tif', values, nodata=-999999.0,
                           gt=(1000.0, 10.0, 0.0, 2040.0, 0.0, -10.0))
        for cell in (0.5, 1.0, 5.0):
            with self.subTest(cell_size=cell):
                manifest, _, _ = self.run_dem([tif], cell_size=cell)
                array, nodata, gt = self.read(manifest['merged_dem'])
                self.assertEqual(nodata, -9999.0)
                self.assertAlmostEqual(gt[1], cell)
                # Value-level semantics only: the resampled boundary
                # of the missing corner may grow or shrink with the
                # GDAL version/alignment, so no exact cell counts.
                self.assertTrue(np.all(np.isfinite(array)))
                self.assertTrue(np.any(array == -9999.0))
                valid = array[array != -9999.0]
                self.assertTrue(np.all((valid >= 100.0) & (valid <= 115.0)))


class TerrainRgbEncodingTests(unittest.TestCase):
    def test_tile_nodata_is_spec_fixed_minus9999(self):
        # A valid -9999 m elevation (alpha 255) and a missing pixel
        # (alpha 0) both decode to -9999: the tile encoding value is
        # fixed by the TerrainRGB specification and must not follow
        # the DEM's output_nodata.
        valid = encode_elevation(np.array([[-9999.0]]), nodata=-32768.0)
        self.assertEqual(int(valid[3, 0, 0]), 255)
        self.assertEqual(float(decode_elevation(valid[:3])[0, 0]), -9999.0)
        missing = encode_elevation(np.array([[123.0]]), valid=np.array([[False]]))
        self.assertEqual(int(missing[3, 0, 0]), 0)
        self.assertEqual(float(decode_elevation(missing[:3])[0, 0]), -9999.0)

    def test_dem_nodata_role_is_separate_from_tile_value(self):
        # With the DEM's NoData at -32768, a -32768 cell is missing
        # (alpha 0) while a -9999 cell is valid data (alpha 255).
        rgba = encode_elevation(np.array([[-32768.0, -9999.0]]), nodata=-32768.0)
        self.assertEqual(int(rgba[3, 0, 0]), 0)
        self.assertEqual(int(rgba[3, 0, 1]), 255)


class ProfileRoundTripTests(unittest.TestCase):
    def test_output_nodata_round_trips_through_profiles(self):
        from dem_map_sheets.profiles import load_profile, save_profile
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'profile.json'
            save_profile(path, {'OUTPUT_NODATA': -8888.0, 'CELL': 5.0})
            loaded = load_profile(path, allowed=['OUTPUT_NODATA', 'CELL'])
            self.assertEqual(loaded['OUTPUT_NODATA'], -8888.0)


if __name__ == '__main__':
    unittest.main()
