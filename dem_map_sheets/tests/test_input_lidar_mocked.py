"""LAS/LAZ input handling around PDAL -- MOCKED, no real PDAL involved.

A tiny shell script stands in for ``pdal`` and answers only ``--version`` and
``info --summary`` with canned JSON.  That is enough to test everything the
plugin decides *before* a pipeline runs: PDAL version gate, summary validation,
CRS resolution and rejection, bounds reprojection, reader selection, block and
pixel limits, and the pipeline JSON that would be handed to PDAL.

What this suite can NOT show (needs real PDAL >= 2.9, see the test matrix):
reading a genuine LAS/LAZ/COPC file, truncated/corrupt LAS handling, the
classification filter really keeping ground points, PDAL's own axis-order
handling of the WKT passed to filters.reprojection, SMRF, --stream support.

Process-level failure/cancel behaviour of run_process and the temp-file cleanup
contract are covered by test_pdal_process.py and test_temp_cleanup.py (not repeated).
"""
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from input_fixtures import IS_POSIX, have_gdal, write_fake_pdal  # noqa: E402

HAVE_GDAL = have_gdal()
if HAVE_GDAL:
    from osgeo import osr
    from dem_map_sheets.engine.input_sources import DEFAULTS, discover
    from dem_map_sheets.engine.point_inputs import (
        build_pipeline, executable, inspect_sources, points_to_rasters, stage_unicode_source)

COMPOUND_WKT = ('COMPD_CS["JGD2011 + JGD2011 height",PROJCS["JGD2011 / Japan Plane Rectangular CS IX",'
                'GEOGCS["JGD2011",DATUM["Japanese_Geodetic_Datum_2011",SPHEROID["GRS 1980",6378137,298.257222101]],'
                'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
                'PARAMETER["latitude_of_origin",36],PARAMETER["central_meridian",139.833333333333],'
                'PARAMETER["scale_factor",0.9999],PARAMETER["false_easting",0],PARAMETER["false_northing",0],'
                'UNIT["metre",1]],VERT_CS["JGD2011 (vertical) height",VERT_DATUM["JGD2011",2005],UNIT["metre",1],'
                'AXIS["Gravity-related height",UP]]]')


def summary(bounds=(0.0, 0.0, 10.0, 10.0), num_points=100, srs=None):
    body = {'num_points': num_points, 'srs': srs if srs is not None else {}}
    if bounds is not None:
        body['bounds'] = dict(zip(('minx', 'miny', 'maxx', 'maxy'), bounds))
    return {'summary': body}


@unittest.skipUnless(HAVE_GDAL and IS_POSIX, 'needs osgeo and a POSIX shell for the fake PDAL')
class LidarCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        osr.UseExceptions()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.exe = write_fake_pdal(self.root)
        self.info_file = self.root / 'info.json'
        for key in ('FAKE_PDAL_VERSION', 'FAKE_PDAL_INFO_JSON'):
            self.addCleanup(os.environ.pop, key, None)
        os.environ['FAKE_PDAL_INFO_JSON'] = str(self.info_file)
        self.las = self.root / 'pts.las'
        self.las.write_bytes(b'LASF-not-really')

    def cfg(self, **kw):
        c = {**DEFAULTS, 'input_type': 'lidar', 'input_crs': 'EPSG:6677', 'target_crs': 'EPSG:6677',
             'cell_size': 1.0, 'source_nodata': None, 'max_pixels': 25_000_000,
             'point_tile_size': 64, 'pdal_path': self.exe, 'inputs': [str(self.las)]}
        c.update(kw)
        return c

    def answer(self, payload):
        text = payload if isinstance(payload, str) else json.dumps(payload)
        self.info_file.write_text(text, encoding='utf-8')

    def inspect(self, payload=None, **kw):
        if payload is not None:
            self.answer(payload)
        c = self.cfg(**kw)
        return inspect_sources(c, self.work, self.exe, osr, None)[0]


class PdalGateTests(LidarCase):
    def test_pdal_older_than_2_9_is_refused(self):
        self.answer(summary())
        for version in ('2.8.4', '2.6.0', '1.9.1'):
            with self.subTest(version):
                os.environ['FAKE_PDAL_VERSION'] = version
                with self.assertRaisesRegex(ValueError, r'PDAL >=2\.9'):
                    points_to_rasters(self.cfg(), self.work, None, osr)

    def test_unparseable_version_is_refused(self):
        os.environ['FAKE_PDAL_VERSION'] = 'garbage'
        with self.assertRaisesRegex(ValueError, r'PDAL >=2\.9'):
            points_to_rasters(self.cfg(), self.work, None, osr)

    def test_executable_must_exist_and_not_be_a_script_wrapper(self):
        with self.assertRaisesRegex(ValueError, 'executable not found'):
            executable({'pdal_path': str(self.root / 'nope')})
        bat = self.root / 'pdal.bat'
        bat.write_text('@echo off')
        with self.assertRaisesRegex(ValueError, 'pdal.exe itself'):
            executable({'pdal_path': str(bat)})
        self.assertEqual(executable({'pdal_path': self.exe}), str(Path(self.exe).resolve()))


