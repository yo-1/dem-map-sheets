"""M-1 regression tests: temporary-file cleanup on abnormal termination.

Target behaviour (the fix is NOT part of this change; the production
code is intentionally untouched):
- When point processing ends abnormally (PDAL failure on a later
  input, pixel/block limit exceeded, user cancellation), temporary
  intermediates must not be left behind: the per-block buffered
  raster (*_buffer.tif) and the ASCII-staged copies of Unicode-named
  inputs (pdal_ascii_inputs/).
- Diagnostic material must survive: PDAL logs (+ .stderr), pipeline
  JSON files, point_sources.json and pdal_version.txt.

All six tests are ordinary passing tests: the four cleanup cases
failed against the pre-fix code (they were introduced as expected
failures in 6eed194) and pass since the M-1 fix; the two
behaviour-preservation tests pin the keep-logs side of the contract.

A fake PDAL executable (POSIX shell-launched Python) stands in for
real PDAL, so these are mocked tests, not real-PDAL verification.
"""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from osgeo import gdal, osr

from dem_map_sheets.engine.input_sources import DEFAULTS
from dem_map_sheets.engine.point_inputs import points_to_rasters

FAKE_PDAL = r'''
import json, os, sys, time
from pathlib import Path

def main():
    args = sys.argv[1:]
    if args and args[0] == '--version':
        print('pdal 2.9.0 (fake)')
        return 0
    if args and args[0] == 'info':
        counter = Path(os.environ['FAKE_PDAL_COUNTER'])
        n = int(counter.read_text()) + 1 if counter.exists() else 1
        counter.write_text(str(n))
        if os.environ.get('FAKE_PDAL_FAIL_INFO_ON') == str(n):
            print('mock info failure', file=sys.stderr)
            return 3
        print(json.dumps({'summary': {'bounds': {'minx': 0.0, 'miny': 0.0,
            'maxx': 10.0, 'maxy': 10.0}, 'srs': {}, 'num_points': 100}}))
        return 0
    if args and args[0] == 'pipeline':
        spec = json.loads(Path(args[1]).read_text())
        writer = next(s for s in spec['pipeline'] if s.get('type') == 'writers.gdal')
        from osgeo import gdal
        import numpy as np
        w = int(writer['width']); h = int(writer['height']); res = float(writer['resolution'])
        ds = gdal.GetDriverByName('GTiff').Create(writer['filename'], w, h, 1, gdal.GDT_Float32)
        ds.SetGeoTransform((float(writer['origin_x']), res, 0,
                            float(writer['origin_y']) + h * res, 0, -res))
        ds.GetRasterBand(1).WriteArray(np.full((h, w), 100.0, dtype='float32'))
        ds.FlushCache(); ds = None
        ready = os.environ.get('FAKE_PDAL_READY')
        if ready: Path(ready).write_text('ready')
        sleep = float(os.environ.get('FAKE_PDAL_SLEEP', '0'))
        if sleep: time.sleep(sleep)
        if os.environ.get('FAKE_PDAL_FAIL') == '1':
            print('mock pipeline failure', file=sys.stderr)
            return 3
        return 0
    return 2

sys.exit(main())
'''


class _Feedback:
    def __init__(self, ready_file=None):
        self.ready_file = ready_file

    def isCanceled(self):
        return self.ready_file is not None and Path(self.ready_file).exists()

    def pushInfo(self, text):
        pass


