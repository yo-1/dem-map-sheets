"""NoData guard for point inputs (Issue #9 / PR #10) - MOCKED PDAL, no real PDAL.

What is pinned here (the real-PDAL counterpart is test_real_pdal_nodata.py):
- a point whose elevation equals the output NoData value, or the internal
  missing-value sentinel, stops the run for text AND LAS/LAZ inputs, with
  one policy, regardless of the interpolation method;
- a point the input declares as missing (source_nodata) is dropped, not
  treated as a collision;
- the comparison is by float32 value, so LAS scale/offset rounding cannot
  hide a collision;
- the LAS pre-scan runs only when the file's Z range can contain a guarded
  value, asks PDAL for an exact count, and never reads a missing report as
  "no points".
A fake pdal shell script answers --version, `info --summary` and the scan
pipeline (`pipeline X --metadata M --stream`) with canned data.
"""
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from input_fixtures import IS_POSIX, have_gdal  # noqa: E402

HAVE_GDAL = have_gdal()
if HAVE_GDAL:
    from osgeo import osr
    from dem_map_sheets.engine.input_sources import DEFAULTS, NoDataCollisionError
    from dem_map_sheets.engine.point_inputs import (
        _f32_window, _guard_windows, build_pipeline, count_points_in_window, inspect_sources)

FAKE = r'''#!/bin/sh
# --version | info --summary FILE | pipeline SPEC --metadata META --stream
echo "$@" >> "$FAKE_PDAL_CALLS"
case "$1" in
  --version) echo "pdal 2.10.2 (fake)" ;;
  info) cat "$FAKE_PDAL_INFO_JSON" ;;
  pipeline)
    cp "$2" "$FAKE_PDAL_LAST_SPEC"
    case "${FAKE_PDAL_SCAN_MODE:-count}" in
      count) printf '{"stages":{"filters.stats":{"statistic":[{"name":"Z","count":%s}]}}}' "${FAKE_PDAL_SCAN_COUNT:-0}" > "$4" ;;
      empty) printf '{"stages":{"filters.stats":{"statistic":[]}}}' > "$4" ;;
      nostage) printf '{"stages":{}}' > "$4" ;;
      fail) echo "boom" >&2; exit 4 ;;
    esac ;;
  *) exit 2 ;;
esac
'''


def info(zmin=100.0, zmax=200.0, with_z=True):
    bounds = {'minx': 0.0, 'miny': 0.0, 'maxx': 10.0, 'maxy': 10.0}
    if with_z:
        bounds.update(minz=zmin, maxz=zmax)
    return {'summary': {'bounds': bounds, 'srs': {}, 'num_points': 50}}


@unittest.skipUnless(HAVE_GDAL and IS_POSIX, 'needs osgeo and a POSIX shell')
class GuardCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        osr.UseExceptions()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        exe = self.root / 'fake_pdal'
        exe.write_text(FAKE)
        exe.chmod(0o755)
        self.exe = str(exe)
        self.calls = self.root / 'calls.txt'
        self.calls.write_text('')
        self.spec = self.root / 'last_spec.json'
        self.infofile = self.root / 'info.json'
        env = {'FAKE_PDAL_CALLS': str(self.calls), 'FAKE_PDAL_LAST_SPEC': str(self.spec),
               'FAKE_PDAL_INFO_JSON': str(self.infofile)}
        for k, v in env.items():
            os.environ[k] = v
            self.addCleanup(os.environ.pop, k, None)
        for k in ('FAKE_PDAL_SCAN_COUNT', 'FAKE_PDAL_SCAN_MODE'):
            self.addCleanup(os.environ.pop, k, None)
        self.infofile.write_text(json.dumps(info()))
        self.las = self.root / 'a.las'
        self.las.write_bytes(b'x')

    def cfg(self, **kw):
        c = {**DEFAULTS, 'input_type': 'lidar', 'input_crs': 'EPSG:6677', 'target_crs': 'EPSG:6677',
             'cell_size': 1.0, 'source_nodata': None, 'inputs': [str(self.las)],
             'pdal_path': self.exe}
        c.update(kw)
        return c

    def inspect(self, **kw):
        return inspect_sources(self.cfg(**kw), self.work, self.exe, osr, None)[0]

    def scans(self):
        return [line for line in self.calls.read_text().splitlines() if line.startswith('pipeline')]