class SummaryValidationTests(LidarCase):
    def test_valid_summary_yields_a_record_with_reprojected_bounds(self):
        (record,) = self.inspect(summary((100.0, 200.0, 300.0, 400.0), 5000))
        self.assertEqual(record['count'], 5000)
        self.assertEqual(record['reader'], 'readers.las')
        self.assertEqual(record['bounds'], [100.0, 200.0, 300.0, 400.0])
        self.assertIn('Japan Plane Rectangular CS IX', record['crs'])
        self.assertEqual(Path(record['read_path']), self.las.resolve())

    def test_missing_bounds_or_partial_bounds(self):
        with self.assertRaisesRegex(ValueError, 'no readable bounds'):
            self.inspect(summary(bounds=None))
        partial = summary()
        del partial['summary']['bounds']['maxy']
        with self.assertRaisesRegex(ValueError, 'no readable bounds'):
            self.inspect(partial)

    def test_empty_or_zero_point_file(self):
        for count in (0, None):
            with self.subTest(count):
                payload = summary()
                if count is None:
                    del payload['summary']['num_points']
                else:
                    payload['summary']['num_points'] = count
                with self.assertRaisesRegex(ValueError, 'Empty LAS/LAZ'):
                    self.inspect(payload)

    def test_nonfinite_bounds(self):
        for token in ('NaN', 'Infinity', '-Infinity'):
            with self.subTest(token):
                text = ('{"summary": {"num_points": 5, "srs": {}, "bounds": '
                        f'{{"minx": {token}, "miny": 0, "maxx": 1, "maxy": 1}}}}}}')
                with self.assertRaisesRegex(ValueError, 'Non-finite'):
                    self.inspect(text)

    def test_missing_summary_section(self):
        with self.assertRaisesRegex(ValueError, 'no readable bounds'):
            self.inspect({'something': 'else'})

    def test_non_json_pdal_output(self):
        with self.assertRaises(ValueError):  # json.JSONDecodeError is a ValueError
            self.inspect('this is not json')

    def test_pdal_failure_on_info_surfaces_stderr(self):
        failing = self.root / 'bad_pdal'
        failing.write_text('#!/bin/sh\necho "Couldn\'t read header" >&2\nexit 1\n')
        failing.chmod(0o755)
        c = self.cfg()
        with self.assertRaisesRegex(RuntimeError, "PDAL failed \\(1\\).*Couldn't read header"):
            inspect_sources(c, self.work, str(failing), osr, None)


