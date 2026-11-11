"""NoData integrity of point inputs against a REAL PDAL (Issue #9 / PR #10).

Opt-in: set REAL_PDAL_PATH to a pdal executable >= 2.9 (the CI job `real-pdal`
does, using environment-pointcloud.yml).  Nothing here is mocked: LAS/LAZ files
are written by the real PDAL, read back by it, interpolated by writers.gdal,
and the final GeoTIFF is inspected.

Defects covered (claude.ai Windows verification of 2849ef4):
 A. a point with Z == -9999 under IDW / mean contaminated neighbouring cells
    and the run completed (only 'min' stopped);
 B. a LAS point with Z == -999999 became a missing cell without any notice,
    while the same value in a text file stopped the run.
Policy under test (one rule for text, LAS and LAZ, every interpolation method):
 - a point at the output NoData value or at the internal sentinel stops the run
   (status=failed, input / value / count recorded in run.json);
 - a point the input declares missing (NODATA) is dropped and never interpolated;
 - the check is by float32 value, so LAS scale/offset rounding cannot hide it.
"""
import json
import os
import subprocess  # nosec B404: tests invoke the real PDAL binary
import tempfile
import unittest
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

from dem_map_sheets.engine.dem_pipeline import run
from dem_map_sheets.engine.input_sources import DEFAULTS, NoDataCollisionError