@unittest.skipIf(os.name == 'nt', 'fake PDAL executable is a POSIX script')
class TempCleanupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.src = root / 'src'; self.src.mkdir()
        self.work = root / 'work'; self.work.mkdir()
        fake = root / 'fake_pdal'
        fake.write_text('#!/usr/bin/env python3\n' + FAKE_PDAL, encoding='utf-8')
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.fake = str(fake)
        self.counter = root / 'counter.txt'
        self.ready = root / 'ready.txt'
        self._env_keys = ('FAKE_PDAL_COUNTER', 'FAKE_PDAL_FAIL_INFO_ON',
                          'FAKE_PDAL_FAIL', 'FAKE_PDAL_SLEEP', 'FAKE_PDAL_READY')
        for key in self._env_keys:
            self.addCleanup(os.environ.pop, key, None)
        os.environ['FAKE_PDAL_COUNTER'] = str(self.counter)
        csv = self.src / 'pts.csv'
        csv.write_text('X,Y,Z\n0,0,100\n10,0,101\n0,10,102\n10,10,103\n', encoding='utf-8')
        self.csv = str(csv)
        las = self.src / '点群その1.las'
        las.write_bytes(b'FAKE')
        self.las_unicode = str(las)
        las2 = self.src / 'second.las'
        las2.write_bytes(b'FAKE')
        self.las_second = str(las2)

    def _cfg(self, **kw):
        c = dict(DEFAULTS)
        c.update(input_crs='EPSG:6677', target_crs='EPSG:6677', cell_size=10.0,
                 point_tile_size=4, max_pixels=10 ** 9, source_nodata=None,
                 text_mode='points', pdal_path=self.fake)
        c.update(kw)
        return c

    def _staged_leftovers(self):
        stage = self.work / 'pdal_ascii_inputs'
        return list(stage.iterdir()) if stage.is_dir() else []

    def _buffer_leftovers(self):
        return list(self.work.glob('*_buffer.tif'))

    def test_second_input_failure_removes_staged_copies(self):
        os.environ['FAKE_PDAL_FAIL_INFO_ON'] = '2'
        with self.assertRaises(RuntimeError):
            points_to_rasters(self._cfg(input_type='lidar',
                                        inputs=[self.las_unicode, self.las_second]),
                              self.work, gdal, osr, None)
        self.assertEqual(self._staged_leftovers(), [])

    def test_limit_exceeded_removes_staged_copies(self):
        with self.assertRaises(ValueError):
            points_to_rasters(self._cfg(input_type='lidar', inputs=[self.las_unicode],
                                        max_pixels=1),
                              self.work, gdal, osr, None)
        self.assertEqual(self._staged_leftovers(), [])

    def test_cancel_removes_buffer_raster(self):
        os.environ['FAKE_PDAL_READY'] = str(self.ready)
        os.environ['FAKE_PDAL_SLEEP'] = '20'
        with self.assertRaises(RuntimeError):
            points_to_rasters(self._cfg(input_type='text', inputs=[self.csv]),
                              self.work, gdal, osr, _Feedback(self.ready))
        self.assertEqual(self._buffer_leftovers(), [])

    def test_pipeline_failure_removes_large_buffer(self):
        os.environ['FAKE_PDAL_FAIL'] = '1'
        with self.assertRaises(RuntimeError):
            points_to_rasters(self._cfg(input_type='text', inputs=[self.csv],
                                        cell_size=1.0, point_tile_size=512),
                              self.work, gdal, osr, None)
        self.assertEqual(self._buffer_leftovers(), [])

    def test_cleanup_permission_error_does_not_mask_failure(self):
        # F-2: on Windows a just-released PDAL handle can make unlink
        # raise PermissionError; cleanup in the finally blocks must
        # never replace the original exception.
        from unittest import mock
        os.environ['FAKE_PDAL_FAIL_INFO_ON'] = '2'
        real_unlink = Path.unlink

        def locked(self, missing_ok=False):
            if self.suffix == '.las':
                raise PermissionError('simulated locked staged file')
            return real_unlink(self, missing_ok=missing_ok)

        with mock.patch.object(Path, 'unlink', locked):
            with self.assertRaises(RuntimeError) as ctx:
                points_to_rasters(self._cfg(input_type='lidar',
                                            inputs=[self.las_unicode, self.las_second]),
                                  self.work, gdal, osr, None)
        self.assertIn('PDAL failed', str(ctx.exception))

    def test_logs_and_json_survive_failure(self):
        os.environ['FAKE_PDAL_FAIL'] = '1'
        with self.assertRaises(RuntimeError):
            points_to_rasters(self._cfg(input_type='text', inputs=[self.csv]),
                              self.work, gdal, osr, None)
        for name in ('block_000000_pdal.log', 'block_000000_pdal.log.stderr',
                     'block_000000_pipeline.json', 'point_sources.json',
                     'pdal_version.txt'):
            self.assertTrue((self.work / name).is_file(), name)

    def test_success_removes_buffer_keeps_logs(self):
        outputs, _meta = points_to_rasters(self._cfg(input_type='text', inputs=[self.csv]),
                                            self.work, gdal, osr, None)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(self._buffer_leftovers(), [])
        self.assertTrue((self.work / 'point_sources.json').is_file())


if __name__ == '__main__':
    unittest.main()


class RunProcessEnvironmentTests(unittest.TestCase):
    """W-1 residual: QGIS's PYTHONHOME/PYTHONPATH must not leak into
    the PDAL child environment (PROJ_DATA/GDAL_DATA are already
    repointed at the PDAL installation by run_process itself)."""

    def test_python_env_vars_are_stripped_from_child(self):
        import sys
        from unittest import mock
        from dem_map_sheets.engine.point_inputs import run_process
        with tempfile.TemporaryDirectory() as td:
            dump = Path(td) / 'env.json'
            log = Path(td) / 'child.log'
            code = ('import os,json,sys;'
                    'json.dump(dict(os.environ),open(sys.argv[1],"w"))')
            with mock.patch.dict('os.environ',
                                 {'PYTHONHOME': '/qgis-python',
                                  'PYTHONPATH': '/qgis/plugins'}):
                run_process(sys.executable, ['-c', code, str(dump)], log)
            child_env = json.loads(dump.read_text(encoding='utf-8'))
            self.assertNotIn('PYTHONHOME', child_env)
            self.assertNotIn('PYTHONPATH', child_env)
            self.assertIn('PATH', child_env)