class LidarCrsTests(LidarCase):
    def test_crs_embedded_in_the_summary_is_used_when_input_crs_is_empty(self):
        record, = self.inspect(summary(srs={'wkt': 'EPSG:6677'}), input_crs='')
        self.assertIn('Japan Plane Rectangular CS IX', record['crs'])

    def test_missing_crs_everywhere_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Invalid/missing input CRS'):
            self.inspect(summary(srs={}), input_crs='')

    def test_input_crs_overrides_the_header(self):
        record, = self.inspect(summary(srs={'wkt': 'EPSG:6677'}), input_crs='EPSG:6678')
        self.assertIn('Japan Plane Rectangular CS X', record['crs'])

    def test_garbage_input_crs(self):
        for crs in ('EPSG:99999', 'not a crs'):
            with self.subTest(crs):
                with self.assertRaises((ValueError, RuntimeError)):
                    self.inspect(summary(), input_crs=crs)

    def test_compound_crs_in_the_header_blocks_until_input_crs_is_given(self):
        # Pins review finding E-1c: a horizontal+vertical CRS embedded in the LAS makes
        # the file unusable without an explicit input_crs, even though 'horizontal' is known.
        payload = summary(srs={'compoundwkt': COMPOUND_WKT, 'horizontal': 'EPSG:6677'})
        with self.assertRaisesRegex(ValueError, 'two-dimensional horizontal'):
            self.inspect(payload, input_crs='')
        record, = self.inspect(payload, input_crs='EPSG:6677')
        self.assertEqual(record['bounds'], [0.0, 0.0, 10.0, 10.0])

    def test_bounds_are_reprojected_to_the_target_crs(self):
        # lon/lat in JGD2011 (x=east, y=north) -> plane rectangular zone IX
        record, = self.inspect(summary((139.70, 35.65, 139.72, 35.67)), input_crs='EPSG:6668',
                               target_crs='EPSG:6677')
        tx = osr.CoordinateTransformation(
            self._crs('EPSG:6668'), self._crs('EPSG:6677'))
        ex, ey, _ = tx.TransformPoint(139.70, 35.65)
        minx, miny, maxx, maxy = record['bounds']
        self.assertLessEqual(minx, ex + 1e-6)
        self.assertLessEqual(miny, ey + 1e-6)
        self.assertGreater(maxx - minx, 1000)  # 0.02 deg of longitude is ~1.8 km
        self.assertGreater(maxy - miny, 1000)

    @staticmethod
    def _crs(code):
        from dem_map_sheets.engine.input_sources import crs_of
        return crs_of(code, osr)

    def test_swapped_axis_input_is_not_silently_accepted_as_valid_japan_data(self):
        # lat/lon given as x/y (35.7, 139.7) transforms to a point far outside zone IX's
        # sensible range or fails; either way it must not come out as ordinary metres.
        try:
            record, = self.inspect(summary((35.65, 139.70, 35.67, 139.72)), input_crs='EPSG:6668',
                                   target_crs='EPSG:6677')
        except (ValueError, RuntimeError):
            return
        self.assertFalse(all(abs(v) < 200_000 for v in record['bounds']))


class ReaderAndStagingTests(LidarCase):
    def test_reader_selection_by_file_name(self):
        for name, reader in [('a.las', 'readers.las'), ('b.laz', 'readers.las'),
                             ('c.copc.laz', 'readers.copc'), ('D.COPC.LAZ', 'readers.copc')]:
            with self.subTest(name):
                path = self.root / name
                path.write_bytes(b'x')
                record, = self.inspect(summary(), inputs=[str(path)])
                self.assertEqual(record['reader'], reader)

    def test_ascii_path_is_passed_through_unstaged(self):
        path, staged, method = stage_unicode_source(self.las, self.work, 0)
        self.assertEqual((staged, method), (None, None))
        self.assertEqual(path, str(self.las.resolve()))

    def test_unicode_path_is_staged_under_an_ascii_name_in_work(self):
        jp = self.root / '点群その1.las'
        jp.write_bytes(b'x')
        path, staged, method = stage_unicode_source(jp, self.work, 7)
        self.assertTrue(path.isascii())
        self.assertEqual(Path(path).name, 'input_000007.las')
        self.assertIn(method, ('hardlink', 'copy'))
        self.assertTrue(staged.is_file())

    def test_unicode_path_with_non_ascii_work_dir_is_refused_clearly(self):
        jp = self.root / '点群.las'
        jp.write_bytes(b'x')
        work = self.root / '作業'
        work.mkdir()
        with self.assertRaisesRegex(ValueError, 'ASCII-only output folder'):
            stage_unicode_source(jp, work, 0)

    def test_summary_srs_may_be_a_plain_string(self):
        record, = self.inspect(summary(srs='EPSG:6677'), input_crs='')
        self.assertIn('Japan Plane Rectangular CS IX', record['crs'])

    def test_discovery_accepts_las_family_extensions_only(self):
        for name in ('a.las', 'b.LAZ', 'c.copc.laz', 'd.e57', 'e.xyz'):
            (self.root / name).write_bytes(b'x')
        found = sorted(Path(p).name for p in discover([str(self.root)], '', self.cfg()))
        self.assertEqual(found, ['a.las', 'b.LAZ', 'c.copc.laz', 'pts.las'])