class WindowTests(GuardCase):
    def test_window_covers_what_gdal_reads_back_as_nodata(self):
        # measured on GDAL 3.8.4: -9999 +-4 float32 steps (0.000977 each) is NoData
        v, lo, hi = _f32_window(-9999.0)
        self.assertEqual(v, -9999.0)
        for inside in (-9999.0, -9999.0004, -9998.9996, -9999.0 + 1e-12, -9999.002, -9999.0039, -9998.9961):
            self.assertTrue(lo <= inside <= hi, inside)
        for outside in (-9999.01, -9998.99, -9998.0, -10000.0):
            self.assertFalse(lo <= outside <= hi, outside)

    def test_window_scales_with_the_magnitude_of_the_nodata_value(self):
        for value, step_count in ((-32768.0, 7), (-999999.0, 7), (-1.0, 4)):
            v, lo, hi = _f32_window(value)
            import numpy as np
            step = float(np.nextafter(np.float32(v), np.float32(np.inf)) - np.float32(v))
            self.assertGreaterEqual(v - lo, step_count * step * 0.999)
            self.assertGreaterEqual(hi - v, step_count * step * 0.999)

    def test_zero_nodata_has_no_tolerance_beyond_float32_rounding(self):
        _v, lo, hi = _f32_window(0.0)
        self.assertFalse(lo <= 1e-6 <= hi)
        self.assertTrue(lo <= 0.0 <= hi)

    def test_guards_default_output_and_sentinel(self):
        guards = _guard_windows({'output_nodata': -9999.0})
        self.assertEqual([(g[0], g[1]) for g in guards], [(-999999.0, 'internal'), (-9999.0, 'output')])

    def test_guards_follow_the_configured_output_nodata(self):
        self.assertEqual([g[0] for g in _guard_windows({'output_nodata': -32768.0})], [-999999.0, -32768.0])
        self.assertEqual([g[0] for g in _guard_windows({})], [-999999.0, -9999.0])

    def test_output_nodata_equal_to_the_sentinel_is_a_single_guard(self):
        guards = _guard_windows({'output_nodata': -999999.0})
        self.assertEqual([(g[0], g[1]) for g in guards], [(-999999.0, 'internal')])


class TextGuardTests(GuardCase):
    def points(self, zs):
        path = self.root / 'p.csv'
        path.write_text('X,Y,Z\n' + ''.join(f'{1000 + 10 * i},2000,{z}\n' for i, z in enumerate(zs)))
        return path

    def text(self, zs, **kw):
        return self.inspect(input_type='text', text_mode='points', inputs=[str(self.points(zs))], **kw)

    def test_point_at_output_nodata_stops_with_structured_detail(self):
        with self.assertRaises(NoDataCollisionError) as ctx:
            self.text([50.0, -9999.0, -9999.0, 60.0])
        err = ctx.exception
        self.assertEqual(err.detail, [{'source': str(self.root / 'p.csv'), 'value': -9999.0,
                                       'role': 'output', 'count': 2}])
        for needle in ('p.csv', '-9999', '2 件', 'NODATA', '別の出力NoData値'):
            self.assertIn(needle, str(err))

    def test_point_at_internal_sentinel_stops_for_text_too(self):
        with self.assertRaises(NoDataCollisionError) as ctx:
            self.text([50.0, -999999.0])
        self.assertEqual(ctx.exception.detail[0]['role'], 'internal')
        self.assertIn('-999999', str(ctx.exception))
        self.assertIn('source_nodata', str(ctx.exception))  # remedy kept from the previous message

    def test_both_values_are_reported_together(self):
        with self.assertRaises(NoDataCollisionError) as ctx:
            self.text([-9999.0, -999999.0])
        self.assertEqual({(d['value'], d['role']) for d in ctx.exception.detail},
                         {(-9999.0, 'output'), (-999999.0, 'internal')})

    def test_every_output_nodata_value_is_guarded(self):
        for out in (-9999.0, -32768.0, -999999.0, 0.0, -1.0):
            with self.subTest(output_nodata=out):
                with self.assertRaises(NoDataCollisionError):
                    self.text([50.0, out], output_nodata=out)

    def test_value_is_valid_when_it_is_not_the_configured_output_nodata(self):
        # -9999 is an ordinary (if deep) elevation once the output NoData is -32768
        records = self.text([50.0, -9999.0], output_nodata=-32768.0)
        self.assertEqual(records[0]['count'], 2)

    def test_float32_equality_not_exact_double(self):
        for z in ('-9999.0000000001', '-9999.0004', '-9998.9996', '-9999', '-9999.002', '-9998.998'):
            with self.subTest(z=z):
                with self.assertRaises(NoDataCollisionError):
                    self.text([50.0, z])
        records = self.text([50.0, '-9999.02', '-9998.98'])  # beyond GDAL's NoData tolerance
        self.assertEqual(records[0]['count'], 3)

    def test_declared_source_nodata_drops_the_points_instead_of_stopping(self):
        for declared in (-9999.0, -999999.0):
            with self.subTest(declared=declared):
                records = self.text([50.0, declared, 60.0], source_nodata=declared)
                self.assertEqual(records[0]['count'], 2)
                cleaned = Path(records[0]['read_path']).read_text()
                self.assertNotIn(str(int(declared)), cleaned)

    def test_declaring_one_value_does_not_excuse_the_other(self):
        with self.assertRaises(NoDataCollisionError) as ctx:
            self.text([50.0, -9999.0, -999999.0], source_nodata=-9999.0)
        self.assertEqual([d['value'] for d in ctx.exception.detail], [-999999.0])

    def test_nan_and_inf_remain_hard_errors(self):
        for token in ('nan', 'inf'):
            with self.subTest(token):
                with self.assertRaisesRegex(ValueError, 'non-finite'):
                    self.text([50.0, token])

    def test_collision_in_a_later_input_names_that_input(self):
        good = self.points([50.0, 60.0])
        bad = self.root / 'bad.csv'
        bad.write_text('X,Y,Z\n1000,2000,-9999\n')
        with self.assertRaises(NoDataCollisionError) as ctx:
            self.inspect(input_type='text', text_mode='points', inputs=[str(good), str(bad)])
        self.assertEqual(ctx.exception.detail[0]['source'], str(bad))

    def test_collision_error_is_a_value_error_for_existing_handlers(self):
        with self.assertRaises(ValueError):
            self.text([-9999.0])