PDAL = os.environ.get('REAL_PDAL_PATH', '')
METHODS = ('idw', 'mean', 'min')
SOURCES = ('text', 'las', 'laz')
GRID = [(x, y, 100 + x // 10 + y // 10) for y in (0, 10, 20, 30) for x in (0, 10, 20, 30)]


def make_las(csv_path, las_path, scale_z=0.001, offset_z=0.0):
    spec = {'pipeline': [
        {'type': 'readers.text', 'filename': str(csv_path)},
        {'type': 'writers.las', 'filename': str(las_path), 'scale_x': 0.001, 'scale_y': 0.001,
         'scale_z': scale_z, 'offset_x': 0, 'offset_y': 0, 'offset_z': offset_z,
         **({'compression': 'laszip'} if str(las_path).endswith('.laz') else {})}]}
    spec_file = Path(str(las_path) + '.make.json')
    spec_file.write_text(json.dumps(spec), encoding='utf-8')
    done = subprocess.run([PDAL, 'pipeline', str(spec_file)],  # nosec B603: argv only; fixture generation
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError('LAS generation failed: ' + done.stderr[:500])


@unittest.skipUnless(PDAL, 'REAL_PDAL_PATH is not set')
class RealPdalNoDataCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gdal.UseExceptions()
        osr.UseExceptions()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._n = 0

    # -- inputs --------------------------------------------------------------
    def make_input(self, source, extra_z=None, name='pts', scale_z=0.001, offset_z=0.0, folder=None):
        """16 ground points on a 10 m grid (+ one point at (5,5) when extra_z is given)."""
        folder = Path(folder) if folder else self.root
        folder.mkdir(parents=True, exist_ok=True)
        rows = list(GRID)
        if extra_z is not None:
            rows.append((5, 5, extra_z))
        if source == 'text':
            path = folder / f'{name}.csv'
            path.write_text('X,Y,Z\n' + ''.join(f'{x},{y},{z}\n' for x, y, z in rows), encoding='utf-8')
            return path
        csv = folder / f'{name}_src.csv'
        csv.write_text('X,Y,Z,Classification\n' + ''.join(f'{x},{y},{z},2\n' for x, y, z in rows),
                       encoding='utf-8')
        path = folder / f'{name}.{source}'
        make_las(csv, path, scale_z, offset_z)
        return path

    def config(self, source, path, method, **kw):
        c = dict(DEFAULTS)
        c.update(input_crs='EPSG:6677', target_crs='EPSG:6677', cell_size=10.0, point_tile_size=64,
                 max_pixels=10 ** 9, source_nodata=None, point_radius=15.0, pdal_path=PDAL,
                 point_method=method, inputs=[str(path)],
                 input_type='text' if source == 'text' else 'lidar',
                 **({'text_mode': 'points'} if source == 'text' else {}))
        c.update(kw)
        return c

    def run_dem(self, source, path, method, out=None, **kw):
        self._n += 1
        out = Path(out) if out else self.root / f'out{self._n}'
        manifest = run(self.config(source, path, method, **kw), out, gdal, osr, None)
        ds = gdal.Open(manifest['merged_dem'])
        band = ds.GetRasterBand(1)
        array, nodata = band.ReadAsArray(), band.GetNoDataValue()
        ds = None
        return manifest, array, nodata, out

    def expect_stop(self, source, path, method, **kw):
        self._n += 1
        out = self.root / f'stop{self._n}'
        with self.assertRaises(NoDataCollisionError) as ctx:
            run(self.config(source, path, method, **kw), out, gdal, osr, None)
        record = json.loads((out / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(record['status'], 'failed')
        self.assertFalse(list(out.glob('02_merged*')), 'a collision stop must not publish a DEM')
        return ctx.exception, record

    @staticmethod
    def valid(array, nodata):
        return array[np.isfinite(array) & (array != nodata)]


class CollisionStopTests(RealPdalNoDataCase):
    def test_point_at_output_nodata_stops_every_method_and_source(self):
        """Defect A: IDW and mean used to complete with contaminated cells."""
        for source in SOURCES:
            path = self.make_input(source, -9999, name=f'a_{source}')
            for method in METHODS:
                with self.subTest(source=source, method=method):
                    error, record = self.expect_stop(source, path, method)
                    detail = record['failure_detail']
                    self.assertEqual([(d['value'], d['role'], d['count']) for d in detail],
                                     [(-9999.0, 'output', 1)])
                    self.assertEqual(Path(detail[0]['source']).name, path.name)
                    self.assertIn(path.name, record['error'])
                    self.assertIn('-9999', record['error'])
                    self.assertIn('NODATA', record['error'])

    def test_point_at_internal_sentinel_stops_every_method_and_source(self):
        """Defect B: LAS used to turn -999999 points into missing cells silently."""
        for source in SOURCES:
            path = self.make_input(source, -999999, name=f'b_{source}')
            for method in METHODS:
                with self.subTest(source=source, method=method):
                    error, record = self.expect_stop(source, path, method)
                    detail = record['failure_detail']
                    self.assertEqual([(d['value'], d['role'], d['count']) for d in detail],
                                     [(-999999.0, 'internal', 1)])

    def test_text_and_las_report_the_same_collision(self):
        text = self.make_input('text', -9999, name='t')
        las = self.make_input('las', -9999, name='l')
        _e, rec_text = self.expect_stop('text', text, 'idw')
        _e, rec_las = self.expect_stop('las', las, 'idw')
        norm = lambda rec: [(d['value'], d['role'], d['count']) for d in rec['failure_detail']]  # noqa: E731
        self.assertEqual(norm(rec_text), norm(rec_las))

    def test_both_values_in_one_file_are_both_reported(self):
        csv = self.root / 'two.csv'
        csv.write_text('X,Y,Z,Classification\n' + ''.join(f'{x},{y},{z},2\n' for x, y, z in GRID)
                       + '5,5,-9999,2\n6,6,-999999,2\n7,7,-999999,2\n')
        las = self.root / 'two.las'
        make_las(csv, las)
        _e, record = self.expect_stop('las', las, 'idw')
        self.assertEqual({(d['value'], d['count']) for d in record['failure_detail']},
                         {(-9999.0, 1), (-999999.0, 2)})

    def test_many_colliding_points_are_counted_exactly(self):
        csv = self.root / 'many.csv'
        extra = ''.join(f'{10 + i},{10 + i},-9999,2\n' for i in range(7))
        csv.write_text('X,Y,Z,Classification\n' + ''.join(f'{x},{y},{z},2\n' for x, y, z in GRID) + extra)
        las = self.root / 'many.las'
        make_las(csv, las)
        _e, record = self.expect_stop('las', las, 'mean')
        self.assertEqual(record['failure_detail'][0]['count'], 7)


class CleanRunTests(RealPdalNoDataCase):
    def test_ordinary_point_completes_without_contamination(self):
        for source in SOURCES:
            path = self.make_input(source, 105.5, name=f'ok_{source}')
            for method in METHODS:
                with self.subTest(source=source, method=method):
                    manifest, array, nodata, _out = self.run_dem(source, path, method)
                    self.assertEqual(manifest['status'], 'completed')
                    self.assertEqual(nodata, -9999.0)
                    cells = self.valid(array, nodata)
                    self.assertGreater(cells.size, 8)
                    self.assertGreaterEqual(float(cells.min()), 99.5)
                    self.assertLessEqual(float(cells.max()), 106.5)

    def test_point_cloud_without_any_guarded_value_pays_no_scan(self):
        path = self.make_input('las', 105.5, name='noscan')
        manifest, *_ = self.run_dem('las', path, 'idw', keep_work=True)
        work = Path(manifest['merged_dem']).parent / 'work'
        self.assertEqual(list(work.glob('nodatascan_*')), [])

    def test_nan_text_point_is_a_hard_error_not_a_hole(self):
        path = self.root / 'nan.csv'
        path.write_text('X,Y,Z\n' + ''.join(f'{x},{y},{z}\n' for x, y, z in GRID) + '5,5,nan\n')
        for method in METHODS:
            with self.subTest(method=method):
                with self.assertRaisesRegex(ValueError, 'non-finite'):
                    self.run_dem('text', path, method)
        # LAS/LAZ stores integers: NaN cannot occur there (nothing to test).


class DeclaredNoDataTests(RealPdalNoDataCase):
    def test_declared_missing_points_are_dropped_not_interpolated(self):
        """NODATA given explicitly: the point is missing data, so the DEM equals
        the DEM built without that point, for every method and source."""
        for value in (-9999, -999999):
            for source in SOURCES:
                clean = self.make_input(source, None, name=f'clean_{source}')
                dirty = self.make_input(source, value, name=f'dirty_{source}{abs(value)}')
                for method in METHODS:
                    with self.subTest(value=value, source=source, method=method):
                        _m, base, nd0, _o = self.run_dem(source, clean, method)
                        manifest, array, nd, _o = self.run_dem(source, dirty, method, source_nodata=float(value))
                        self.assertEqual(manifest['status'], 'completed')
                        self.assertEqual(nd, nd0)
                        np.testing.assert_allclose(array, base, atol=1e-3)

    def test_declaring_one_value_does_not_excuse_the_other(self):
        csv = self.root / 'mixed.csv'
        csv.write_text('X,Y,Z,Classification\n' + ''.join(f'{x},{y},{z},2\n' for x, y, z in GRID)
                       + '5,5,-9999,2\n6,6,-999999,2\n')
        las = self.root / 'mixed.las'
        make_las(csv, las)
        _e, record = self.expect_stop('las', las, 'idw', source_nodata=-9999.0)
        self.assertEqual([d['value'] for d in record['failure_detail']], [-999999.0])


class OutputNoDataVariantTests(RealPdalNoDataCase):
    def test_output_nodata_variants(self):
        for out_nd in (-9999.0, -32768.0, -999999.0):
            for source in SOURCES:
                for method in METHODS:
                    with self.subTest(output_nodata=out_nd, source=source, method=method):
                        stop = self.make_input(source, int(out_nd), name=f'v{abs(int(out_nd))}_{source}')
                        _e, record = self.expect_stop(source, stop, method, output_nodata=out_nd)
                        self.assertEqual(record['failure_detail'][0]['value'], out_nd)
                        ok = self.make_input(source, 105.5, name=f'w{abs(int(out_nd))}_{source}')
                        manifest, array, nodata, _o = self.run_dem(source, ok, method, output_nodata=out_nd)
                        self.assertEqual(manifest['status'], 'completed')
                        self.assertEqual(manifest['output_nodata'], out_nd)
                        self.assertEqual(nodata, out_nd)
                        self.assertGreater(self.valid(array, nodata).size, 8)

    def test_a_value_that_is_not_the_output_nodata_is_an_ordinary_elevation(self):
        # -9999 is a valid (deep) elevation once the output NoData is -32768
        for source in SOURCES:
            path = self.make_input(source, -9999, name=f'deep_{source}')
            for method in METHODS:
                with self.subTest(source=source, method=method):
                    manifest, array, nodata, _o = self.run_dem(source, path, method, output_nodata=-32768.0)
                    self.assertEqual(manifest['status'], 'completed')
                    self.assertEqual(nodata, -32768.0)
                    self.assertLess(float(self.valid(array, nodata).min()), 0)

    def test_output_nodata_equal_to_the_sentinel_reports_one_internal_collision(self):
        path = self.make_input('las', -999999, name='same')
        _e, record = self.expect_stop('las', path, 'idw', output_nodata=-999999.0)
        self.assertEqual([(d['value'], d['role']) for d in record['failure_detail']],
                         [(-999999.0, 'internal')])


class LasRoundingTests(RealPdalNoDataCase):
    def test_scale_and_offset_rounding_cannot_hide_a_collision(self):
        # stored -9999.0003 / -9999.004 after quantisation: still the float32 value -9999
        for scale, offset, z in ((0.001, 0.0037, -9999), (0.01, 0.0, -9999.004), (0.001, 0.0, -9999.0004)):
            path = self.make_input('las', z, name=f'r{scale}_{offset}', scale_z=scale, offset_z=offset)
            for method in METHODS:
                with self.subTest(scale=scale, offset=offset, z=z, method=method):
                    _e, record = self.expect_stop('las', path, method)
                    self.assertEqual(record['failure_detail'][0]['value'], -9999.0)

    def test_value_inside_gdals_nodata_tolerance_is_a_collision(self):
        # GDAL re-reads anything within ~4*FLT_EPSILON*|v| of the NoData value as NoData
        # (-9999 +-0.0039): -9999.002 is a different float32 value but would vanish
        # downstream (found by the first real-PDAL run: neighbouring cells disappeared).
        path = self.make_input('las', -9999.002, name='tol')
        for method in METHODS:
            with self.subTest(method=method):
                _e, record = self.expect_stop('las', path, method)
                self.assertEqual(record['failure_detail'][0]['value'], -9999.0)

    def test_value_beyond_the_tolerance_is_an_ordinary_elevation(self):
        path = self.make_input('las', -9999.02, name='beyond')
        manifest, array, nodata, _o = self.run_dem('las', path, 'min')
        self.assertEqual(manifest['status'], 'completed')
        self.assertAlmostEqual(float(self.valid(array, nodata).min()), -9999.02, delta=0.002)

    def test_declared_nodata_matches_through_rounding_too(self):
        path = self.make_input('las', -9999, name='declr', scale_z=0.001, offset_z=0.0037)
        manifest, array, nodata, _o = self.run_dem('las', path, 'idw', source_nodata=-9999.0)
        self.assertEqual(manifest['status'], 'completed')
        self.assertGreaterEqual(float(self.valid(array, nodata).min()), 99.5)


class TempAreaAndPathTests(RealPdalNoDataCase):
    """A Japanese output folder forces the PDAL work into a temporary ASCII area
    (dem_map_sheets_pdal_*); it must not outlive a stop or a success."""

    def with_private_tmp(self):
        sandbox = self.root / 'tmp'
        sandbox.mkdir()
        previous = tempfile.tempdir
        tempfile.tempdir = str(sandbox)
        self.addCleanup(setattr, tempfile, 'tempdir', previous)
        saved = os.environ.pop('DEM_MAP_SHEETS_TEMP', None)
        if saved is not None:
            self.addCleanup(os.environ.__setitem__, 'DEM_MAP_SHEETS_TEMP', saved)
        return sandbox

    def test_failed_runs_leave_no_temp_area_or_staged_copy(self):
        sandbox = self.with_private_tmp()
        for source, z in (('las', -9999), ('laz', -999999), ('text', -9999)):
            with self.subTest(source=source, z=z):
                path = self.make_input(source, z, name=f'点群_{source}', folder=self.root / '入力')
                out = self.root / '出力フォルダ' / f'失敗_{source}'
                with self.assertRaises(NoDataCollisionError):
                    run(self.config(source, path, 'idw'), out, gdal, osr, None)
                self.assertEqual(list(sandbox.glob('dem_map_sheets_pdal_*')), [])
                self.assertEqual(list(out.rglob('pdal_ascii_inputs')), [])
                self.assertEqual(list(out.rglob('*_buffer.tif')), [])
                self.assertEqual(json.loads((out / 'run.json').read_text(encoding='utf-8'))['status'], 'failed')

    def test_successful_run_with_japanese_paths_leaves_no_temp_area(self):
        sandbox = self.with_private_tmp()
        for source in ('las', 'text'):
            with self.subTest(source=source):
                path = self.make_input(source, 105.5, name=f'点群_{source}', folder=self.root / '入力')
                manifest, array, nodata, out = self.run_dem(
                    source, path, 'idw', out=self.root / '出力フォルダ' / f'成功_{source}')
                self.assertEqual(manifest['status'], 'completed')
                self.assertGreater(self.valid(array, nodata).size, 8)
                self.assertEqual(list(sandbox.glob('dem_map_sheets_pdal_*')), [])

    def test_collision_stop_leaves_diagnostics_and_no_bulk_intermediates(self):
        path = self.make_input('las', -9999, name='diag')
        _e, record = self.expect_stop('las', path, 'idw', keep_work=False)
        work = Path(record['work_dir'])
        self.assertEqual(record['work_retained'], 'diagnostics_only')
        self.assertEqual([p.name for p in work.rglob('*') if p.is_file() and p.suffix in ('.tif', '.laz', '.las')], [])
        self.assertTrue(any(p.name.startswith('info_') for p in work.glob('*.json')))


if __name__ == '__main__':
    unittest.main()