class LidarLimitTests(LidarCase):
    def run_points(self, bounds, **kw):
        self.answer(summary(bounds))
        return points_to_rasters(self.cfg(**kw), self.work, None, osr)

    def test_mosaic_larger_than_max_pixels_is_refused_before_any_pipeline_runs(self):
        # 1 km x 1 km at 1 m = 1e6 px > 5e5
        with self.assertRaisesRegex(ValueError, 'max_pixels'):
            self.run_points((0, 0, 1000, 1000), max_pixels=500_000)
        self.assertEqual(list(self.work.glob('*_pipeline.json')), [])

    def test_too_many_blocks(self):
        with self.assertRaisesRegex(ValueError, 'Too many point DEM blocks'):
            self.run_points((0, 0, 1000, 1000), max_input_files=4)

    def test_buffered_block_larger_than_max_input_pixels(self):
        with self.assertRaisesRegex(ValueError, 'Buffered point block exceeds max_input_pixels'):
            self.run_points((0, 0, 10, 10), max_input_pixels=100)

    def test_continental_scale_extent_is_refused_not_attempted(self):
        # a stray outlier point turning a tile into a continent
        with self.assertRaises(ValueError):
            self.run_points((0, 0, 5_000_000, 5_000_000))

    def test_cancelled_before_start(self):
        class Cancelled:
            def isCanceled(self):
                return True

            def pushInfo(self, _):
                pass
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            points_to_rasters(self.cfg(), self.work, None, osr, Cancelled())


class PipelineContractTests(LidarCase):
    BOUNDS = (100.0, 200.0, 164.0, 264.0)

    def records(self, n=1):
        return [dict(read_path=f'/data/in{i}.las', reader='readers.las', crs='EPSG:6677')
                for i in range(n)]

    def build(self, **kw):
        pipeline, halo = build_pipeline(self.records(), self.BOUNDS, self.cfg(**kw),
                                        self.root / 'o.tif')
        return pipeline['pipeline'], halo

    def stage(self, steps, type_):
        return [s for s in steps if s['type'] == type_]

    def test_classified_mode_keeps_only_configured_ground_classes(self):
        steps, _ = self.build(lidar_mode='classified', ground_classes=[2, 11])
        expr = self.stage(steps, 'filters.expression')[0]['expression']
        self.assertIn('Classification == 2', expr)
        self.assertIn('Classification == 11', expr)
        self.assertIn('Withheld == 0', expr)
        self.assertIn('Classification != 7', expr)   # low noise
        self.assertIn('Classification != 18', expr)  # high noise

    def test_ground_only_mode_does_not_filter_by_class(self):
        steps, _ = self.build(lidar_mode='ground_only')
        expr = self.stage(steps, 'filters.expression')[0]['expression']
        self.assertNotIn('Classification == ', expr)
        self.assertIn('Withheld == 0', expr)

    def test_auto_mode_resets_labels_then_runs_smrf_before_selecting_ground(self):
        steps, _ = self.build(lidar_mode='auto')
        kinds = [s['type'] for s in steps]
        self.assertLess(kinds.index('filters.assign'), kinds.index('filters.smrf'))
        self.assertIn('Classification = 0', self.stage(steps, 'filters.assign')[0]['value'])
        self.assertEqual(self.stage(steps, 'filters.expression')[-1]['expression'], 'Classification == 2')

    def test_reader_reprojection_crop_chain_and_writer_grid(self):
        steps, halo = self.build(cell_size=1.0, point_radius=2.0)
        reader = self.stage(steps, 'readers.las')[0]
        self.assertEqual(reader['override_srs'], 'EPSG:6677')
        self.assertEqual(self.stage(steps, 'filters.reprojection')[0]['out_srs'], 'EPSG:6677')
        writer = self.stage(steps, 'writers.gdal')[0]
        self.assertEqual(halo, math.ceil(2.0 / 1.0) + 2)
        self.assertEqual(writer['width'], 64 + 2 * halo)
        self.assertEqual((writer['origin_x'], writer['origin_y']), (100.0 - halo, 200.0 - halo))
        self.assertEqual(writer['resolution'], 1.0)
        self.assertEqual(writer['nodata'], -9999.0)  # writers.gdal follows output_nodata (default)
        self.assertEqual(writer['output_type'], 'idw')
        self.assertTrue(writer['allow_empty'])

    def test_point_method_and_radius_are_forwarded(self):
        steps, _ = self.build(point_method='min', point_radius=3.5)
        writer = self.stage(steps, 'writers.gdal')[0]
        self.assertEqual((writer['output_type'], writer['radius']), ('min', 3.5))

    def test_every_branch_has_a_unique_tag_so_readers_do_not_inherit_each_other(self):
        c = self.cfg()
        steps = build_pipeline(self.records(3), self.BOUNDS, c, self.root / 'o.tif')[0]['pipeline']
        tags = [s['tag'] for s in steps if 'tag' in s]
        self.assertEqual(len(tags), len(set(tags)))
        self.assertEqual(len(self.stage(steps, 'readers.las')), 3)
        self.assertEqual(len(self.stage(steps, 'writers.gdal')[0]['inputs']), 3)


if __name__ == '__main__':
    unittest.main()