class LasGuardTests(GuardCase):
    def test_file_whose_z_range_excludes_the_guards_is_not_scanned(self):
        self.infofile.write_text(json.dumps(info(100.0, 200.0)))
        self.inspect()
        self.assertEqual(self.scans(), [])

    def test_z_range_touching_a_guard_triggers_an_exact_scan_with_a_float32_window(self):
        self.infofile.write_text(json.dumps(info(-20000.0, 200.0)))  # contains -9999 only
        os.environ['FAKE_PDAL_SCAN_COUNT'] = '0'
        self.inspect()
        self.assertEqual(len(self.scans()), 1)
        spec = json.loads(self.spec.read_text())
        kinds = [s['type'] for s in spec['pipeline']]
        self.assertEqual(kinds, ['readers.las', 'filters.expression', 'filters.stats'])
        self.assertIn('Z >= -9999.00595', spec['pipeline'][1]['expression'])
        self.assertIn('Z <= -9998.99404', spec['pipeline'][1]['expression'])
        self.assertIn('--stream', self.scans()[0])

    def test_hits_stop_the_run_with_the_exact_count(self):
        self.infofile.write_text(json.dumps(info(-20000.0, 200.0)))
        os.environ['FAKE_PDAL_SCAN_COUNT'] = '32'
        with self.assertRaises(NoDataCollisionError) as ctx:
            self.inspect()
        self.assertEqual(ctx.exception.detail[0]['count'], 32)
        self.assertEqual(ctx.exception.detail[0]['role'], 'output')
        self.assertIn('a.las', str(ctx.exception))

    def test_sentinel_valued_las_points_stop_like_text_points(self):
        self.infofile.write_text(json.dumps(info(-1000000.0, 200.0)))
        os.environ['FAKE_PDAL_SCAN_COUNT'] = '32'
        with self.assertRaises(NoDataCollisionError) as ctx:
            self.inspect()
        self.assertEqual({d['role'] for d in ctx.exception.detail}, {'internal', 'output'})
        self.assertIn(-999999.0, [d['value'] for d in ctx.exception.detail])

    def test_declared_nodata_is_excluded_inside_the_scan_expression(self):
        self.infofile.write_text(json.dumps(info(-1000000.0, 200.0)))
        self.inspect(source_nodata=-999999.0)
        # the fake keeps only the last spec: re-run one guard at a time for a precise check
        from dem_map_sheets.engine.point_inputs import _declared_window
        declared = _declared_window({'source_nodata': -999999.0})
        count_points_in_window(self.exe, 'readers.las', str(self.las), (-999999.0005, -998999.0),
                               declared, self.work, 'probe')
        expression = json.loads(self.spec.read_text())['pipeline'][1]['expression']
        self.assertIn('Z >= -999999.0005', expression)
        self.assertIn('(Z < ', expression)
        self.assertIn(' || Z > ', expression)

    def test_missing_z_range_means_scan_not_skip(self):
        self.infofile.write_text(json.dumps(info(with_z=False)))
        os.environ['FAKE_PDAL_SCAN_COUNT'] = '0'
        self.inspect()
        self.assertEqual(len(self.scans()), 2)  # one per guarded value

    def test_empty_statistic_list_is_zero_points(self):
        self.infofile.write_text(json.dumps(info(-20000.0, 200.0)))
        os.environ['FAKE_PDAL_SCAN_MODE'] = 'empty'
        self.inspect()

    def test_unreadable_pdal_report_is_never_read_as_zero(self):
        self.infofile.write_text(json.dumps(info(-20000.0, 200.0)))
        os.environ['FAKE_PDAL_SCAN_MODE'] = 'nostage'
        with self.assertRaisesRegex(RuntimeError, 'could not be verified'):
            self.inspect()

    def test_scan_failure_surfaces_pdal_stderr(self):
        self.infofile.write_text(json.dumps(info(-20000.0, 200.0)))
        os.environ['FAKE_PDAL_SCAN_MODE'] = 'fail'
        with self.assertRaisesRegex(RuntimeError, r'PDAL failed \(4\).*boom'):
            self.inspect()

    def test_scan_files_are_json_and_log_records_kept_in_work(self):
        self.infofile.write_text(json.dumps(info(-20000.0, 200.0)))
        os.environ['FAKE_PDAL_SCAN_COUNT'] = '0'
        self.inspect()
        names = sorted(p.name for p in self.work.iterdir())
        self.assertIn('nodatascan_000000_1_metadata.json', names)
        self.assertIn('nodatascan_000000_1_pipeline.json', names)

    def test_copc_reader_is_used_for_the_scan(self):
        copc = self.root / 'a.copc.laz'
        copc.write_bytes(b'x')
        self.infofile.write_text(json.dumps(info(-20000.0, 200.0)))
        self.inspect(inputs=[str(copc)])
        self.assertEqual(json.loads(self.spec.read_text())['pipeline'][0]['type'], 'readers.copc')


class InterpolationPipelineTests(GuardCase):
    def expression(self, **kw):
        records = [dict(read_path='/data/in.las', reader='readers.las', crs='EPSG:6677')]
        pipeline, _h = build_pipeline(records, (0.0, 0.0, 64.0, 64.0), self.cfg(**kw), self.root / 'o.tif')
        return [s for s in pipeline['pipeline'] if s['type'] == 'filters.expression'][0]['expression']

    def test_declared_nodata_points_are_removed_before_interpolation(self):
        expression = self.expression(source_nodata=-999999.0)
        self.assertIn('(Z < -999999.59', expression)   # 5*FLT_EPSILON*|v| (0.596) below
        self.assertIn('|| Z > -999998.40', expression)  # ... and above

    def test_no_z_filter_without_a_declaration(self):
        self.assertNotIn('Z <', self.expression())
        self.assertNotIn('Z <', self.expression(source_nodata=None))

    def test_exclusion_applies_to_every_lidar_mode(self):
        for mode in ('classified', 'ground_only', 'auto'):
            with self.subTest(mode=mode):
                self.assertIn('(Z < ', self.expression(source_nodata=-9999.0, lidar_mode=mode))

    def test_the_writer_still_uses_the_internal_sentinel_for_empty_cells(self):
        records = [dict(read_path='/data/in.las', reader='readers.las', crs='EPSG:6677')]
        pipeline, _h = build_pipeline(records, (0.0, 0.0, 64.0, 64.0),
                                      self.cfg(source_nodata=-9999.0, output_nodata=-32768.0), self.root / 'o.tif')
        writer = [s for s in pipeline['pipeline'] if s['type'] == 'writers.gdal'][0]
        self.assertEqual(writer['nodata'], -999999.0)


class RunRecordTests(GuardCase):
    def test_failed_run_records_the_cause_in_run_json(self):
        from osgeo import gdal
        from dem_map_sheets.engine.dem_pipeline import run
        gdal.UseExceptions()
        path = self.root / 'p.csv'
        path.write_text('X,Y,Z\n1000,2000,50\n1010,2000,-9999\n')
        c = self.cfg(input_type='text', text_mode='points', inputs=[str(path)],
                     cell_size=10.0, point_tile_size=64, max_pixels=10 ** 9)
        out = self.root / 'out'
        with self.assertRaises(NoDataCollisionError):
            run(c, out, gdal, osr, None)
        record = json.loads((out / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual(record['status'], 'failed')
        self.assertIn('p.csv', record['error'])
        self.assertEqual(record['failure_detail'],
                         [{'source': str(path), 'value': -9999.0, 'role': 'output', 'count': 1}])
        self.assertFalse(any(out.glob('02_merged*')))  # no deliverable on a collision stop


if __name__ == '__main__':
    unittest.main()
